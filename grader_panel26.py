from dataclasses import replace
from datetime import datetime
import os
import pickle
import shutil
import traceback
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLabel,
    QLineEdit, QPushButton, QFileDialog, QDateEdit, QCheckBox, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QDialog,
    QRadioButton, QButtonGroup, QInputDialog,
)
from PyQt6.QtCore import QDate, Qt
from PyQt6.QtGui import QColor

from database26 import (
    clear_grading_sessions, delete_quiz_attempt, get_all_students, get_course_info,
    get_pending_quant_quizzes, get_quiz_by_id, reassign_quiz_attempt,
    get_quiz_score_by_quiz_id, get_student_by_code, student_name_sort_key,
)
from grading26 import (
    ComponentScore, GradingResult, build_replacement_packet, find_prior_grading, parse_scan_file,
    is_spare_student_code, needs_transfer_review,
    record_grading_result, record_results, rescore_result, resolve_held_up_result,
    save_grading_artifacts,
    _send_feedback_if_enabled, _queue_test_feedback_email, _write_replacement_list, TEST_MODE,
)
from grading_session26 import start_grading_session
from unresolved_scan_dialog26 import (
    BlankPageDialog, MissingAnswerDialog, MultipleMarkDialog, TransferPromptDialog,
    UnresolvedScanDialog,
)
from regrade_dialog26 import QuizSelectionDialog, RegradeDialog


# Results table column indices
COL_QUIZ_ID = 0
COL_NAME = 1
COL_SEC = 2
COL_CODE = 3
COL_MODULE = 4
COL_MCQ = 5
COL_QUANT = 6
COL_MAX_Q = 7
COL_TOTAL = 8
COL_STATUS = 9
NUM_COLS = 10


