from datetime import datetime
import os
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLabel,
    QLineEdit, QPushButton, QFileDialog, QDateEdit, QCheckBox, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QDialog,
    QRadioButton, QButtonGroup, QSpinBox,
)
from PyQt6.QtCore import QDate, Qt
from PyQt6.QtGui import QColor

from database26 import (
    get_course_info, get_quiz_score_by_quiz_id, get_section_meetings,
    get_section_meeting_grades, get_students_for_section, save_section_meeting_grade,
)
from grading26 import parse_scan_file, record_results, resolve_held_up_result, TEST_MODE
from grading_session26 import start_grading_session
from unresolved_scan_dialog26 import UnresolvedScanDialog
# from section_workspace26 import export_meeting_grades


class GraderPanel(QWidget):
    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._displayed_results = []
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        source_group = QGroupBox('Grade Scanned Quiz')
        form = QFormLayout(source_group)

        self.scan_path = QLineEdit()
        scan_row = QHBoxLayout()
        scan_row.addWidget(self.scan_path)
        scan_browse = QPushButton('Browse…')
        scan_browse.clicked.connect(self._browse_scan)
        scan_row.addWidget(scan_browse)
        form.addRow('Scan PDF:', scan_row)

        self.scan_mode_group = QButtonGroup(self)
        self.one_pdf_radio = QRadioButton('One pdf')
        self.all_in_folder_radio = QRadioButton('All in folder')
        self.all_in_folder_radio.setChecked(True)
        self.scan_mode_group.addButton(self.one_pdf_radio)
        self.scan_mode_group.addButton(self.all_in_folder_radio)
        mode_row = QHBoxLayout()
        mode_row.addWidget(self.one_pdf_radio)
        mode_row.addWidget(self.all_in_folder_radio)
        mode_row.addStretch()
        form.addRow('', mode_row)

        self.date_taken = QDateEdit()
        self.date_taken.setCalendarPopup(True)
        self.date_taken.setDate(QDate.currentDate())
        self.date_taken.setDisplayFormat('yyyy-MM-dd')
        self.date_taken.setFixedWidth(115)
        form.addRow('Date Taken:', self.date_taken)

        self.send_feedback = QCheckBox('Generate feedback emails')
        self.send_feedback.setChecked(True)
        form.addRow('', self.send_feedback)

        self.extract_quant = QCheckBox('extract Quant')
        self.extract_quant.setChecked(False)
        form.addRow('', self.extract_quant)

        self.single_sides = QCheckBox('single sides')
        self.single_sides.setChecked(False)
        form.addRow('', self.single_sides)

        grade_row = QHBoxLayout()
        grade_button = QPushButton('Grade')
        grade_button.setMaximumWidth(70)
        grade_button.clicked.connect(self._grade_scan)
        update_quant_button = QPushButton('Update Quant Scores')
        update_quant_button.setMaximumWidth(130)
        update_quant_button.clicked.connect(self._update_quant_scores)
        grade_row.addWidget(grade_button)
        grade_row.addWidget(update_quant_button)
        grade_row.addStretch()
        form.addRow('', grade_row)

        quant_points_row = QHBoxLayout()
        quant_points_row.addWidget(QLabel('Quant points per quiz:'))
        self.max_quant_points_spin = QSpinBox()
        self.max_quant_points_spin.setRange(0, 1000)
        self.max_quant_points_spin.setValue(0)
        self.max_quant_points_spin.setMaximumWidth(60)
        quant_points_row.addWidget(self.max_quant_points_spin)
        set_max_button = QPushButton('Set Max Quant Points')
        set_max_button.setMaximumWidth(140)
        set_max_button.clicked.connect(self._set_max_quant_points)
        quant_points_row.addWidget(set_max_button)
        quant_points_row.addStretch()
        form.addRow('', quant_points_row)

        layout.addWidget(source_group)

        layout.addWidget(QLabel('Results'))
        self.results_table = QTableWidget(0, 8)
        self.results_table.setHorizontalHeaderLabels([
            'Quiz ID', 'Student Code', 'Module', 'MCQ Score', 'Quant Score',
            'Max Quant Points', 'Total Score', 'Status',
        ])
        self.results_table.setEditTriggers(QTableWidget.EditTrigger.AllEditTriggers)
        self.results_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        header = self.results_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        header.setDefaultSectionSize(70)
        self.results_table.setColumnWidth(0, 120)
        self.results_table.setColumnWidth(1, 90)
        self.results_table.setColumnWidth(2, 50)
        self.results_table.setColumnWidth(3, 60)
        self.results_table.setColumnWidth(4, 60)
        self.results_table.setColumnWidth(5, 80)
        self.results_table.setColumnWidth(6, 60)
        layout.addWidget(self.results_table)
        self.status_label = QLabel(
            'Select one scan PDF or a folder of scan PDFs and click Grade. '
            'A grading session folder is created automatically under '
            '<course_folder>/grading/<date>/<date><letter>/.'
        )
        layout.addWidget(self.status_label)

        section_group = QGroupBox('Section Meeting Grades')
        section_layout = QVBoxLayout(section_group)
        controls = QHBoxLayout()
        controls.addWidget(QLabel('Meeting:'))
        self.section_meeting_combo = QComboBox()
        self.section_meeting_combo.currentIndexChanged.connect(self._load_section_meeting_grades)
        controls.addWidget(self.section_meeting_combo)
        refresh_button = QPushButton('Refresh')
        refresh_button.clicked.connect(self._refresh_section_meeting_grades)
        controls.addWidget(refresh_button)
        save_button = QPushButton('Save Grade Changes')
        save_button.clicked.connect(self._save_section_meeting_grades)
        controls.addWidget(save_button)
        export_button = QPushButton('Export Selected Grades')
        export_button.clicked.connect(self._export_section_meeting_grades)
        controls.addWidget(export_button)
        controls.addStretch()
        section_layout.addLayout(controls)
        section_layout.addWidget(QLabel('Scores must be blank, 0, 1, or 2.'))
        self.section_grades_table = QTableWidget(0, 6)
        self.section_grades_table.setHorizontalHeaderLabels([
            'Student Code', 'Student', 'Worksheet ID', 'Score', 'Attendance', 'Note',
        ])
        self.section_grades_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        section_layout.addWidget(self.section_grades_table)
        layout.addWidget(section_group)
        self._refresh_section_meeting_grades()

    def _browse_scan(self):
        start = self._course_folder()
        if self.one_pdf_radio.isChecked():
            path, _ = QFileDialog.getOpenFileName(self, 'Select Scan PDF', start, 'PDF Files (*.pdf)')
            if path:
                self.scan_path.setText(path)
        else:
            path = QFileDialog.getExistingDirectory(self, 'Select Scan Folder', start)
            if path:
                self.scan_path.setText(path)

    def _course_folder(self):
        return get_course_info(self.engine).get('course_folder', '') or os.path.expanduser('~')

    def _grade_scan(self):
        scan_input = self.scan_path.text().strip()
        if not scan_input:
            QMessageBox.warning(self, 'Missing Scan', 'Select a scan PDF or folder.')
            return

        if self.one_pdf_radio.isChecked():
            scan_files = [scan_input] if os.path.isfile(scan_input) else []
            if not scan_files:
                QMessageBox.warning(self, 'Missing Scan', 'Select an existing scan PDF.')
                return
        else:
            if not os.path.isdir(scan_input):
                QMessageBox.warning(self, 'Missing Folder', 'Select an existing folder of scan PDFs.')
                return
            scan_files = sorted(
                str(p) for p in Path(scan_input).rglob('*')
                if p.is_file() and p.suffix.lower() == '.pdf'
            )
            if not scan_files:
                QMessageBox.warning(self, 'No PDFs', 'No PDF files found in the selected folder.')
                return

        course_folder = self._course_folder()
        if not course_folder or not os.path.isdir(course_folder):
            QMessageBox.warning(self, 'Missing Course Folder', 'Set the course folder in Course Info first.')
            return

        extract_quant = self.extract_quant.isChecked()
        single_sides = self.single_sides.isChecked()

        all_results = []
        all_recorded = []
        processed_count = 0
        for scan_path in scan_files:
            try:
                results, recorded = self._process_one_scan(scan_path, course_folder, extract_quant, single_sides)
                all_results.extend(results)
                all_recorded.extend(recorded)
                processed_count += 1
            except Exception as error:
                QMessageBox.critical(self, 'Could Not Grade Scan', f'{scan_path}:\n{error}')

        if extract_quant and all_results:
            resolved = [r for r in all_results if not r.held_up]
            if not any(r.has_quant for r in resolved):
                QMessageBox.information(
                    self, 'No Quant Component',
                    'No Quant/ODT component was found for any quiz in this batch. '
                    'Quant extraction and single-sided options have been disabled.'
                )
                self.extract_quant.setChecked(False)
                self.single_sides.setChecked(False)

        self._show_results(all_results, all_recorded)
        mode_note = ' [TEST MODE: no DB updates]' if TEST_MODE else ''
        self.status_label.setText(
            f'Processed {len(all_results)} quiz result(s) from {processed_count} scan(s); '
            f'recorded {len(all_recorded)} quiz attempt(s) at {datetime.now().strftime("%H:%M:%S")}.'
            f'{mode_note}'
        )

    def _process_one_scan(self, scan_path, course_folder, extract_quant, single_sides):
        session_info = start_grading_session(self.engine, course_folder, scan_path)
        grading_dir = str(session_info['directory'])
        results = parse_scan_file(self.engine, scan_path, grading_dir, course_folder)

        for result in results:
            self._resolve_if_needed(result, course_folder)
            if not result.held_up:
                self._confirm_overwrite_if_needed(result)

        to_record = [r for r in results if not r.held_up and not r.overwrite_declined]
        recorded = record_results(
            self.engine, to_record, grading_dir,
            grading_session_id=session_info['grading_session_id'],
            date_taken=self.date_taken.date().toString('yyyy-MM-dd'),
            send_feedback=self.send_feedback.isChecked(),
            extract_quant=extract_quant,
            single_sides=single_sides,
            scan_path=session_info.get('scan_path') or scan_path,
        )
        return results, recorded

    def _resolve_if_needed(self, result, course_folder):
        """Prompt the grader to identify a scan whose QR code could not be read.

        Keeps prompting on invalid input until the grader resolves it or
        chooses to skip (Cancel), in which case the result stays unrecorded.
        """
        while result.held_up:
            dialog = UnresolvedScanDialog(result.unresolved_image_path, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            module_number, student_code, quiz_number = dialog.values()
            if not student_code:
                QMessageBox.warning(self, 'Missing Student Code', 'Enter a student code.')
                continue
            try:
                resolve_held_up_result(
                    self.engine, result, module_number, student_code, quiz_number, course_folder,
                )
            except Exception as error:
                QMessageBox.warning(self, 'Could Not Resolve Scan', str(error))

    def _confirm_overwrite_if_needed(self, result):
        """Ask before overwriting a quiz that was already graded with a different score.

        This is what happens when the same physical quiz is graded a second
        time (e.g. re-scanning to fix a misread or an earlier bad manual
        resolution): re-recording it updates the existing attempt in place,
        so we confirm with the grader first.
        """
        existing_score = get_quiz_score_by_quiz_id(self.engine, result.quiz_id)
        new_score = round(result.total_score)
        if existing_score is None or existing_score == new_score:
            return
        answer = QMessageBox.question(
            self, 'Overwrite Existing Score?',
            f'Quiz {result.quiz_id} was already graded with score {existing_score}.\n'
            f'Overwrite it with the new score of {new_score}?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            result.overwrite_declined = True

    def _show_results(self, results, recorded):
        self._displayed_results = list(results)
        recorded_ids = {getattr(quiz, 'quiz_id', '') for quiz in recorded}
        self.results_table.setRowCount(len(results))
        for row, result in enumerate(results):
            status = 'Recorded' if result.quiz_id in recorded_ids else 'Not recorded'
            if result.held_up:
                status = 'Unresolved'
            elif result.overwrite_declined:
                status = 'Skipped (not overwritten)'

            mcq_score = result.mcq_score
            mcq_text = f'{mcq_score:.0f}' if mcq_score is not None else ''
            total_text = mcq_text

            quiz_id_item = QTableWidgetItem(result.quiz_id)
            student_code_item = QTableWidgetItem(result.student_code or '')
            module_item = QTableWidgetItem(str(result.module_number))
            mcq_score_item = QTableWidgetItem(mcq_text)
            quant_score_item = QTableWidgetItem('0' if result.has_quant else '-')
            max_quant_item = QTableWidgetItem('0' if result.has_quant else '-')
            total_score_item = QTableWidgetItem(total_text)
            status_item = QTableWidgetItem(status)

            # Make sure most cells are not editable
            for item in (quiz_id_item, student_code_item, module_item,
                         mcq_score_item, total_score_item, status_item):
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)

            if not result.has_quant:
                gray = QColor('lightgray')
                for item in (quant_score_item, max_quant_item):
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    item.setBackground(gray)
                    item.setForeground(QColor('gray'))
            else:
                quant_score_item.setData(Qt.ItemDataRole.UserRole, 0)
                max_quant_item.setData(Qt.ItemDataRole.UserRole, 0)

            if result.validation_issues:
                status_item.setToolTip('\n'.join(result.validation_issues))

            values = [
                quiz_id_item,
                student_code_item,
                module_item,
                mcq_score_item,
                quant_score_item,
                max_quant_item,
                total_score_item,
                status_item,
            ]
            for column, item in enumerate(values):
                self.results_table.setItem(row, column, item)

    def _update_quant_scores(self):
        for row in range(self.results_table.rowCount()):
            if row >= len(self._displayed_results):
                continue
            result = self._displayed_results[row]
            if not result.has_quant:
                continue

            mcq_component = next((c for c in result.components if c.component_type == 'mcq'), None)
            if mcq_component is None:
                continue
            mcq_correct = mcq_component.correct
            mcq_total = mcq_component.total

            quant_item = self.results_table.item(row, 4)
            max_item = self.results_table.item(row, 5)
            if quant_item is None or max_item is None:
                continue

            try:
                quant_achieved = int(quant_item.text().strip() or '0')
            except ValueError:
                quant_achieved = 0
            try:
                quant_max = int(max_item.text().strip() or '0')
            except ValueError:
                quant_max = 0

            if quant_max <= 0:
                total_pct = result.total_score
            else:
                total_pct = 100.0 * (mcq_correct + quant_achieved) / (mcq_total + quant_max)

            total_item = self.results_table.item(row, 6)
            if total_item is not None:
                total_item.setText(f'{round(total_pct)}')

    def _set_max_quant_points(self):
        value = self.max_quant_points_spin.value()
        for row in range(self.results_table.rowCount()):
            if row >= len(self._displayed_results):
                continue
            result = self._displayed_results[row]
            if not result.has_quant:
                continue
            item = self.results_table.item(row, 5)
            if item is not None:
                item.setText(str(value))
                item.setData(Qt.ItemDataRole.UserRole, value)

    def _refresh_section_meeting_grades(self):
        current_id = self.section_meeting_combo.currentData()
        self.section_meeting_combo.blockSignals(True)
        self.section_meeting_combo.clear()
        for meeting in get_section_meetings(self.engine):
            label = f"Section {meeting['section_number']} — {meeting['meeting_date']} {meeting['start_time']}"
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
                '' if grade.get('score') is None else str(grade.get('score')),
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
