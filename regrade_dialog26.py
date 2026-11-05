"""Manual regrade UI for MCQ26.

Two dialogs:

* QuizSelectionDialog - a table of quiz attempts (student, quiz ID, module,
  current score). Selecting a row opens RegradeDialog.
* RegradeDialog - lets the grader correct the detected answer for any MCQ
  question (choosing the intended answer, e.g. when a check mark was
  misread) and/or correct the whole Quant score. The relevant scanned page
  is shown beside the questions; selecting a question shows the page it
  appears on, focusing the Quant field shows the Quant pages, and a button
  shows the Quant answer key. Save writes the corrected grade to the
  database, updates student progress, and queues a new feedback email.
"""
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import fitz  # PyMuPDF
import numpy as np
from PIL import Image
from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QTableWidget,
    QTableWidgetItem, QHeaderView, QDialogButtonBox, QLabel, QLineEdit,
    QMessageBox, QComboBox, QScrollArea, QSplitter, QPushButton, QWidget,
    QSpinBox, QCheckBox, QTextEdit, QFormLayout,
)
from sqlalchemy.orm import Session

from database26 import (
    Engine, Quiz, get_all_quizzes, get_all_students, get_course_info,
    get_grading_session, get_grading_sessions, get_quiz_questions,
    get_student_by_id, record_quiz_attempt, record_quiz_regrade,
    revise_quiz_score, student_name_sort_key,
)
from grading26 import (
    ComponentScore, GradingResult, TEST_MODE, _score_against_database,
    _send_feedback_if_enabled, _queue_test_feedback_email,
)
import bubble_scoring26
from quiz_scanner26 import extract_quiz_id_and_page_from_qr

logger = logging.getLogger(__name__)

SCAN_RENDER_DPI = 150


def _artifact_for_quiz(engine: Engine, quiz: Quiz) -> Optional[Path]:
    if quiz.grading_session_id is None:
        return None
    grading_session = get_grading_session(engine, quiz.grading_session_id)
    if not grading_session:
        return None
    session_path = Path(grading_session['scan_path'])
    root = session_path if session_path.is_dir() else session_path.parent
    return next(root.rglob(f'{quiz.quiz_id}_results.json'), None)


def _load_result_artifact(path: Path) -> Dict:
    with open(path, 'r', encoding='utf-8') as file:
        return json.load(file)


def _load_student_answers(path: Path) -> Dict[int, List[str]]:
    data = _load_result_artifact(path)
    return {
        int(number): ([answers] if isinstance(answers, str) else list(answers))
        for number, answers in data.get('student_answers', {}).items()
    }


def _pdf_page_image(pdf_path: Path, page_index: int,
                    dpi: int = SCAN_RENDER_DPI) -> Optional[np.ndarray]:
    """Render one PDF page to an RGB numpy array."""
    try:
        doc = fitz.open(str(pdf_path))
        if page_index < 0 or page_index >= len(doc):
            return None
        pix = doc.load_page(page_index).get_pixmap(
            matrix=fitz.Matrix(dpi / 72, dpi / 72))
        return np.array(Image.frombytes('RGB', (pix.width, pix.height), pix.samples))
    except Exception as e:
        logger.warning(f'Could not render page {page_index} of {pdf_path}: {e}')
        return None


def _find_quant_answer_key(course_folder: str, quiz_id: str) -> Optional[Path]:
    """Locate the stored Quant answer key for *quiz_id* (answers/QUANT/)."""
    quizzes_root = Path(course_folder).expanduser()
    target = f'{quiz_id}_quant_answer.pdf'
    for root, _dirs, files in os.walk(quizzes_root):
        if root.endswith(os.path.join('answers', 'QUANT')) and target in files:
            return Path(root) / target
    return None


def _np_to_pixmap(image: np.ndarray) -> QPixmap:
    rgb = np.ascontiguousarray(image)
    height, width = rgb.shape[:2]
    qimage = QImage(rgb.data, width, height, width * 3,
                    QImage.Format.Format_RGB888).copy()
    return QPixmap.fromImage(qimage)


