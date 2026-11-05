"""
Student Progress Application - GUI for tracking student progress and quiz attempts.

MCQ26 version. Uses database26 models and the simplified progress schema:
- One row per student/module records attempts_count and highest_score.
  There is no "completion" state; students may take a quiz up to the
  configured number of attempts (or more, if manually overridden).
- Quiz attempts are stored in the quizzes table.
- Signup/session management is intentionally omitted for now.
"""
import sys
import os
import re
import logging
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTabWidget, QComboBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QGroupBox, QFormLayout,
    QDialog,
)
from PyQt6.QtCore import Qt, QDate

# Local MCQ26 modules
from database26 import (
    Student, Module, Quiz,
    get_all_students, get_modules, get_course_info,
    get_all_student_progress, get_all_quizzes, get_grading_sessions,
    get_grading_session,
    increment_attempts_count,
    delete_quiz_attempt,
    reassign_quiz_attempt,
    reset_module_attempts,
    student_name_sort_key,
)
from sqlalchemy.orm import Session
from shared_gui26 import BaseMCQApp
from regrade_dialog26 import RegradeDialog
from course_info_panel26 import CourseInfoPanel
from qsession_panel26 import QSessionPanel
from grader_panel26 import GraderPanel
from email_management26 import EmailManagementPanel
from participation_panel26 import ParticipationPanel

logger = logging.getLogger(__name__)


def _format_student(student: Student) -> str:
    """Return the student's name for display."""
    return student.name


