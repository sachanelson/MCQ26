"""
Course Info Panel - A reusable component for managing course information.
"""
from datetime import datetime
from pathlib import Path
from collections import Counter
import json
import shutil
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QScrollArea,
    QLineEdit, QLabel, QPushButton, QDialog, QDialogButtonBox,
    QMessageBox, QTableWidget, QTableWidgetItem, QListWidget, QListWidgetItem,
    QHeaderView, QFileDialog, QHBoxLayout, QSpinBox, QTextEdit, QComboBox,
    QInputDialog
)
from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from sqlalchemy.orm import Session
from sqlalchemy import text
from database26 import (
    CourseInfo, Module, get_course_info, save_course_info, get_modules,
    get_all_sections, save_section, delete_section,
    get_semester_calendar, save_semester_calendar, compute_meeting_dates,
    get_all_classrooms, save_classroom, delete_classroom,
    get_section_meetings, save_section_meeting,
    get_all_students, save_enrolled_students,
    get_lecture_meetings, generate_lecture_meetings,
    student_name_sort_key,
)
# Legacy module status text files deprecated; no import needed


class EnrolledStudentsDialog(QDialog):
    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._removed_student_ids = []
        self.setWindowTitle('Manage Enrolled Students')
        self.resize(700, 480)
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['Name', 'Code', 'Section', 'Email'])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        buttons = QHBoxLayout()
        add_button = QPushButton('Add Student')
        add_button.clicked.connect(self._add_student_row)
        buttons.addWidget(add_button)
        remove_button = QPushButton('Remove Selected Student')
        remove_button.clicked.connect(self._remove_selected_student)
        buttons.addWidget(remove_button)
        buttons.addStretch()
        save_button = QPushButton('Save Students')
        save_button.clicked.connect(self._save_students)
        buttons.addWidget(save_button)
        close_button = QPushButton('Close')
        close_button.clicked.connect(self.reject)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        self._load_students()

    def _load_students(self):
        self.table.setRowCount(0)

        for student in sorted(get_all_students(self.engine), key=lambda s: student_name_sort_key(s.name)):
            row = self.table.rowCount()
            self.table.insertRow(row)
            name_item = QTableWidgetItem(student.name or '')
            name_item.setData(Qt.ItemDataRole.UserRole, student.student_id)
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, QTableWidgetItem(student.student_code or ''))
            section = '' if student.section_number is None else str(student.section_number)
            self.table.setItem(row, 2, QTableWidgetItem(section))
            self.table.setItem(row, 3, QTableWidgetItem(student.email or ''))

    def _add_student_row(self):
        row = self.table.rowCount()
        self.table.insertRow(row)
        name_item = QTableWidgetItem('')
        name_item.setData(Qt.ItemDataRole.UserRole, None)
        self.table.setItem(row, 0, name_item)
        self.table.setItem(row, 1, QTableWidgetItem(''))
        self.table.setItem(row, 2, QTableWidgetItem(''))
        self.table.setItem(row, 3, QTableWidgetItem(''))
        self.table.setCurrentCell(row, 0)
        self.table.editItem(self.table.item(row, 0))

    def _remove_selected_student(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, 'No Selection', 'Select a student to remove.')
            return
        name_item = self.table.item(row, 0)
        name = name_item.text() if name_item else 'this student'
        reply = QMessageBox.question(
            self,
            'Remove Student',
            f"Remove {name} from the enrolled roster? Their database history and course files will be kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        student_id = name_item.data(Qt.ItemDataRole.UserRole) if name_item else None
        if student_id is not None:
            self._removed_student_ids.append(student_id)
        self.table.removeRow(row)

    def _move_student_files_for_section_change(self, student_code: str, old_section: int,
                                               new_section: int, course_folder: str) -> None:
        """Move a student's generated quiz/worksheet files from one section folder to another.

        Files are moved under module*/quizzes/section{old} -> section{new} and
        module*/worksheets/section{old} -> section{new}, preserving their relative
        subdirectory structure (attempts, questions, answers, JSON, etc.).
        """
        if old_section == new_section or not course_folder or not Path(course_folder).expanduser().is_dir():
            return
        base = Path(course_folder).expanduser()
        for subdir in ('quizzes', 'worksheets'):
            for old_path in base.glob(f'module*/{subdir}/section{old_section}'):
                new_root = old_path.parent / f'section{new_section}'
                for old_file in old_path.rglob('*'):
                    if not old_file.is_file():
                        continue
                    # Only move files whose name contains the student code
                    if student_code not in old_file.name:
                        continue
                    rel = old_file.relative_to(old_path)
                    new_file = new_root / rel
                    new_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(old_file), str(new_file))

    def _save_students(self):
        students = []
        try:
            existing_by_id = {
                s.student_id: s for s in get_all_students(self.engine, enrolled_only=False)
            }

            for row in range(self.table.rowCount()):
                name_item = self.table.item(row, 0)
                code_item = self.table.item(row, 1)
                section_item = self.table.item(row, 2)
                email_item = self.table.item(row, 3)
                section_text = section_item.text().strip() if section_item else ''
                if section_text and not section_text.isdigit():
                    raise ValueError(f"Section for row {row + 1} must be a whole number.")
                students.append({
                    'student_id': name_item.data(Qt.ItemDataRole.UserRole) if name_item else None,
                    'name': name_item.text() if name_item else '',
                    'student_code': code_item.text() if code_item else '',
                    'section_number': int(section_text) if section_text else None,
                    'email': email_item.text() if email_item else '',
                })

            # A new row whose code or name matches a previously removed student
            # restores that record rather than creating a duplicate — codes are
            # unique across all students, so a removed student's code can only
            # ever be reused by restoring their existing record.
            removed_students = [s for s in existing_by_id.values() if not s.enrolled]
            for st in students:
                if st.get('student_id') is not None:
                    continue
                code_norm = (st.get('student_code') or '').strip().casefold()
                name_norm = (st.get('name') or '').strip().casefold()
                match = next(
                    (s for s in removed_students
                     if code_norm and (s.student_code or '').casefold() == code_norm),
                    None,
                ) or next(
                    (s for s in removed_students
                     if name_norm and (s.name or '').strip().casefold() == name_norm),
                    None,
                )
                if match is None:
                    continue
                reply = QMessageBox.question(
                    self,
                    'Restore Removed Student?',
                    f'"{st["name"]}" ({st["student_code"] or "no code yet"}) matches '
                    f'previously removed student "{match.name}" ({match.student_code}).\n\n'
                    "Restore that student's existing record — keeping their quiz and "
                    'grading history — using the details entered here?\n\n'
                    "Choose No to go back and edit the row first (a removed student's "
                    'code cannot be assigned to a brand-new record).',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    st['student_id'] = match.student_id
                else:
                    return

            duplicate_names = self._check_duplicate_names(students)
            duplicate_emails = self._check_duplicate_emails(students)
            if duplicate_names or duplicate_emails:
                messages = []
                if duplicate_names:
                    messages.append(
                        'Duplicate names:\n'
                        + '\n'.join(f"  {name}" for name in sorted(duplicate_names))
                    )
                if duplicate_emails:
                    messages.append(
                        'Duplicate emails:\n'
                        + '\n'.join(f"  {email}" for email in sorted(duplicate_emails))
                    )
                reply = QMessageBox.question(
                    self,
                    'Duplicate Names or Emails',
                    '\n\n'.join(messages) + '\n\nDo you want to save anyway?',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

            # Detect section changes for students that already had a section assigned.
            course_folder = get_course_info(self.engine).get('course_folder', '')
            section_changes = []
            for st in students:
                student_id = st.get('student_id')
                old_section = existing_by_id.get(student_id).section_number if student_id in existing_by_id else None
                new_section = st.get('section_number')
                if student_id is not None and old_section is not None and new_section != old_section:
                    section_changes.append((st, old_section, new_section))

            if section_changes:
                change_lines = [
                    f"  {st['name']} ({st['student_code']}): section {old_section} -> {new_section}"
                    for st, old_section, new_section in section_changes
                ]
                reply = QMessageBox.question(
                    self,
                    'Confirm Section Changes',
                    'The following section changes were detected:\n'
                    + '\n'.join(change_lines)
                    + '\n\nMove generated quiz/worksheet files to the new section folders?',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Yes,
                )
                if reply == QMessageBox.StandardButton.Cancel:
                    return
                move_files = (reply == QMessageBox.StandardButton.Yes)
                if not move_files:
                    # Revert the section changes if the user chose No.
                    for st, old_section, _ in section_changes:
                        st['section_number'] = old_section
                else:
                    for st, old_section, new_section in section_changes:
                        self._move_student_files_for_section_change(
                            st['student_code'], old_section, new_section, course_folder
                        )

            save_enrolled_students(self.engine, students, self._removed_student_ids)
        except Exception as error:
            QMessageBox.warning(self, 'Could Not Save Students', str(error))
            return
        self._removed_student_ids = []
        self._load_students()
        parent = self.parentWidget()
        if isinstance(parent, CourseInfoPanel):
            parent.students_saved.emit()
        QMessageBox.information(self, 'Students Saved', 'The enrolled-student roster was saved.')

    def _check_duplicate_names(self, students):
        """Return a set of names that appear more than once or match existing students."""
        name_counts = Counter(
            (s.get('name') or '').strip().casefold()
            for s in students
            if (s.get('name') or '').strip()
        )
        duplicate_names = {name for name, count in name_counts.items() if count > 1}

        edited_ids = {
            s.get('student_id')
            for s in students
            if s.get('student_id') is not None
        }
        for existing in get_all_students(self.engine, enrolled_only=False):
            if existing.student_id in edited_ids:
                continue
            name_lower = (existing.name or '').strip().casefold()
            if name_lower and name_lower in name_counts:
                duplicate_names.add(name_lower)

        return duplicate_names

    def _check_duplicate_emails(self, students):
        """Return a set of emails that appear more than once or match existing students."""
        email_counts = Counter(
            (s.get('email') or '').strip().casefold()
            for s in students
            if (s.get('email') or '').strip()
        )
        duplicate_emails = {email for email, count in email_counts.items() if count > 1}

        edited_ids = {
            s.get('student_id')
            for s in students
            if s.get('student_id') is not None
        }
        for existing in get_all_students(self.engine, enrolled_only=False):
            if existing.student_id in edited_ids:
                continue
            email_lower = (existing.email or '').strip().casefold()
            if email_lower and email_lower in email_counts:
                duplicate_emails.add(email_lower)

        return duplicate_emails


class RosterDuplicateEditDialog(QDialog):
    """Simple dialog for editing a duplicate roster row's name and email."""

    def __init__(self, name, email, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Edit Roster Entry')
        layout = QFormLayout(self)
        self.name_edit = QLineEdit(name)
        self.email_edit = QLineEdit(email)
        layout.addRow('Name:', self.name_edit)
        layout.addRow('Email:', self.email_edit)
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addRow(self.button_box)

    def get_values(self):
        return self.name_edit.text().strip(), self.email_edit.text().strip()


class RosterDuplicateDialog(QDialog):
    """Dialog for reviewing roster rows with duplicate names or emails."""

    def __init__(self, duplicates, parent=None):
        super().__init__(parent)
        self.duplicates = duplicates
        for dup in self.duplicates:
            dup.setdefault('action', 'skip')
        self.setWindowTitle('Review Duplicate Roster Entries')
        self.resize(900, 500)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            'The following rows have names or emails that appear more than once '
            'in the imported roster. Use the buttons below to edit, skip, or keep each row.'
        ))
        self.table = QTableWidget(len(duplicates), 5)
        self.table.setHorizontalHeaderLabels(['Row #', 'Name', 'Email', 'Issue', 'Decision'])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._refresh_table()
        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        edit_btn = QPushButton('Edit Selected')
        edit_btn.clicked.connect(self._edit_selected)
        skip_btn = QPushButton('Skip Selected')
        skip_btn.clicked.connect(self._skip_selected)
        add_btn = QPushButton('Add Selected Anyway')
        add_btn.clicked.connect(self._add_selected)
        btn_layout.addWidget(edit_btn)
        btn_layout.addWidget(skip_btn)
        btn_layout.addWidget(add_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def _refresh_table(self):
        for i, dup in enumerate(self.duplicates):
            self.table.setItem(i, 0, QTableWidgetItem(str(dup['source_row'])))
            self.table.setItem(i, 1, QTableWidgetItem(dup['name']))
            self.table.setItem(i, 2, QTableWidgetItem(dup['email']))
            self.table.setItem(
                i, 3, QTableWidgetItem('; '.join(dup.get('duplicate_reasons', [])))
            )
            decision = 'Add anyway' if dup.get('action') == 'add' else 'Skip'
            self.table.setItem(i, 4, QTableWidgetItem(decision))

    def _edit_selected(self):
        row = self.table.currentRow()
        if row < 0:
            return
        dup = self.duplicates[row]
        dlg = RosterDuplicateEditDialog(dup['name'], dup['email'], self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name, email = dlg.get_values()
        if not name or not email or '@' not in email:
            QMessageBox.warning(self, 'Invalid Entry', 'A valid name and email are required.')
            return
        dup['name'] = name
        dup['email'] = email
        dup['action'] = 'add'
        self._refresh_table()

    def _skip_selected(self):
        row = self.table.currentRow()
        if row < 0:
            return
        self.duplicates[row]['action'] = 'skip'
        self._refresh_table()

    def _add_selected(self):
        row = self.table.currentRow()
        if row < 0:
            return
        self.duplicates[row]['action'] = 'add'
        self._refresh_table()


class CourseInfoPanel(QWidget):
    """A reusable panel for managing course information."""

    students_saved = pyqtSignal()

    def __init__(self, engine, parent=None):
        """Initialize the course info panel.
        
        Args:
            engine: SQLAlchemy engine instance
            parent: Parent widget
        """
        super().__init__(parent)
        self.engine = engine
        self.parent = parent
        
        # Initialize instance variables with defaults
        self.course_value = ''
        self.year_value = 2026
        self.semester_value = 'F'
        self.course_title_value = ''
        self.instructors_value = ''
        self.course_folder_value = ''
        self.moodle_url_value = ''
        self.min_signup_value = 24
        self.min_cancel_value = 24
        self.section_first_day_value = ''
        self.section_meeting_count_value = 12

        self._sections_data: list = []   # list of section dicts currently shown

        self._init_ui()
        self.load_course_info()
    
    def _init_ui(self):
        """Initialize the user interface components."""
        from database26 import NUM_MODULES
        layout = QVBoxLayout(self)

        # Form layout for course info
        form_layout = QFormLayout()

        # Course code
        self.course_input = QLineEdit()
        self.course_input.setMinimumWidth(200)
        self.course_input.textChanged.connect(self._on_course_changed)
        form_layout.addRow("Course Code:", self.course_input)

        # Year and Semester on one row
        year_sem_layout = QHBoxLayout()
        self.year_input = QLineEdit()
        self.year_input.setMaximumWidth(80)
        self.year_input.setPlaceholderText("e.g. 2026")
        year_sem_layout.addWidget(QLabel("Year:"))
        year_sem_layout.addWidget(self.year_input)
        year_sem_layout.addSpacing(20)
        self.semester_input = QLineEdit()
        self.semester_input.setMaximumWidth(50)
        self.semester_input.setPlaceholderText("F/S/Su")
        self.semester_input.setToolTip("F = Fall, S = Spring, Su = Summer")
        year_sem_layout.addWidget(QLabel("Semester:"))
        year_sem_layout.addWidget(self.semester_input)
        year_sem_layout.addStretch()
        form_layout.addRow("Year / Semester:", year_sem_layout)

        # Course title
        self.course_title_input = QLineEdit()
        self.course_title_input.setMinimumWidth(400)
        form_layout.addRow("Course Title:", self.course_title_input)

        # Instructors
        self.instructors_input = QLineEdit()
        self.instructors_input.setMinimumWidth(400)
        self.instructors_input.setPlaceholderText("Comma-separated list of instructors")
        form_layout.addRow("Instructors:", self.instructors_input)

        # Course folder
        folder_layout = QHBoxLayout()
        self.course_folder_input = QLineEdit()
        self.course_folder_input.setMinimumWidth(400)
        self.course_folder_input.setPlaceholderText("Base path for course files")
        self.course_folder_input.textChanged.connect(self._on_course_folder_changed)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._select_course_folder)
        folder_layout.addWidget(self.course_folder_input)
        folder_layout.addWidget(browse_btn)
        form_layout.addRow("Course Folder:", folder_layout)

        moodle_layout = QHBoxLayout()
        self.moodle_url_input = QLineEdit()
        self.moodle_url_input.setMinimumWidth(400)
        self.moodle_url_input.setPlaceholderText("https://moodle.example.edu/course/view.php?id=...")
        moodle_layout.addWidget(self.moodle_url_input)
        test_moodle_button = QPushButton("Test")
        test_moodle_button.clicked.connect(self._open_moodle_url)
        moodle_layout.addWidget(test_moodle_button)
        form_layout.addRow("Course Moodle URL:", moodle_layout)

        # Signup / cancel time
        time_layout = QHBoxLayout()
        min_signup_label = QLabel("Min Signup (hrs):")
        self.min_signup_input = QLineEdit()
        self.min_signup_input.setMaximumWidth(60)
        self.min_signup_input.setToolTip("Minimum hours before quiz to allow signup")
        time_layout.addWidget(min_signup_label)
        time_layout.addWidget(self.min_signup_input)
        time_layout.addSpacing(20)
        min_cancel_label = QLabel("Min Cancel (hrs):")
        self.min_cancel_input = QLineEdit()
        self.min_cancel_input.setMaximumWidth(60)
        self.min_cancel_input.setToolTip("Minimum hours before quiz to allow cancellation")
        time_layout.addWidget(min_cancel_label)
        time_layout.addWidget(self.min_cancel_input)
        time_layout.addStretch()
        form_layout.addRow("Time Settings:", time_layout)

        # ---- Class meeting info ----
        class_days_layout = QHBoxLayout()
        self.class_days_input = QLineEdit()
        self.class_days_input.setMaximumWidth(120)
        self.class_days_input.setPlaceholderText("e.g. T,Th")
        self.class_days_input.setToolTip("Comma-separated: M T W Th F")
        class_days_layout.addWidget(self.class_days_input)
        class_days_layout.addStretch()
        form_layout.addRow("Class Days:", class_days_layout)

        class_time_layout = QHBoxLayout()
        self.class_start_input = QLineEdit()
        self.class_start_input.setMaximumWidth(70)
        self.class_start_input.setPlaceholderText("HH:MM")
        self.class_end_input = QLineEdit()
        self.class_end_input.setMaximumWidth(70)
        self.class_end_input.setPlaceholderText("HH:MM")
        class_time_layout.addWidget(QLabel("Start:"))
        class_time_layout.addWidget(self.class_start_input)
        class_time_layout.addSpacing(12)
        class_time_layout.addWidget(QLabel("End:"))
        class_time_layout.addWidget(self.class_end_input)
        class_time_layout.addStretch()
        form_layout.addRow("Class Times:", class_time_layout)

        section_first_day_layout = QHBoxLayout()
        self.section_first_day_input = QLineEdit()
        self.section_first_day_input.setMaximumWidth(100)
        self.section_first_day_input.setPlaceholderText("MM-DD")
        self.section_first_day_input.setToolTip(
            "Earliest date on which section meetings may occur (defaults to first day of classes)"
        )
        section_first_day_layout.addWidget(self.section_first_day_input)
        section_first_day_layout.addStretch()
        form_layout.addRow("Section First Day:", section_first_day_layout)

        section_meeting_count_layout = QHBoxLayout()
        self.section_meeting_count_input = QLineEdit()
        self.section_meeting_count_input.setMaximumWidth(60)
        self.section_meeting_count_input.setPlaceholderText("12")
        self.section_meeting_count_input.setToolTip(
            "Maximum number of meetings per section"
        )
        section_meeting_count_layout.addWidget(self.section_meeting_count_input)
        section_meeting_count_layout.addStretch()
        form_layout.addRow("# Section Meetings:", section_meeting_count_layout)

        self.class_classroom_combo = QComboBox()
        self.class_classroom_combo.setMinimumWidth(240)
        self.class_classroom_combo.setEditable(True)
        self.class_classroom_combo.activated.connect(self._on_classroom_activated)
        form_layout.addRow("Class Classroom:", self.class_classroom_combo)

        layout.addLayout(form_layout)

        # ---- Semester Calendar button ----
        cal_row = QHBoxLayout()
        cal_btn = QPushButton("Edit Semester Calendar…")
        cal_btn.clicked.connect(self._open_calendar_dialog)
        cal_row.addWidget(cal_btn)
        cal_row.addStretch()
        layout.addLayout(cal_row)

        # Modules table (NUM_MODULES rows, 1-based numbering in header)
        layout.addWidget(QLabel(f'Modules (1–{NUM_MODULES}):'))
        self.module_table = QTableWidget(NUM_MODULES, 2)
        self.module_table.setHorizontalHeaderLabels(['Module Name', 'Associated Readings'])
        self.module_table.setColumnWidth(0, 300)
        self.module_table.setColumnWidth(1, 400)
        self.module_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.module_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.module_table.setVerticalHeaderLabels([str(i) for i in range(1, NUM_MODULES + 1)])
        layout.addWidget(self.module_table)

        # ---- Sections table ----
        layout.addWidget(QLabel("Course Sections:"))
        self.sections_table = QTableWidget(0, 9)
        self.sections_table.setHorizontalHeaderLabels(
            ["#", "Day", "Start", "End", "Meeting Dates", "Classroom", "TA / Instructor", "Comment", "Code"])
        self.sections_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for col in range(1, 8):
            self.sections_table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.Interactive)
        self.sections_table.setMinimumHeight(120)
        layout.addWidget(self.sections_table)

        sec_btn_row = QHBoxLayout()
        add_sec_btn = QPushButton("Add Section")
        add_sec_btn.clicked.connect(self._add_section_row)
        sec_btn_row.addWidget(add_sec_btn)
        del_sec_btn = QPushButton("Delete Selected")
        del_sec_btn.clicked.connect(self._delete_section_row)
        sec_btn_row.addWidget(del_sec_btn)
        import_roster_btn = QPushButton("Import Section Roster…")
        import_roster_btn.clicked.connect(self._import_section_roster)
        sec_btn_row.addWidget(import_roster_btn)
        manage_students_btn = QPushButton('Manage Enrolled Students…')
        manage_students_btn.clicked.connect(self._manage_enrolled_students)
        sec_btn_row.addWidget(manage_students_btn)
        compute_dates_btn = QPushButton("Compute Section Dates from Calendar")
        compute_dates_btn.setToolTip(
            'Fill each section\'s Meeting Dates from the Semester Calendar and its Day of Week'
        )
        compute_dates_btn.clicked.connect(self._compute_all_section_dates)
        sec_btn_row.addWidget(compute_dates_btn)
        sec_btn_row.addStretch()
        layout.addLayout(sec_btn_row)

        # Save button
        self.save_btn = QPushButton("Save Course Info")
        self.save_btn.clicked.connect(self.save_course_info)
        layout.addWidget(self.save_btn)

        self.load_modules_into_table()
    
    def _on_course_changed(self):
        """Track course code locally."""
        self.course_value = self.course_input.text()

    def _on_year_changed(self):
        """Track year locally."""
        try:
            self.year_value = int(self.year_input.text())
        except ValueError:
            pass

    def _on_semester_changed(self):
        """Track semester locally."""
        self.semester_value = self.semester_input.text().strip()

    def _on_course_folder_changed(self, text):
        """Track course folder locally."""
        self.course_folder_value = text

    def _select_course_folder(self):
        """Open folder-picker dialog."""
        folder = QFileDialog.getExistingDirectory(
            self, "Select Course Folder", str(Path.home()),
            QFileDialog.Option.ShowDirsOnly
        )
        if folder:
            self.course_folder_input.setText(folder)

    def _open_moodle_url(self):
        url = QUrl.fromUserInput(self.moodle_url_input.text().strip())
        if not url.isValid() or url.scheme() not in ('http', 'https'):
            QMessageBox.warning(self, 'Invalid Moodle URL', 'Enter a valid HTTP or HTTPS URL.')
            return
        if not QDesktopServices.openUrl(url):
            QMessageBox.warning(self, 'Could Not Open Moodle', 'The browser could not open this URL.')
    
    def load_course_info(self):
        """Load course info from database26."""
        try:
            info = get_course_info(self.engine)
            self.course_input.setText(info.get('course', ''))
            self.year_input.setText(str(info.get('year', 2026)))
            self.semester_input.setText(info.get('semester', 'F'))
            self.course_title_input.setText(info.get('course_title', ''))
            self.instructors_input.setText(info.get('instructors', ''))
            self.course_folder_input.setText(info.get('course_folder', ''))
            self.moodle_url_input.setText(info.get('moodle_url', ''))
            self.min_signup_input.setText(str(info.get('min_signup_time', 24)))
            self.min_cancel_input.setText(str(info.get('min_cancel_time', 24)))
            self.class_days_input.setText(info.get('class_days', 'T,Th'))
            self.class_start_input.setText(info.get('class_start_time', '15:55'))
            self.class_end_input.setText(info.get('class_end_time', '17:15'))
            self.section_first_day_input.setText(info.get('section_first_day', ''))
            self.section_meeting_count_input.setText(str(info.get('section_meeting_count', 12)))
            self._populate_classroom_combo(self.class_classroom_combo,
                                           info.get('class_classroom', ''))
            # sync instance vars
            self.course_value        = info.get('course', '')
            self.year_value          = info.get('year', 2026)
            self.semester_value      = info.get('semester', 'F')
            self.course_title_value  = info.get('course_title', '')
            self.instructors_value   = info.get('instructors', '')
            self.course_folder_value = info.get('course_folder', '')
            self.moodle_url_value    = info.get('moodle_url', '')
            self.min_signup_value    = info.get('min_signup_time', 24)
            self.min_cancel_value    = info.get('min_cancel_time', 24)
            self.section_first_day_value = info.get('section_first_day', '')
            self.section_meeting_count_value = info.get('section_meeting_count', 12)
            self.load_modules_into_table()
            self._load_sections_into_table()
            self._ensure_lecture_meetings(silent=True)
            return True
        except Exception as e:
            print(f"[ERROR] Failed to load course info: {e}")
            return False
    
    def save_course_info(self):
        """Save course information to database26."""
        try:
            min_signup_text = self.min_signup_input.text().strip()
            min_cancel_text = self.min_cancel_input.text().strip()
            year_text = self.year_input.text().strip()
            data = {
                'course':          self.course_input.text().strip(),
                'year':            int(year_text) if year_text.isdigit() else 2026,
                'semester':        self.semester_input.text().strip() or 'F',
                'course_title':    self.course_title_input.text().strip(),
                'instructors':     self.instructors_input.text().strip(),
                'course_folder':   self.course_folder_input.text().strip(),
                'moodle_url':      self.moodle_url_input.text().strip(),
                'min_signup_time': int(min_signup_text) if min_signup_text.isdigit() else 24,
                'min_cancel_time': int(min_cancel_text) if min_cancel_text.isdigit() else 24,
                'class_days':         self.class_days_input.text().strip(),
                'class_start_time':   self.class_start_input.text().strip(),
                'class_end_time':         self.class_end_input.text().strip(),
                'class_classroom':        self.class_classroom_combo.currentText().strip(),
                'section_first_day':      self.section_first_day_input.text().strip(),
                'section_meeting_count':  int(self.section_meeting_count_input.text().strip())
                                          if self.section_meeting_count_input.text().strip().isdigit()
                                          else 12,
            }
            save_course_info(self.engine, data)

            # Save each module row (1-based)
            from database26 import save_module, NUM_MODULES
            for i in range(NUM_MODULES):
                name_item     = self.module_table.item(i, 0)
                readings_item = self.module_table.item(i, 1)
                name     = name_item.text().strip()     if name_item     else ''
                readings = readings_item.text().strip() if readings_item else ''
                save_module(self.engine, number=i + 1, name=name, readings=readings)

            # Save sections
            self._save_sections_from_table()

            # Sync instance vars
            self.course_value        = data['course']
            self.year_value          = data['year']
            self.semester_value      = data['semester']
            self.course_title_value  = data['course_title']
            self.instructors_value   = data['instructors']
            self.course_folder_value = data['course_folder']
            self.moodle_url_value    = data['moodle_url']
            self.min_signup_value    = data['min_signup_time']
            self.min_cancel_value    = data['min_cancel_time']
            self.section_first_day_value = data['section_first_day']
            self.section_meeting_count_value = data['section_meeting_count']

            # Propagate to parent app if available
            if self.parent:
                if hasattr(self.parent, 'sync_course'):
                    self.parent.sync_course(data['course'])
                if hasattr(self.parent, 'sync_course_title'):
                    self.parent.sync_course_title(data['course_title'])
                if hasattr(self.parent, 'sync_instructors'):
                    self.parent.sync_instructors(data['instructors'])

            QMessageBox.information(self, "Saved", "Course info saved successfully.")
            return True
        except Exception as e:
            print(f"[ERROR] Failed to save course info: {e}")
            return False
            
    def get_course_info(self):
        """Return the currently-displayed course info as a dict."""
        return {
            'course':              self.course_value,
            'year':                self.year_value,
            'semester':            self.semester_value,
            'course_title':        self.course_title_value,
            'instructors':         self.instructors_value,
            'course_folder':       self.course_folder_value,
            'moodle_url':          self.moodle_url_value,
            'min_signup_time':       self.min_signup_value,
            'min_cancel_time':       self.min_cancel_value,
            'section_first_day':     self.section_first_day_value,
            'section_meeting_count': self.section_meeting_count_value,
        }
        
    # ------------------------------------------------------------------
    # Classroom combo helper
    # ------------------------------------------------------------------

    def _populate_classroom_combo(self, combo: QComboBox, current_value: str = '') -> None:
        """Fill *combo* with room names from the DB; set current to *current_value*."""
        combo.blockSignals(True)
        combo.clear()
        combo.addItem('')  # blank / none
        rooms = get_all_classrooms(self.engine)
        for r in rooms:
            combo.addItem(r['name'])
        combo.addItem('Add classroom...', '__add__')
        idx = combo.findText(current_value)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            combo.setCurrentText(current_value)
        combo.blockSignals(False)

    def _on_classroom_activated(self, index: int):
        """Open the classroom editor when the 'Add classroom...' combo item is chosen."""
        combo = self.sender()
        if combo is None or index < 0 or combo.itemText(index) != 'Add classroom...':
            return
        combo.blockSignals(True)
        combo.setCurrentIndex(0)  # reset to blank while editing
        combo.blockSignals(False)

        dlg = ClassroomEditorDialog(self.engine, self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_name:
            self._refresh_all_classroom_combos()
            new_idx = combo.findText(dlg.selected_name)
            if new_idx >= 0:
                combo.blockSignals(True)
                combo.setCurrentIndex(new_idx)
                combo.blockSignals(False)

    def _refresh_all_classroom_combos(self):
        """Repopulate every classroom combo while preserving the currently selected room."""
        self._populate_classroom_combo(
            self.class_classroom_combo,
            self.class_classroom_combo.currentText().strip()
        )
        for row in range(self.sections_table.rowCount()):
            combo = self.sections_table.cellWidget(row, 5)
            if isinstance(combo, QComboBox):
                self._populate_classroom_combo(combo, combo.currentText().strip())

    # ------------------------------------------------------------------
    # Sections helpers
    # ------------------------------------------------------------------

    def _load_sections_into_table(self):
        """Populate the sections table from the database."""
        self._sections_data = get_all_sections(self.engine)
        self.sections_table.setRowCount(0)
        for sec in self._sections_data:
            self._append_section_row(sec)

    def _append_section_row(self, sec: dict):
        """Add one section dict as a new table row."""
        row = self.sections_table.rowCount()
        self.sections_table.insertRow(row)
        for col, key in enumerate(['section_number', 'day_of_week', 'start_time',
                                    'end_time', 'meeting_dates', 'classroom', 'ta_instructor', 'comment', 'code']):
            if key == 'meeting_dates':
                item = QTableWidgetItem(self._format_meeting_dates(sec.get(key, [])))
                self.sections_table.setItem(row, col, item)
            elif key == 'classroom':
                combo = QComboBox()
                combo.setEditable(True)
                self._populate_classroom_combo(combo, sec.get('classroom') or '')
                combo.activated.connect(self._on_classroom_activated)
                self.sections_table.setCellWidget(row, col, combo)
            else:
                val = str(sec.get(key) or '')
                item = QTableWidgetItem(val)
                if col == 0:  # section number — not editable after creation
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.sections_table.setItem(row, col, item)

    def _add_section_row(self):
        """Prompt for a section number and add a blank row."""
        from PyQt6.QtWidgets import QInputDialog
        num, ok = QInputDialog.getInt(
            self, "Add Section", "Section number:", 1, 1, 999)
        if not ok:
            return
        # Check for duplicate
        for r in range(self.sections_table.rowCount()):
            item = self.sections_table.item(r, 0)
            if item and item.text() == str(num):
                QMessageBox.warning(self, "Duplicate", f"Section {num} already exists.")
                return
        self._append_section_row({'section_number': num})

    def _manage_enrolled_students(self):
        EnrolledStudentsDialog(self.engine, self).exec()

    def _import_section_roster(self):
        sections = get_all_sections(self.engine)
        if not sections:
            QMessageBox.warning(self, 'No Sections', 'Define and save a course section before importing its roster.')
            return
        section_numbers = [str(section['section_number']) for section in sections]
        section_text, accepted = QInputDialog.getItem(
            self, 'Import Section Roster', 'Assign all listed students to section:', section_numbers, 0, False
        )
        if not accepted:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, 'Select Enrollment Report', str(Path.home()), 'Excel Files (*.xlsx *.xls)'
        )
        if not path:
            return
        try:
            from student_roster26 import preview_section_roster, import_section_roster

            section_number = int(section_text)
            preview = preview_section_roster(path, self.engine, section_number)

            rows = preview['rows']
            if preview['duplicates']:
                dlg = RosterDuplicateDialog(preview['duplicates'], self)
                if dlg.exec() != QDialog.DialogCode.Accepted:
                    return
                rows = [row for row in rows if row.get('action') != 'skip']

            result = import_section_roster(rows, self.engine, section_number)
            message = f"Imported or updated {result['imported']} students."
            if result['conflicts']:
                conflict_text = '\n'.join(result['conflicts'])
                message += (
                    f"\n\nThe following rows were skipped because they matched "
                    f"existing students ambiguously:\n\n{conflict_text}"
                )
            QMessageBox.information(self, 'Roster Imported', message)
        except Exception as error:
            QMessageBox.warning(self, 'Could Not Import Roster', str(error))

    def _delete_section_row(self):
        """Delete the currently selected section row (and its DB record)."""
        rows = self.sections_table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        item = self.sections_table.item(row, 0)
        if item and item.text().isdigit():
            try:
                delete_section(self.engine, int(item.text()))
            except Exception as e:
                print(f"[ERROR] Could not delete section: {e}")
        self.sections_table.removeRow(row)

    def _save_sections_from_table(self):
        """Write all visible section rows to the database."""
        for row in range(self.sections_table.rowCount()):
            num_item = self.sections_table.item(row, 0)
            if not num_item or not num_item.text().isdigit():
                continue
            def _cell(c):
                widget = self.sections_table.cellWidget(row, c)
                if widget is not None:   # QComboBox
                    return widget.currentText().strip()
                item = self.sections_table.item(row, c)
                return item.text().strip() if item else ''
            section_number = int(num_item.text())
            meeting_dates = self._parse_meeting_dates(_cell(4))
            start_time = _cell(2)
            end_time = _cell(3)
            save_section(self.engine, {
                'section_number': section_number,
                'day_of_week':    _cell(1),
                'start_time':     start_time,
                'end_time':       end_time,
                'meeting_dates':  meeting_dates,
                'classroom':      _cell(5),
                'ta_instructor':  _cell(6),
                'comment':        _cell(7),
                'code':           _cell(8),
            })
            self._save_section_meetings(section_number, meeting_dates, start_time, end_time)

        # Ensure whole-class lecture meetings exist from the course calendar.
        self._ensure_lecture_meetings(silent=True)

    def _ensure_lecture_meetings(self, silent: bool = False):
        """Generate lecture_meetings rows if the table is empty and calendar allows."""
        try:
            if get_lecture_meetings(self.engine):
                return
            generate_lecture_meetings(self.engine)
        except Exception as error:
            if not silent:
                QMessageBox.warning(
                    self,
                    'Could Not Generate Lecture Dates',
                    str(error),
                )

    @staticmethod
    def _format_meeting_dates(dates):
        return ', '.join(f'{int(month):02d}-{int(day):02d}' for month, day in dates)

    @staticmethod
    def _parse_meeting_dates(value):
        dates = []
        for token in value.split(','):
            token = token.strip()
            if not token:
                continue
            try:
                month, day = (int(part.strip()) for part in token.split('-', 1))
            except ValueError as error:
                raise ValueError(f'Invalid meeting date {token!r}; use MM-DD') from error
            if not 1 <= month <= 12 or not 1 <= day <= 31:
                raise ValueError(f'Invalid meeting date {token!r}; use MM-DD')
            dates.append([month, day])
        return dates

    def _save_section_meetings(self, section_number, meeting_dates, start_time, end_time):
        year_text = self.year_input.text().strip()
        year = int(year_text) if year_text.isdigit() else self.year_value
        existing = {
            (meeting['meeting_date'], meeting['start_time']): meeting
            for meeting in get_section_meetings(self.engine, section_number)
        }
        for month, day in meeting_dates:
            meeting_date = f'{year}-{month:02d}-{day:02d}'
            current = existing.get((meeting_date, start_time))
            save_section_meeting(self.engine, {
                'meeting_id': current['meeting_id'] if current else None,
                'section_number': section_number,
                'meeting_date': meeting_date,
                'start_time': start_time,
                'end_time': end_time,
                'meeting_sequence': current['meeting_sequence'] if current else None,
            })

    def _compute_all_section_dates(self):
        """Use the Semester Calendar to compute and store meeting dates for every section."""
        year_text = self.year_input.text().strip()
        year = int(year_text) if year_text.isdigit() else self.year_value
        semester = self.semester_input.text().strip() or self.semester_value
        cal = get_semester_calendar(self.engine, year, semester)
        if not cal or not cal.get('first_day') or not cal.get('last_day'):
            QMessageBox.warning(
                self, "No Calendar",
                "Please set up the Semester Calendar (first day, last day) first.")
            return
        updated = 0
        for row in range(self.sections_table.rowCount()):
            num_item = self.sections_table.item(row, 0)
            day_item = self.sections_table.item(row, 1)
            if not num_item or not num_item.text().isdigit():
                continue
            sec_num = int(num_item.text())
            day_str = day_item.text().strip() if day_item else ''
            if not day_str:
                continue
            # Section meetings may start on a different date than the first day of classes
            # and are capped at the configured number per section.
            course = get_course_info(self.engine)
            section_first_day = course.get('section_first_day') or cal['first_day']
            max_meetings = course.get('section_meeting_count') or 12
            dates = compute_meeting_dates(
                first_day=section_first_day,
                last_day=cal['last_day'],
                days_of_week=[d.strip() for d in day_str.split(',') if d.strip()],
                no_class_days=cal['no_class_days'],
                brandeis_days=cal['brandeis_days'],
            )[:max_meetings]
            # Store as [month, day] pairs
            month_day = [[int(d.split('-')[1]), int(d.split('-')[2])] for d in dates]
            save_section(self.engine, {'section_number': sec_num, 'meeting_dates': month_day})
            # Reflect the computed dates in the table so they are persisted with the section
            dates_item = self.sections_table.item(row, 4)
            if dates_item is None:
                dates_item = QTableWidgetItem()
                self.sections_table.setItem(row, 4, dates_item)
            dates_item.setText(self._format_meeting_dates(month_day))
            updated += 1
        # Persist all visible section rows (including computed dates) to section_meetings.
        self._save_sections_from_table()
        self._load_sections_into_table()
        QMessageBox.information(
            self, "Done",
            f"Meeting dates computed and saved for {updated} section(s).")

    # ------------------------------------------------------------------
    # Semester Calendar popup dialog
    # ------------------------------------------------------------------

    def _open_calendar_dialog(self):
        """Open the Semester Calendar editor dialog."""
        year_text = self.year_input.text().strip()
        year = int(year_text) if year_text.isdigit() else self.year_value
        semester = self.semester_input.text().strip() or self.semester_value
        dlg = SemesterCalendarDialog(self.engine, year, semester, self)
        dlg.exec()

    # ------------------------------------------------------------------
    # Module table
    # ------------------------------------------------------------------

    def load_modules_into_table(self):
        """Load modules from database26 into the table (1-based, NUM_MODULES rows)."""
        from database26 import NUM_MODULES
        modules_by_num = {m['number']: m for m in get_modules(self.engine)}
        self.module_table.setRowCount(NUM_MODULES)
        for i in range(NUM_MODULES):
            num = i + 1
            m = modules_by_num.get(num, {'name': f'Module {num}', 'readings': ''})
            name_item = QTableWidgetItem(m['name'])
            name_item.setFlags(name_item.flags() | Qt.ItemFlag.ItemIsEditable)
            readings_item = QTableWidgetItem(m['readings'])
            readings_item.setFlags(readings_item.flags() | Qt.ItemFlag.ItemIsEditable)
            self.module_table.setItem(i, 0, name_item)
            self.module_table.setItem(i, 1, readings_item)
        self.module_table.setVerticalHeaderLabels([str(i) for i in range(1, NUM_MODULES + 1)])


class ClassroomEditorDialog(QDialog):
    """Popup dialog for viewing and editing the list of classrooms.

    Allows adding, updating, and deleting classrooms (name and capacity).
    """

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.selected_name = ''
        self._rooms = []
        self.setWindowTitle("Edit Classrooms")
        self.setMinimumWidth(450)

        layout = QVBoxLayout(self)

        self.list_widget = QListWidget()
        self.list_widget.currentRowChanged.connect(self._on_selection_changed)
        layout.addWidget(self.list_widget)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. Bassine 208")
        form.addRow("Name:", self.name_edit)
        self.capacity_spin = QSpinBox()
        self.capacity_spin.setRange(1, 1000)
        self.capacity_spin.setValue(25)
        form.addRow("Capacity:", self.capacity_spin)
        layout.addLayout(form)

        btn_layout = QHBoxLayout()
        self.add_btn = QPushButton("Add")
        self.add_btn.clicked.connect(self._add)
        btn_layout.addWidget(self.add_btn)
        self.update_btn = QPushButton("Update")
        self.update_btn.clicked.connect(self._update)
        btn_layout.addWidget(self.update_btn)
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.clicked.connect(self._delete)
        btn_layout.addWidget(self.delete_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

        self._load_rooms()

    def _load_rooms(self):
        """Reload the room list from the database."""
        self.list_widget.clear()
        self._rooms = get_all_classrooms(self.engine)
        for r in self._rooms:
            item = QListWidgetItem(f"{r['name']}  ({r['capacity']})")
            item.setData(Qt.ItemDataRole.UserRole, r['id'])
            self.list_widget.addItem(item)

    def _current_id(self):
        item = self.list_widget.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_selection_changed(self):
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self._rooms):
            return
        r = self._rooms[row]
        self.name_edit.setText(r['name'])
        self.capacity_spin.setValue(r['capacity'])

    def _select_by_name(self, name: str):
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item and name in item.text():
                self.list_widget.setCurrentItem(item)
                break

    def _add(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing Name", "Please enter a classroom name.")
            return
        try:
            save_classroom(self.engine, name, self.capacity_spin.value())
            self.selected_name = name
            self._load_rooms()
            self._select_by_name(name)
        except Exception as e:
            QMessageBox.warning(self, "Could Not Add", str(e))

    def _update(self):
        classroom_id = self._current_id()
        if classroom_id is None:
            QMessageBox.warning(self, "No Selection", "Select a classroom to update.")
            return
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing Name", "Please enter a classroom name.")
            return
        try:
            save_classroom(self.engine, name, self.capacity_spin.value(), classroom_id)
            self.selected_name = name
            self._load_rooms()
            self._select_by_name(name)
        except Exception as e:
            QMessageBox.warning(self, "Could Not Update", str(e))

    def _delete(self):
        classroom_id = self._current_id()
        if classroom_id is None:
            QMessageBox.warning(self, "No Selection", "Select a classroom to delete.")
            return
        name = self.name_edit.text().strip()
        reply = QMessageBox.question(
            self, "Confirm Delete",
            f"Delete classroom '{name}'?"
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_classroom(self.engine, classroom_id)
            self.name_edit.clear()
            self._load_rooms()
        except Exception as e:
            QMessageBox.warning(self, "Could Not Delete", str(e))


class SemesterCalendarDialog(QDialog):
    """Popup dialog for editing the Semester Calendar.

    Fields:
      - First day of classes (YYYY-MM-DD)
      - Last day of classes  (YYYY-MM-DD)
      - No-class days: one YYYY-MM-DD per line
      - Brandeis days: one entry per line, format  YYYY-MM-DD <sub>
        where <sub> is the substitute weekday abbreviation (M/T/W/Th/F)
    """

    def __init__(self, engine, year: int, semester: str, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.year = year
        self.semester = semester
        self.setWindowTitle(f"Semester Calendar \u2014 {year} {semester}")
        self.setMinimumWidth(500)
        self._init_ui()
        self._load()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.first_day_input = QLineEdit()
        self.first_day_input.setPlaceholderText("MM-DD")
        self.first_day_input.setMaximumWidth(100)
        form.addRow("First day of classes:", self.first_day_input)

        self.last_day_input = QLineEdit()
        self.last_day_input.setPlaceholderText("MM-DD")
        self.last_day_input.setMaximumWidth(100)
        form.addRow("Last day of classes:", self.last_day_input)

        self.end_of_semester_input = QLineEdit()
        self.end_of_semester_input.setPlaceholderText("MM-DD")
        self.end_of_semester_input.setMaximumWidth(100)
        form.addRow("End of semester (finals):", self.end_of_semester_input)

        layout.addLayout(form)

        layout.addWidget(QLabel("No-class days (one MM-DD per line):"))
        self.no_class_edit = QTextEdit()
        self.no_class_edit.setMaximumHeight(120)
        self.no_class_edit.setPlaceholderText("11-26\n11-27\n\u2026")
        layout.addWidget(self.no_class_edit)

        layout.addWidget(QLabel(
            "Brandeis days (one per line: MM-DD <substitute day>)\n"
            "Example:  10-13 T   means Oct 13 runs as a Tuesday schedule"))
        self.brandeis_edit = QTextEdit()
        self.brandeis_edit.setMaximumHeight(120)
        self.brandeis_edit.setPlaceholderText("10-13 T\n11-11 M\n\u2026")
        layout.addWidget(self.brandeis_edit)

        cal_link_row = QHBoxLayout()
        cal_link_btn = QPushButton("Brandeis Academic Calendar \u2197")
        cal_link_btn.setToolTip("https://www.brandeis.edu/registrar/calendar/")
        cal_link_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://www.brandeis.edu/registrar/calendar/")))
        cal_link_row.addWidget(cal_link_btn)
        cal_link_row.addStretch()
        layout.addLayout(cal_link_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _to_mmdd(iso: str) -> str:
        """Convert YYYY-MM-DD to MM-DD, pass through if already MM-DD."""
        parts = iso.split('-')
        if len(parts) == 3:
            return f"{parts[1]}-{parts[2]}"
        return iso

    def _to_full(self, mmdd: str) -> str:
        """Convert MM-DD to YYYY-MM-DD using self.year."""
        mmdd = mmdd.strip()
        if not mmdd:
            return ''
        parts = mmdd.split('-')
        if len(parts) == 2:
            return f"{self.year}-{parts[0]}-{parts[1]}"
        return mmdd  # already full

    def _load(self):
        cal = get_semester_calendar(self.engine, self.year, self.semester)
        if not cal:
            return
        self.first_day_input.setText(self._to_mmdd(cal.get('first_day', '')))
        self.last_day_input.setText(self._to_mmdd(cal.get('last_day', '')))
        self.end_of_semester_input.setText(self._to_mmdd(cal.get('end_of_semester', '')))
        self.no_class_edit.setPlainText(
            '\n'.join(self._to_mmdd(d) for d in cal.get('no_class_days', [])))
        bd_lines = [
            f"{self._to_mmdd(bd['date'])} {bd['substitute']}"
            for bd in cal.get('brandeis_days', [])
        ]
        self.brandeis_edit.setPlainText('\n'.join(bd_lines))

    def _save(self):
        no_class = [
            ln.strip() for ln in self.no_class_edit.toPlainText().splitlines()
            if ln.strip()
        ]
        brandeis = []
        for ln in self.brandeis_edit.toPlainText().splitlines():
            parts = ln.strip().split()
            if len(parts) == 2:
                brandeis.append({'date': parts[0], 'substitute': parts[1]})
        try:
            save_semester_calendar(self.engine, {
                'year':          self.year,
                'semester':      self.semester,
                'first_day':       self._to_full(self.first_day_input.text()),
                'last_day':        self._to_full(self.last_day_input.text()),
                'end_of_semester': self._to_full(self.end_of_semester_input.text()),
                'no_class_days': [self._to_full(d) for d in no_class],
                'brandeis_days': [{'date': self._to_full(bd['date']), 'substitute': bd['substitute']}
                                  for bd in brandeis],
            })
            QMessageBox.information(self, "Saved", "Semester calendar saved.")
            self.accept()
        except Exception as e:
            print(f"[ERROR] Failed to save semester calendar: {e}")
            QMessageBox.critical(self, "Error", f"Failed to save: {e}")