def regrade_quiz_attempt(
    engine: Engine, quiz_database_id: int,
    student_answers: Optional[Dict[int, List[str]]] = None,
    quant_points_earned: Optional[float] = None,
) -> Optional[int]:
    """Re-score one quiz attempt from (optionally corrected) answers.

    *quant_points_earned*, when given, replaces the stored Quant score so the
    quantitative portion of a specific quiz can be regraded here too.

    Returns the new score, or None if the regrade leaves the stored score
    unchanged - in that case nothing is written: no artifact, no regrade
    record, and no feedback email.
    """
    with Session(engine) as session:
        quiz = session.query(Quiz).filter_by(id=quiz_database_id).first()
        if quiz is None:
            raise ValueError(f'Quiz record {quiz_database_id} was not found.')
        quiz_id = quiz.quiz_id
        student_id = quiz.student_id
        module_number = quiz.module_number
        date_taken = quiz.date_taken
        previous_score = quiz.score
        has_quant = bool(quiz.has_odt)
        quant_total_points = float(quiz.quant_total_points or 0)
        if quant_points_earned is None:
            quant_points_earned = quiz.quant_points_earned
        grading_session_id = quiz.grading_session_id
        artifact = _artifact_for_quiz(engine, quiz)

    if artifact is None or not artifact.is_file():
        raise ValueError(f'No saved grading artifact was found for {quiz_id}.')
    if student_answers is None:
        student_answers = _load_student_answers(artifact)

    questions = get_quiz_questions(engine, quiz_id)
    components, correct_answers, questions = _score_against_database(
        engine, quiz_id, student_answers, questions=questions,
    )
    if has_quant and quant_points_earned is not None:
        components.append(ComponentScore(
            component_type='quant',
            correct=float(quant_points_earned),
            total=quant_total_points,
            score_pct=(100.0 * float(quant_points_earned) / quant_total_points
                       if quant_total_points else 0.0),
        ))
    student = get_student_by_id(engine, student_id)
    result = GradingResult(
        quiz_id=quiz_id,
        student_code=student.student_code if student else None,
        module_number=module_number,
        student_answers=student_answers,
        correct_answers=correct_answers,
        components=components,
        has_quant=has_quant,
        quant_total_points=quant_total_points,
        section_number=student.section_number if student else None,
        student_name=student.name if student else '',
        quiz_questions=questions,
    )
    score = round(result.total_score)
    if previous_score is not None and score == previous_score:
        return None

    regrade_dir = artifact.parents[1] / 'regrades'
    regrade_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    with open(regrade_dir / f'{quiz_id}_{timestamp}.json', 'w', encoding='utf-8') as file:
        json.dump({
            'quiz_id': quiz_id,
            'student_answers': student_answers,
            'correct_answers': correct_answers,
            'quant_points_earned': quant_points_earned,
            'score': score,
            'timestamp': datetime.now().isoformat(),
        }, file, indent=2)

    if TEST_MODE:
        _queue_test_feedback_email(engine, result, is_regrade=True)
    else:
        record_quiz_regrade(engine, quiz_database_id, previous_score, score, student_answers)
        mcq = next(component for component in components if component.component_type == 'mcq')
        record_quiz_attempt(
            engine=engine,
            student_id=student_id,
            module_number=module_number,
            quiz_id=quiz_id,
            date_taken=date_taken,
            score=score,
            total_questions=mcq.total + (quant_total_points if has_quant else 0),
            grading_session_id=grading_session_id,
            mcq_points_earned=mcq.correct,
            mcq_points_possible=mcq.total,
            quant_points_earned=quant_points_earned,
            quant_total_points=quant_total_points if has_quant else 0,
        )
        _send_feedback_if_enabled(engine, result, is_regrade=True)
    return score


