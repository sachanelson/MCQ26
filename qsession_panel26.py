"""
Quiz Session Panel - MCQ26

Manages quiz session (qsession) blocks for the MCQ26 system.

Session types:
  - lecture  : defaults pulled from course-level class info
  - section  : defaults pulled from the matching CourseSection
  - extra    : fully manual; no defaults applied

For 'lecture' and 'section' types, date entry is validated against the
appropriate day of week (and Brandeis-day substitutions from the
SemesterCalendar). The proctor, classroom, start/end time, and capacity
are pre-filled from the matching QuizSessionDefault or CourseSection but
remain editable.
"""
import json
from datetime import date as date_cls, datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QComboBox, QDateEdit, QTimeEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QDialog, QDialogButtonBox, QSpinBox, QTextEdit, QSizePolicy,
)
from PyQt6.QtCore import Qt, QDate, QTime

from database26 import (
    SESSION_TYPE_CLASS, SESSION_TYPE_SECTION, SESSION_TYPE_EXTRA,
    get_course_info, get_all_sections, get_semester_calendar,
    get_all_classrooms,
    get_all_quiz_sessions, get_quiz_sessions_for_month,
    get_quiz_session, save_quiz_session, delete_quiz_session,
    get_session_default, save_session_default,
)

# Internal label -> DB constant
_TYPE_LABELS  = ['lecture', 'section', 'extra']
_TYPE_DB      = {
    'lecture': SESSION_TYPE_CLASS,
    'section': SESSION_TYPE_SECTION,
    'extra':   SESSION_TYPE_EXTRA,
}
_DOW_ABBR = {'M': 0, 'T': 1, 'W': 2, 'Th': 3, 'F': 4,
             'Monday': 0, 'Tuesday': 1, 'Wednesday': 2,
             'Thursday': 3, 'Friday': 4}
_DOW_NAME = {0: 'Monday', 1: 'Tuesday', 2: 'Wednesday',
             3: 'Thursday', 4: 'Friday', 5: 'Saturday', 6: 'Sunday'}


# ---------------------------------------------------------------------------
# Helper: validate date against expected day-of-week with Brandeis days
# ---------------------------------------------------------------------------

def _validate_date_for_type(engine, session_type_label: str,
                             date_str: str, section_number: int = None
                             ) -> tuple[bool, str]:
    """Return (ok, error_message).

    For 'lecture' and 'section' types, checks that the chosen date falls on
    the correct weekday (or is a matching Brandeis-day substitute).
    """
    if session_type_label == 'extra':
        return True, ''

    if not date_str:
        return False, 'Please enter a date.'

    try:
        chosen = date_cls.fromisoformat(date_str)
    except ValueError:
        return False, f'Invalid date: {date_str}'

    # Determine expected day-of-week abbreviations
    expected_abbrs: list[str] = []
    if session_type_label == 'lecture':
        info = get_course_info(engine)
        days_str = info.get('class_days', '')  # e.g. "T,Th"
        expected_abbrs = [d.strip() for d in days_str.split(',') if d.strip()]
    elif session_type_label == 'section':
        sections = get_all_sections(engine)
        sec = next((s for s in sections if s['section_number'] == section_number), None)
        if sec and sec.get('day_of_week'):
            expected_abbrs = [sec['day_of_week'].strip()]

    if not expected_abbrs:
        return True, ''  # no constraint configured

    expected_dows = set()
    for abbr in expected_abbrs:
        if abbr in _DOW_ABBR:
            expected_dows.add(_DOW_ABBR[abbr])

    # Check Brandeis-day substitutions from SemesterCalendar
    info = get_course_info(engine)
    cal = get_semester_calendar(engine, info.get('year', 0), info.get('semester', ''))
    brandeis_map: dict[str, int] = {}
    if cal:
        for bd in cal.get('brandeis_days', []):
            sub = bd.get('substitute', '')
            if sub in _DOW_ABBR:
                brandeis_map[bd['date']] = _DOW_ABBR[sub]

    effective_dow = brandeis_map.get(date_str, chosen.weekday())
    if effective_dow not in expected_dows:
        expected_names = [_DOW_NAME.get(_DOW_ABBR.get(a, -1), a) for a in expected_abbrs]
        actual_name = _DOW_NAME.get(effective_dow, str(effective_dow))
        bd_note = ' (Brandeis day)' if date_str in brandeis_map else ''
        return (False,
                f'{date_str} is a {actual_name}{bd_note}; '
                f'expected {" or ".join(expected_names)} for a {session_type_label} session.')

    return True, ''


