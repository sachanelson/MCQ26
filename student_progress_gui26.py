"""
Student Progress Application - GUI for tracking student progress and quiz attempts.

MCQ26 version. Uses database26 models and the simplified progress schema:
- One row per student/module records completed, attempts_count, highest_score.
- Quiz attempts are stored in the quizzes table.
- Signup/session management is intentionally omitted for now.
"""
import sys
import os
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTabWidget, QComboBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QGroupBox, QFormLayout,
    QSpinBox, QCheckBox, QDateEdit, QDialog, QDialogButtonBox
)
from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QColor

# Local MCQ26 modules
from database26 import (
    Student, Module, Quiz,
    get_all_students, get_modules, get_course_info,
    get_all_student_progress, get_all_quizzes,
    update_progress_completed, increment_attempts_count,
    record_quiz_attempt, delete_quiz_attempt,
    get_section_meeting_grades, get_section_meetings, get_students_for_section,
    save_section_meeting_grade,
)
from shared_gui26 import BaseMCQApp
from regrade_dialog26 import RegradeDialog
from course_info_panel26 import CourseInfoPanel
from qsession_panel26 import QSessionPanel
from section_workspace26 import export_meeting_grades


def _format_student(student: Student) -> str:
    """Return the student's name for display."""
    return student.name


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

        # Quiz Session tab
        self.qsession_panel = QSessionPanel(self.engine, self)
        self.tabs.addTab(self.qsession_panel, "Quiz Session")

        # Quiz Attempts tab
        self.quizzes_tab = self._create_quizzes_tab()
        self.tabs.addTab(self.quizzes_tab, "Quiz Attempts")

        self.section_grades_tab = self._create_section_grades_tab()
        self.tabs.addTab(self.section_grades_tab, "Section Meeting Grades")

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
        self.detail_completed = QCheckBox("Completed")
        self.detail_completed.stateChanged.connect(self._detail_completed_changed)
        self.detail_attempts = QLabel("0")
        self.detail_score = QLabel("0.0")

        detail_layout.addRow("Student:", self.detail_student_label)
        detail_layout.addRow("Module:", self.detail_module_label)
        detail_layout.addRow("Completed:", self.detail_completed)
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
                'completed': p.completed,
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
                completed = info.get('completed', False)
                attempts = info.get('attempts_count', 0)
                score = info.get('highest_score', 0.0)

                if score:
                    text = f"{score:.0f}"
                else:
                    text = ""
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setData(Qt.ItemDataRole.UserRole, (student.student_id, mnum))
                if completed:
                    item.setBackground(QColor(200, 255, 200))
                else:
                    item.setBackground(QColor(255, 255, 255))
                self.progress_table.setItem(row, col, item)

        header = self.progress_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for col in range(1, len(modules) + 1):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)

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
        completed = False
        attempts = 0
        highest_score = 0.0
        for p in get_all_student_progress(self.engine):
            if p.student_id == student_id and p.module_number == module_number:
                completed = p.completed
                attempts = p.attempts_count or 0
                highest_score = p.highest_score or 0.0
                break

        self.detail_completed.blockSignals(True)
        self.detail_completed.setChecked(completed)
        self.detail_completed.blockSignals(False)
        self.detail_attempts.setText(str(attempts))
        self.detail_score.setText(f"{highest_score:.1f}")

    def _detail_completed_changed(self, state):
        """Persist the completed checkbox change."""
        if not hasattr(self, '_selected_student_id') or not hasattr(self, '_selected_module_number'):
            return
        completed = state == Qt.CheckState.Checked.value
        update_progress_completed(
            self.engine,
            self._selected_student_id,
            self._selected_module_number,
            completed
        )
        self._refresh_progress()

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
    # Section Meeting Grades tab
    # -----------------------------------------------------------------------
    def _create_section_grades_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Meeting:"))
        self.section_meeting_combo = QComboBox()
        self.section_meeting_combo.currentIndexChanged.connect(self._load_section_meeting_grades)
        controls.addWidget(self.section_meeting_combo)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh_section_meeting_grades)
        controls.addWidget(refresh_btn)
        save_btn = QPushButton("Save Grade Changes")
        save_btn.clicked.connect(self._save_section_meeting_grades)
        controls.addWidget(save_btn)
        export_btn = QPushButton("Export Selected Grades")
        export_btn.clicked.connect(self._export_section_meeting_grades)
        controls.addWidget(export_btn)
        controls.addStretch()
        layout.addLayout(controls)

        layout.addWidget(QLabel("Scores must be blank, 0, 1, or 2."))
        self.section_grades_table = QTableWidget(0, 6)
        self.section_grades_table.setHorizontalHeaderLabels([
            "Student Code", "Student", "Worksheet ID", "Score", "Attendance", "Note",
        ])
        self.section_grades_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.section_grades_table)
        self._refresh_section_meeting_grades()
        return tab

    def _refresh_section_meeting_grades(self):
        current_id = self.section_meeting_combo.currentData()
        self.section_meeting_combo.blockSignals(True)
        self.section_meeting_combo.clear()
        for meeting in get_section_meetings(self.engine):
            label = (
                f"Section {meeting['section_number']} — {meeting['meeting_date']} "
                f"{meeting['start_time']}"
            )
            self.section_meeting_combo.addItem(label, meeting['meeting_id'])
        index = self.section_meeting_combo.findData(current_id)
        if index >= 0:
            self.section_meeting_combo.setCurrentIndex(index)
        self.section_meeting_combo.blockSignals(False)
        self._load_section_meeting_grades()

    def _selected_section_meeting(self):
        meeting_id = self.section_meeting_combo.currentData()
        return next(
            (meeting for meeting in get_section_meetings(self.engine)
             if meeting['meeting_id'] == meeting_id),
            None,
        )

    def _load_section_meeting_grades(self):
        meeting = self._selected_section_meeting()
        self.section_grades_table.setRowCount(0)
        if meeting is None:
            return
        grades = {
            grade['student_id']: grade
            for grade in get_section_meeting_grades(self.engine, meeting['meeting_id'])
        }
        students = get_students_for_section(self.engine, meeting['section_number'])
        self.section_grades_table.setRowCount(len(students))
        for row, student in enumerate(students):
            grade = grades.get(student.student_id, {})
            values = [
                student.student_code,
                student.name,
                grade.get('worksheet_id', ''),
                '' if grade.get('score') is None else str(grade['score']),
                grade.get('attendance_status', ''),
                grade.get('note', ''),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, student.student_id)
                if column in (0, 1, 2):
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.section_grades_table.setItem(row, column, item)
        self.section_grades_table.resizeColumnsToContents()

    def _save_section_meeting_grades(self):
        meeting = self._selected_section_meeting()
        if meeting is None:
            return
        try:
            for row in range(self.section_grades_table.rowCount()):
                student_id = self.section_grades_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                score_text = self.section_grades_table.item(row, 3).text().strip()
                if score_text not in ('', '0', '1', '2'):
                    raise ValueError('Scores must be blank, 0, 1, or 2.')
                save_section_meeting_grade(self.engine, {
                    'section_meeting_id': meeting['meeting_id'],
                    'student_id': student_id,
                    'worksheet_id': self.section_grades_table.item(row, 2).text().strip(),
                    'score': int(score_text) if score_text else None,
                    'attendance_status': self.section_grades_table.item(row, 4).text().strip(),
                    'note': self.section_grades_table.item(row, 5).text().strip(),
                })
            self._export_section_meeting_grades(show_message=False)
            self._load_section_meeting_grades()
        except Exception as error:
            QMessageBox.warning(self, 'Could Not Save Grades', str(error))

    def _export_section_meeting_grades(self, show_message=True):
        meeting = self._selected_section_meeting()
        course_folder = get_course_info(self.engine).get('course_folder', '').strip()
        if meeting is None or not course_folder:
            return
        try:
            path = export_meeting_grades(self.engine, course_folder, meeting)
            if show_message:
                QMessageBox.information(self, 'Grades Exported', path)
        except Exception as error:
            QMessageBox.warning(self, 'Could Not Export Grades', str(error))

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

        add_btn = QPushButton("Add Quiz Attempt")
        add_btn.clicked.connect(self._add_quiz_dialog)
        filter_layout.addWidget(add_btn)

        delete_btn = QPushButton("Delete Selected")
        delete_btn.clicked.connect(self._delete_selected_quiz)
        filter_layout.addWidget(delete_btn)

        regrade_btn = QPushButton("Regrade Selected")
        regrade_btn.clicked.connect(self._regrade_selected_quiz)
        filter_layout.addWidget(regrade_btn)

        layout.addLayout(filter_layout)

        # Quiz table
        self.quizzes_table = QTableWidget()
        self.quizzes_table.setColumnCount(5)
        self.quizzes_table.setHorizontalHeaderLabels(
            ["Student", "Module", "Quiz ID", "Date", "Score"]
        )
        self.quizzes_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.quizzes_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.quizzes_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
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

        rows = []
        for q in quizzes:
            if student_filter is not None and q.student_id != student_filter:
                continue
            # Only show graded attempts; printed quizzes have score=None
            if q.score is None:
                continue
            student = students.get(q.student_id)
            rows.append({
                'id': q.id,
                'student': _format_student(student) if student else f"ID:{q.student_id}",
                'module': q.module_number,
                'quiz_id': q.quiz_id,
                'date': q.date_taken,
                'score': q.score if q.score is not None else '',
            })

        self.quizzes_table.setRowCount(len(rows))
        for row, r in enumerate(rows):
            self.quizzes_table.setItem(row, 0, QTableWidgetItem(r['student']))
            self.quizzes_table.setItem(row, 1, QTableWidgetItem(str(r['module'])))
            self.quizzes_table.setItem(row, 2, QTableWidgetItem(r['quiz_id']))
            self.quizzes_table.setItem(row, 3, QTableWidgetItem(r['date']))
            self.quizzes_table.setItem(row, 4, QTableWidgetItem(str(r['score'])))
            self.quizzes_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, r['id'])

    def _add_quiz_dialog(self):
        """Open a dialog to add a new quiz attempt."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Add Quiz Attempt")
        layout = QFormLayout()
        dialog.setLayout(layout)

        student_combo = QComboBox()
        for student in get_all_students(self.engine):
            student_combo.addItem(_format_student(student), student.student_id)
        layout.addRow("Student:", student_combo)

        module_spin = QSpinBox()
        module_spin.setRange(1, 99)
        module_spin.setValue(1)
        layout.addRow("Module:", module_spin)

        quiz_id_edit = QLineEdit()
        quiz_id_edit.setPlaceholderText("e.g. quiz_001")
        layout.addRow("Quiz ID:", quiz_id_edit)

        date_edit = QDateEdit()
        date_edit.setCalendarPopup(True)
        date_edit.setDate(QDate.currentDate())
        layout.addRow("Date:", date_edit)

        score_spin = QSpinBox()
        score_spin.setRange(0, 100)
        score_spin.setSpecialValueText("Ungraded")
        score_spin.setValue(0)
        layout.addRow("Score (0-100):", score_spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        student_id = student_combo.currentData()
        module_number = module_spin.value()
        quiz_id = quiz_id_edit.text().strip() or "manual"
        date_taken = date_edit.date().toString("yyyy-MM-dd")
        score = score_spin.value() if score_spin.value() > 0 else None

        record_quiz_attempt(
            self.engine,
            student_id=student_id,
            module_number=module_number,
            quiz_id=quiz_id,
            date_taken=date_taken,
            score=score,
        )
        self._refresh_quizzes()
        self._refresh_progress()

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


def main():
    """Main function to run the Student Progress application."""
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    window = StudentProgressGUI()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