def revise_quiz_total(
    engine: Engine, quiz_database_id: int, new_score: int, note: str,
    include_quant: bool,
) -> None:
    """Apply a direct total-score override to one quiz attempt.

    Writes the score + note to the Quiz row (see revise_quiz_score), records
    a QuizRegrade audit row carrying the note, and - when a grading artifact
    for the attempt is on disk - appends a revision record to the session's
    `regrades/` directory, matching the regrade_quiz_attempt convention.
    """
    with Session(engine) as session:
        quiz = session.query(Quiz).filter_by(id=quiz_database_id).first()
        if quiz is None:
            raise ValueError(f'Quiz record {quiz_database_id} was not found.')
        previous_score = quiz.score
        quiz_id = quiz.quiz_id
        artifact = _artifact_for_quiz(engine, quiz)

    if revise_quiz_score(
            engine, quiz_database_id, new_score, note=note,
            include_quant=include_quant) is None:
        raise ValueError(f'Quiz record {quiz_database_id} was not found.')
    record_quiz_regrade(
        engine, quiz_database_id, previous_score, new_score, {},
        notes=note or 'Score revision',
    )

    if artifact is not None and artifact.is_file():
        try:
            regrade_dir = artifact.parents[1] / 'regrades'
            regrade_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            with open(regrade_dir / f'{quiz_id}_revise_{timestamp}.json',
                      'w', encoding='utf-8') as file:
                json.dump({
                    'quiz_id': quiz_id,
                    'type': 'score_revision',
                    'previous_score': previous_score,
                    'new_score': new_score,
                    'note': note,
                    'include_quant': include_quant,
                    'timestamp': datetime.now().isoformat(),
                }, file, indent=2)
        except Exception as e:
            logger.warning(f'Could not write score-revision artifact for {quiz_id}: {e}')


class ScoreRevisionDialog(QDialog):
    """Override a quiz attempt's total score (e.g. disqualification).

    Collects the new 0-100 total, whether to include the Quant component in
    the override (scaled to match, e.g. 0 for a full disqualification), and
    a free-text note recorded on the quiz and the regrade audit row.
    """

    def __init__(self, quiz_id: str, current_score: Optional[int],
                 has_quant: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f'Revise Total Score — {quiz_id}')
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f'Set the total score for {quiz_id} directly. Use this for\n'
            'disqualifications or other instructor overrides.\n'
            f"Current score: {current_score if current_score is not None else 'ungraded'}"
        ))
        form = QFormLayout()
        self.score_spin = QSpinBox()
        self.score_spin.setRange(0, 100)
        self.score_spin.setValue(
            int(current_score) if current_score is not None else 0)
        form.addRow('Total score (0-100):', self.score_spin)
        self.include_quant = QCheckBox('Inc. quant if applic.')
        self.include_quant.setChecked(bool(has_quant))
        self.include_quant.setEnabled(bool(has_quant))
        if not has_quant:
            self.include_quant.setToolTip('This quiz has no Quant section.')
        form.addRow('', self.include_quant)
        self.notes_edit = QTextEdit()
        self.notes_edit.setPlaceholderText(
            'Reason for the revision (stored on the quiz record)')
        self.notes_edit.setMaximumHeight(120)
        form.addRow('Notes:', self.notes_edit)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Apply Revision')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        """Return (new_score, include_quant, note)."""
        return (
            self.score_spin.value(),
            self.include_quant.isChecked(),
            self.notes_edit.toPlainText().strip(),
        )


