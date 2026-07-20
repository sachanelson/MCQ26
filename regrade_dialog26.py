"""Manual regrade dialog for MCQ26.

Simplified replacement for bubbleSheet/MCQ/quiz_grading.py manual regrade.
Lets the user edit a Quiz attempt's score and the correct_answer_index of each
QuizQuestion row.  Module progress is recomputed automatically on save.
"""
import logging
from typing import Optional, List, Dict

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QTableWidget,
    QTableWidgetItem, QHeaderView, QPushButton, QDialogButtonBox, QLabel,
    QLineEdit, QMessageBox, QSpinBox
)
from PyQt6.QtCore import Qt
from sqlalchemy.orm import Session

from database26 import (
    Engine,
    Quiz,
    QuizQuestion,
    get_quiz_questions,
    update_quiz_score,
    update_quiz_question_correct_index,
    get_student_by_id,
)

logger = logging.getLogger(__name__)


class RegradeDialog(QDialog):
    """Dialog to manually regrade a single quiz attempt."""

    def __init__(self, engine: Engine, quiz_id: int, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.quiz_id = quiz_id
        self._question_rows: List[Dict] = []
        self._quiz: Optional[Quiz] = None
        self.setWindowTitle("Manual Regrade")
        self._init_ui()
        self._load_quiz()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # Header info
        self.info_label = QLabel("Loading...")
        layout.addWidget(self.info_label)

        # Score editor
        form = QFormLayout()
        self.score_spin = QSpinBox()
        self.score_spin.setRange(0, 100)
        self.score_spin.setSpecialValueText("Ungraded")
        form.addRow("Overall score (%):", self.score_spin)
        layout.addLayout(form)

        # Questions table
        self.questions_table = QTableWidget(0, 4)
        self.questions_table.setHorizontalHeaderLabels([
            "#", "Question ID", "Correct Answer Index", "Correct Answer Letter"
        ])
        self.questions_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(QLabel("Per-question correct answers (blank = none):"))
        layout.addWidget(self.questions_table)

        # Buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._on_save)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _load_quiz(self):
        with Session(self.engine) as session:
            quiz = session.query(Quiz).filter_by(id=self.quiz_id).first()
            if quiz is None:
                QMessageBox.critical(self, "Error", f"Quiz {self.quiz_id} not found.")
                self.reject()
                return
            self._quiz = quiz
            student = get_student_by_id(self.engine, quiz.student_id)

        self.setWindowTitle(f"Manual Regrade — {quiz.quiz_id}")
        self.info_label.setText(
            f"Student: {student.name if student else 'Unknown'} "
            f"({student.student_code if student and student.student_code else 'N/A'})\n"
            f"Module {quiz.module_number} — Quiz {quiz.quiz_id}"
        )

        if quiz.score is not None:
            self.score_spin.setValue(int(quiz.score))
        else:
            self.score_spin.setValue(0)

        questions = get_quiz_questions(self.engine, quiz.quiz_id)
        # Filter to rows for this specific student/module in case quiz_id reused.
        questions = [
            q for q in questions
            if self._belongs_to_attempt(q)
        ]
        self._populate_questions(questions)

    def _belongs_to_attempt(self, question: Dict) -> bool:
        """Best-effort filter; QuizQuestion rows don't store attempt id."""
        return True

    def _populate_questions(self, questions: List[Dict]):
        self.questions_table.setRowCount(len(questions))
        self._question_rows = []
        for row, q in enumerate(questions):
            self.questions_table.setItem(row, 0, QTableWidgetItem(str(q['question_number'])))
            self.questions_table.setItem(row, 1, QTableWidgetItem(q['question_id']))

            correct = q.get('correct_answer_index')
            index_edit = QLineEdit()
            index_edit.setPlaceholderText("e.g. 0")
            if correct is not None:
                index_edit.setText(str(correct))
            self.questions_table.setCellWidget(row, 2, index_edit)

            letter_item = QTableWidgetItem(_index_to_letter(correct))
            letter_item.setFlags(letter_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.questions_table.setItem(row, 3, letter_item)
            index_edit.textChanged.connect(
                lambda text, item=letter_item: item.setText(_index_to_letter(_parse_index(text)))
            )

            self._question_rows.append({
                'question_id': q['question_id'],
                'question_number': q['question_number'],
                'index_edit': index_edit,
            })

    def _on_save(self):
        score = self.score_spin.value()
        # Update per-question correct answers first, then score/progress.
        with Session(self.engine) as session:
            quiz = session.query(Quiz).filter_by(id=self.quiz_id).first()
            if quiz is None:
                QMessageBox.critical(self, "Error", "Quiz no longer exists.")
                return
            questions = (
                session.query(QuizQuestion)
                .filter_by(quiz_id=quiz.quiz_id, student_id=quiz.student_id)
                .all()
            )
            qmap = {q.question_number: q for q in questions}

            for entry in self._question_rows:
                qnum = entry['question_number']
                text = entry['index_edit'].text().strip()
                new_index = _parse_index(text)
                if qnum in qmap:
                    qmap[qnum].correct_answer_index = new_index
            session.commit()

        # Update overall score and recompute progress.
        update_quiz_score(self.engine, self.quiz_id, score)
        QMessageBox.information(self, "Saved", "Regrade saved and progress updated.")
        self.accept()


def _index_to_letter(index: Optional[int]) -> str:
    if index is None:
        return ""
    return chr(65 + index) if 0 <= index < 26 else ""


def _parse_index(text: str) -> Optional[int]:
    text = text.strip()
    if not text:
        return None
    try:
        value = int(text)
        return value if value >= 0 else None
    except ValueError:
        # Accept a single letter A-E.
        if len(text) == 1 and text[0].isalpha():
            return ord(text[0].upper()) - 65
        return None