# ---------------------------------------------------------------------------
# Defaults editor dialog
# ---------------------------------------------------------------------------

class SessionDefaultsDialog(QDialog):
    """Edit or view the QuizSessionDefault for lecture or section type."""

    def __init__(self, engine, session_type_label: str, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.session_type_label = session_type_label
        self.db_type = _TYPE_DB[session_type_label]
        self.setWindowTitle(f'Defaults for {session_type_label.capitalize()} sessions')
        self.setMinimumWidth(420)
        self._init_ui()
        self._load()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        form = QFormLayout()

        dow_options = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday',
                       'Saturday', 'Sunday']
        self.dow_combo = QComboBox()
        self.dow_combo.addItems(dow_options)
        form.addRow('Expected day of week:', self.dow_combo)

        self.start_edit = QTimeEdit()
        self.start_edit.setDisplayFormat('HH:mm')
        form.addRow('Start time:', self.start_edit)

        self.end_edit = QTimeEdit()
        self.end_edit.setDisplayFormat('HH:mm')
        form.addRow('End time:', self.end_edit)

        self.room_combo = QComboBox()
        self.room_combo.setEditable(True)
        self.room_combo.setMinimumWidth(200)
        rooms = get_all_classrooms(self.engine)
        self.room_combo.addItem('')
        for r in rooms:
            self.room_combo.addItem(r['name'])
        form.addRow('Room:', self.room_combo)

        self.capacity_spin = QSpinBox()
        self.capacity_spin.setRange(0, 999)
        self.capacity_spin.setMaximumWidth(80)
        self.room_combo.currentTextChanged.connect(self._autofill_capacity)
        form.addRow('Capacity:', self.capacity_spin)

        self.proctor_edit = QLineEdit()
        form.addRow('Proctor:', self.proctor_edit)

        layout.addLayout(form)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _autofill_capacity(self, room_name: str):
        rooms = get_all_classrooms(self.engine)
        for r in rooms:
            if r['name'] == room_name:
                self.capacity_spin.setValue(r['capacity'])
                return

    def _load(self):
        d = get_session_default(self.engine, self.db_type)
        if not d:
            return
        dow = d.get('day_of_week')
        if dow is not None:
            self.dow_combo.setCurrentIndex(dow)
        for fmt, val in [('HH:mm', d.get('start_time', '09:00'))]:
            try:
                h, m = val.split(':')
                self.start_edit.setTime(QTime(int(h), int(m)))
            except Exception:
                pass
        for fmt, val in [('HH:mm', d.get('end_time', '10:00'))]:
            try:
                h, m = val.split(':')
                self.end_edit.setTime(QTime(int(h), int(m)))
            except Exception:
                pass
        idx = self.room_combo.findText(d.get('room', ''))
        if idx >= 0:
            self.room_combo.setCurrentIndex(idx)
        else:
            self.room_combo.setCurrentText(d.get('room', ''))
        self.capacity_spin.setValue(d.get('capacity', 0))
        self.proctor_edit.setText(d.get('proctor', ''))

    def _save(self):
        save_session_default(self.engine, {
            'session_type': self.db_type,
            'day_of_week':  self.dow_combo.currentIndex(),
            'start_time':   self.start_edit.time().toString('HH:mm'),
            'end_time':     self.end_edit.time().toString('HH:mm'),
            'room':         self.room_combo.currentText().strip(),
            'proctor':      self.proctor_edit.text().strip(),
            'capacity':     self.capacity_spin.value(),
        })
        QMessageBox.information(self, 'Saved', 'Defaults saved.')
        self.accept()


# ---------------------------------------------------------------------------
# Main panel
# ---------------------------------------------------------------------------