def _format_template_name(odt_template_path: str) -> str:
    """Return a short Quant template name, e.g. 'ODTquiz2b' from a template path."""
    if not odt_template_path:
        return ''
    name = os.path.basename(odt_template_path)
    name = re.sub(r'\.odt$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'_Template$', '', name, flags=re.IGNORECASE)
    return name


class StudentProgressGUI(BaseMCQApp):
    """Main window for the MCQ26 Student Progress tracker."""

    def __init__(self):
        """Initialize the GUI."""
        super().__init__()
        self.setWindowTitle("Student Progress")
        self._init_ui()

    def _init_ui(self):
        """Build the tab layout."""
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        # Course Info tab (shared with the generator GUI)
        self.course_info_panel = CourseInfoPanel(self.engine, self)
        self.tabs.addTab(self.course_info_panel, "Course Info")

        # Student Progress tab
        self.progress_tab = self._create_progress_tab()
        self.tabs.addTab(self.progress_tab, "Student Progress")

        # Qsessions tab
        self.qsession_panel = QSessionPanel(self.engine, self)
        self.tabs.addTab(self.qsession_panel, "Qsessions")

        # Grader tab
        self.grader_panel = GraderPanel(self.engine, self)
        self.tabs.addTab(self.grader_panel, "Grader")

        # Email Management tab
        self.email_management_panel = EmailManagementPanel(self.engine, self)
        self.tabs.addTab(self.email_management_panel, "Email Management")

        # Participation tab
        self.participation_panel = ParticipationPanel(self.engine, self)
        self.tabs.addTab(self.participation_panel, "Participation")
        self.course_info_panel.students_saved.connect(self.participation_panel.reload)
        self.participation_panel.reload()

        # Quiz Attempts tab
        self.quizzes_tab = self._create_quizzes_tab()
        self.tabs.addTab(self.quizzes_tab, "Quiz Attempts")

        # Quizzes Generated tab
        self.generated_quizzes_tab = self._create_generated_quizzes_tab()
        self.tabs.addTab(self.generated_quizzes_tab, "Quizzes Generated")

        self.tabs.currentChanged.connect(self._on_tab_changed)

    def _on_tab_changed(self, index: int):
        """Refresh data tables when their tab becomes active."""
        if self.tabs.widget(index) is self.quizzes_tab:
            self._refresh_quizzes()
        elif self.tabs.widget(index) is self.progress_tab:
            self._refresh_progress()

    # -----------------------------------------------------------------------
    # Student Progress tab
    # -----------------------------------------------------------------------
    def _create_progress_tab(self):
        """Create the student-versus-module progress table."""
        tab = QWidget()
        layout = QVBoxLayout()
        tab.setLayout(layout)

        # Summary / controls
        controls = QHBoxLayout()
        self.segment_label = QLabel("First segment: loading...")
        controls.addWidget(self.segment_label)
        controls.addStretch()

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh_progress)
        controls.addWidget(refresh_btn)

        layout.addLayout(controls)

        # Progress table: students as rows, modules as columns
        self.progress_table = QTableWidget()
        self.progress_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.progress_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.progress_table.verticalHeader().setVisible(True)
        self.progress_table.itemClicked.connect(self._on_progress_cell_clicked)
        layout.addWidget(self.progress_table)

        # Detail panel for the selected student/module
        detail_group = QGroupBox("Selected Student / Module")
        detail_layout = QFormLayout()
        detail_group.setLayout(detail_layout)

        self.detail_student_label = QLabel("None")
        self.detail_module_label = QLabel("None")
        self.detail_attempts = QLabel("0")
        self.detail_score = QLabel("0.0")

        detail_layout.addRow("Student:", self.detail_student_label)
        detail_layout.addRow("Module:", self.detail_module_label)
        detail_layout.addRow("Attempts:", self.detail_attempts)
        detail_layout.addRow("Highest score:", self.detail_score)

        add_attempt_btn = QPushButton("Add Attempt")
        add_attempt_btn.clicked.connect(self._add_attempt_for_selected)
        detail_layout.addRow(add_attempt_btn)

        layout.addWidget(detail_group)

        self._refresh_progress()
        return tab

    def _refresh_progress(self):
        """Reload the student progress table from the database."""
        students = get_all_students(self.engine)
        modules = get_modules(self.engine)
        course_info = get_course_info(self.engine)
        first_segment = course_info.get('first_segment_count', 4)
        self.segment_label.setText(
            f"First segment: {first_segment} modules | Max attempts per module: {course_info.get('max_attempts_per_module', 4)}"
        )

        self._progress_students = students
        self._progress_modules = modules

        self.progress_table.clear()
        self.progress_table.setRowCount(len(students))
        self.progress_table.setColumnCount(len(modules) + 1)

        # Headers: student | 1 | 2 | ...
        headers = ["Student"] + [str(m['number']) for m in modules]
        self.progress_table.setHorizontalHeaderLabels(headers)

        # Build a quick lookup: (student_id, module_number) -> progress dict
        progress_lookup = {}
        for p in get_all_student_progress(self.engine):
            progress_lookup[(p.student_id, p.module_number)] = {
                'attempts_count': p.attempts_count or 0,
                'highest_score': p.highest_score or 0.0,
            }

        for row, student in enumerate(students):
            self.progress_table.setVerticalHeaderItem(row, QTableWidgetItem(student.student_code))
            name_item = QTableWidgetItem(_format_student(student))
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.progress_table.setItem(row, 0, name_item)

            for col, mod in enumerate(modules, start=1):
                mnum = mod['number']
                info = progress_lookup.get((student.student_id, mnum), {})
                attempts = info.get('attempts_count', 0)
                score = info.get('highest_score', 0.0)

                if score:
                    text = f"{score:.0f} ({attempts})"
                elif attempts:
                    text = f"({attempts})"
                else:
                    text = ""
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setData(Qt.ItemDataRole.UserRole, (student.student_id, mnum))
                self.progress_table.setItem(row, col, item)

        header = self.progress_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for col in range(1, len(modules) + 1):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self.progress_table.setColumnWidth(col, 70)

    def _on_progress_cell_clicked(self, item: QTableWidgetItem):
        """Show details for the clicked student/module cell."""
        if item.column() == 0:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        student_id, module_number = data

        student = self._student_by_id(student_id)
        if student is None:
            return

        self._selected_student_id = student_id
        self._selected_module_number = module_number

        self.detail_student_label.setText(_format_student(student))
        self.detail_module_label.setText(f"Module {module_number}")

        # Load current progress values
        attempts = 0
        highest_score = 0.0
        for p in get_all_student_progress(self.engine):
            if p.student_id == student_id and p.module_number == module_number:
                attempts = p.attempts_count or 0
                highest_score = p.highest_score or 0.0
                break

        self.detail_attempts.setText(str(attempts))
        self.detail_score.setText(f"{highest_score:.1f}")

    def _add_attempt_for_selected(self):
        """Increment attempts for the currently selected student/module."""
        if not hasattr(self, '_selected_student_id') or not hasattr(self, '_selected_module_number'):
            QMessageBox.information(self, "No Selection", "Please select a student/module cell first.")
            return
        increment_attempts_count(
            self.engine,
            self._selected_student_id,
            self._selected_module_number
        )
        self._on_progress_cell_clicked(
            self.progress_table.currentItem()
        )
        self._refresh_progress()

    def _student_by_id(self, student_id: int) -> Student | None:
        """Return a Student object by id from the cached student list."""
        for s in getattr(self, '_progress_students', []):
            if s.student_id == student_id:
                return s
        return None

    # -----------------------------------------------------------------------
    # Quiz Attempts tab
    # -----------------------------------------------------------------------
    def _create_quizzes_tab(self):
        """Create the quiz attempts tab."""
        tab = QWidget()
        layout = QVBoxLayout()
        tab.setLayout(layout)

        # Filter by student
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Student:"))
        self.quiz_student_filter = QComboBox()
        self.quiz_student_filter.addItem("All students", None)
        self._populate_student_combo(self.quiz_student_filter)
        self.quiz_student_filter.currentIndexChanged.connect(self._refresh_quizzes)
        filter_layout.addWidget(self.quiz_student_filter)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh_quizzes)
        filter_layout.addWidget(refresh_btn)

        delete_btn = QPushButton("Delete Selected")
        delete_btn.clicked.connect(self._delete_selected_quiz)
        filter_layout.addWidget(delete_btn)

        regrade_btn = QPushButton("Regrade Selected")
        regrade_btn.clicked.connect(self._regrade_selected_quiz)
        filter_layout.addWidget(regrade_btn)

        layout.addLayout(filter_layout)

        # Quiz table
        self.quizzes_table = QTableWidget()
        self.quizzes_table.setColumnCount(8)
        self.quizzes_table.setHorizontalHeaderLabels(
            ["Student", "Section", "Module", "Quiz ID", "Session", "Score", "Corrected", "Quant Template"]
        )
        self.quizzes_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.quizzes_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.quizzes_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.quizzes_table.cellChanged.connect(self._handle_quiz_student_change)
        layout.addWidget(self.quizzes_table)

        self._refresh_quizzes()
        return tab

    def _populate_student_combo(self, combo: QComboBox):
        """Fill a QComboBox with students, storing student_id as item data."""
        combo.clear()
        combo.addItem("All students", None)
        for student in get_all_students(self.engine):
            combo.addItem(_format_student(student), student.student_id)

    def _refresh_quizzes(self):
        """Reload the quiz attempts table."""
        student_filter = self.quiz_student_filter.currentData()
        quizzes = get_all_quizzes(self.engine)
        students = {s.student_id: s for s in get_all_students(self.engine)}
        sessions = {}
        for sess in get_grading_sessions(self.engine):
            folder = os.path.basename((sess.get('scan_path') or '').rstrip(os.sep))
            sessions[sess['grading_session_id']] = (
                folder or f"{sess['session_date']}{sess['letter']}"
            )

        def _sort_key(quiz):
            student = students.get(quiz.student_id)
            last, first = student_name_sort_key(student.name if student else '')
            return (last, first, quiz.module_number or 0, quiz.date_taken or '')

        rows = []
        for q in sorted(quizzes, key=_sort_key):
            if student_filter is not None and q.student_id != student_filter:
                continue
            # Only show graded attempts; printed quizzes have score=None
            if q.score is None:
                continue
            student = students.get(q.student_id)
            rows.append({
                'id': q.id,
                'student': _format_student(student) if student else f"ID:{q.student_id}",
                'section': (str(student.section_number)
                            if student is not None and student.section_number is not None else ''),
                'module': q.module_number,
                'quiz_id': q.quiz_id,
                'session': sessions.get(q.grading_session_id, ''),
                'score': q.score if q.score is not None else '',
                'corrected': q.date_score_corrected or '' if q.score_corrected else '',
                'template': _format_template_name(q.odt_template_path),
            })

        self._updating_quizzes = True
        try:
            self.quizzes_table.setRowCount(len(rows))
            for row, r in enumerate(rows):
                self.quizzes_table.setItem(row, 0, QTableWidgetItem(r['student']))
                self.quizzes_table.setItem(row, 1, QTableWidgetItem(r['section']))
                self.quizzes_table.setItem(row, 2, QTableWidgetItem(str(r['module'])))
                self.quizzes_table.setItem(row, 3, QTableWidgetItem(r['quiz_id']))
                self.quizzes_table.setItem(row, 4, QTableWidgetItem(r['session']))
                self.quizzes_table.setItem(row, 5, QTableWidgetItem(str(r['score'])))
                self.quizzes_table.setItem(row, 6, QTableWidgetItem(r['corrected']))
                self.quizzes_table.setItem(row, 7, QTableWidgetItem(r['template']))
                self.quizzes_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, r['id'])
                # Only the Student column is editable - editing it starts a
                # quiz transfer to another student.
                for col in range(1, 8):
                    item = self.quizzes_table.item(row, col)
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        finally:
            self._updating_quizzes = False

    def _handle_quiz_student_change(self, row: int, column: int):
        """Handle an edit to the Student column — transfer the quiz to a
        different student, mirroring the transfer flow in GraderPanel."""
        if getattr(self, '_updating_quizzes', False) or column != 0:
            return
        item = self.quizzes_table.item(row, 0)
        if item is None:
            return
        quiz_row_id = item.data(Qt.ItemDataRole.UserRole)
        new_name = item.text().strip()

        with Session(self.engine) as session:
            quiz = session.query(Quiz).filter_by(id=quiz_row_id).first()
            if quiz is None:
                self._refresh_quizzes()
                return
            quiz_id = quiz.quiz_id
            module_number = quiz.module_number
            score = quiz.score
            date_taken = quiz.date_taken
            grading_session_id = quiz.grading_session_id
            old_student_id = quiz.student_id

        students = {s.student_id: s for s in get_all_students(self.engine)}
        old_student = students.get(old_student_id)
        old_name = old_student.name if old_student else f"ID:{old_student_id}"
        if new_name == old_name or not new_name:
            self._refresh_quizzes()
            return

        # Look up the new student by name (case-insensitive exact, then substring).
        all_students = get_all_students(self.engine, enrolled_only=False)
        matches = [s for s in all_students if s.name.lower() == new_name.lower()]
        if not matches:
            matches = [s for s in all_students if new_name.lower() in s.name.lower()]
        if not matches:
            QMessageBox.warning(
                self, 'Student Not Found',
                f'No student matching "{new_name}" was found in the database.',
            )
            self._refresh_quizzes()
            return
        if len(matches) > 1:
            names_list = '\n'.join(f'  {s.name} ({s.student_code})' for s in matches)
            QMessageBox.warning(
                self, 'Multiple Matches',
                f'Multiple students match "{new_name}":\n{names_list}\n\n'
                'Please type the full exact name.',
            )
            self._refresh_quizzes()
            return

        new_student = matches[0]
        if new_student.student_id == old_student_id:
            self._refresh_quizzes()
            return

        msg = QMessageBox(self)
        msg.setWindowTitle('Transfer Quiz')
        msg.setText(
            f'Transfer quiz {quiz_id} from\n'
            f'  {old_name}\n'
            f'to\n'
            f'  {new_student.name} ({new_student.student_code})?'
        )
        msg.setInformativeText(
            'The quiz record will be reassigned to the new student and a new '
            'feedback email will be queued for them.'
        )
        cancel_btn = msg.addButton(QMessageBox.StandardButton.Cancel)
        transfer_btn = msg.addButton('Transfer', QMessageBox.ButtonRole.AcceptRole)
        msg.setDefaultButton(cancel_btn)
        msg.exec()
        if msg.clickedButton() != transfer_btn:
            self._refresh_quizzes()
            return

        # Reassign the Quiz row and its QuizQuestion rows in place, and
        # recompute module progress for both students.
        reassign_quiz_attempt(self.engine, quiz_row_id, new_student.student_id)

        email_status = self._queue_transfer_feedback(
            quiz_id=quiz_id,
            module_number=module_number,
            score=score,
            date_taken=date_taken,
            grading_session_id=grading_session_id,
            new_student=new_student,
        )
        QMessageBox.information(
            self, 'Quiz Transferred',
            f'{quiz_id} transferred to {new_student.name}.\n{email_status}',
        )
        self._refresh_quizzes()
        self._refresh_progress()

    def _queue_transfer_feedback(
        self, quiz_id, module_number, score, date_taken, grading_session_id, new_student,
    ) -> str:
        """Queue a new feedback email for the quiz's new owner.

        Reuses the grading/regrade email path: if the grading artifact for
        this quiz is still on disk it provides the answers needed for
        detailed per-question feedback; otherwise a basic score email is
        queued. Returns a short status string for the confirmation dialog.
        """
        from email26 import generate_and_queue_quiz_feedback

        grading_dir = None
        if grading_session_id:
            sess = get_grading_session(self.engine, grading_session_id)
            if sess:
                grading_dir = sess.get('scan_path') or None

        result_obj = None
        if grading_dir:
            try:
                from grading26 import (
                    ComponentScore, GradingResult, load_grading_artifact,
                    _send_feedback_if_enabled,
                )
                artifact = load_grading_artifact(grading_dir, quiz_id)
                if artifact:
                    result_obj = GradingResult(
                        quiz_id=quiz_id,
                        student_code=new_student.student_code,
                        module_number=module_number,
                        date_taken=date_taken or '',
                        has_quant=bool(artifact.get('has_quant')),
                        quant_total_points=float(artifact.get('quant_total_points') or 0),
                        student_answers=artifact.get('student_answers') or {},
                        correct_answers=artifact.get('correct_answers') or {},
                        components=[
                            ComponentScore(
                                c.get('component_type', 'mcq'),
                                c.get('correct', 0), c.get('total', 0),
                                c.get('score_pct', 0),
                            )
                            for c in artifact.get('components', [])
                        ],
                    )
                    _send_feedback_if_enabled(self.engine, result_obj)
            except Exception as e:
                logger.warning(f'Could not send detailed transfer feedback for {quiz_id}: {e}')
                result_obj = None

        if result_obj is None:
            outcome = generate_and_queue_quiz_feedback(
                student_code=new_student.student_code,
                quiz_score=round(score) if score is not None else 0,
                module_number=module_number or 0,
                date_taken=date_taken or datetime.now().strftime('%Y-%m-%d'),
                engine=self.engine,
            )
            if not outcome.get('queued'):
                return f"Could not queue a feedback email: {outcome.get('error') or outcome.get('reason')}"
            return 'A new feedback email was queued for review.'

        return 'A new feedback email was queued for review.'

    def _regrade_selected_quiz(self):
        """Open the manual regrade dialog for the selected quiz attempt."""
        selected = self.quizzes_table.currentRow()
        if selected < 0:
            QMessageBox.information(self, "No Selection", "Please select a quiz attempt to regrade.")
            return
        quiz_id = self.quizzes_table.item(selected, 0).data(Qt.ItemDataRole.UserRole)
        dialog = RegradeDialog(self.engine, quiz_id, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._refresh_quizzes()
            self._refresh_progress()

    def _delete_selected_quiz(self):
        """Delete the selected quiz attempt."""
        selected = self.quizzes_table.currentRow()
        if selected < 0:
            QMessageBox.information(self, "No Selection", "Please select a quiz attempt to delete.")
            return
        quiz_id_item = self.quizzes_table.item(selected, 0)
        quiz_id = quiz_id_item.data(Qt.ItemDataRole.UserRole)

        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Delete quiz attempt {quiz_id}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        delete_quiz_attempt(self.engine, quiz_id)
        self._refresh_quizzes()
        self._refresh_progress()

    # -----------------------------------------------------------------------
    # Quizzes Generated tab
    # -----------------------------------------------------------------------
    def _create_generated_quizzes_tab(self):
        """Create the quizzes-generated management tab."""
        tab = QWidget()
        layout = QVBoxLayout()
        tab.setLayout(layout)

        # Filters
        filter_layout = QHBoxLayout()

        filter_layout.addWidget(QLabel("Student:"))
        self.generated_student_filter = QComboBox()
        self._populate_student_combo(self.generated_student_filter)
        self.generated_student_filter.currentIndexChanged.connect(
            self._refresh_generated_quizzes
        )
        filter_layout.addWidget(self.generated_student_filter)

        filter_layout.addWidget(QLabel("Module from:"))
        self.generated_module_from = QComboBox()
        self.generated_module_from.addItem("All", None)
        self._populate_module_combo(self.generated_module_from)
        self.generated_module_from.currentIndexChanged.connect(
            self._refresh_generated_quizzes
        )
        filter_layout.addWidget(self.generated_module_from)

        filter_layout.addWidget(QLabel("Module to:"))
        self.generated_module_to = QComboBox()
        self.generated_module_to.addItem("All", None)
        self._populate_module_combo(self.generated_module_to)
        self.generated_module_to.currentIndexChanged.connect(
            self._refresh_generated_quizzes
        )
        filter_layout.addWidget(self.generated_module_to)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh_generated_quizzes)
        filter_layout.addWidget(refresh_btn)

        delete_btn = QPushButton("Delete Selected")
        delete_btn.clicked.connect(self._delete_selected_generated_quiz)
        filter_layout.addWidget(delete_btn)

        reset_btn = QPushButton("Reset Module Attempts")
        reset_btn.clicked.connect(self._reset_attempts_for_selected_generated_quiz)
        filter_layout.addWidget(reset_btn)

        layout.addLayout(filter_layout)

        # Quiz table: all generated quizzes, graded or not
        self.generated_quizzes_table = QTableWidget()
        self.generated_quizzes_table.setColumnCount(6)
        self.generated_quizzes_table.setHorizontalHeaderLabels(
            ["Student", "Module", "Quiz ID", "Date", "Time", "Graded"]
        )
        self.generated_quizzes_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.generated_quizzes_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.generated_quizzes_table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        layout.addWidget(self.generated_quizzes_table)

        self._refresh_generated_quizzes()
        return tab

    def _populate_module_combo(self, combo: QComboBox):
        """Fill a QComboBox with module numbers."""
        combo.clear()
        combo.addItem("All", None)
        for module in get_modules(self.engine):
            number = module['number']
            combo.addItem(str(number), number)

    def _refresh_generated_quizzes(self):
        """Reload the generated-quizzes table."""
        student_filter = self.generated_student_filter.currentData()
        module_from = self.generated_module_from.currentData()
        module_to = self.generated_module_to.currentData()

        quizzes = get_all_quizzes(self.engine)
        students = {s.student_id: s for s in get_all_students(self.engine)}

        rows = []
        for q in quizzes:
            if student_filter is not None and q.student_id != student_filter:
                continue
            if module_from is not None and q.module_number < module_from:
                continue
            if module_to is not None and q.module_number > module_to:
                continue
            student = students.get(q.student_id)
            rows.append({
                'quiz_id': q.id,
                'student_id': q.student_id,
                'module_number': q.module_number,
                'student': _format_student(student) if student else f"ID:{q.student_id}",
                'quiz_id_text': q.quiz_id,
                'date': q.date_taken,
                'time': q.time_taken or '',
                'graded': 'Yes' if q.score is not None else 'No',
            })

        self.generated_quizzes_table.setRowCount(len(rows))
        for row, r in enumerate(rows):
            self.generated_quizzes_table.setItem(row, 0, QTableWidgetItem(r['student']))
            self.generated_quizzes_table.setItem(row, 1, QTableWidgetItem(str(r['module_number'])))
            self.generated_quizzes_table.setItem(row, 2, QTableWidgetItem(r['quiz_id_text']))
            self.generated_quizzes_table.setItem(row, 3, QTableWidgetItem(r['date']))
            self.generated_quizzes_table.setItem(row, 4, QTableWidgetItem(r['time']))
            self.generated_quizzes_table.setItem(row, 5, QTableWidgetItem(r['graded']))
            self.generated_quizzes_table.item(row, 0).setData(
                Qt.ItemDataRole.UserRole,
                {
                    'quiz_id': r['quiz_id'],
                    'student_id': r['student_id'],
                    'module_number': r['module_number'],
                }
            )

    def _delete_selected_generated_quiz(self):
        """Delete the selected generated quiz from the database."""
        selected = self.generated_quizzes_table.currentRow()
        if selected < 0:
            QMessageBox.information(
                self, "No Selection", "Please select a generated quiz to delete."
            )
            return
        data = self.generated_quizzes_table.item(selected, 0).data(Qt.ItemDataRole.UserRole)
        quiz_id = data.get('quiz_id') if isinstance(data, dict) else data

        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Delete generated quiz {quiz_id}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        delete_quiz_attempt(self.engine, quiz_id)
        self._refresh_generated_quizzes()
        self._refresh_quizzes()
        self._refresh_progress()

    def _reset_attempts_for_selected_generated_quiz(self):
        """Reset attempts_count to 0 for the student/module of the selected quiz."""
        selected = self.generated_quizzes_table.currentRow()
        if selected < 0:
            QMessageBox.information(
                self, "No Selection",
                "Please select a generated quiz to reset attempts for."
            )
            return
        data = self.generated_quizzes_table.item(selected, 0).data(Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict):
            return
        student_id = data['student_id']
        module_number = data['module_number']
        student = self._student_by_id(student_id)
        student_label = _format_student(student) if student else f"ID:{student_id}"

        reply = QMessageBox.question(
            self,
            "Confirm Reset",
            f"Reset attempts to 0 for {student_label}, module {module_number}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        reset_module_attempts(self.engine, student_id, module_number)
        self._refresh_progress()


def main():
    """Main function to run the Student Progress application."""
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    window = StudentProgressGUI()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
