"""
Participation panel for the Student Progress GUI.

Provides editable tables for lecture and section participation grades.
- Lectures: all enrolled students as rows, whole-class lecture dates as columns.
  Lecture dates are generated automatically from the course calendar in the
  Course Info tab; if none appear, make sure the Semester Calendar is set.
- Sections: students in the selected section as rows, that section's meeting
  dates as columns. Separate section tables are selected via the section combo.

Scores are integral values only; blank cells store NULL in the database.
"""
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QSpinBox,
)
from PyQt6.QtCore import Qt

from database26 import (
    get_all_students,
    get_all_sections,
    get_students_in_section,
    get_lecture_meetings,
    get_section_meetings,
    get_lecture_participation,
    get_section_participation,
    save_lecture_participation,
    save_section_participation,
)


class ParticipationPanel(QWidget):
    """Panel for editing lecture and section participation grades."""

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._mode = 'lecture'  # 'lecture' or 'section'
        self._current_section_number = None
        self._table_mode = None
        self._table_meetings = []
        self._table_students = []
        self._updating_table = False

        self._init_ui()
        self._set_mode('lecture')

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # Top controls: mode buttons, section selector, generate, save
        controls = QHBoxLayout()
        self.lectures_btn = QPushButton('Lectures')
        self.sections_btn = QPushButton('Sections')
        self.lectures_btn.setCheckable(True)
        self.sections_btn.setCheckable(True)
        self.lectures_btn.clicked.connect(lambda: self._set_mode('lecture'))
        self.sections_btn.clicked.connect(lambda: self._set_mode('section'))
        controls.addWidget(self.lectures_btn)
        controls.addWidget(self.sections_btn)

        controls.addSpacing(20)
        controls.addWidget(QLabel('Section:'))
        self.section_combo = QComboBox()
        self.section_combo.setMinimumWidth(140)
        self.section_combo.currentIndexChanged.connect(self._on_section_changed)
        controls.addWidget(self.section_combo)

        controls.addStretch()

        self.add_to_range_btn = QPushButton('add to range')
        self.add_to_range_btn.setToolTip(
            'Add the selected amount to each participation cell in the selected range.'
        )
        self.add_to_range_btn.clicked.connect(self._add_to_range)
        controls.addWidget(self.add_to_range_btn)

        self.range_increment_spinbox = QSpinBox()
        self.range_increment_spinbox.setRange(1, 10)
        self.range_increment_spinbox.setValue(1)
        controls.addWidget(self.range_increment_spinbox)

        self.save_btn = QPushButton('Save')
        self.save_btn.clicked.connect(self._save)
        controls.addWidget(self.save_btn)

        layout.addLayout(controls)

        self.info_label = QLabel('Select Lectures or Sections.')
        layout.addWidget(self.info_label)

        self.table = QTableWidget()
        self.table.setSelectionMode(QTableWidget.SelectionMode.ContiguousSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(True)
        self.table.cellChanged.connect(self._on_cell_changed)
        layout.addWidget(self.table)

    def _set_mode(self, mode: str):
        """Switch between lecture and section editing modes."""
        self._mode = mode
        self.lectures_btn.setChecked(mode == 'lecture')
        self.sections_btn.setChecked(mode == 'section')
        self.section_combo.setEnabled(mode == 'section')

        if mode == 'section':
            self.info_label.setText('Select a section to edit its participation grades.')
            self._load_sections()
        else:
            self.info_label.setText('Edit lecture participation grades.')

        self._refresh_table()

    def reload(self):
        """Reload the current participation table (e.g. after roster changes)."""
        if self._mode == 'section':
            self._load_sections()
        self._refresh_table()

    def _load_sections(self):
        """Populate the section selector from the database."""
        self.section_combo.blockSignals(True)
        self.section_combo.clear()
        for sec in get_all_sections(self.engine):
            self.section_combo.addItem(
                f"Section {sec['section_number']}",
                sec['section_number'],
            )
        self.section_combo.blockSignals(False)
        if self.section_combo.count() > 0:
            self._current_section_number = self.section_combo.currentData()
        else:
            self._current_section_number = None

    def _on_section_changed(self, index: int):
        """Refresh the table when a different section is selected."""
        if index < 0:
            self._current_section_number = None
            return
        self._current_section_number = self.section_combo.currentData()
        self._refresh_table()

    def _refresh_table(self):
        """Rebuild the participation table for the current mode."""
        self.table.clear()
        self.table.setRowCount(0)
        self.table.setColumnCount(0)
        self._table_mode = None
        self._table_meetings = []
        self._table_students = []

        if self._mode == 'lecture':
            self._load_lectures_table()
        else:
            self._load_section_table()

    def _load_lectures_table(self):
        """Build the lecture participation table."""
        students = [s for s in get_all_students(self.engine) if s.email and s.email.strip()]
        meetings = get_lecture_meetings(self.engine)
        if not meetings:
            self.info_label.setText(
                'No lecture meetings. Set the Semester Calendar and class days in Course Info.'
            )
            return
        self.info_label.setText(
            f'{len(meetings)} lecture date(s) | {len(students)} student(s)'
        )
        self._build_table(students, meetings, 'lecture')

    def _load_section_table(self):
        """Build the section participation table for the selected section."""
        section_number = self._current_section_number
        if section_number is None:
            self.info_label.setText('No section selected.')
            return
        students = [s for s in get_students_in_section(self.engine, section_number) if s.email and s.email.strip()]
        meetings = get_section_meetings(self.engine, section_number)
        if not meetings:
            self.info_label.setText(
                f'Section {section_number} has no meeting dates. '
                'Define them in Course Info first.'
            )
            return
        self.info_label.setText(
            f'Section {section_number}: {len(meetings)} meeting date(s) | {len(students)} student(s)'
        )
        self._build_table(students, meetings, 'section')

    @staticmethod
    def _format_lecture_date_header(date_str: str) -> str:
        """Return a compact date header like 'S5' or 'O12' from a YYYY-MM-DD string."""
        try:
            dt = datetime.strptime(date_str, '%Y-%m-%d')
            return f"{dt.strftime('%b')[0]}{dt.day}"
        except (ValueError, TypeError):
            return date_str

    def _build_table(self, students, meetings, mode: str):
        """Construct the editable participation table."""
        self._table_mode = mode
        self._table_meetings = meetings
        self._table_students = students

        has_total_row = mode == 'lecture'
        self.table.setRowCount(len(students) + (1 if has_total_row else 0))
        self.table.setColumnCount(len(meetings) + 1)

        if mode == 'lecture':
            headers = ['Student'] + [self._format_lecture_date_header(m['meeting_date']) for m in meetings]
        else:
            headers = ['Student'] + [m['meeting_date'] for m in meetings]
        self.table.setHorizontalHeaderLabels(headers)

        # Load existing scores into a lookup keyed by (meeting_id, student_id).
        scores = {}
        if mode == 'lecture':
            for meeting in meetings:
                for p in get_lecture_participation(self.engine, meeting['meeting_id']):
                    scores[(meeting['meeting_id'], p['student_id'])] = p['score']
        else:
            for meeting in meetings:
                for p in get_section_participation(self.engine, meeting['meeting_id']):
                    scores[(meeting['meeting_id'], p['student_id'])] = p['score']

        self._updating_table = True
        try:
            for row, student in enumerate(students):
                self.table.setVerticalHeaderItem(row, QTableWidgetItem(student.student_code or ''))

                name_item = QTableWidgetItem(student.name or '')
                name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, 0, name_item)

                for col, meeting in enumerate(meetings, start=1):
                    score = scores.get((meeting['meeting_id'], student.student_id))
                    text = str(score) if score is not None else ''
                    item = QTableWidgetItem(text)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    item.setData(
                        Qt.ItemDataRole.UserRole,
                        (student.student_id, meeting['meeting_id']),
                    )
                    self.table.setItem(row, col, item)

            if has_total_row:
                total_row = len(students)
                self.table.setVerticalHeaderItem(total_row, QTableWidgetItem('Total'))
                total_label = QTableWidgetItem('Total')
                total_label.setFlags(total_label.flags() & ~Qt.ItemFlag.ItemIsEditable)
                total_label.setToolTip('Sum of participation scores for each meeting')
                self.table.setItem(total_row, 0, total_label)
                for col in range(1, len(meetings) + 1):
                    item = QTableWidgetItem('')
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    item.setToolTip('Sum of column values')
                    self.table.setItem(total_row, col, item)
                self._update_total_row()
        finally:
            self._updating_table = False

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for col in range(1, len(meetings) + 1):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)

    def _save(self):
        """Persist the currently-displayed participation table."""
        if self._table_mode == 'lecture':
            self._save_lectures()
        elif self._table_mode == 'section':
            self._save_sections()

    def _parse_score(self, text: str):
        """Return integer score from text, or None for blank; raise ValueError otherwise."""
        text = text.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError as error:
            raise ValueError(f"'{text}' is not an integer.") from error

    def _save_lectures(self):
        """Save lecture participation scores from the table.

        Invalid cells are skipped with a warning; all valid cells are saved."""
        meetings = self._table_meetings
        students = self._table_students
        updates = []
        invalid = []
        for row, student in enumerate(students):
            for col, meeting in enumerate(meetings, start=1):
                item = self.table.item(row, col)
                if item is None:
                    continue
                try:
                    score = self._parse_score(item.text())
                except ValueError as error:
                    invalid.append(f"Row {row + 1}, column {col}: {error}")
                    continue
                updates.append({
                    'lecture_meeting_id': meeting['meeting_id'],
                    'student_id': student.student_id,
                    'score': score,
                })

        if invalid:
            QMessageBox.warning(
                self,
                'Invalid Scores Skipped',
                'The following cells were invalid and were not saved; '
                'all other cells were saved.\n\n' + '\n'.join(invalid),
            )

        try:
            for data in updates:
                save_lecture_participation(self.engine, data)
        except Exception as error:
            QMessageBox.warning(self, 'Could Not Save', str(error))
            return

        if not invalid:
            QMessageBox.information(self, 'Saved', 'Lecture participation saved.')
        self._refresh_table()

    def _save_sections(self):
        """Save section participation scores from the table.

        Invalid cells are skipped with a warning; all valid cells are saved."""
        meetings = self._table_meetings
        students = self._table_students
        updates = []
        invalid = []
        for row, student in enumerate(students):
            for col, meeting in enumerate(meetings, start=1):
                item = self.table.item(row, col)
                if item is None:
                    continue
                try:
                    score = self._parse_score(item.text())
                except ValueError as error:
                    invalid.append(f"Row {row + 1}, column {col}: {error}")
                    continue
                updates.append({
                    'section_meeting_id': meeting['meeting_id'],
                    'student_id': student.student_id,
                    'score': score,
                })

        if invalid:
            QMessageBox.warning(
                self,
                'Invalid Scores Skipped',
                'The following cells were invalid and were not saved; '
                'all other cells were saved.\n\n' + '\n'.join(invalid),
            )

        try:
            for data in updates:
                save_section_participation(self.engine, data)
        except Exception as error:
            QMessageBox.warning(self, 'Could Not Save', str(error))
            return

        if not invalid:
            QMessageBox.information(self, 'Saved', 'Section participation saved.')
        self._refresh_table()

    def _add_to_range(self):
        """Add the spinbox value to each participation cell in the selected range."""
        if self._table_mode not in ('lecture', 'section') or self.table.columnCount() <= 1:
            QMessageBox.information(
                self, 'No Table', 'Load a lecture or section participation table first.'
            )
            return

        selected_ranges = self.table.selectedRanges()
        if not selected_ranges:
            QMessageBox.information(
                self, 'No Selection', 'Select a range of participation cells first.'
            )
            return

        increment = self.range_increment_spinbox.value()
        updated_count = 0
        invalid_cells = []
        self._updating_table = True
        try:
            for selected_range in selected_ranges:
                first_row = max(0, selected_range.topRow())
                last_row = min(len(self._table_students) - 1, selected_range.bottomRow())
                first_col = max(1, selected_range.leftColumn())
                last_col = min(len(self._table_meetings), selected_range.rightColumn())
                for row in range(first_row, last_row + 1):
                    for col in range(first_col, last_col + 1):
                        item = self.table.item(row, col)
                        text = item.text().strip() if item is not None else ''
                        try:
                            current_value = int(text) if text else 0
                        except ValueError:
                            invalid_cells.append(f'Row {row + 1}, column {col}')
                            continue
                        if item is None:
                            item = QTableWidgetItem()
                            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                            item.setData(
                                Qt.ItemDataRole.UserRole,
                                self._item_data_for_cell(row, col),
                            )
                            self.table.setItem(row, col, item)
                        item.setText(str(current_value + increment))
                        updated_count += 1
            if self._table_mode == 'lecture':
                self._update_total_row()
        finally:
            self._updating_table = False

        if invalid_cells:
            QMessageBox.warning(
                self,
                'Invalid Scores Skipped',
                'The following selected cells do not contain integers and were skipped:\n\n'
                + '\n'.join(invalid_cells),
            )
        self.info_label.setText(
            f'Added {increment} to {updated_count} selected participation cell(s). '
            'Click Save to persist.'
        )

    def _item_data_for_cell(self, row: int, col: int):
        """Return the (student_id, meeting_id) UserRole data for a table cell."""
        if col <= 0 or col > len(self._table_meetings):
            return None
        if row < 0 or row >= len(self._table_students):
            return None
        return (self._table_students[row].student_id, self._table_meetings[col - 1]['meeting_id'])

    def _on_cell_changed(self, row: int, column: int):
        """Recalculate the lecture participation total row when a cell changes."""
        if self._updating_table:
            return
        if self._table_mode != 'lecture':
            return
        if column <= 0:
            return
        if row >= len(self._table_students):
            return
        self._update_total_row()

    def _update_total_row(self):
        """Compute and display the column sums in the lecture table total row."""
        if self._table_mode != 'lecture' or not self._table_students:
            return
        total_row = len(self._table_students)
        if total_row >= self.table.rowCount():
            return
        self._updating_table = True
        try:
            for col in range(1, self.table.columnCount()):
                total = 0
                for row in range(total_row):
                    item = self.table.item(row, col)
                    if item is None:
                        continue
                    try:
                        total += int(item.text().strip())
                    except ValueError:
                        pass
                total_item = self.table.item(total_row, col)
                if total_item is None:
                    total_item = QTableWidgetItem('')
                    total_item.setFlags(total_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    total_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    self.table.setItem(total_row, col, total_item)
                total_item.setText(str(total))
        finally:
            self._updating_table = False