class QSessionPanel(QWidget):
    """Quiz Session management tab for student_progress_gui26."""

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._selected_session_id: int | None = None
        self._init_ui()
        self._refresh_sessions()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _init_ui(self):
        outer = QVBoxLayout(self)

        # ---- Create / Edit form ----------------------------------------
        form_group = QGroupBox('Create / Edit Quiz Session')
        form_layout = QFormLayout()

        # Session type selector
        type_row = QHBoxLayout()
        self.type_combo = QComboBox()
        self.type_combo.addItems(_TYPE_LABELS)
        self.type_combo.currentTextChanged.connect(self._on_type_changed)
        type_row.addWidget(self.type_combo)

        self.section_combo = QComboBox()
        self.section_combo.setVisible(False)
        self.section_combo.currentIndexChanged.connect(self._on_section_changed)
        type_row.addWidget(QLabel('Section:'))
        type_row.addWidget(self.section_combo)
        type_row.addStretch()

        edit_defaults_btn = QPushButton('Edit Defaults…')
        edit_defaults_btn.setToolTip('Edit default time/room/proctor for the selected type')
        edit_defaults_btn.clicked.connect(self._open_defaults_dialog)
        type_row.addWidget(edit_defaults_btn)
        form_layout.addRow('Session Type:', type_row)

        # Date
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDate(QDate.currentDate())
        self.date_edit.setDisplayFormat('yyyy-MM-dd')
        form_layout.addRow('Date:', self.date_edit)

        # Start / End time
        time_row = QHBoxLayout()
        self.start_edit = QTimeEdit()
        self.start_edit.setDisplayFormat('HH:mm')
        self.start_edit.setTime(QTime(9, 0))
        self.end_edit = QTimeEdit()
        self.end_edit.setDisplayFormat('HH:mm')
        self.end_edit.setTime(QTime(17, 0))
        time_row.addWidget(QLabel('Start:'))
        time_row.addWidget(self.start_edit)
        time_row.addSpacing(12)
        time_row.addWidget(QLabel('End:'))
        time_row.addWidget(self.end_edit)
        time_row.addStretch()
        form_layout.addRow('Time:', time_row)

        # Room
        self.room_combo = QComboBox()
        self.room_combo.setEditable(True)
        self.room_combo.setMinimumWidth(220)
        self.room_combo.currentTextChanged.connect(self._autofill_capacity)
        form_layout.addRow('Room:', self.room_combo)

        # Capacity
        self.capacity_spin = QSpinBox()
        self.capacity_spin.setRange(0, 999)
        self.capacity_spin.setMaximumWidth(80)
        form_layout.addRow('Capacity:', self.capacity_spin)

        # Proctor
        self.proctor_edit = QLineEdit()
        form_layout.addRow('Proctor:', self.proctor_edit)

        form_group.setLayout(form_layout)
        outer.addWidget(form_group)

        # ---- Action buttons row ----------------------------------------
        btn_row = QHBoxLayout()
        self.create_btn = QPushButton('Create Session')
        self.create_btn.clicked.connect(self._create_session)
        btn_row.addWidget(self.create_btn)

        self.update_btn = QPushButton('Update Selected')
        self.update_btn.clicked.connect(self._update_session)
        self.update_btn.setEnabled(False)
        btn_row.addWidget(self.update_btn)

        self.delete_btn = QPushButton('Delete Selected')
        self.delete_btn.clicked.connect(self._delete_session)
        self.delete_btn.setEnabled(False)
        btn_row.addWidget(self.delete_btn)

        self.clear_btn = QPushButton('Clear Form')
        self.clear_btn.clicked.connect(self._clear_form)
        btn_row.addWidget(self.clear_btn)

        btn_row.addStretch()
        outer.addLayout(btn_row)

        # ---- Session list ----------------------------------------------
        sessions_group = QGroupBox('Scheduled Sessions')
        sessions_layout = QVBoxLayout()

        # Month filter
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel('Month:'))
        self.month_combo = QComboBox()
        self._populate_month_combo()
        self.month_combo.currentIndexChanged.connect(self._refresh_sessions)
        filter_row.addWidget(self.month_combo)
        filter_row.addStretch()

        refresh_btn = QPushButton('Refresh')
        refresh_btn.clicked.connect(self._refresh_sessions)
        filter_row.addWidget(refresh_btn)
        sessions_layout.addLayout(filter_row)

        # Table
        self.sessions_table = QTableWidget(0, 7)
        self.sessions_table.setHorizontalHeaderLabels(
            ['ID', 'Type', 'Date', 'Start', 'End', 'Room', 'Proctor'])
        self.sessions_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self.sessions_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents)
        self.sessions_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents)
        self.sessions_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents)
        self.sessions_table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.ResizeToContents)
        self.sessions_table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeMode.Stretch)
        self.sessions_table.horizontalHeader().setSectionResizeMode(
            6, QHeaderView.ResizeMode.Stretch)
        self.sessions_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self.sessions_table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection)
        self.sessions_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        self.sessions_table.itemSelectionChanged.connect(
            self._on_table_selection_changed)
        sessions_layout.addWidget(self.sessions_table)

        sessions_group.setLayout(sessions_layout)
        outer.addWidget(sessions_group)

        # Populate rooms and section combos
        self._populate_room_combo()
        self._populate_section_combo()

        # Apply initial type defaults
        self._on_type_changed(self.type_combo.currentText())

    # ------------------------------------------------------------------
    # Helpers: populating controls
    # ------------------------------------------------------------------

    def _populate_month_combo(self):
        self.month_combo.blockSignals(True)
        self.month_combo.clear()
        self.month_combo.addItem('All', None)
        now = datetime.now()
        for delta in range(-2, 8):
            y = now.year + (now.month - 1 + delta) // 12
            m = (now.month - 1 + delta) % 12 + 1
            label = date_cls(y, m, 1).strftime('%B %Y')
            self.month_combo.addItem(label, (y, m))
        self.month_combo.setCurrentIndex(3)  # current month
        self.month_combo.blockSignals(False)

    def _populate_room_combo(self):
        self.room_combo.blockSignals(True)
        current = self.room_combo.currentText()
        self.room_combo.clear()
        self.room_combo.addItem('')
        for r in get_all_classrooms(self.engine):
            self.room_combo.addItem(r['name'])
        idx = self.room_combo.findText(current)
        if idx >= 0:
            self.room_combo.setCurrentIndex(idx)
        self.room_combo.blockSignals(False)

    def _populate_section_combo(self):
        self.section_combo.blockSignals(True)
        self.section_combo.clear()
        for sec in get_all_sections(self.engine):
            label = f'Section {sec["section_number"]}'
            if sec.get('ta_instructor'):
                label += f' ({sec["ta_instructor"]})'
            self.section_combo.addItem(label, sec['section_number'])
        self.section_combo.blockSignals(False)

    def _autofill_capacity(self, room_name: str):
        for r in get_all_classrooms(self.engine):
            if r['name'] == room_name:
                self.capacity_spin.setValue(r['capacity'])
                return

    # ------------------------------------------------------------------
    # Session-type change → fill defaults
    # ------------------------------------------------------------------

    def _on_type_changed(self, label: str):
        self.section_combo.setVisible(label == 'section')
        self.section_combo.parentWidget().layout() if False else None

        if label == 'lecture':
            self._apply_lecture_defaults()
        elif label == 'section':
            self._on_section_changed()
        # extra: leave form as-is

    def _apply_lecture_defaults(self):
        """Pre-fill form from course_info class meeting fields."""
        info = get_course_info(self.engine)
        self._set_time(self.start_edit, info.get('class_start_time', ''))
        self._set_time(self.end_edit,   info.get('class_end_time', ''))
        room = info.get('class_classroom', '')
        self._set_room(room)
        # proctor = instructor
        self.proctor_edit.setText(info.get('instructors', ''))

    def _on_section_changed(self, _=None):
        """Pre-fill form from the selected CourseSection."""
        sec_num = self.section_combo.currentData()
        if sec_num is None:
            return
        sections = get_all_sections(self.engine)
        sec = next((s for s in sections if s['section_number'] == sec_num), None)
        if not sec:
            return
        self._set_time(self.start_edit, sec.get('start_time', ''))
        self._set_time(self.end_edit,   sec.get('end_time', ''))
        self._set_room(sec.get('classroom', ''))
        self.proctor_edit.setText(sec.get('ta_instructor', ''))

    @staticmethod
    def _set_time(widget: QTimeEdit, value: str):
        if not value:
            return
        try:
            h, m = value.split(':')
            widget.setTime(QTime(int(h), int(m)))
        except Exception:
            pass

    def _set_room(self, room: str):
        idx = self.room_combo.findText(room)
        if idx >= 0:
            self.room_combo.setCurrentIndex(idx)
        else:
            self.room_combo.setCurrentText(room)

    # ------------------------------------------------------------------
    # Session table
    # ------------------------------------------------------------------

    def _refresh_sessions(self):
        month_data = self.month_combo.currentData()
        if month_data is None:
            sessions = get_all_quiz_sessions(self.engine)
        else:
            y, m = month_data
            sessions = get_quiz_sessions_for_month(self.engine, y, m)

        self.sessions_table.setRowCount(0)
        for s in sessions:
            row = self.sessions_table.rowCount()
            self.sessions_table.insertRow(row)
            items = [
                str(s['session_id']),
                s['session_type'],
                s['date'],
                s['start_time'],
                s['end_time'],
                s['room'],
                s['proctor'],
            ]
            for col, text in enumerate(items):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, s['session_id'])
                self.sessions_table.setItem(row, col, item)

    def _on_table_selection_changed(self):
        rows = self.sessions_table.selectionModel().selectedRows()
        if not rows:
            self._selected_session_id = None
            self.update_btn.setEnabled(False)
            self.delete_btn.setEnabled(False)
            return

        sid = self.sessions_table.item(rows[0].row(), 0).data(
            Qt.ItemDataRole.UserRole)
        self._selected_session_id = sid
        self.update_btn.setEnabled(True)
        self.delete_btn.setEnabled(True)

        s = get_quiz_session(self.engine, sid)
        if not s:
            return
        # Populate form with selected session values
        self._set_type_combo(s['session_type'])
        try:
            self.date_edit.setDate(QDate.fromString(s['date'], 'yyyy-MM-dd'))
        except Exception:
            pass
        self._set_time(self.start_edit, s['start_time'])
        self._set_time(self.end_edit,   s['end_time'])
        self._set_room(s['room'])
        self.capacity_spin.setValue(s['capacity'])
        self.proctor_edit.setText(s['proctor'])

    def _set_type_combo(self, db_type: str):
        label = {v: k for k, v in _TYPE_DB.items()}.get(db_type, 'extra')
        idx = self.type_combo.findText(label)
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)

    # ------------------------------------------------------------------
    # CRUD actions
    # ------------------------------------------------------------------

    def _collect_form(self) -> dict:
        return {
            'session_type': _TYPE_DB[self.type_combo.currentText()],
            'date':         self.date_edit.date().toString('yyyy-MM-dd'),
            'start_time':   self.start_edit.time().toString('HH:mm'),
            'end_time':     self.end_edit.time().toString('HH:mm'),
            'room':         self.room_combo.currentText().strip(),
            'proctor':      self.proctor_edit.text().strip(),
            'capacity':     self.capacity_spin.value(),
            'active':       True,
        }

    def _validate_form(self) -> tuple[bool, str]:
        data = self._collect_form()
        if not data['date']:
            return False, 'Date is required.'
        if not data['start_time'] or not data['end_time']:
            return False, 'Start and end times are required.'
        if data['start_time'] >= data['end_time']:
            return False, 'Start time must be before end time.'
        if not data['room']:
            return False, 'Room is required.'
        if not data['proctor']:
            return False, 'Proctor is required.'

        label = self.type_combo.currentText()
        sec_num = (self.section_combo.currentData()
                   if label == 'section' else None)
        ok, msg = _validate_date_for_type(
            self.engine, label, data['date'], sec_num)
        if not ok:
            return False, msg

        return True, ''

    def _create_session(self):
        ok, msg = self._validate_form()
        if not ok:
            QMessageBox.warning(self, 'Validation Error', msg)
            return
        data = self._collect_form()
        sid = save_quiz_session(self.engine, data)
        print(f'[QSession] Created session {sid}')
        self._refresh_sessions()
        self._clear_form()

    def _update_session(self):
        if self._selected_session_id is None:
            return
        ok, msg = self._validate_form()
        if not ok:
            QMessageBox.warning(self, 'Validation Error', msg)
            return
        data = self._collect_form()
        data['session_id'] = self._selected_session_id
        save_quiz_session(self.engine, data)
        print(f'[QSession] Updated session {self._selected_session_id}')
        self._refresh_sessions()

    def _delete_session(self):
        if self._selected_session_id is None:
            return
        reply = QMessageBox.question(
            self, 'Confirm Delete',
            f'Delete session {self._selected_session_id}?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        delete_quiz_session(self.engine, self._selected_session_id)
        print(f'[QSession] Deleted session {self._selected_session_id}')
        self._selected_session_id = None
        self.update_btn.setEnabled(False)
        self.delete_btn.setEnabled(False)
        self._refresh_sessions()
        self._clear_form()

    def _clear_form(self):
        self._selected_session_id = None
        self.update_btn.setEnabled(False)
        self.delete_btn.setEnabled(False)
        self.sessions_table.clearSelection()
        self.type_combo.setCurrentIndex(0)
        self.date_edit.setDate(QDate.currentDate())
        self.start_edit.setTime(QTime(9, 0))
        self.end_edit.setTime(QTime(17, 0))
        self.room_combo.setCurrentIndex(0)
        self.capacity_spin.setValue(0)
        self.proctor_edit.clear()

    # ------------------------------------------------------------------
    # Defaults dialog
    # ------------------------------------------------------------------

    def _open_defaults_dialog(self):
        label = self.type_combo.currentText()
        if label == 'extra':
            QMessageBox.information(
                self, 'No Defaults',
                "'Extra' sessions have no defaults template.")
            return
        dlg = SessionDefaultsDialog(self.engine, label, self)
        dlg.exec()
        # After saving defaults, re-apply them to the form
        self._on_type_changed(label)
