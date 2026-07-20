from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from database26 import (
    get_course_info,
    get_section_meeting_grades,
    get_section_meetings,
    get_students_for_section,
    save_section_meeting,
    save_section_meeting_grade,
)
from section_workspace26 import (
    export_meeting_grades,
    generate_quantitative_worksheets,
    prepare_meeting_workspace,
    sync_section_meetings,
)


class SectionWorksheetPanel(QWidget):
    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.meetings_table = QTableWidget(0, 8)
        self.grades_table = QTableWidget(0, 6)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        synchronize = QPushButton('Synchronize Meeting Dates')
        synchronize.clicked.connect(self._synchronize)
        controls.addWidget(synchronize)
        refresh = QPushButton('Refresh')
        refresh.clicked.connect(self.refresh)
        controls.addWidget(refresh)
        enable = QPushButton('Enable Worksheet')
        enable.clicked.connect(self._enable_worksheet)
        controls.addWidget(enable)
        prepare = QPushButton('Prepare Selected Meeting')
        prepare.clicked.connect(self._prepare_selected)
        controls.addWidget(prepare)
        generate = QPushButton('Generate OneUn Worksheets')
        generate.clicked.connect(self._generate_oneun)
        controls.addWidget(generate)
        export = QPushButton('Export Selected Grades')
        export.clicked.connect(self._export_selected)
        controls.addWidget(export)
        controls.addStretch()
        layout.addLayout(controls)

        self.meetings_table.setHorizontalHeaderLabels([
            'Section', 'Date', 'Start', 'Sequence', 'Worksheet', 'Roster', 'Packages', 'Graded',
        ])
        self.meetings_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.meetings_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.meetings_table.itemSelectionChanged.connect(self._load_grades)
        layout.addWidget(self.meetings_table)

        grade_controls = QHBoxLayout()
        save_grades = QPushButton('Save Grade Changes')
        save_grades.clicked.connect(self._save_grades)
        grade_controls.addWidget(save_grades)
        grade_controls.addWidget(QLabel('Scores must be blank, 0, 1, or 2.'))
        grade_controls.addStretch()
        layout.addLayout(grade_controls)

        self.grades_table.setHorizontalHeaderLabels([
            'Student Code', 'Student', 'Worksheet ID', 'Score', 'Attendance', 'Note',
        ])
        self.grades_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.grades_table)

    def refresh(self):
        meetings = get_section_meetings(self.engine)
        self.meetings_table.setRowCount(len(meetings))
        for row_index, meeting in enumerate(meetings):
            students = get_students_for_section(self.engine, meeting['section_number'])
            grades = get_section_meeting_grades(self.engine, meeting['meeting_id'])
            packages = sum(1 for grade in grades if grade['worksheet_id'])
            graded = sum(1 for grade in grades if grade['score'] is not None)
            values = [
                str(meeting['section_number']),
                meeting['meeting_date'],
                meeting['start_time'],
                str(meeting['meeting_sequence']),
                'Yes' if meeting['worksheet_enabled'] else 'No',
                str(len(students)), str(packages), str(graded),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, meeting['meeting_id'])
                self.meetings_table.setItem(row_index, column, item)
        self.meetings_table.resizeColumnsToContents()
        if self.meetings_table.rowCount() and self.meetings_table.currentRow() < 0:
            self.meetings_table.selectRow(0)

    def _selected_meeting(self):
        row = self.meetings_table.currentRow()
        if row < 0:
            return None
        item = self.meetings_table.item(row, 0)
        if item is None:
            return None
        meeting_id = item.data(Qt.ItemDataRole.UserRole)
        return next((meeting for meeting in get_section_meetings(self.engine)
                     if meeting['meeting_id'] == meeting_id), None)

    def _course_folder(self):
        return get_course_info(self.engine).get('course_folder', '').strip()

    def _synchronize(self):
        try:
            meetings = sync_section_meetings(self.engine)
            self.refresh()
            QMessageBox.information(self, 'Synchronized', f'Synchronized {len(meetings)} section meetings.')
        except Exception as error:
            QMessageBox.warning(self, 'Could Not Synchronize', str(error))

    def _enable_worksheet(self):
        meeting = self._selected_meeting()
        if meeting is None:
            return
        try:
            save_section_meeting(self.engine, {
                **meeting,
                'worksheet_enabled': True,
            })
            self.refresh()
        except Exception as error:
            QMessageBox.warning(self, 'Could Not Enable Worksheet', str(error))

    def _prepare_selected(self):
        meeting = self._selected_meeting()
        if meeting is None:
            return
        if not meeting['worksheet_enabled']:
            QMessageBox.warning(self, 'Worksheet Disabled', 'Enable a worksheet for this meeting first.')
            return
        try:
            result = prepare_meeting_workspace(self.engine, self._course_folder(), meeting)
            self.refresh()
            QMessageBox.information(self, 'Meeting Prepared', result['workspace'])
        except Exception as error:
            QMessageBox.warning(self, 'Could Not Prepare Meeting', str(error))

    def _generate_oneun(self):
        meeting = self._selected_meeting()
        if meeting is None:
            return
        if not meeting['worksheet_enabled']:
            QMessageBox.warning(self, 'Worksheet Disabled', 'Enable a worksheet for this meeting first.')
            return
        owner = self.parent()
        params = owner._oneun_get_params() if owner and hasattr(owner, '_oneun_get_params') else None
        if params is None:
            return
        (definition, mode, base_seed, metadata,
         output_path, template_path, answer_key_template_path, plot_config, def_path) = params
        try:
            metadata['doc_type'] = 'Worksheet'
            metadata['quiz_date'] = meeting['meeting_date']
            generated = generate_quantitative_worksheets(
                self.engine,
                self._course_folder(),
                meeting,
                definition,
                template_path,
                metadata,
                answer_key_template_path=answer_key_template_path or None,
                plot_config=plot_config,
                mode=mode,
                base_seed=base_seed,
            )
            self.refresh()
            QMessageBox.information(self, 'Worksheets Generated', f'Generated {len(generated)} worksheet files.')
        except Exception as error:
            QMessageBox.warning(self, 'Could Not Generate Worksheets', str(error))

    def _export_selected(self):
        meeting = self._selected_meeting()
        if meeting is None:
            return
        try:
            path = export_meeting_grades(self.engine, self._course_folder(), meeting)
            QMessageBox.information(self, 'Grades Exported', path)
        except Exception as error:
            QMessageBox.warning(self, 'Could Not Export Grades', str(error))

    def _load_grades(self):
        meeting = self._selected_meeting()
        self.grades_table.setRowCount(0)
        if meeting is None:
            return
        grades = {grade['student_id']: grade for grade in get_section_meeting_grades(self.engine, meeting['meeting_id'])}
        students = get_students_for_section(self.engine, meeting['section_number'])
        self.grades_table.setRowCount(len(students))
        for row_index, student in enumerate(students):
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
                self.grades_table.setItem(row_index, column, item)
        self.grades_table.resizeColumnsToContents()

    def _save_grades(self):
        meeting = self._selected_meeting()
        if meeting is None:
            return
        try:
            for row in range(self.grades_table.rowCount()):
                student_id = self.grades_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                score_text = self.grades_table.item(row, 3).text().strip()
                if score_text not in ('', '0', '1', '2'):
                    raise ValueError('Scores must be blank, 0, 1, or 2.')
                save_section_meeting_grade(self.engine, {
                    'section_meeting_id': meeting['meeting_id'],
                    'student_id': student_id,
                    'worksheet_id': self.grades_table.item(row, 2).text().strip(),
                    'score': int(score_text) if score_text else None,
                    'attendance_status': self.grades_table.item(row, 4).text().strip(),
                    'note': self.grades_table.item(row, 5).text().strip(),
                })
            if self._course_folder():
                export_meeting_grades(self.engine, self._course_folder(), meeting)
            self.refresh()
            self._load_grades()
        except Exception as error:
            QMessageBox.warning(self, 'Could Not Save Grades', str(error))