class QuizSelectionDialog(QDialog):
    """Pick a quiz attempt to regrade: student, quiz ID, module, score.

    With *session_filter* set, only quizzes recorded in that grading session
    are shown. With *exclude_session* set, quizzes recorded in that session
    are hidden (useful for 'prior sessions'). If neither is set, every graded
    quiz is listed.
    """

    def __init__(
        self, engine: Engine, parent=None,
        title: str = 'Select Quiz to Regrade',
        session_filter: Optional[int] = None,
        exclude_session: Optional[int] = None,
    ):
        super().__init__(parent)
        self.engine = engine
        self.session_filter = session_filter
        self.exclude_session = exclude_session
        self.selected_quiz_id: Optional[int] = None
        self.setWindowTitle(title)
        self.resize(700, 500)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Select a quiz attempt, then choose Regrade.'))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ['Student', 'Module', 'Quiz ID', 'Session', 'Score'])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemDoubleClicked.connect(lambda _item: self._accept_selection())
        layout.addWidget(self.table)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Regrade')
        buttons.accepted.connect(self._accept_selection)
        buttons.rejected.connect(self.reject)
        revise_button = QPushButton('Revise Total Score…')
        revise_button.clicked.connect(self._on_revise_score)
        button_row = QHBoxLayout()
        button_row.addWidget(revise_button)
        button_row.addWidget(buttons)
        layout.addLayout(button_row)
        self._populate()

    def _populate(self):
        students = {s.student_id: s for s in get_all_students(self.engine)}
        sessions = {}
        for sess in get_grading_sessions(self.engine):
            folder = os.path.basename((sess.get('scan_path') or '').rstrip(os.sep))
            sessions[sess['grading_session_id']] = (
                folder or f"{sess['session_date']}{sess['letter']}"
            )
        # Scoreless attempts are included only when they were actually scanned
        # (grading_session_id set) - e.g. quizzes skipped during resolution -
        # while generated-but-never-scanned placeholders stay hidden.
        quizzes = [
            q for q in get_all_quizzes(self.engine)
            if q.score is not None or q.grading_session_id is not None
        ]
        if self.session_filter is not None:
            quizzes = [q for q in quizzes if q.grading_session_id == self.session_filter]
        if self.exclude_session is not None:
            quizzes = [q for q in quizzes if q.grading_session_id != self.exclude_session]

        def _sort_key(q):
            student = students.get(q.student_id)
            last, first = student_name_sort_key(student.name if student else '')
            return (last, first, q.module_number or 0, q.quiz_id or '')

        quizzes.sort(key=_sort_key)
        self._quiz_ids = []
        self._quiz_by_id = {}
        self.table.setRowCount(len(quizzes))
        for row, q in enumerate(quizzes):
            self._quiz_ids.append(q.id)
            self._quiz_by_id[q.id] = q
            student = students.get(q.student_id)
            values = [
                student.name if student else '',
                str(q.module_number),
                q.quiz_id,
                sessions.get(q.grading_session_id, ''),
                '' if q.score is None else str(q.score),
            ]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))

    def _accept_selection(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, 'No Selection', 'Select a quiz attempt first.')
            return
        self.selected_quiz_id = self._quiz_ids[row]
        self.accept()

    def _on_revise_score(self):
        """Override the selected attempt's total score (disqualification etc.)."""
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, 'No Selection', 'Select a quiz attempt first.')
            return
        quiz = self._quiz_by_id.get(self._quiz_ids[row])
        if quiz is None:
            return
        dialog = ScoreRevisionDialog(
            quiz.quiz_id, quiz.score, bool(quiz.has_odt), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_score, include_quant, note = dialog.values()
        try:
            revise_quiz_total(
                self.engine, quiz.id, new_score, note, include_quant)
        except Exception as error:
            logger.exception('Could not revise score for %s', quiz.quiz_id)
            QMessageBox.critical(self, 'Could Not Revise Score', str(error))
            return
        quiz.score = new_score
        self.table.setItem(row, 4, QTableWidgetItem(str(new_score)))


class RegradeDialog(QDialog):
    def __init__(self, engine: Engine, quiz_id: int, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.quiz_id = quiz_id
        self._question_rows: List[Dict] = []
        self._quiz: Optional[Quiz] = None
        # Total-score override applied via Revise Total Score in this dialog;
        # saving a regrade afterwards would recalculate from the answers and
        # silently overwrite it, so _on_save confirms first.
        self._revised_score: Optional[int] = None
        self._artifact: Optional[Path] = None
        self._artifact_data: Dict = {}
        self._has_quant = False
        self._quant_total = 0.0
        self._quant_earned: Optional[float] = None
        self._q_to_page: Dict[int, int] = {}
        self._scan_loaded = False
        self._scan_images: Dict[int, np.ndarray] = {}   # printed page -> image
        self._quant_images: List[np.ndarray] = []
        self._scan_path: Optional[Path] = None
        self._ak_dialog: Optional[QDialog] = None
        self.setWindowTitle('Manual Regrade')
        self.resize(1200, 800)
        self._init_ui()
        self._load_quiz()

    # ------------------------------------------------------------------ UI
    def _init_ui(self):
        layout = QVBoxLayout(self)
        self.info_label = QLabel('Loading...')
        layout.addWidget(self.info_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, stretch=1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.questions_table = QTableWidget(0, 4)
        self.questions_table.setHorizontalHeaderLabels([
            '#', 'Question ID', 'Student Answer', 'Correct Answer',
        ])
        self.questions_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.questions_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self.questions_table.currentCellChanged.connect(self._on_question_selected)
        left_layout.addWidget(
            QLabel('Select the intended answer for any misread question:'))
        left_layout.addWidget(self.questions_table)

        self.quant_widget = QWidget()
        quant_row = QHBoxLayout(self.quant_widget)
        quant_row.setContentsMargins(0, 0, 0, 0)
        quant_row.addWidget(QLabel('Quant score:'))
        self.quant_edit = QLineEdit()
        self.quant_edit.setPlaceholderText('points earned')
        self.quant_edit.setMaximumWidth(80)
        self.quant_edit.installEventFilter(self)
        self.quant_edit.textChanged.connect(self._update_score_preview)
        quant_row.addWidget(self.quant_edit)
        self.quant_max_label = QLabel('')
        quant_row.addWidget(self.quant_max_label)
        self.quant_pages_button = QPushButton('Display Quant Pages')
        self.quant_pages_button.clicked.connect(self._show_quant_pages)
        quant_row.addWidget(self.quant_pages_button)
        self.answer_key_button = QPushButton('Show Quant Answer Key')
        self.answer_key_button.clicked.connect(self._show_answer_key)
        quant_row.addWidget(self.answer_key_button)
        quant_row.addStretch()
        self.quant_widget.setVisible(False)
        left_layout.addWidget(self.quant_widget)

        self.score_label = QLabel('Recalculated score: --')
        left_layout.addWidget(self.score_label)

        self.revise_button = QPushButton('Revise Total Score…')
        self.revise_button.clicked.connect(self._on_revise_score)
        left_layout.addWidget(self.revise_button)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self._on_save)
        button_box.rejected.connect(self.reject)
        left_layout.addWidget(button_box)
        splitter.addWidget(left)

        self.page_scroll = QScrollArea()
        self.page_scroll.setWidgetResizable(True)
        self._page_container = QLabel('(select a question to view its scanned page)')
        self._page_container.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_scroll.setWidget(self._page_container)
        splitter.addWidget(self.page_scroll)
        splitter.setSizes([450, 750])

    # ------------------------------------------------------------- loading
    def _load_quiz(self):
        with Session(self.engine) as session:
            quiz = session.query(Quiz).filter_by(id=self.quiz_id).first()
            if quiz is None:
                QMessageBox.critical(self, 'Error', f'Quiz {self.quiz_id} not found.')
                self.reject()
                return
            self._quiz = quiz
            self._artifact = _artifact_for_quiz(self.engine, quiz)
            student = get_student_by_id(self.engine, quiz.student_id)
            quiz_id = quiz.quiz_id
            module_number = quiz.module_number
            self._has_quant = bool(quiz.has_odt)
            self._quant_total = float(quiz.quant_total_points or 0)
            self._quant_earned = quiz.quant_points_earned

        if self._artifact is None or not self._artifact.is_file():
            QMessageBox.critical(
                self, 'Error', f'No saved grading artifact was found for {quiz_id}.')
            self.reject()
            return
        self._artifact_data = _load_result_artifact(self._artifact)
        answers = {
            int(n): ([a] if isinstance(a, str) else list(a))
            for n, a in (self._artifact_data.get('student_answers') or {}).items()
        }
        scan_file = self._artifact_data.get('scan_file')
        if scan_file and Path(scan_file).is_file():
            self._scan_path = Path(scan_file)
        self.setWindowTitle(f'Manual Regrade — {quiz_id}')
        self.info_label.setText(
            f"Student: {student.name if student else 'Unknown'} "
            f"({student.student_code if student and student.student_code else 'N/A'})\n"
            f'Module {module_number} — Quiz {quiz_id}'
        )
        questions = get_quiz_questions(self.engine, quiz_id)
        self._load_question_page_map(questions)
        self._populate_questions(questions, answers)
        if self._has_quant:
            self.quant_widget.setVisible(True)
            self.quant_edit.setText(
                '' if self._quant_earned is None else f'{self._quant_earned:g}')
            self.quant_max_label.setText(f'/ {self._quant_total:g} points')
        self._update_score_preview()

    def _load_question_page_map(self, questions: List[Dict]):
        """Map question number -> printed page using the quiz's metadata."""
        try:
            course_folder = get_course_info(self.engine).get('course_folder', '')
            metadata = bubble_scoring26.load_quiz_metadata(
                course_folder, self._quiz.quiz_id) or {}
            for page_key, page_data in metadata.items():
                if not (isinstance(page_key, str) and page_key.isdigit()):
                    continue
                for q in page_data.get('questions', []):
                    if q.get('number') is not None:
                        self._q_to_page[q['number']] = int(page_key)
        except Exception as e:
            logger.warning(f'Could not load page map for {self._quiz.quiz_id}: {e}')

    def _populate_questions(self, questions: List[Dict], answers: Dict[int, List[str]]):
        self.questions_table.setRowCount(len(questions))
        self._question_rows = []
        for row, question in enumerate(questions):
            number = question['question_number']
            number_item = QTableWidgetItem(str(number))
            id_item = QTableWidgetItem(question['question_id'])
            correct_item = QTableWidgetItem(
                _index_to_letter(question.get('correct_answer_index')))
            for item in (number_item, id_item, correct_item):
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.questions_table.setItem(row, 0, number_item)
            self.questions_table.setItem(row, 1, id_item)
            combo = self._answer_combo(question, answers.get(number, []))
            self.questions_table.setCellWidget(row, 2, combo)
            self.questions_table.setItem(row, 3, correct_item)
            self._question_rows.append({
                'question_number': number,
                'correct': correct_item.text(),
                'combo': combo,
            })

    def _answer_combo(self, question: Dict, detected: List[str]) -> QComboBox:
        """Dropdown of intended answers: blank plus the question's choices."""
        choice_count = len(question.get('answers') or []) or 5
        combo = QComboBox()
        combo.addItem('(unanswered)', [])
        for i in range(max(choice_count, 1)):
            combo.addItem(chr(65 + i), [chr(65 + i)])
        detected = [str(a).upper() for a in detected]
        index = combo.findData(detected)
        if index < 0 and detected:
            combo.addItem(f'{"+".join(detected)} (multiple)', detected)
            index = combo.count() - 1
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.currentIndexChanged.connect(self._update_score_preview)
        return combo

    # ----------------------------------------------------------- scan view
    def eventFilter(self, obj, event):
        if (obj is self.quant_edit and event.type() == QEvent.Type.FocusIn
                and self._has_quant):
            self._show_quant_pages()
        return super().eventFilter(obj, event)

    def _on_question_selected(self, row, _col, _prev_row, _prev_col):
        if 0 <= row < len(self._question_rows):
            self._show_question_page(self._question_rows[row]['question_number'])

    def _ensure_scan_loaded(self):
        if self._scan_loaded:
            return
        self._scan_loaded = True
        if self._scan_path is None:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            doc = fitz.open(str(self._scan_path))
            quiz_id = self._quiz.quiz_id
            mcq_pages_needed = set(self._q_to_page.values())
            zoom = fitz.Matrix(SCAN_RENDER_DPI / 72, SCAN_RENDER_DPI / 72)
            no_qr_pages = []
            for index in range(len(doc)):
                if not mcq_pages_needed:
                    break
                pix = doc.load_page(index).get_pixmap(matrix=zoom)
                image = np.array(
                    Image.frombytes('RGB', (pix.width, pix.height), pix.samples))
                found_id, page_number = extract_quiz_id_and_page_from_qr(
                    image, quiet=True)
                if found_id is None:
                    no_qr_pages.append(index)
                elif found_id == quiz_id and page_number in mcq_pages_needed:
                    self._scan_images[page_number] = image
                    mcq_pages_needed.discard(page_number)
            doc.close()
            if no_qr_pages:
                pages = ', '.join(str(i + 1) for i in no_qr_pages)
                logger.warning(
                    f'{quiz_id}: no QR code on scan page(s) {pages} '
                    f'(expected for quant section pages)')
            for scan_index in self._artifact_data.get('quant_scan_pages') or []:
                image = _pdf_page_image(self._scan_path, int(scan_index))
                if image is not None:
                    self._quant_images.append(image)
        except Exception as e:
            logger.warning(f'Could not load scan pages from {self._scan_path}: {e}')
        finally:
            QApplication.restoreOverrideCursor()

    def _show_question_page(self, question_number: int):
        self._ensure_scan_loaded()
        page = self._q_to_page.get(question_number)
        image = self._scan_images.get(page)
        if image is None:
            self._set_page_text(
                f'Scan page for question {question_number} is unavailable.')
            return
        self._set_page_images([image], f'Question {question_number} — scan page {page}')

    def _show_quant_pages(self):
        self._ensure_scan_loaded()
        if not self._quant_images:
            self._set_page_text('No Quant scan pages recorded for this quiz.')
            return
        self._set_page_images(self._quant_images, 'Quant scan pages')

    def _show_answer_key(self):
        course_folder = get_course_info(self.engine).get('course_folder', '')
        key_path = _find_quant_answer_key(course_folder, self._quiz.quiz_id)
        if key_path is None:
            QMessageBox.information(
                self, 'No Answer Key',
                f'No Quant answer key found for {self._quiz.quiz_id}.')
            return
        if self._ak_dialog is None:
            dialog = QDialog(self)
            dialog.setWindowTitle(f'Quant Answer Key — {self._quiz.quiz_id}')
            dialog.resize(700, 900)
            layout = QVBoxLayout(dialog)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            container = QWidget()
            images_layout = QVBoxLayout(container)
            try:
                doc = fitz.open(str(key_path))
                zoom = fitz.Matrix(SCAN_RENDER_DPI / 72, SCAN_RENDER_DPI / 72)
                for index in range(len(doc)):
                    pix = doc.load_page(index).get_pixmap(matrix=zoom)
                    image = np.array(Image.frombytes(
                        'RGB', (pix.width, pix.height), pix.samples))
                    label = QLabel()
                    label.setPixmap(_np_to_pixmap(image))
                    images_layout.addWidget(label)
            except Exception as e:
                images_layout.addWidget(QLabel(f'Could not render {key_path}: {e}'))
            scroll.setWidget(container)
            layout.addWidget(scroll)
            self._ak_dialog = dialog
        self._ak_dialog.show()
        self._ak_dialog.raise_()

    def _set_page_text(self, text: str):
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_scroll.setWidget(label)
        self._page_container = label

    def _set_page_images(self, images: List[np.ndarray], caption: str):
        container = QWidget()
        vbox = QVBoxLayout(container)
        header = QLabel(caption)
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vbox.addWidget(header)
        for image in images:
            label = QLabel()
            label.setPixmap(_np_to_pixmap(image))
            vbox.addWidget(label)
        self.page_scroll.setWidget(container)

    # ------------------------------------------------------------- scoring
    def _answers(self) -> Dict[int, List[str]]:
        return {
            row['question_number']: list(row['combo'].currentData() or [])
            for row in self._question_rows
        }

    def _quant_override(self) -> Optional[float]:
        """Parsed quant score, or None to keep the stored value."""
        if not self._has_quant:
            return None
        text = self.quant_edit.text().strip()
        if not text:
            return self._quant_earned
        try:
            value = float(text)
        except ValueError:
            raise ValueError('Quant score must be a number.')
        if value < 0 or value > self._quant_total:
            raise ValueError(
                f'Quant score must be between 0 and {self._quant_total:g}.')
        return value

    def _update_score_preview(self, *args):
        try:
            answers = self._answers()
            quant = self._quant_override()
        except ValueError:
            self.score_label.setText('Recalculated score: invalid input')
            return
        correct = sum(
            1 for row in self._question_rows
            if row['correct'] and row['correct'] in answers.get(row['question_number'], [])
        )
        earned, total = float(correct), float(len(self._question_rows))
        if self._has_quant and quant is not None:
            earned += quant
            total += self._quant_total
        score = round(100 * earned / total) if total else 0
        self.score_label.setText(
            f'Recalculated score: {earned:g}/{total:g} = {score}%')

    def _on_revise_score(self):
        """Override the total score directly (disqualification etc.)."""
        if self._quiz is None:
            return
        dialog = ScoreRevisionDialog(
            self._quiz.quiz_id, self._quiz.score, self._has_quant, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_score, include_quant, note = dialog.values()
        try:
            revise_quiz_total(
                self.engine, self.quiz_id, new_score, note, include_quant)
        except Exception as error:
            logger.exception('Could not revise score for %s', self._quiz.quiz_id)
            QMessageBox.critical(self, 'Could Not Revise Score', str(error))
            return
        self._quiz.score = new_score
        self._revised_score = new_score
        self.score_label.setText(f'Score revised to {new_score}.')
        self.info_label.setText(
            self.info_label.text() + f'\nTotal score revised to {new_score}'
            + (f' — {note}' if note else ''))

    def _on_save(self):
        if self._revised_score is not None:
            box = QMessageBox(self)
            box.setWindowTitle('Revised Score Applied')
            box.setIcon(QMessageBox.Icon.Warning)
            box.setText(
                f'The total score was manually revised to {self._revised_score}. '
                'Regrading recalculates the score from the answers and will '
                'overwrite the revision.')
            overwrite = box.addButton(
                'Regrade Anyway', QMessageBox.ButtonRole.DestructiveRole)
            keep = box.addButton(
                'Keep Revision', QMessageBox.ButtonRole.AcceptRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.exec()
            clicked = box.clickedButton()
            if clicked is keep:
                self.accept()
                return
            if clicked is not overwrite:
                return
            self._revised_score = None
        try:
            score = regrade_quiz_attempt(
                self.engine, self.quiz_id, self._answers(), self._quant_override())
        except Exception as error:
            QMessageBox.critical(self, 'Could Not Regrade Quiz', str(error))
            return
        if score is None:
            QMessageBox.information(
                self, 'Regrade Complete',
                'The regrade does not change the stored score. Nothing was updated.')
            return
        action = ('Test feedback email queued' if TEST_MODE
                  else 'Score, progress, and feedback queue updated')
        QMessageBox.information(self, 'Regrade Complete', f'New score: {score}%. {action}.')
        self.accept()


def _index_to_letter(index: Optional[int]) -> str:
    if index is None:
        return ''
    return chr(65 + index) if 0 <= index < 26 else ''
