"""
Course Info Panel - A reusable component for managing course information.
"""
from datetime import datetime
from pathlib import Path
import json
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QScrollArea,
    QLineEdit, QLabel, QPushButton, QDialog, QDialogButtonBox,
    QMessageBox, QTableWidget, QTableWidgetItem, QListWidget, QListWidgetItem,
    QHeaderView, QFileDialog, QHBoxLayout, QSpinBox, QTextEdit, QComboBox
)
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from sqlalchemy.orm import Session
from sqlalchemy import text
from database26 import (
    CourseInfo, Module, get_course_info, save_course_info, get_modules,
    get_all_sections, save_section, delete_section,
    get_semester_calendar, save_semester_calendar, compute_meeting_dates,
    get_all_classrooms, save_classroom, delete_classroom,
    get_section_meetings, save_section_meeting,
)
# Legacy module status text files deprecated; no import needed


class CourseInfoPanel(QWidget):
    """A reusable panel for managing course information."""
    
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
        self.min_signup_value = 24
        self.min_cancel_value = 24

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
        self.sections_table = QTableWidget(0, 8)
        self.sections_table.setHorizontalHeaderLabels(
            ["#", "Day", "Start", "End", "Meeting Dates", "Classroom", "TA / Instructor", "Comment"])
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
            self.min_signup_input.setText(str(info.get('min_signup_time', 24)))
            self.min_cancel_input.setText(str(info.get('min_cancel_time', 24)))
            self.class_days_input.setText(info.get('class_days', 'T,Th'))
            self.class_start_input.setText(info.get('class_start_time', '15:55'))
            self.class_end_input.setText(info.get('class_end_time', '17:15'))
            self._populate_classroom_combo(self.class_classroom_combo,
                                           info.get('class_classroom', ''))
            # sync instance vars
            self.course_value        = info.get('course', '')
            self.year_value          = info.get('year', 2026)
            self.semester_value      = info.get('semester', 'F')
            self.course_title_value  = info.get('course_title', '')
            self.instructors_value   = info.get('instructors', '')
            self.course_folder_value = info.get('course_folder', '')
            self.min_signup_value    = info.get('min_signup_time', 24)
            self.min_cancel_value    = info.get('min_cancel_time', 24)
            self.load_modules_into_table()
            self._load_sections_into_table()
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
                'min_signup_time': int(min_signup_text) if min_signup_text.isdigit() else 24,
                'min_cancel_time': int(min_cancel_text) if min_cancel_text.isdigit() else 24,
                'class_days':       self.class_days_input.text().strip(),
                'class_start_time': self.class_start_input.text().strip(),
                'class_end_time':   self.class_end_input.text().strip(),
                'class_classroom':  self.class_classroom_combo.currentText().strip(),
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
            self.min_signup_value    = data['min_signup_time']
            self.min_cancel_value    = data['min_cancel_time']

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
            'course':          self.course_value,
            'year':            self.year_value,
            'semester':        self.semester_value,
            'course_title':    self.course_title_value,
            'instructors':     self.instructors_value,
            'course_folder':   self.course_folder_value,
            'min_signup_time': self.min_signup_value,
            'min_cancel_time': self.min_cancel_value,
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
                                    'end_time', 'meeting_dates', 'classroom', 'ta_instructor', 'comment']):
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

    def _import_section_roster(self):
        from PyQt6.QtWidgets import QInputDialog
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
            from student_roster26 import import_section_roster
            imported = import_section_roster(path, self.engine, int(section_text))
            QMessageBox.information(self, 'Roster Imported', f'Imported or updated {imported} students.')
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
            })
            self._save_section_meetings(section_number, meeting_dates, start_time, end_time)

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
            dates = compute_meeting_dates(
                first_day=cal['first_day'],
                last_day=cal['last_day'],
                days_of_week=[d.strip() for d in day_str.split(',') if d.strip()],
                no_class_days=cal['no_class_days'],
                brandeis_days=cal['brandeis_days'],
            )
            # Store as [month, day] pairs
            month_day = [[int(d.split('-')[1]), int(d.split('-')[2])] for d in dates]
            save_section(self.engine, {'section_number': sec_num, 'meeting_dates': month_day})
            updated += 1
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