class GraderPanel(QWidget):
    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._displayed_results = []
        self._recorded_ids = set()
        self._updating_table = False
        self._last_grading_session_id = None
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

        self.count_correct_ids = QLineEdit()
        self.count_correct_ids.setPlaceholderText('e.g. RST0_11')
        form.addRow('Count correct IDs:', self.count_correct_ids)

        grade_row = QHBoxLayout()
        grade_button = QPushButton('Grade')
        grade_button.setMaximumWidth(70)
        grade_button.clicked.connect(self._grade_scan)
        update_scores_button = QPushButton('Update Scores')
        update_scores_button.setMaximumWidth(110)
        update_scores_button.clicked.connect(self._update_scores)
        regrade_current_button = QPushButton('Regrade Current Session')
        regrade_current_button.setMaximumWidth(150)
        regrade_current_button.clicked.connect(self._regrade_current_session)
        regrade_prior_button = QPushButton('Regrade Prior Sessions')
        regrade_prior_button.setMaximumWidth(150)
        regrade_prior_button.clicked.connect(self._regrade_prior_session)
        grade_row.addWidget(grade_button)
        grade_row.addWidget(update_scores_button)
        grade_row.addWidget(regrade_current_button)
        grade_row.addWidget(regrade_prior_button)
        grade_row.addStretch()
        form.addRow('', grade_row)

        quant_points_row = QHBoxLayout()
        load_pending_button = QPushButton('Load Pending Quant')
        load_pending_button.setMaximumWidth(130)
        load_pending_button.clicked.connect(self._load_pending_quant)
        quant_points_row.addWidget(load_pending_button)
        quant_points_row.addStretch()
        form.addRow('', quant_points_row)

        layout.addWidget(source_group)

        layout.addWidget(QLabel('Results'))
        self.results_table = QTableWidget(0, NUM_COLS)
        self.results_table.setHorizontalHeaderLabels([
            'Quiz ID', 'Name', 'Sec', 'Code', 'Module', 'MCQ', 'Quant Score',
            'Max Q', 'Total', 'Status',
        ])
        self.results_table.setEditTriggers(QTableWidget.EditTrigger.AllEditTriggers)
        self.results_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.results_table.cellChanged.connect(self._on_results_cell_changed)
        header = self.results_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        header.setDefaultSectionSize(70)
        self.results_table.setColumnWidth(COL_QUIZ_ID, 120)
        self.results_table.setColumnWidth(COL_NAME, 120)
        self.results_table.setColumnWidth(COL_SEC, 30)
        self.results_table.setColumnWidth(COL_CODE, 60)
        self.results_table.setColumnWidth(COL_MODULE, 55)
        self.results_table.setColumnWidth(COL_MCQ, 60)
        self.results_table.setColumnWidth(COL_QUANT, 100)
        self.results_table.setColumnWidth(COL_MAX_Q, 60)
        self.results_table.setColumnWidth(COL_TOTAL, 60)
        self.results_table.setColumnWidth(COL_STATUS, 65)
        layout.addWidget(self.results_table)

        results_button_row = QHBoxLayout()
        save_temp_button = QPushButton('Save Temp')
        save_temp_button.setMaximumWidth(90)
        save_temp_button.clicked.connect(self._save_temp_results)
        results_button_row.addWidget(save_temp_button)
        load_temp_button = QPushButton('Load Temp')
        load_temp_button.setMaximumWidth(90)
        load_temp_button.clicked.connect(self._load_temp_results)
        results_button_row.addWidget(load_temp_button)
        results_button_row.addStretch()
        layout.addLayout(results_button_row)

        self.status_label = QLabel(
            'Select one scan PDF or a folder of scan PDFs and click Grade. '
            'A grading session folder is created automatically under '
            '<course_folder>/grading/<date>/<date><letter>/.'
        )
        layout.addWidget(self.status_label)

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

    def _temp_results_path(self) -> Path:
        """Return a temporary pickle file path for saved results.

        Uses the course folder if one is configured, otherwise the system temp
        directory, so saved results stay with the course being worked on.
        """
        course_folder = get_course_info(self.engine).get('course_folder', '').strip()
        if course_folder and os.path.isdir(course_folder):
            return Path(course_folder) / '.mcq26_grader_temp_results.pkl'
        return Path(os.path.expanduser('~')) / '.mcq26_grader_temp_results.pkl'

    def _save_temp_results(self):
        """Pickle the currently displayed results for later reload."""
        if not self._displayed_results:
            QMessageBox.information(self, 'Nothing to Save', 'No results are currently displayed.')
            return
        path = self._temp_results_path()
        try:
            # Avoid pickling large cached page images; they aren't needed for reload.
            results = [replace(result, page_images=[]) for result in self._displayed_results]
            recorded = [{'quiz_id': qid} for qid in getattr(self, '_recorded_ids', set())]
            with open(path, 'wb') as f:
                pickle.dump((results, recorded), f)
            self.status_label.setText(f'Saved {len(results)} result(s) to temp file: {path}')
        except Exception as error:
            traceback.print_exc()
            QMessageBox.warning(self, 'Could Not Save Temp Results', str(error))

    def _load_temp_results(self):
        """Load previously pickled results back into the results table."""
        path = self._temp_results_path()
        if not path.exists():
            QMessageBox.information(self, 'No Temp File', f'No temp results file found at {path}.')
            return
        try:
            with open(path, 'rb') as f:
                data = pickle.load(f)
            # New format: (results, recorded); old format: results only
            if isinstance(data, tuple) and len(data) == 2:
                results, recorded = data
            elif isinstance(data, list):
                results, recorded = data, []
            else:
                raise ValueError('Temp file does not contain a valid result list.')
            if not isinstance(results, list):
                raise ValueError('Temp file does not contain a valid result list.')
            for r in results:
                # Fields added after the temp file was written.
                if not hasattr(r, 'maybe_blank'):
                    r.maybe_blank = False
                if not hasattr(r, 'resolved_as_blank'):
                    r.resolved_as_blank = False
            self._show_results(results, recorded)
            self.status_label.setText(f'Loaded {len(results)} result(s) from temp file: {path}')
        except Exception as error:
            traceback.print_exc()
            QMessageBox.warning(self, 'Could Not Load Temp Results', str(error))

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
                str(p) for p in Path(scan_input).iterdir()
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

        try:
            session_info = start_grading_session(self.engine, course_folder, scan_files)
            self._last_grading_session_id = session_info.get('grading_session_id')
        except Exception as error:
            traceback.print_exc()
            QMessageBox.critical(self, 'Could Not Start Grading Session', str(error))
            return

        processing_path = str(Path(session_info['directory']) / 'combined_scan.pdf')
        try:
            if len(scan_files) > 1:
                self._combine_scan_files(scan_files, processing_path)
            else:
                shutil.copy2(scan_files[0], processing_path)
        except Exception as error:
            traceback.print_exc()
            QMessageBox.critical(self, 'Could Not Save Scan to Session Folder', str(error))
            if TEST_MODE:
                clear_grading_sessions(self.engine)
            return

        all_results = []
        all_recorded = []
        processed_count = 0
        try:
            all_results, all_recorded = self._process_one_scan(
                processing_path, course_folder, extract_quant, single_sides, session_info,
            )
            processed_count = len(scan_files)
        except Exception as error:
            traceback.print_exc()
            QMessageBox.critical(self, 'Could Not Grade Scan', f'{processing_path}:\n{error}')

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
        if TEST_MODE:
            clear_grading_sessions(self.engine)
        mode_note = ' [TEST MODE: no DB updates; grading sessions reset]' if TEST_MODE else ''
        self.status_label.setText(
            f'Processed {len(all_results)} quiz result(s) from {processed_count} scan(s); '
            f'recorded {len(all_recorded)} quiz attempt(s) at {datetime.now().strftime("%H:%M:%S")}.'
            f'{mode_note}'
        )

    def _combine_scan_files(self, scan_files, output_path):
        writer = PdfWriter()
        for scan_file in scan_files:
            reader = PdfReader(scan_file)
            for page in reader.pages:
                writer.add_page(page)
        with open(output_path, 'wb') as output:
            writer.write(output)

    def _regrade_current_session(self):
        """Pick a quiz recorded in the most recent grading session."""
        if self._last_grading_session_id is None:
            QMessageBox.information(
                self, 'No Current Session',
                'No grading session is in progress. Grade a scan first.'
            )
            return
        picker = QuizSelectionDialog(
            self.engine, self,
            title='Regrade Current Session',
            session_filter=self._last_grading_session_id,
        )
        if picker.exec() != QDialog.DialogCode.Accepted or picker.selected_quiz_id is None:
            return
        RegradeDialog(self.engine, picker.selected_quiz_id, self).exec()

    def _regrade_prior_session(self):
        """Pick a quiz recorded in any prior grading session."""
        picker = QuizSelectionDialog(
            self.engine, self,
            title='Regrade Prior Sessions',
            exclude_session=self._last_grading_session_id,
        )
        if picker.exec() != QDialog.DialogCode.Accepted or picker.selected_quiz_id is None:
            return
        RegradeDialog(self.engine, picker.selected_quiz_id, self).exec()

    def _process_one_scan(
        self, scan_path, course_folder, extract_quant, single_sides, session_info,
    ):
        grading_dir = str(session_info['directory'])
        results = parse_scan_file(self.engine, scan_path, grading_dir, course_folder, single_sides=single_sides)

        forced_correct_ids = {
            value.strip() for value in self.count_correct_ids.text().replace(',', ' ').split()
            if value.strip()
        }
        date_taken = self.date_taken.date().toString('yyyy-MM-dd')
        for result in results:
            result.grading_dir = grading_dir
            result.grading_session_id = session_info['grading_session_id']
            result.date_taken = date_taken
        for result in results:
            self._resolve_if_needed(result, course_folder, results)
            if not result.held_up and not result.resolved_as_quant and not result.resolved_as_blank:
                self._prompt_transfer_if_unenrolled(result)
                self._warn_scan_issues(result)
                self._resolve_reading_issues(result)
            if not result.held_up and not result.resolved_as_quant and not result.resolved_as_blank:
                rescore_result(result, forced_correct_ids)
                if not self._warn_if_duplicate_quiz(result) and not result.has_quant:
                    self._confirm_overwrite_if_needed(result)

        recorded = record_results(
            self.engine, results, grading_dir,
            grading_session_id=session_info['grading_session_id'],
            date_taken=date_taken,
            send_feedback=self.send_feedback.isChecked(),
            extract_quant=extract_quant,
            single_sides=single_sides,
            scan_path=scan_path,
            course_folder=course_folder,
        )
        # The replacement packet is built inside record_results; if it is
        # missing while a replacement list exists, its build failed - surface
        # that instead of leaving the user wondering where it went.
        if (Path(grading_dir) / 'replacement_list.txt').exists() and not (
                Path(grading_dir) / 'replacement_packet.pdf').exists():
            self.status_label.setText(
                'Warning: replacement list was written but '
                'replacement_packet.pdf was not generated - check the log.')
        return results, recorded

    def _resolve_if_needed(self, result, course_folder, all_results=None):
        """Prompt the grader to identify a scan whose QR code could not be read.

        Keeps prompting on invalid input until the grader resolves it or
        chooses to skip (Cancel), in which case the result stays unrecorded.
        """
        while result.held_up:
            if result.maybe_blank:
                # Probably-blank page: one click to skip it rather than a full
                # identification dialog.  'Not blank' falls through to the
                # normal resolution prompt below.
                blank_dialog = BlankPageDialog(result.unresolved_image_path, self)
                if blank_dialog.exec() == QDialog.DialogCode.Accepted:
                    result.resolved_as_blank = True
                    result.held_up = False
                    return
                result.maybe_blank = False
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
                    all_results=all_results,
                )
            except Exception as error:
                traceback.print_exc()
                QMessageBox.warning(self, 'Could Not Resolve Scan', str(error))

    def _prompt_transfer_if_unenrolled(self, result):
        """Offer to transfer a quiz whose owner is not a currently enrolled student.

        Quizzes printed for TAs, departed students, or spare codes such as
        'SaQ' (Sample Questions) are sometimes taken by someone else;
        catching that here - after the scan is identified but before the
        result is graded and recorded under the printed owner - avoids a
        manual transfer afterwards.  The quiz's first page is shown so the
        grader can read the name the student wrote.
        """
        if not needs_transfer_review(self.engine, result.student_code):
            return
        student = get_student_by_code(self.engine, result.student_code)

        owner = result.student_name or (student.name if student else '') or result.student_code
        reason = (
            'which is a spare (Sample Questions) quiz.'
            if is_spare_student_code(result.student_code)
            else 'who is not a currently enrolled student.'
        )
        dialog = TransferPromptDialog(
            result.first_page_image_path or result.unresolved_image_path,
            f'Quiz {result.quiz_id} is associated with\n'
            f'  {owner} ({result.student_code})\n'
            f'{reason}\n\n'
            'Transfer this quiz to a different student?',
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        new_student = self._prompt_for_student(result)
        if new_student is not None:
            self._perform_transfer(result, new_student)

    def _prompt_for_student(self, result):
        """Ask for the transfer target's name or student code; return the Student or None."""
        while True:
            text, ok = QInputDialog.getText(
                self, 'Transfer Quiz',
                f'Transfer {result.quiz_id} to which student?\n'
                'Enter a name or student code:')
            if not ok or not text.strip():
                return None
            text = text.strip()
            student = get_student_by_code(self.engine, text)
            if student is None:
                all_students = get_all_students(self.engine, enrolled_only=False)
                matches = [s for s in all_students if s.name.lower() == text.lower()]
                if not matches:
                    matches = [s for s in all_students if text.lower() in s.name.lower()]
                if len(matches) == 1:
                    student = matches[0]
                elif len(matches) > 1:
                    names_list = '\n'.join(f'  {s.name} ({s.student_code})' for s in matches)
                    QMessageBox.warning(
                        self, 'Multiple Matches',
                        f'Multiple students match "{text}":\n{names_list}\n\n'
                        'Enter the full exact name or student code.',
                    )
                    continue
            if student is None:
                QMessageBox.warning(
                    self, 'Student Not Found',
                    f'No student matching "{text}" was found in the database.',
                )
                continue
            if student.student_code == result.student_code:
                QMessageBox.warning(
                    self, 'Same Student',
                    'That is the student the quiz is already assigned to.',
                )
                continue
            return student

    def _warn_scan_issues(self, result):
        """Tell the grader about page-level scan problems worth knowing up front.

        Calibration failure means no bubbles on that page were read at all,
        so several questions may appear unanswered even though the student
        marked them - they will come up in the missing-answer prompts. A
        duplicate-scanned page is also reported, since only the first copy
        was graded and the grader may want to verify via the scan itself.
        """
        issues = [
            issue for issue in result.validation_issues
            if 'calibration' in issue.lower() or 'scanned more than once' in issue
        ]
        if not issues:
            return
        notes = []
        if any('calibration' in issue.lower() for issue in issues):
            notes.append(
                'No marks could be read on calibration-failed page(s); any '
                'filled answers there will need to be resolved manually.'
            )
        if any('scanned more than once' in issue for issue in issues):
            notes.append(
                'The extra copy was ignored; if the copies differ, verify '
                'against the original scan.'
            )
        QMessageBox.warning(
            self,
            'Scan Problem Detected',
            f'{result.quiz_id}: ' + '\n'.join(issues) + '\n\n' + ' '.join(notes),
        )

    def _resolve_reading_issues(self, result):
        questions = {q['question_number']: q for q in result.quiz_questions}

        # Prompt for missing answers (no mark detected) before scoring.
        for question in result.quiz_questions:
            question_number = question['question_number']
            if question_number in result.student_answers:
                continue
            question_id = question.get('question_id', '')
            dialog = MissingAnswerDialog(
                result.missing_image_paths.get(question_number),
                result.quiz_id,
                question_number,
                question_id,
                self,
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                result.held_up = True
                result.validation_issues.append(
                    f'Question {question_number}: missing answer was not resolved.'
                )
                return
            choice = dialog.value()
            if choice == 'Count correct':
                correct = result.correct_answers.get(question_number)
                result.student_answers[question_number] = [correct] if correct else []
            elif choice == 'Leave unanswered':
                result.student_answers[question_number] = []
            else:
                result.student_answers[question_number] = [choice]

        for question_number, detected in list(result.student_answers.items()):
            if len(detected) <= 1:
                continue
            question = questions.get(question_number, {})
            question_id = question.get('question_id', '')
            dialog = MultipleMarkDialog(
                result.ambiguous_image_paths.get(question_number),
                result.quiz_id,
                question_number,
                question_id,
                list(detected),
                self,
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                result.held_up = True
                result.validation_issues.append(
                    f'Question {question_number}: multiple marked circles were not resolved.'
                )
                return
            choice = dialog.value()
            if choice == 'Count correct':
                correct = result.correct_answers.get(question_number)
                result.student_answers[question_number] = [correct] if correct else []
            elif choice == 'Leave unanswered':
                result.student_answers[question_number] = []
            else:
                result.student_answers[question_number] = [choice]

    def _warn_if_duplicate_quiz(self, result) -> bool:
        """Warn when this quiz ID was already graded in an earlier session.

        Either the same sheet was rescanned or a second copy of the same
        quiz ID was taken; both replace the earlier record. Returns True if
        the warning was shown (the grader's choice is final).
        """
        prior = find_prior_grading(self.engine, result)
        if prior is None:
            return False

        earlier = f"score {prior['score'] if prior['score'] is not None else 'none'}"
        if prior['quant_points_earned'] is not None:
            earlier += f", Quant {prior['quant_points_earned']:g}/{prior['quant_total_points']:g}"
        differing = prior['differing_questions']
        if differing is None:
            comparison = 'The earlier answers could not be found for comparison.'
        elif differing:
            comparison = (
                'The answers differ on question(s) '
                f"{', '.join(str(n) for n in differing)}, so this may be a DIFFERENT "
                'copy of the same quiz (duplicate quiz ID).'
            )
        else:
            comparison = 'The answers match the earlier scan, so this is probably a rescan of the same sheet.'
        new_score = (
            'pending Quant' if result.has_quant else str(round(result.total_score))
        )

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle('Quiz Already Graded')
        box.setText(
            f"Quiz {result.quiz_id} ({prior['student_name'] or result.student_code}) was already "
            f"graded in session {prior['session_label']} (taken {prior['date_taken'] or '?'}): "
            f"{earlier}.\n\n{comparison}\n\n"
            f'This scan: score {new_score}. There is one record per quiz ID, so recording '
            'this scan replaces the earlier record (including its stored Quant score). '
            'If the new score equals the earlier one, no new feedback email is generated.'
        )
        record_button = box.addButton('Record This Scan', QMessageBox.ButtonRole.AcceptRole)
        skip_button = box.addButton('Skip This Scan', QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(skip_button)
        box.setEscapeButton(skip_button)
        box.exec()
        if box.clickedButton() is not record_button:
            result.overwrite_declined = True
        return True

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

    def _is_row_complete(self, row: int) -> bool:
        """Determine whether a results row is ready to be recorded.

        Non-quant rows are always complete (MCQ-only score is known).
        Quant rows are complete when the Quant Score cell contains an
        explicit value (even '0') — an empty cell means ungraded.
        """
        if row >= len(self._displayed_results):
            return False
        result = self._displayed_results[row]
        if result.held_up or result.overwrite_declined or result.resolved_as_blank:
            return False
        if not result.has_quant:
            return True
        quant_item = self.results_table.item(row, COL_QUANT)
        if quant_item is None:
            return False
        return quant_item.text().strip() != ''

    def _status_text(self, row: int) -> str:
        if row >= len(self._displayed_results):
            return ''
        result = self._displayed_results[row]
        if result.held_up:
            return 'Held'
        if result.resolved_as_blank:
            return 'Blank'
        if result.overwrite_declined:
            return 'Skipped'
        if result.has_quant and not self._is_row_complete(row):
            return 'Pending'
        if result.quiz_id in self._recorded_ids:
            return 'Recorded'
        return 'Ready'

    def _refresh_status_column(self):
        """Update the Status column text for every row."""
        self._updating_table = True
        try:
            for row in range(self.results_table.rowCount()):
                status_item = self.results_table.item(row, COL_STATUS)
                if status_item is not None:
                    status_item.setText(self._status_text(row))
        finally:
            self._updating_table = False

    def _show_results(self, results, recorded):
        self._updating_table = True
        try:
            def _result_name_key(result):
                name = result.student_name or ''
                if not name and result.student_code:
                    student = get_student_by_code(self.engine, result.student_code)
                    if student:
                        name = student.name or ''
                return student_name_sort_key(name)

            sorted_results = sorted(results, key=_result_name_key)
            self._displayed_results = sorted_results
            recorded_ids = set()
            for quiz in recorded:
                if isinstance(quiz, dict):
                    recorded_ids.add(quiz.get('quiz_id', ''))
                else:
                    recorded_ids.add(getattr(quiz, 'quiz_id', ''))
            self._recorded_ids = recorded_ids
            self.results_table.setRowCount(len(sorted_results))
            for row, result in enumerate(sorted_results):
                mcq_score = result.mcq_score
                mcq_text = f'{mcq_score:.0f}' if mcq_score is not None else ''
                total_text = f'{result.total_score:.0f}'
                max_quant_text = f'{result.quant_total_points:g}' if result.has_quant else '-'
                quant_component = next(
                    (component for component in result.components if component.component_type == 'quant'), None,
                )
                # Empty string means "not yet graded"; '0' means graded with zero.
                if quant_component is not None:
                    quant_text = f'{quant_component.correct:g}'
                elif result.has_quant:
                    quant_text = ''
                else:
                    quant_text = '-'

                name = result.student_name or ''
                section_number = result.section_number
                if not name and result.student_code:
                    student = get_student_by_code(self.engine, result.student_code)
                    if student:
                        name = student.name or ''
                        if section_number is None:
                            section_number = student.section_number
                section_text = str(section_number) if section_number is not None else ''

                quiz_id_item = QTableWidgetItem(result.quiz_id)
                name_item = QTableWidgetItem(name)
                section_item = QTableWidgetItem(section_text)
                section_item.setFlags(section_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                student_code_item = QTableWidgetItem(result.student_code or '')
                module_item = QTableWidgetItem(str(result.module_number))
                mcq_score_item = QTableWidgetItem(mcq_text)
                quant_score_item = QTableWidgetItem(quant_text)
                max_quant_item = QTableWidgetItem(max_quant_text)
                total_score_item = QTableWidgetItem(total_text)
                status_item = QTableWidgetItem('')
                status_item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                )

                # Make sure most cells are not editable
                for item in (quiz_id_item, section_item, student_code_item,
                             module_item, mcq_score_item, total_score_item, status_item):
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)

                if not result.has_quant:
                    gray = QColor('lightgray')
                    for item in (quant_score_item, max_quant_item):
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                        item.setBackground(gray)
                        item.setForeground(QColor('gray'))
                else:
                    quant_score_item.setData(Qt.ItemDataRole.UserRole, 0)
                    max_quant_item.setData(Qt.ItemDataRole.UserRole, result.quant_total_points)

                if result.validation_issues:
                    status_item.setToolTip('\n'.join(result.validation_issues))

                values = [
                    quiz_id_item,
                    name_item,
                    section_item,
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
        finally:
            self._updating_table = False
        self._refresh_status_column()

    def _handle_name_change(self, row: int):
        """Handle an edit to the Name column — transfer the quiz to a different student."""
        if row >= len(self._displayed_results):
            return
        result = self._displayed_results[row]
        name_item = self.results_table.item(row, COL_NAME)
        if name_item is None:
            return
        new_name = name_item.text().strip()
        old_name = result.student_name or ''
        if new_name.lower() == old_name.lower():
            return  # no real change

        # Look up the new student by name (case-insensitive exact match).
        all_students = get_all_students(self.engine, enrolled_only=False)
        matches = [s for s in all_students if s.name.lower() == new_name.lower()]
        if not matches:
            # Try substring match as a fallback.
            matches = [s for s in all_students if new_name.lower() in s.name.lower()]
        if not matches:
            QMessageBox.warning(
                self, 'Student Not Found',
                f'No student matching "{new_name}" was found in the database.',
            )
            self._updating_table = True
            try:
                name_item.setText(old_name)
            finally:
                self._updating_table = False
            return
        if len(matches) > 1:
            # Multiple matches — let the user pick.
            names_list = '\n'.join(f'  {s.name} ({s.student_code})' for s in matches)
            QMessageBox.warning(
                self, 'Multiple Matches',
                f'Multiple students match "{new_name}":\n{names_list}\n\n'
                'Please type the full exact name.',
            )
            self._updating_table = True
            try:
                name_item.setText(old_name)
            finally:
                self._updating_table = False
            return

        new_student = matches[0]
        old_code = result.student_code or '(unknown)'

        # Confirm the transfer; the original quiz record is always removed
        # so a quiz_id never remains under two students.
        msg = QMessageBox(self)
        msg.setWindowTitle('Transfer Quiz')
        msg.setText(
            f'Transfer quiz {result.quiz_id} from\n'
            f'  {old_name} ({old_code})\n'
            f'to\n'
            f'  {new_student.name} ({new_student.student_code})?'
        )
        msg.setInformativeText(
            'The quiz record will be reassigned to the new student.'
        )
        cancel_btn = msg.addButton(QMessageBox.StandardButton.Cancel)
        transfer_btn = msg.addButton('Transfer', QMessageBox.ButtonRole.AcceptRole)
        msg.setDefaultButton(cancel_btn)
        msg.exec()
        clicked = msg.clickedButton()

        if clicked != transfer_btn:
            # Revert the name cell.
            self._updating_table = True
            try:
                name_item.setText(old_name)
            finally:
                self._updating_table = False
            return

        self._perform_transfer(result, new_student, old_name=old_name, old_code=old_code)

        # Refresh the row display.
        self._updating_table = True
        try:
            name_item.setText(new_student.name)
            code_item = self.results_table.item(row, COL_CODE)
            if code_item is not None:
                code_item.setText(new_student.student_code)
            sec_item = self.results_table.item(row, COL_SEC)
            if sec_item is not None:
                sec_text = str(new_student.section_number) if new_student.section_number is not None else ''
                sec_item.setText(sec_text)
        finally:
            self._updating_table = False
        self._refresh_status_column()

    def _perform_transfer(self, result, new_student, old_name=None, old_code=None):
        """Reassign *result*'s quiz record to *new_student* and refresh products.

        Shared by the results-table name edit and the unenrolled-owner prompt
        during grading.  Moves the DB row, repoints the in-memory result,
        keeps the original name for the replacement list, flags that a fresh
        feedback email is owed to the new owner, and regenerates the
        replacement list and packet.
        """
        if old_name is None:
            old_name = result.student_name or ''
        if old_code is None:
            old_code = result.student_code or '(unknown)'

        # Reassign the original DB record for this quiz_id to the new
        # student, preserving its generated-quiz fields (ODT/quant info).
        existing_quiz = get_quiz_by_id(self.engine, result.quiz_id)
        if existing_quiz:
            reassign_quiz_attempt(self.engine, existing_quiz['id'], new_student.student_id)

        # Keep the name the quiz was originally associated with so the
        # replacement list can show "Original Name (Transferred To Name)".
        if not result.original_student_name:
            old_student = get_student_by_code(self.engine, old_code)
            result.original_student_name = old_name or (
                old_student.name if old_student else '')

        # Update the in-memory result to point to the new student.
        result.student_code = new_student.student_code
        result.student_name = new_student.name
        result.section_number = new_student.section_number
        name_parts = (new_student.name or '').split()
        result.first_name = name_parts[0].lower() if name_parts else ''
        result.last_name = name_parts[-1].lower() if name_parts else ''

        # Mark as no longer recorded so Update Scores will re-record for the
        # new student, and flag that a fresh feedback email is owed to them
        # even when the transferred score is unchanged.
        self._recorded_ids.discard(result.quiz_id)
        result.feedback_pending = True
        result.feedback_sent = False

        # Regenerate the replacement list and packet so they reflect the transfer.
        grading_dir = result.grading_dir or next(
            (r.grading_dir for r in self._displayed_results if r.grading_dir), None)
        if grading_dir:
            _write_replacement_list(self._displayed_results, grading_dir, self.engine)
            course_folder = get_course_info(self.engine).get('course_folder', '').strip()
            if course_folder and os.path.isdir(course_folder):
                try:
                    build_replacement_packet(
                        self.engine, self._displayed_results, grading_dir, course_folder)
                except Exception as error:
                    traceback.print_exc()
                    self.status_label.setText(
                        f'Transferred {result.quiz_id} to {new_student.name}; '
                        f'could not rebuild replacement packet: {error}')
                    return

        self.status_label.setText(
            f'Transferred {result.quiz_id} to {new_student.name}.'
        )

    def _on_results_cell_changed(self, row, column):
        if self._updating_table:
            return
        if column == COL_NAME:
            self._handle_name_change(row)
            return
        if column not in (COL_QUANT, COL_MAX_Q):
            return
        if row >= len(self._displayed_results):
            return
        result = self._displayed_results[row]
        if not result.has_quant:
            return

        quant_item = self.results_table.item(row, COL_QUANT)
        max_item = self.results_table.item(row, COL_MAX_Q)
        if quant_item is None or max_item is None:
            return

        # An empty quant score cell means "not yet graded" — don't update.
        quant_text = quant_item.text().strip()
        if quant_text == '':
            self._refresh_status_column()
            return

        try:
            quant_achieved = float(quant_text)
            quant_max = float(max_item.text().strip() or '0')
        except ValueError:
            return
        if quant_max < 0 or quant_achieved < 0 or quant_achieved > quant_max:
            return

        result.quant_total_points = quant_max
        result.components = [
            component for component in result.components if component.component_type != 'quant'
        ]
        result.components.append(ComponentScore(
            component_type='quant',
            correct=quant_achieved,
            total=quant_max,
            score_pct=100.0 * quant_achieved / quant_max if quant_max else 0.0,
        ))

        self._updating_table = True
        try:
            quant_item.setText(f'{quant_achieved:g}')
            max_item.setText(f'{quant_max:g}')
            max_item.setData(Qt.ItemDataRole.UserRole, quant_max)
            mcq_item = self.results_table.item(row, COL_MCQ)
            if mcq_item is not None:
                mcq_item.setText(f'{result.mcq_score:.0f}')
            total_item = self.results_table.item(row, COL_TOTAL)
            if total_item is not None:
                total_item.setText(f'{round(result.total_score)}')
        finally:
            self._updating_table = False
        self._refresh_status_column()

    def _load_pending_quant(self):
        rows = get_pending_quant_quizzes(self.engine)
        if not rows:
            QMessageBox.information(self, 'No Pending Quant', 'No quizzes are awaiting Quant scores.')
            return
        results = []
        for row in rows:
            student = row.get('student') or {}
            name_parts = (student.get('name') or '').split()
            components = [ComponentScore(
                component_type='mcq',
                correct=float(row['mcq_points_earned']),
                total=float(row['mcq_points_possible']),
                score_pct=(100.0 * float(row['mcq_points_earned']) / float(row['mcq_points_possible'])
                           if row['mcq_points_possible'] else 0.0),
            )]
            if row.get('quant_points_earned') is not None:
                quant_earned = float(row['quant_points_earned'])
                quant_total = float(row['quant_total_points'])
                components.append(ComponentScore(
                    component_type='quant', correct=quant_earned, total=quant_total,
                    score_pct=100.0 * quant_earned / quant_total if quant_total else 0.0,
                ))
            results.append(GradingResult(
                quiz_id=row['quiz_id'],
                student_code=student.get('student_code'),
                module_number=row['module_number'],
                components=components,
                has_quant=True,
                quant_total_points=float(row['quant_total_points']),
                section_number=student.get('section_number'),
                student_name=student.get('name') or '',
                original_student_name=student.get('name') or '',
                first_name=name_parts[0].lower() if name_parts else '',
                last_name=name_parts[-1].lower() if name_parts else '',
                grading_session_id=row.get('grading_session_id'),
                date_taken=row.get('date_taken'),
            ))
        self._show_results(results, [])
        self.status_label.setText(f'Loaded {len(results)} quiz(es) awaiting Quant grading.')

    def _update_scores(self):
        if TEST_MODE:
            test_processed = 0
            for row in range(self.results_table.rowCount()):
                if not self._is_row_complete(row):
                    continue
                result = self._displayed_results[row]
                if result.has_quant:
                    if not self._finalize_quant_row(row, result, test_mode=True):
                        continue
                if result.grading_dir:
                    save_grading_artifacts(result, result.grading_dir)
                if self.send_feedback.isChecked() and not result.feedback_sent:
                    _queue_test_feedback_email(self.engine, result)
                    result.feedback_sent = True
                self._recorded_ids.add(result.quiz_id)
                test_processed += 1
            self._refresh_status_column()
            self.status_label.setText(
                f'[TEST MODE] Updated {test_processed} quiz(es).'
                if test_processed else
                '[TEST MODE] No complete rows to update.'
            )
            return

        processed = 0
        pending = 0
        failures = []
        for row in range(self.results_table.rowCount()):
            if row >= len(self._displayed_results):
                continue
            result = self._displayed_results[row]
            if result.resolved_as_blank:
                continue
            if not self._is_row_complete(row):
                pending += 1
                continue
            if result.has_quant:
                if not self._finalize_quant_row(row, result, test_mode=False):
                    failures.append(f'{result.quiz_id}: Quant Score and Max Quant Points must be valid numbers.')
                    continue

            # Look up the existing score for this quiz under the *current*
            # student (not any student) so that a quiz transferred to a new
            # student is correctly treated as a new record.
            current_student = get_student_by_code(self.engine, result.student_code)
            current_student_id = current_student.student_id if current_student else None
            existing_score = get_quiz_score_by_quiz_id(
                self.engine, result.quiz_id, student_id=current_student_id,
            )
            new_score = round(result.total_score)
            if existing_score is not None and existing_score == new_score:
                # Score unchanged — skip recording but count as processed.
                self._recorded_ids.add(result.quiz_id)
                processed += 1
                # A transferred quiz already carries its score under the new
                # owner, so it lands here - it is still owed a feedback email.
                if (result.feedback_pending and self.send_feedback.isChecked()
                        and not result.feedback_sent):
                    _send_feedback_if_enabled(self.engine, result)
                    result.feedback_sent = True
                    result.feedback_pending = False
                continue
            if existing_score is not None and existing_score != new_score:
                answer = QMessageBox.question(
                    self, 'Overwrite Existing Score?',
                    f'Quiz {result.quiz_id} was already graded with score {existing_score}.\n'
                    f'Overwrite it with the new score of {new_score}?',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    continue

            if result.grading_dir:
                save_grading_artifacts(result, result.grading_dir)
            quiz = record_grading_result(
                self.engine, result, date_taken=result.date_taken,
                grading_session_id=result.grading_session_id,
            )
            if quiz is not None:
                self._recorded_ids.add(result.quiz_id)
                processed += 1
                if self.send_feedback.isChecked():
                    _send_feedback_if_enabled(self.engine, result)
            else:
                failures.append(f'{result.quiz_id}: could not record quiz')

        if failures:
            QMessageBox.warning(self, 'Could Not Update Scores', '\n'.join(failures))
        self._refresh_status_column()
        total_rows = self.results_table.rowCount()
        self.status_label.setText(
            f'Updated {processed} of {total_rows} quiz(es) '
            f'({pending} pending, {len(failures)} failed).'
        )

    def _finalize_quant_row(self, row: int, result, test_mode: bool = False) -> bool:
        mcq_component = next((c for c in result.components if c.component_type == 'mcq'), None)
        if mcq_component is None:
            return False

        quant_item = self.results_table.item(row, COL_QUANT)
        max_item = self.results_table.item(row, COL_MAX_Q)
        if quant_item is None or max_item is None:
            return False

        quant_text = quant_item.text().strip()
        if quant_text == '':
            return False  # empty = not yet graded

        try:
            quant_achieved = float(quant_text)
            quant_max = float(max_item.text().strip() or '0')
        except ValueError:
            return False
        if quant_max < 0 or quant_achieved < 0 or quant_achieved > quant_max:
            return False

        result.quant_total_points = quant_max
        result.components = [
            component for component in result.components if component.component_type != 'quant'
        ]
        result.components.append(ComponentScore(
            component_type='quant',
            correct=quant_achieved,
            total=quant_max,
            score_pct=100.0 * quant_achieved / quant_max if quant_max else 0.0,
        ))

        self._updating_table = True
        try:
            quant_item.setText(f'{quant_achieved:g}')
            max_item.setText(f'{quant_max:g}')
            max_item.setData(Qt.ItemDataRole.UserRole, quant_max)
            mcq_item = self.results_table.item(row, COL_MCQ)
            if mcq_item is not None:
                mcq_item.setText(f'{result.mcq_score:.0f}')
            total_item = self.results_table.item(row, COL_TOTAL)
            if total_item is not None:
                total_item.setText(f'{round(result.total_score)}')
        finally:
            self._updating_table = False
        return True
