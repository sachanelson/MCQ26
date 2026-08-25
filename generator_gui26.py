"""
MCQ Generator Application - Main GUI for generating questions and quizzes.
"""
import warnings
warnings.filterwarnings(
    'ignore',
    message='.*default style.*',
    category=UserWarning,
    module='openpyxl.*',
)
import sys
import os
import re
import random
import shutil
import glob
import subprocess
from typing import Dict, List, Optional, Tuple
from PyQt6.QtWidgets import (
    QFileDialog, QLabel, QWidget
)
from PyQt6.QtCore import Qt
import traceback
from pathlib import Path
from datetime import datetime

# Add the directory containing this script to the Python path
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from PyQt6.QtWidgets import (
    QApplication, QVBoxLayout, QHBoxLayout, QWidget, QLabel, 
    QLineEdit, QPushButton, QMessageBox, QTabWidget, QComboBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QFileDialog, QTextEdit,
    QFormLayout, QSpinBox, QDoubleSpinBox, QGroupBox, QRadioButton,
    QDateEdit, QProgressDialog, QCheckBox
)
from PyQt6.QtCore import Qt, QDate

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from database26 import (
    Student, StudentModuleProgress, get_all_students, get_course_info,
    get_all_sections, get_students_for_section, get_all_students_as_dicts,
    get_student_by_code, get_section, update_quiz_odt_values,
    update_quiz_odt_info, get_student_module_question_ids, reset_quiz_data,
    NUM_MODULES,
)
from shared_gui26 import BaseMCQApp
from course_info_panel26 import CourseInfoPanel
from llm_converter26 import LLMConverter
from quiz_generator26 import (
    _find_start_attempt, create_quizzes_for_students, load_question_banks,
    pdf_page_count, stamp_page_numbers_to_pdf,
)
from document_ids26 import artifact_id, format_quiz_id
from OneUn import load_problem_definition, OneUnODTGenerator, ProblemDefinition



class MCQGeneratorGUI(BaseMCQApp):
    """Main application window for the MCQ Generator."""
    
    def load_default_parameters(self):
        """Load default parameters."""
        # First call the parent class implementation to get the base defaults
        super().load_default_parameters()
        
        # The new system uses 1-based module numbering (1..NUM_MODULES)
        self.defaults['module'] = 1
        
        # Quiz generation parameters
        self.defaults['totalQuestions'] = 20  # Used in quiz creator tab
    
    def __init__(self):
        """Initialize the GUI."""
        super().__init__()
        self.setWindowTitle("MCQ Generator")
        
        # Initialize flag to track if defaults have been loaded from file
        self.defaults_loaded_from_file = False
        
        # Initialize instance variables
        self.questions = []
        self.current_question_index = -1
        self.current_output_dir = ""
        self.current_topic_code = "UNK"
        self.current_difficulty = 0
        
        # Set up the UI
        self._init_ui()
    
    def _init_ui(self):
        """Initialize the user interface components."""
        # Create tab widget and set as central widget
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        
        # Initialize the LLM converter
        from llm_converter26 import LLMConverter
        self.llm_converter = LLMConverter(self)
        
        # Create tabs
        self.course_info_panel = self.add_course_info_tab()
        self.quiz_tab = self.create_quiz_tab()
        self.llm_converter_tab = self.llm_converter.create_llm_converter_tab()
        
        self.oneun_tab = self.create_oneun_tab()
        self.student_codes_tab = self.create_student_codes_tab()

        # Add tabs to the tab widget in the desired order
        # Course Info tab is already added in add_course_info_tab()
        self.tabs.addTab(self.quiz_tab, "Quiz Creator")
        self.tabs.addTab(self.llm_converter_tab, "LLM Question Converter")
        self.tabs.addTab(self.oneun_tab, "Quant ODT")
        self.tabs.addTab(self.student_codes_tab, "Student Codes")
        
        # Set up keyboard shortcuts for the LLM Converter tab
        self.llm_converter.setup_shortcuts()
        
        # Synchronize initial values across tabs
        self.sync_course(self.course_value)
        self.sync_course_title(self.course_title_value)
        self.sync_instructors(self.instructors_value)
    
    def add_course_info_tab(self):
        """Add the course information tab."""
        from course_info_panel26 import CourseInfoPanel
        
        # Create course info panel
        course_info_panel = CourseInfoPanel(self.engine, self)
        
        # The CourseInfoPanel's load_course_info method will load all values from the database
        # We don't need to set any initial values here as they'll be loaded from the database
        # This ensures all fields, including time-related fields, are properly initialized
        
        # Add tab
        self.tabs.addTab(course_info_panel, "Course Info")
        
        return course_info_panel
    
    def create_quiz_tab(self):
        """Create the quiz creator tab."""
        tab = QWidget()
        layout = QVBoxLayout()
        tab.setLayout(layout)
        
        # Initialize question banks
        self.question_banks = {}
        self.loaded_default_banks = False
        
        # Course info group
        course_group = QGroupBox("Course Information")
        course_layout = QFormLayout()
        course_group.setLayout(course_layout)
        
        # Create course inputs for this tab
        self.quiz_course = QLineEdit(self.course_value)
        self.quiz_title = QLineEdit(self.course_title_value)
        self.quiz_instructors = QLineEdit(', '.join(self.instructors_value) if isinstance(self.instructors_value, list) else (self.instructors_value or ''))
        
        # Set minimum width and font for all fields
        for field in [self.quiz_course, self.quiz_title, self.quiz_instructors]:
            field.setMinimumWidth(400)
            font = field.font()
            font.setPointSize(10)
            field.setFont(font)
        
        # Connect signals
        self.quiz_course.textChanged.connect(self.sync_course)
        self.quiz_title.textChanged.connect(self.sync_course_title)
        self.quiz_instructors.textChanged.connect(self.sync_instructors)
        
        # Add to layout
        course_layout.addRow("Course:", self.quiz_course)
        course_layout.addRow("Course Title:", self.quiz_title)
        course_layout.addRow("Instructors:", self.quiz_instructors)
        layout.addWidget(course_group)
        
        # Quiz creation form
        form_group = QGroupBox("Quiz Parameters")
        form_layout = QFormLayout()
        form_group.setLayout(form_layout)
        
        # Add date picker
        date_layout = QHBoxLayout()
        date_label = QLabel("Quiz Date:")
        self.quiz_date_edit = QDateEdit()
        self.quiz_date_edit.setCalendarPopup(True)
        self.quiz_date_edit.setDate(QDate.currentDate())  # Set to today's date
        self.quiz_date_edit.setDisplayFormat("yyyy-MM-dd")
        date_layout.addWidget(date_label)
        date_layout.addWidget(self.quiz_date_edit)
        form_layout.addRow("Quiz Date:", date_layout)
        
        # Module input with randomize answers checkbox
        module_layout = QHBoxLayout()
        
        self.quiz_module_input = QSpinBox()
        self.quiz_module_input.setValue(self.defaults['module'])
        self.quiz_module_input.valueChanged.connect(self.module_changed)
        module_layout.addWidget(self.quiz_module_input)
        
        module_layout.addStretch()  # Push checkbox to the left
        
        form_layout.addRow("Module:", module_layout)
        
        # Total questions input
        total_questions_layout = QHBoxLayout()
        self.total_questions_input = QSpinBox()
        self.total_questions_input.setRange(1, 1000)
        self.total_questions_input.setValue(self.defaults['totalQuestions'])
        total_questions_layout.addWidget(self.total_questions_input)
        
        total_questions_layout.addSpacing(20)
        total_questions_layout.addWidget(QLabel("Quizzes per student:"))
        self.num_quizzes_input = QSpinBox()
        self.num_quizzes_input.setRange(1, 10)
        self.num_quizzes_input.setValue(1)
        total_questions_layout.addWidget(self.num_quizzes_input)
        
        total_questions_layout.addStretch()
        form_layout.addRow("Total MCQ Questions:", total_questions_layout)

        # Append One Unknown quiz option
        self.append_oneun_checkbox = QCheckBox("Append Quant ODT")
        self.append_oneun_checkbox.setChecked(False)
        self.append_oneun_checkbox.setToolTip(
            "Also generate One Unknown ODTs appended to the MCQ quiz "
            "using settings from the One Unknown tab."
        )
        form_layout.addRow("", self.append_oneun_checkbox)

        # Group packet option
        self.create_packet_checkbox = QCheckBox("Create group packet PDF")
        self.create_packet_checkbox.setChecked(False)
        self.create_packet_checkbox.setToolTip(
            "Merge all generated quiz PDFs into a single group packet, "
            "sorted by student last name. Answer keys are not included."
        )
        form_layout.addRow("", self.create_packet_checkbox)

        # Reset quiz data (testing)
        reset_layout = QHBoxLayout()
        reset_layout.addStretch()
        self.reset_quiz_button = QPushButton("Reset Quiz Data")
        self.reset_quiz_button.setToolTip(
            "Drop all quiz attempts and quiz questions from the database "
            "for testing.  Student and question-bank data are kept."
        )
        self.reset_quiz_button.clicked.connect(self._reset_quiz_data)
        reset_layout.addWidget(self.reset_quiz_button)
        form_layout.addRow("", reset_layout)

        layout.addWidget(form_group)
        
        # Student Codes are managed in the shared Student Codes tab
        sc_note = QLabel("Student codes are set in the \u2018Student Codes\u2019 tab.")
        sc_note.setStyleSheet("color: #666; font-style: italic;")
        layout.addWidget(sc_note)
        
        # Question banks group
        qbank_group = QGroupBox("Question Banks")
        qbank_layout = QVBoxLayout()
        qbank_group.setLayout(qbank_layout)
        
        # Create table for question banks - change from 10 to 6 rows for better visibility
        self.qbank_table = QTableWidget(10, 3)
        self.qbank_table.setHorizontalHeaderLabels(["Bank Name", "File Path", "Questions"])
        self.qbank_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.qbank_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.qbank_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.qbank_table.verticalHeader().setVisible(False)
        self.qbank_table.setMinimumHeight(200)  # Increase minimum height for better visibility
        
        # Add spinboxes for question counts
        self.questions_spinboxes = []
        for i in range(10):
            spinbox = QSpinBox()
            spinbox.setRange(0, 1000)
            spinbox.setValue(0)
            self.qbank_table.setCellWidget(i, 2, spinbox)
            self.questions_spinboxes.append(spinbox)
        
        qbank_layout.addWidget(self.qbank_table)
        
        # Buttons for question banks
        qbank_btn_layout = QHBoxLayout()
        
        # Add spinner to select number of banks to load
        qbank_count_layout = QHBoxLayout()
        qbank_count_layout.addWidget(QLabel("Banks to load:"))
        self.qbank_count_spinner = QSpinBox()
        self.qbank_count_spinner.setRange(1, 10)
        self.qbank_count_spinner.setValue(1)
        qbank_count_layout.addWidget(self.qbank_count_spinner)
        qbank_btn_layout.addLayout(qbank_count_layout)
        
        load_btn = QPushButton("Load Question Bank")
        load_btn.clicked.connect(self.select_quiz_file)
        qbank_btn_layout.addWidget(load_btn)
        
        load_defaults_btn = QPushButton("Load Defaults")
        load_defaults_btn.clicked.connect(self.load_default_banks)
        qbank_btn_layout.addWidget(load_defaults_btn)
        
        save_defaults_btn = QPushButton("Save as Defaults")
        save_defaults_btn.clicked.connect(self.save_default_banks)
        qbank_btn_layout.addWidget(save_defaults_btn)
        
        clear_btn = QPushButton("Clear All")
        clear_btn.clicked.connect(self.clear_question_banks)
        qbank_btn_layout.addWidget(clear_btn)
        
        qbank_layout.addLayout(qbank_btn_layout)
        layout.addWidget(qbank_group)
        
        # Create Quizzes button
        quiz_btn_layout = QHBoxLayout()

        self.create_quiz_btn = QPushButton("Create Quizzes")
        self.create_quiz_btn.setStyleSheet("font-size: 14px; font-weight: bold; padding: 8px;")
        self.create_quiz_btn.clicked.connect(self.create_quiz_set_from_gui)
        quiz_btn_layout.addWidget(self.create_quiz_btn)

        layout.addLayout(quiz_btn_layout)

        # Add Quiz Number entry and Delete Quizzes button
        quiz_actions_layout = QHBoxLayout()

        # Quiz number entry
        self.quiz_number = QLineEdit()
        self.quiz_number.setPlaceholderText("Quiz # (optional)")
        self.quiz_number.setFixedWidth(100)
        self.quiz_number.setToolTip("Enter quiz number to delete specific quiz (leave empty for all)")
        self.quiz_number.setEnabled(False)  # Disable until needed

        # Delete Quizzes button
        self.delete_quiz_btn = QPushButton("Delete Quizzes")
        self.delete_quiz_btn.setStyleSheet("""
        QPushButton {
            background-color: #ff6b6b;
            color: white;
            font-weight: bold;
            padding: 8px;
            border: 1px solid #dc3545;
            border-radius: 4px;
        }
        QPushButton:hover {
            background-color: #dc3545;
        }
        QPushButton:disabled {
            background-color: #cccccc;
            border-color: #999999;
        }
    """)

        
        self.delete_quiz_btn.setEnabled(False)  # Disable until needed
        self.delete_quiz_btn.clicked.connect(self.delete_quizzes)

        # Add to layout
        quiz_actions_layout.addWidget(self.quiz_number)
        quiz_actions_layout.addWidget(self.delete_quiz_btn)
        layout.addLayout(quiz_actions_layout)

        # Load default question banks
        self.load_default_banks()        
        return tab
    
    def _reset_quiz_data(self):
        """Reset quiz data after confirmation."""
        reply = QMessageBox.question(
            self,
            'Reset Quiz Data',
            'This will delete all quiz attempts and quiz-question records, '
            'but will keep students, question banks and course info.\n\n'
            'Continue?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            reset_quiz_data(self.engine)
            QMessageBox.information(
                self, 'Reset Complete', 'Quiz data has been reset.'
            )
        except Exception as e:
            QMessageBox.warning(self, 'Reset Failed', f'Could not reset quiz data: {e}')

    def create_quiz_set_from_gui(self):
        """Create quizzes based on the current settings."""
        try:
            # Gather question banks from the table
            bank_paths = []
            questions_per_bank = {}
            total_bank_questions = 0

            for row in range(self.qbank_table.rowCount()):
                path_item = self.qbank_table.item(row, 1)
                if not path_item or not path_item.text().strip():
                    continue
                bank_path = path_item.text().strip()
                if not os.path.exists(bank_path):
                    print(f"[ERROR] Question bank not found: {bank_path}")
                    return
                bank_paths.append(bank_path)
                spinbox = self.questions_spinboxes[row]
                n = spinbox.value()
                questions_per_bank[bank_path] = n
                total_bank_questions += n

            if not bank_paths:
                print("[ERROR] Please load at least one question bank.")
                return

            if total_bank_questions == 0:
                # Fall back to distributing the total questions across all banks evenly
                total = self.total_questions_input.value()
                per_bank = max(1, total // len(bank_paths))
                for bank_path in bank_paths:
                    questions_per_bank[bank_path] = per_bank
                total_bank_questions = sum(questions_per_bank.values())

            # Gather student codes
            student_codes_text = self.student_codes_text.toPlainText().strip()
            if not student_codes_text:
                print("[ERROR] Please enter at least one student code.")
                return
            student_codes = [c.strip() for c in student_codes_text.split(',') if c.strip()]

            # Module and date
            module_number = self.quiz_module_input.value()
            quiz_date = self.quiz_date_edit.date().toString("yyyy-MM-dd")

            # Use the course folder from Course Info
            course_folder = self.course_info_panel.course_folder_value.strip()
            if not course_folder:
                QMessageBox.warning(
                    self,
                    'Course Folder Missing',
                    'Please set the Course Folder in the Course Info panel.'
                )
                return
            if not os.path.isdir(course_folder):
                QMessageBox.warning(
                    self,
                    'Course Folder Not Found',
                    'The Course Folder does not exist: ' + course_folder
                )
                return

            attempts = self.num_quizzes_input.value()

            bank_questions = load_question_banks(bank_paths)

            if self.append_oneun_checkbox.isChecked():
                created = self._append_oneun_to_quiz(
                    module_number=module_number,
                    student_codes=student_codes,
                    total_mcq_questions=total_bank_questions,
                    bank_paths=bank_paths,
                    questions_per_bank=questions_per_bank,
                    quiz_date=quiz_date,
                    attempts=attempts,
                    course_folder=course_folder,
                    bank_questions=bank_questions,
                )
            else:
                created = create_quizzes_for_students(
                    engine=self.engine,
                    module_number=module_number,
                    student_codes=student_codes,
                    bank_paths=bank_paths,
                    questions_per_bank=questions_per_bank,
                    quiz_date=quiz_date,
                    attempts=attempts,
                    course_folder=course_folder,
                    bank_questions=bank_questions,
                )
                quiz_folder = os.path.join(course_folder, f'module{module_number}', 'quizzes')
                for code, quiz_ids in created.items():
                    for quiz_id in quiz_ids:
                        attempt = int(quiz_id.split('_')[-1])
                        quiz_pdf = os.path.join(quiz_folder, f'attempt{attempt}', 'questions', f'{artifact_id(quiz_id, "Q")}.pdf')
                        if os.path.exists(quiz_pdf):
                            stamp_page_numbers_to_pdf(quiz_pdf)

            if self.create_packet_checkbox.isChecked():
                try:
                    packet_title = 'MCQ + Quant Quiz Packet' if self.append_oneun_checkbox.isChecked() else 'MCQ Quiz Packet'
                    self._create_quiz_packets(
                        created, module_number, course_folder, title=packet_title
                    )
                except Exception as pkt_err:
                    print(f"[ERROR] Failed to create quiz packet: {pkt_err}")
                    import traceback
                    traceback.print_exc()

            total_quizzes = sum(len(v) for v in created.values())
            created_codes = ', '.join(sorted(created.keys()))

            QMessageBox.information(
                self,
                "Quizzes Created",
                f"Created {total_quizzes} quiz file(s) for {len(created)} student(s): {created_codes}\n"
                f"Stored in module {module_number}/quizzes/"
            )

        except Exception as e:
            print(f"[ERROR] Failed to create quizzes: {e}")
            import traceback
            traceback.print_exc()

    def _append_oneun_to_quiz(self, module_number: int, student_codes: List[str],
                              total_mcq_questions: int,
                              bank_paths: List[str],
                              questions_per_bank: Dict[str, int],
                              quiz_date: str,
                              attempts: Optional[int] = None,
                              course_folder: str = '',
                              bank_questions: Optional[Dict] = None):
        """Generate One Unknown ODTs appended to the MCQ PDFs.

        Selects a random starting template set and then proceeds through the
        requested number of attempts, one per template set, wrapping cyclically.
        """
        params = self._oneun_get_params()
        if params is None:
            raise ValueError("One Unknown settings are incomplete; cannot append.")

        (definition_sets, base_seed, metadata, plot_config,
         _module, _doc_type, _) = params

        if _doc_type != 'Quiz':
            raise ValueError(
                "One Unknown document type must be 'Quiz' when appending to an MCQ quiz."
            )

        if attempts is None:
            course_info = get_course_info(self.engine)
            attempts = course_info.get('max_attempts_per_module', 4)

        num_sets = len(definition_sets)
        start_set = random.randint(0, num_sets - 1) if num_sets > 1 else 0
        odt_template_paths = [
            os.path.basename(definition_sets[(start_set + a) % num_sets]['template_path'])
            for a in range(attempts)
        ]
        odt_variable_names_list = [
            sorted(definition_sets[(start_set + a) % num_sets]['definition'].variables.keys())
            if definition_sets[(start_set + a) % num_sets]['definition']
            and getattr(definition_sets[(start_set + a) % num_sets]['definition'], 'variables', None)
            else []
            for a in range(attempts)
        ]

        quiz_folder = os.path.join(course_folder, 'module' + str(module_number), 'quizzes')
        odt_quiz_folder = os.path.join(course_folder, 'module' + str(module_number), 'templates', 'ODT_quizzes')
        os.makedirs(odt_quiz_folder, exist_ok=True)

        # -------------------------------------------------------------------
        # 1. First student MCQ
        # -------------------------------------------------------------------
        first_code = student_codes[0]
        first_created = create_quizzes_for_students(
            engine=self.engine,
            module_number=module_number,
            student_codes=[first_code],
            bank_paths=bank_paths,
            questions_per_bank=questions_per_bank,
            quiz_date=quiz_date,
            attempts=attempts,
            course_folder=course_folder,

            has_odt=True,
            odt_template_paths=odt_template_paths,
            odt_variable_names_list=odt_variable_names_list,
            bank_questions=bank_questions,
        )
        if not first_created or first_code not in first_created or not first_created[first_code]:
            raise ValueError('Failed to generate first MCQ.')
        first_quiz_id = first_created[first_code][0]
        first_attempt = int(first_quiz_id.split('_')[-1])
        first_mcq_pdf = os.path.join(quiz_folder, f'attempt{first_attempt}', 'questions', artifact_id(first_quiz_id, 'Q') + '.pdf')
        if not os.path.exists(first_mcq_pdf):
            raise FileNotFoundError('First MCQ PDF not found: ' + first_mcq_pdf)
        mcq_pages = pdf_page_count(first_mcq_pdf)
        if mcq_pages == 0:
            raise ValueError('First MCQ PDF has zero pages.')

        # -------------------------------------------------------------------
        # 2. Trial ODT to determine ODT page count
        # -------------------------------------------------------------------
        odt_out_stem = os.path.join(odt_quiz_folder, 'oneunknown_quiz.odt')
        odt_gen = OneUnODTGenerator()

        student_names: Dict[str, str] = {}
        student_section_codes: Dict[str, str] = {}
        for code in student_codes:
            student = get_student_by_code(self.engine, code)
            if student is not None:
                student_names[code] = student.name or code
                if student.section_number is not None:
                    student_section_codes[code] = str(student.section_number)

        metadata.setdefault('course', self.course_value)
        metadata.setdefault('instructors', self.instructors_value)
        metadata.setdefault('quiz_date', quiz_date)

        trial_out = os.path.splitext(odt_out_stem)[0] + f'_A{first_attempt}.odt'
        trial_main, trial_ak, _ = odt_gen.generate_quiz(
            definition=definition_sets[start_set]['definition'],
            template_path=definition_sets[start_set]['template_path'],
            output_path=trial_out,
            student_codes=[first_code],
            quiz_metadata=metadata,
            answer_key_template_path=definition_sets[start_set]['answer_key_template_path'] or None,
            student_names=student_names,
            student_section_codes=student_section_codes,
            base_seed=base_seed,
            start_question=total_mcq_questions + 1,
            start_page=mcq_pages + 1,
            attempts=1,
            return_values=True,
        )
        if not trial_main or not trial_main[0]:
            raise ValueError('Trial ODT main not generated.')
        trial_pdf = self._convert_odt_to_pdf(trial_main[0], odt_quiz_folder, insert_blank_pages=True)
        odt_pages = pdf_page_count(trial_pdf)
        if odt_pages == 0:
            raise ValueError('Converted trial ODT has zero pages.')

        total_pages = odt_pages + mcq_pages
        metadata['total_pages'] = total_pages

        # -------------------------------------------------------------------
        # 3. Final ODTs for all students and attempts
        # -------------------------------------------------------------------
        final_odt_files: List[str] = []
        final_odt_values: Dict[str, List[Dict]] = {}
        student_odt_template_paths: Dict[str, List[str]] = {code: [] for code in student_codes}
        student_odt_variable_names: Dict[str, List[List[str]]] = {code: [] for code in student_codes}
        odt_pdf_map: Dict[Tuple[str, int], str] = {}
        odt_ak_pdf_map: Dict[Tuple[str, int], str] = {}

        # Precompute a shuffled set order for each student so templates vary
        # randomly within the session and pseudo-randomly across attempts.
        student_set_indices: Dict[str, List[int]] = {}
        for code in student_codes:
            indices = list(range(num_sets))
            random.shuffle(indices)
            student_set_indices[code] = [indices[a % num_sets] for a in range(attempts)]

        for a in range(attempts):
            actual_attempt = first_attempt + a
            attempt_out = os.path.splitext(odt_out_stem)[0] + f'_A{actual_attempt}.odt'
            for i, code in enumerate(student_codes):
                set_index = student_set_indices[code][a]
                set_info = definition_sets[set_index]
                attempt_base = (base_seed + i + a) if base_seed is not None else None
                attempt_main, attempt_ak, attempt_values = odt_gen.generate_quiz(
                    definition=set_info['definition'],
                    template_path=set_info['template_path'],
                    output_path=attempt_out,
                    student_codes=[code],
                    quiz_metadata=metadata,
                    answer_key_template_path=set_info['answer_key_template_path'] or None,
                    base_seed=attempt_base,
                    student_names=student_names,
                    student_section_codes=student_section_codes,
                    start_question=total_mcq_questions + 1,
                    start_page=mcq_pages + 1,
                    attempts=1,
                    return_values=True,
                )
                if attempt_main:
                    final_odt_files.extend(attempt_main)
                    pdf_path = self._convert_odt_to_pdf(attempt_main[0], odt_quiz_folder, insert_blank_pages=True)
                    odt_pdf_map[(code, a + 1)] = pdf_path
                if attempt_ak:
                    final_odt_files.extend(attempt_ak)
                    ak_pdf_path = self._convert_odt_to_pdf(attempt_ak[0], odt_quiz_folder, insert_blank_pages=False)
                    odt_ak_pdf_map[(code, a + 1)] = ak_pdf_path
                if code in attempt_values:
                    final_odt_values.setdefault(code, []).append(attempt_values[code][0])
                student_odt_template_paths[code].append(set_info['template_path'])
                var_names = []
                if set_info['definition'] and getattr(set_info['definition'], 'variables', None):
                    var_names = sorted(set_info['definition'].variables.keys())
                student_odt_variable_names[code].append(var_names)

        # -------------------------------------------------------------------
        # 4. Remaining MCQ PDFs for the other students
        # -------------------------------------------------------------------
        created = dict(first_created)
        if len(student_codes) > 1:
            rest_created = create_quizzes_for_students(
                engine=self.engine,
                module_number=module_number,
                student_codes=student_codes[1:],
                bank_paths=bank_paths,
                questions_per_bank=questions_per_bank,
                quiz_date=quiz_date,
                attempts=attempts,
                course_folder=course_folder,
    
                has_odt=True,
                odt_template_paths=odt_template_paths,
                odt_variable_names_list=odt_variable_names_list,
                bank_questions=bank_questions,
            )
            created.update(rest_created)

        # Update each generated Quiz row with the ODT values drawn for it.
        for code, quiz_ids in created.items():
            values_list = final_odt_values.get(code, [])
            template_paths_list = student_odt_template_paths.get(code, [])
            var_names_list = student_odt_variable_names.get(code, [])
            for attempt_index, quiz_id in enumerate(quiz_ids, start=1):
                idx = attempt_index - 1
                odt_value = values_list[idx] if idx < len(values_list) else None
                odt_template = template_paths_list[idx] if idx < len(template_paths_list) else None
                odt_var_names = var_names_list[idx] if idx < len(var_names_list) else None
                update_quiz_odt_info(self.engine, quiz_id, odt_template, odt_var_names, odt_value)

        # -------------------------------------------------------------------
        # 5. Append each ODT PDF to the corresponding MCQ PDF
        # -------------------------------------------------------------------
        for code, quiz_ids in created.items():
            for attempt_index, quiz_id in enumerate(quiz_ids, start=1):
                attempt = int(quiz_id.split('_')[-1])
                quiz_pdf = os.path.join(quiz_folder, f'attempt{attempt}', 'questions', artifact_id(quiz_id, 'Q') + '.pdf')
                if (code, attempt_index) in odt_pdf_map and os.path.exists(quiz_pdf):
                    self._append_pdf_to_pdf(quiz_pdf, odt_pdf_map[(code, attempt_index)], quiz_pdf)

                ak_pdf = os.path.join(quiz_folder, f'attempt{attempt}', 'answers', artifact_id(quiz_id, 'A') + '.pdf')
                if (code, attempt_index) in odt_ak_pdf_map and os.path.exists(ak_pdf):
                    self._append_pdf_to_pdf(ak_pdf, odt_ak_pdf_map[(code, attempt_index)], ak_pdf)

        # -------------------------------------------------------------------
        # 6. Stamp page numbers on the combined PDFs
        # -------------------------------------------------------------------
        for code, quiz_ids in created.items():
            for quiz_id in quiz_ids:
                attempt = int(quiz_id.split('_')[-1])
                quiz_pdf = os.path.join(quiz_folder, f'attempt{attempt}', 'questions', artifact_id(quiz_id, 'Q') + '.pdf')
                if os.path.exists(quiz_pdf):
                    stamp_page_numbers_to_pdf(quiz_pdf)
                ak_pdf = os.path.join(quiz_folder, f'attempt{attempt}', 'answers', artifact_id(quiz_id, 'A') + '.pdf')
                if os.path.exists(ak_pdf):
                    stamp_page_numbers_to_pdf(ak_pdf)

        # -------------------------------------------------------------------
        # 6. Summary log
        # -------------------------------------------------------------------
        odt_gen._write_summary_log(
            log_path=os.path.join(odt_quiz_folder, 'oneunknown_quiz_summary.txt'),
            definition_path=definition_sets[start_set]['def_path'],
            template_path=definition_sets[start_set]['template_path'],
            output_files=final_odt_files,
            student_seeds={
                (sc or 'generic'): (base_seed + i if base_seed is not None else None)
                for i, sc in enumerate(student_codes)
            },
            metadata=metadata,
            plot_config=plot_config,
            generated_at=datetime.now().isoformat(timespec='seconds')
        )

        QMessageBox.information(
            self,
            'One Unknown Appended',
            'Generated ' + str(len(final_odt_files)) + ' One Unknown ODT file(s) appended to the MCQ quiz. '
            'Quant questions start at Q' + str(total_mcq_questions + 1) + ', page ' + str(mcq_pages + 1) + '. '
            'Combined total pages: ' + str(total_pages) + '. MCQs: ' + quiz_folder + ' ODTs: ' + odt_quiz_folder
        )

        return created

    def _convert_odt_to_pdf(self, odt_path: str, output_dir: str,
                            insert_blank_pages: bool = False) -> str:
        '''Convert an ODT file to PDF using LibreOffice headless.'''
        office_bin = None
        for name in ('soffice', 'libreoffice'):
            office_bin = shutil.which(name)
            if office_bin:
                break
        if not office_bin:
            for path in (
                '/Applications/LibreOffice.app/Contents/MacOS/soffice',
                '/Applications/LibreOffice.app/Contents/MacOS/soffice.bin',
            ):
                if os.path.exists(path):
                    office_bin = path
                    break
        if not office_bin:
            raise FileNotFoundError(
                'No ODT-to-PDF converter found. '
                'Please install LibreOffice and ensure soffice is on PATH.'
            )

        base = os.path.splitext(os.path.basename(odt_path))[0]
        expected_pdf = os.path.join(output_dir, base + '.pdf')
        try:
            subprocess.run(
                [office_bin, '--headless', '--convert-to', 'pdf',
                 '--outdir', output_dir, odt_path],
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError('LibreOffice conversion failed for ' + odt_path + ': ' + str(e.stderr))

        if not os.path.exists(expected_pdf):
            candidates = [
                f for f in os.listdir(output_dir)
                if f.lower().endswith('.pdf') and f.startswith(base)
            ]
            if not candidates:
                raise FileNotFoundError('PDF output not found for ' + odt_path)
            expected_pdf = os.path.join(output_dir, sorted(candidates)[-1])

        if insert_blank_pages:
            from pypdf import PdfReader, PdfWriter
            reader = PdfReader(expected_pdf)
            writer = PdfWriter()
            for page in reader.pages:
                writer.add_page(page)
                writer.add_blank_page(
                    width=float(page.mediabox.width),
                    height=float(page.mediabox.height)
                )
            tmp_pdf = expected_pdf + '.blanks.tmp'
            with open(tmp_pdf, 'wb') as f:
                writer.write(f)
            os.replace(tmp_pdf, expected_pdf)

        return expected_pdf

    def _append_pdf_to_pdf(self, base_pdf_path: str, append_pdf_path: str, output_pdf_path: str):
        '''Append append_pdf_path to base_pdf_path, writing to output_pdf_path.'''
        from pypdf import PdfReader, PdfWriter
        writer = PdfWriter()
        tmp_path = output_pdf_path + '.tmp'
        for path in (base_pdf_path, append_pdf_path):
            reader = PdfReader(path)
            for page in reader.pages:
                writer.add_page(page)
        with open(tmp_path, 'wb') as f:
            writer.write(f)
        os.replace(tmp_path, output_pdf_path)

    def _student_sort_key(self, code: str):
        """Return a sort key (last name, full name) for a student code."""
        student = get_student_by_code(self.engine, code)
        if student is None or not student.name:
            return ('~', '')
        parts = student.name.strip().split()
        last = parts[-1].lower() if parts else ''
        return (last, student.name.lower())

    def _sort_codes_by_last_name(self, codes: List[str]) -> List[str]:
        """Return student codes sorted by last name."""
        return sorted(codes, key=self._student_sort_key)

    def _merge_pdfs(self, pdf_paths: List[str], output_pdf_path: str,
                    cover_pdf: Optional[str] = None):
        '''Merge multiple PDFs into a single PDF, optionally prepending a coversheet.'''
        from pypdf import PdfReader, PdfWriter
        writer = PdfWriter()
        paths = [cover_pdf] + pdf_paths if cover_pdf else pdf_paths
        for path in paths:
            reader = PdfReader(path)
            for page in reader.pages:
                writer.add_page(page)
        os.makedirs(os.path.dirname(output_pdf_path), exist_ok=True)
        tmp_path = output_pdf_path + '.tmp'
        with open(tmp_path, 'wb') as f:
            writer.write(f)
        os.replace(tmp_path, output_pdf_path)

    def _build_coversheet_pdf(self, title: str, student_codes: List[str],
                              output_path: str, date_label: str = 'Date quizzes taken:'):
        '''Generate a coversheet PDF with a date field and checkbox list of students.'''
        from fpdf import FPDF
        pdf = FPDF(orientation='P', unit='mm', format='Letter')  # matches physical printer paper
        pdf.set_auto_page_break(auto=True, margin=25)
        pdf.add_page()

        pdf.set_font('Helvetica', 'B', 16)
        pdf.cell(0, 10, title, ln=1, align='C')
        pdf.ln(5)

        pdf.set_font('Helvetica', '', 11)
        pdf.cell(0, 8, f'{date_label} ____________________', ln=1)
        pdf.ln(5)

        pdf.set_font('Helvetica', 'B', 12)
        pdf.cell(0, 8, 'Students (alphabetical):', ln=1)

        pdf.set_font('Helvetica', '', 11)
        line_h = 7
        sorted_codes = self._sort_codes_by_last_name(student_codes)
        for code in sorted_codes:
            student = get_student_by_code(self.engine, code)
            name = student.name if student and student.name else code
            if pdf.get_y() + line_h + 5 > 297 - 25:
                pdf.add_page()
            start_x = pdf.get_x()
            y = pdf.get_y()
            box_size = 4
            pdf.rect(start_x, y + 1.5, box_size, box_size)
            pdf.set_xy(start_x + box_size + 3, y)
            pdf.cell(0, line_h, name, ln=1)

        pdf.ln(6)
        pdf.cell(0, 6, 'Scanned by: _____________________________________', ln=1)

        # Ensure the coversheet is an even number of sides so it forms one or
        # more complete physical sheets separate from the following quizzes.
        if pdf.page_no() % 2 != 0:
            pdf.add_page()

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        pdf.output(output_path)

    def _create_quiz_packets(self, created: Dict[str, List[str]], module_number: int,
                             course_folder: str, title: str = 'Quiz Packet'):
        '''Merge MCQ question PDFs for each attempt into a single group packet.'''
        quiz_folder = os.path.join(course_folder, f'module{module_number}', 'quizzes')
        attempts: Dict[int, List[Tuple[str, str]]] = {}
        for code, quiz_ids in created.items():
            for quiz_id in quiz_ids:
                try:
                    attempt = int(quiz_id.split('_')[-1])
                except ValueError:
                    continue
                attempts.setdefault(attempt, []).append((code, quiz_id))

        for attempt, code_quiz_ids in attempts.items():
            sorted_items = sorted(code_quiz_ids, key=lambda item: self._student_sort_key(item[0]))
            question_pdf_paths = []
            answer_pdf_paths = []
            for code, quiz_id in sorted_items:
                question_pdf = os.path.join(
                    quiz_folder, f'attempt{attempt}', 'questions',
                    f"{artifact_id(quiz_id, 'Q')}.pdf"
                )
                answer_pdf = os.path.join(
                    quiz_folder, f'attempt{attempt}', 'answers',
                    f"{artifact_id(quiz_id, 'A')}.pdf"
                )
                if os.path.exists(question_pdf):
                    question_pdf_paths.append(question_pdf)
                if os.path.exists(answer_pdf):
                    answer_pdf_paths.append(answer_pdf)
            if not question_pdf_paths:
                continue
            group_name = artifact_id(format_quiz_id('Group', module_number, attempt), 'Q')
            group_path = os.path.join(quiz_folder, f'attempt{attempt}', 'questions', f'{group_name}.pdf')
            cover_path = os.path.join(quiz_folder, f'attempt{attempt}', 'questions', f'{group_name}_coversheet.pdf')
            self._build_coversheet_pdf(
                title,
                [code for code, _ in sorted_items],
                cover_path,
            )
            self._merge_pdfs(question_pdf_paths, group_path, cover_pdf=cover_path)

            # Move component PDFs into subdirectories
            individual_question_dir = os.path.join(quiz_folder, f'attempt{attempt}', 'questions', 'Individual')
            individual_answer_dir = os.path.join(quiz_folder, f'attempt{attempt}', 'answers', 'Individual_answer')
            os.makedirs(individual_question_dir, exist_ok=True)
            os.makedirs(individual_answer_dir, exist_ok=True)
            for src in question_pdf_paths:
                shutil.move(src, os.path.join(individual_question_dir, os.path.basename(src)))
            for src in answer_pdf_paths:
                shutil.move(src, os.path.join(individual_answer_dir, os.path.basename(src)))

            # Remove the temporary coversheet PDF
            try:
                os.remove(cover_path)
            except Exception:
                pass

            print(f'[INFO] Created quiz packet: {group_path}')

    def _create_quant_packets(self, main_files: List[str], answer_key_files: List[str],
                              student_codes: List[str], module: int, output_dir: str,
                              output_ids: Optional[Dict[str, str]] = None,
                              answer_key_output_ids: Optional[Dict[str, str]] = None,
                              title: str = 'Quant Packet',
                              doc_type: str = 'Quiz'):
        '''Convert generated Quant ODTs to PDFs and merge into group packets.'''
        output_ids = output_ids or {}
        answer_key_output_ids = answer_key_output_ids or {}

        def _file_for_id(files: List[str], output_id: str):
            target = f'{output_id}.odt'
            for f in files:
                if os.path.basename(f) == target:
                    return f
            return None

        sorted_codes = self._sort_codes_by_last_name(student_codes)

        # Subdirectories for component ODT files
        main_odt_dir_name = 'worksheet_ODT' if doc_type == 'Worksheet' else 'quiz_ODT'
        ak_odt_dir_name = 'worksheet_answers_ODT' if doc_type == 'Worksheet' else 'quiz_answers_odt'
        main_odt_dir = os.path.join(output_dir, main_odt_dir_name)
        ak_odt_dir = os.path.join(output_dir, ak_odt_dir_name)
        os.makedirs(main_odt_dir, exist_ok=True)
        os.makedirs(ak_odt_dir, exist_ok=True)

        main_pdfs = []
        main_odt_paths = []
        for code in sorted_codes:
            oid = output_ids.get(code, code)
            odt = _file_for_id(main_files, oid)
            if odt and os.path.exists(odt):
                main_pdfs.append(self._convert_odt_to_pdf(odt, output_dir, insert_blank_pages=(doc_type == 'Quiz')))
                main_odt_paths.append(odt)
        if main_pdfs:
            group_main_id = f"{format_quiz_id('Group', module, 1)}WS"
            main_packet_path = os.path.join(output_dir, f'{group_main_id}.pdf')
            main_cover_path = os.path.join(output_dir, f'{group_main_id}_coversheet.pdf')
            self._build_coversheet_pdf(title, sorted_codes, main_cover_path)
            self._merge_pdfs(main_pdfs, main_packet_path, cover_pdf=main_cover_path)

            # Move main ODTs into subdirectory
            for odt in main_odt_paths:
                shutil.move(odt, os.path.join(main_odt_dir, os.path.basename(odt)))

            # Delete component PDFs and coversheet
            for pdf in main_pdfs + [main_cover_path]:
                try:
                    os.remove(pdf)
                except Exception:
                    pass

            print(f'[INFO] Created quant packet: {main_packet_path}')

        ak_pdfs = []
        ak_odt_paths = []
        if answer_key_files:
            for code in sorted_codes:
                oid = answer_key_output_ids.get(code, code)
                odt = _file_for_id(answer_key_files, oid)
                if odt and os.path.exists(odt):
                    ak_pdfs.append(self._convert_odt_to_pdf(odt, output_dir, insert_blank_pages=False))
                    ak_odt_paths.append(odt)
            if ak_pdfs:
                group_ak_id = f"{format_quiz_id('Group', module, 1)}WA"
                ak_packet_path = os.path.join(output_dir, f'{group_ak_id}.pdf')
                self._merge_pdfs(ak_pdfs, ak_packet_path)

                # Move answer-key ODTs into subdirectory
                for odt in ak_odt_paths:
                    shutil.move(odt, os.path.join(ak_odt_dir, os.path.basename(odt)))

                # Delete component answer-key PDFs
                for pdf in ak_pdfs:
                    try:
                        os.remove(pdf)
                    except Exception:
                        pass

                print(f'[INFO] Created quant answer-key packet: {ak_packet_path}')

    def _populate_section_filter_combo(self):
        """Populate the section filter combo from the database."""
        self.section_filter_combo.clear()
        for sec in get_all_sections(self.engine):
            label = f'Section {sec["section_number"]}'
            if sec.get('ta_instructor'):
                label += f' ({sec["ta_instructor"]})'
            self.section_filter_combo.addItem(label, sec['section_number'])

    def populate_student_combo(self):
        """Populate the student combobox with names and codes from the database."""
        try:
            # Clear existing items
            self.student_combo.clear()
        
            # Get all students from the database
            students = get_all_students(self.engine)
            
            # Sort students by student_code
            students.sort(key=lambda student: student.student_code)
        
            # Add each student to the combobox with format "Name (Code)"
            for student in students:
                display_text = f"{student.name} ({student.student_code})"
                self.student_combo.addItem(display_text, student.student_code)
            
        except Exception as e:
            if 'no such table' not in str(e).lower():
                print(f"Error populating student combo: {str(e)}")
    
    def add_selected_student(self):
        """Add the selected student code from the combobox to the student codes text widget."""
        try:
            # Get the selected student code from the combobox
            selected_index = self.student_combo.currentIndex()
            if selected_index < 0:
                return
            
            selected_code = self.student_combo.itemData(selected_index)
            if not selected_code:
                return
            
            # Get current text and parse existing codes
            current_text = self.student_codes_text.toPlainText().strip()
            codes = [code.strip() for code in current_text.split(',') if code.strip()]
        
            # Add the new code if it's not already in the list
            if selected_code not in codes:
                codes.append(selected_code)
            
            # Update the text widget with the sorted, comma-separated list
            self.student_codes_text.setPlainText(", ".join(sorted(codes)))
            
        except Exception as e:
            print(f"Error adding selected student: {str(e)}")
    
    def add_all_students(self):
        """Add all student codes from the database to the student codes text widget."""
        try:
            # Get all students from the database
            students = get_all_students(self.engine)
        
            # Extract all student codes
            codes = [student.student_code for student in students]
        
            # Update the text widget with the sorted, comma-separated list
            self.student_codes_text.setPlainText(", ".join(sorted(codes)))
            
        except Exception as e:
            print(f"Error adding all students: {str(e)}")
    
    def add_section_students(self):
        """Add student codes for the selected section to the student codes text widget."""
        try:
            sec_num = self.section_filter_combo.currentData()
            if sec_num is None:
                print("No section selected")
                return
            students = get_students_for_section(self.engine, sec_num)
            codes = [s.student_code for s in students]
            self.student_codes_text.setPlainText(", ".join(sorted(codes)))
        except Exception as e:
            print(f"Error adding section students: {str(e)}")
    
    def load_default_banks(self):
        """Load the module-specific default question banks into the table."""
        import os
        defaults_file = os.path.expanduser('~/textProcessing/MCQ26/default_question_banks.txt')
        
        # Get the current module number
        module_number = self.quiz_module_input.value()
        
        # Clear table first
        for row in range(self.qbank_table.rowCount()):
            self.qbank_table.setItem(row, 0, None)
            self.qbank_table.setItem(row, 1, None)
            self.questions_spinboxes[row].setValue(0)
        
        # Initialize module_default_banks if it doesn't exist
        if 'module_default_banks' not in self.defaults:
            self.defaults['module_default_banks'] = {}
        
        # Initialize banks list
        banks = []
        
        # First check if we have module-specific defaults in memory
        # Only use cached banks if we've already loaded from file at least once
        if hasattr(self, 'defaults_loaded_from_file') and self.defaults_loaded_from_file and module_number in self.defaults.get('module_default_banks', {}):
            banks = self.defaults['module_default_banks'][module_number]
            print(f"Using {len(banks)} cached banks for module {module_number}")
        
        # If no banks found in memory, check the file
        if not banks and os.path.exists(defaults_file):
            # Parse the file to find module-specific defaults
            module_banks = []
            old_format_banks = []
            
            with open(defaults_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        try:
                            # Check if line has module prefix (new format)
                            if line.startswith('M'):
                                parts = line.split(':', 1)
                                if len(parts) == 2:
                                    mod = int(parts[0][1:])
                                    path_parts = parts[1].split('|')
                                    if len(path_parts) == 2:
                                        bank = {
                                            'path': path_parts[0],
                                            'questions': int(path_parts[1])
                                        }
                                        
                                        # Store in module defaults dictionary
                                        if mod not in self.defaults['module_default_banks']:
                                            self.defaults['module_default_banks'][mod] = []
                                        
                                        # Don't append if this bank is already in the list
                                        if not any(existing['path'] == bank['path'] for existing in self.defaults['module_default_banks'][mod]):
                                            self.defaults['module_default_banks'][mod].append(bank)
                                        
                                        # If this is the current module, add to banks list
                                        if mod == module_number:
                                            # Don't add duplicate banks to module_banks
                                            if not any(existing['path'] == bank['path'] for existing in module_banks):
                                                module_banks.append(bank)
                            else:
                                # Old format without module prefix
                                path_parts = line.split('|')
                                if len(path_parts) == 2:
                                    old_format_banks.append({
                                        'path': path_parts[0],
                                        'questions': int(path_parts[1])
                                    })
                        except Exception as e:
                            print(f"Error parsing line '{line}': {e}")
                            continue
            
            # Use module-specific banks if available, otherwise fall back to old format
            if module_banks:
                banks = module_banks
                print(f"Loaded {len(banks)} banks for module {module_number} from file")
                # Mark that we've loaded from file
                self.defaults_loaded_from_file = True
            elif old_format_banks and not module_number in self.defaults['module_default_banks']:
                # Only use old format if we don't have any module-specific banks
                banks = old_format_banks
                print("Using old format banks (no module specified)")
                # Mark that we've loaded from file
                self.defaults_loaded_from_file = True
        
        # If still no banks found, fall back to old default_banks
        if not banks:
            banks = self.defaults.get('default_banks', [])
            print(f"No module-specific banks found for module {module_number}, using {len(banks)} default banks")
        
        # Clear question banks dictionary
        self.question_banks = {}
        
        # Populate the table with the banks
        for i, bank in enumerate(banks):
            if i >= self.qbank_table.rowCount():
                break
            self.qbank_table.setItem(i, 0, QTableWidgetItem(os.path.basename(bank['path'])))
            self.qbank_table.setItem(i, 1, QTableWidgetItem(bank['path']))
            self.questions_spinboxes[i].setValue(bank['questions'])
            self.question_banks[bank['path']] = bank['questions']
        
        # Force update of the table
        self.qbank_table.update()
        
        self.loaded_default_banks = True
        self.statusBar().showMessage(f"Default question banks loaded for module {module_number}", 3000)
    
    def select_quiz_file(self):
        """Load question bank files into the quiz creator table based on the count spinner."""
        # Import re for pattern matching
        import re
        from PyQt6.QtWidgets import QMessageBox, QTableWidgetItem, QFileDialog
        import os
    
        # Define the pattern for integrated bank files: M#_TOPIC#_INT.txt
        # M followed by 1-2 digits, underscore, topic letters, bank number, _INT.txt
        pattern = r'^M\d{1,2}_[A-Za-z]+\d+_INT\.txt$'

        # Help text for error messages
        error_msg = (
            "Selected file must be an integrated question bank matching "
            "M#_TOPIC#_INT.txt, for example:\n"
            "- M4_CNS0_INT.txt\n"
            "- M4_WHO0_INT.txt\n"
            "These are the files produced by integrate_Qbanks.py."
        )

        # Get number of banks to load from spinner
        num_banks = self.qbank_count_spinner.value()
        
        # Find first empty row
        start_row = 0
        while start_row < self.qbank_table.rowCount():
            item = self.qbank_table.item(start_row, 0)
            if not item or not item.text().strip():
                break
            start_row += 1        
        if start_row + num_banks > self.qbank_table.rowCount():
            print(f"[ERROR] Not enough empty rows for {num_banks} banks. Clear some rows first.")
            return
            
        # Load the specified number of banks
        last_dir = str(self.defaults.get('basePath', os.path.expanduser('~')))
        for i in range(num_banks):
            row = start_row + i
            while True:
                file_name, _ = QFileDialog.getOpenFileName(
                    self,
                    f"Load Question Bank {i+1} of {num_banks}",
                    last_dir,
                    "Text Files (*.txt)"
                )
                if not file_name:
                    break  # User cancelled for this bank
                
                base = os.path.basename(file_name)
                if re.match(pattern, base):
                    # Create new table items
                    name_item = QTableWidgetItem(base)
                    path_item = QTableWidgetItem(file_name)
                    
                    # Set the items in the table
                    self.qbank_table.setItem(row, 0, name_item)
                    self.qbank_table.setItem(row, 1, path_item)
                    
                    # Set default question count to 0
                    self.questions_spinboxes[row].setValue(0)
                    self.question_banks[file_name] = 0
                    
                    # Update last directory for next file
                    last_dir = os.path.dirname(file_name)
                    
                    # Force update of the table
                    self.qbank_table.update()
                    
                    # Show status message
                    self.statusBar().showMessage(f"Added question bank: {base}", 3000)
                    break  # Success, move to next bank
                else:
                    print(f"[ERROR] Invalid file: {os.path.basename(file_name)}. {error_msg}")
                
    def save_default_banks(self):
        """Save the current question banks as defaults for the current module."""
        import os
        from datetime import datetime
        from PyQt6.QtWidgets import QMessageBox, QTableWidgetItem
        
        try:
            # Create a new list to store the default banks
            default_banks = []
            
            # Get the current module number
            module_number = self.quiz_module_input.value()
            
            # Get all rows from the table
            for i in range(self.qbank_table.rowCount()):
                path_item = self.qbank_table.item(i, 1)
                if path_item and path_item.text().strip():
                    path = path_item.text().strip()
                    questions = self.questions_spinboxes[i].value()
                    default_banks.append({
                        'path': path,
                        'questions': questions
                    })
            
            # Initialize module_default_banks if it doesn't exist
            if 'module_default_banks' not in self.defaults:
                self.defaults['module_default_banks'] = {}
            
            # Save the default banks for this module
            self.defaults['module_default_banks'][module_number] = default_banks
            
            # Save to file
            defaults_file = os.path.expanduser('~/textProcessing/MCQ26/default_question_banks.txt')
            
            # Read existing content to preserve other modules' defaults
            existing_content = {}
            old_format_lines = []
            
            if os.path.exists(defaults_file):
                with open(defaults_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#'):
                            if line.startswith('M'):
                                parts = line.split(':', 1)
                                if len(parts) == 2:
                                    mod = int(parts[0][1:])
                                    if mod != module_number:  # Skip current module as we'll rewrite it
                                        if mod not in existing_content:
                                            existing_content[mod] = []
                                        existing_content[mod].append(parts[1])
                            else:
                                # Preserve old format lines
                                old_format_lines.append(line)
            
            # Write the file with a header
            with open(defaults_file, 'w', encoding='utf-8') as f:
                f.write(f"# Default Question Banks - Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"# Format: M<module_number>:<path>|<questions>\n\n")
                
                # Write current module's defaults first
                for bank in default_banks:
                    f.write(f"M{module_number}:{bank['path']}|{bank['questions']}\n")
                
                # Write other modules' defaults
                for mod, banks in existing_content.items():
                    for bank_str in banks:
                        f.write(f"M{mod}:{bank_str}\n")
                
                # Write old format lines at the end for backward compatibility
                if old_format_lines:
                    f.write("\n# Old format entries (no module number)\n")
                    for line in old_format_lines:
                        f.write(f"{line}\n")
            
            self.statusBar().showMessage(f"Default question banks saved for module {module_number}", 3000)
            
        except Exception as e:
            print(f"[ERROR] Failed to save default banks: {e}")
            import traceback
            traceback.print_exc()
            
    def clear_question_banks(self):
        """Clear all question banks from the table."""
        # Clear the question banks dictionary
        self.question_banks = {}
        self.loaded_default_banks = False
        
        # Clear the table
        self.qbank_table.setRowCount(10)  # Reset to 10 rows
        self.questions_spinboxes = []  # Clear existing spinboxes
        
        # Initialize empty rows
        for i in range(10):
            self.qbank_table.setItem(i, 0, QTableWidgetItem(""))
            self.qbank_table.setItem(i, 1, QTableWidgetItem(""))
            spinbox = QSpinBox()
            spinbox.setRange(0, 1000)
            spinbox.setValue(0)
            self.qbank_table.setCellWidget(i, 2, spinbox)
            self.questions_spinboxes.append(spinbox)
        
        # Update status
        self.statusBar().showMessage("Question banks cleared", 3000)

    def select_output_directory(self):
        """Open a file dialog to select the output directory for quiz sets."""
        current_dir = self.output_dir_label.text() if hasattr(self, 'output_dir_label') else os.path.expanduser('~')
        
        # If the current directory doesn't exist, use the home directory
        if not os.path.exists(current_dir):
            current_dir = os.path.expanduser('~')
            
        # Open directory selection dialog
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Output Directory",
            current_dir,
            QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontUseNativeDialog
        )
        
        if directory:
            # Update the output directory label
            self.output_dir_label.setText(directory)
            
            # Store the directory in defaults
            self.defaults['outputDir'] = directory
            self.save_defaults()


    def update_output_directory(self):
        """Update the output directory display based on current settings."""
        try:
            base_path = self.defaults.get('basePath', os.path.expanduser('~'))
            course = self.llm_course.text().strip()
            module_number = self.llm_module_num.value()
            topic = self.llm_topic_code.text().strip()
            
            if not course or not topic:
                self.output_dir_label.setText("Please enter course and topic code")
                return None
                
            # Create the output directory path
            output_dir = os.path.join(
                base_path,
                course.replace(' ', ''),  # Remove spaces from course name
                f'module{module_number}',  # No leading zeros in module number
                topic,
                'QBanks'
            )
            
            # Update the display
            self.output_dir_label.setText(output_dir)
            self._full_basepath = output_dir
            return output_dir
            
        except Exception as e:
            self.output_dir_label.setText(f"Error: {str(e)}")
            return None
    
    def module_changed(self, value):
        """Handle module number change and load the appropriate default banks."""
        try:
            # Load the default banks for the new module
            self.load_default_banks()
            self.statusBar().showMessage(f"Loaded defaults for module {value}", 3000)
        except Exception as e:
            print(f"Error loading defaults for module {value}: {e}")
    
    def delete_quizzes(self):
        """Delete quizzes for selected students and module.

        Note: The actual quiz deletion backend has not been ported to MCQ26 yet.
        """
        QMessageBox.information(
            self,
            "Not Implemented",
            "Quiz deletion backend has not yet been ported to MCQ26."
        )

    def _extract_module_number(self, filepath):
        """Extract module number from a file path.

        Looks for an M#_ prefix in the filename first, then for a moduleN
        directory component in the full path.

        Args:
            filepath: Path to a file (e.g. question bank or OneUn definition)

        Returns:
            int: Module number or None if not found
        """
        import re
        filename = os.path.basename(filepath)
        # Match M followed by 1-2 digits at start of filename
        match = re.search(r'^M(\d{1,2})_', filename)
        if match:
            try:
                return int(match.group(1))
            except (ValueError, IndexError):
                pass

        # Fallback: look for .../moduleN/... in the path
        dir_match = re.search(r'[/\\]module(\d{1,2})[/\\]', filepath, re.IGNORECASE)
        if dir_match:
            try:
                return int(dir_match.group(1))
            except (ValueError, IndexError):
                pass

        return None

    def toggle_quiz_buttons(self, enabled):
        """Enable/disable quiz-related buttons based on selection."""
        has_selection = bool(self.student_codes_text.toPlainText().strip())
        self.create_quiz_btn.setEnabled(has_selection and enabled)
        self.delete_quiz_btn.setEnabled(has_selection and enabled)
        self.quiz_number.setEnabled(has_selection and enabled)
    
    # ------------------------------------------------------------------
    # Quant ODT (OneUn) Tab
    # ------------------------------------------------------------------

    def create_oneun_tab(self):
        """Create the quantitative ODT generator tab."""
        tab = QWidget()
        outer = QVBoxLayout()
        tab.setLayout(outer)

        # Use a scroll area so the panel doesn't get cramped
        from PyQt6.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout()
        content.setLayout(layout)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        # Storage for matched template sets
        self.oneun_definition_sets = []

        # --- Generation Parameters Group ---
        params_group = QGroupBox("Generation Parameters")
        params_layout = QFormLayout()
        params_group.setLayout(params_layout)

        doctype_layout = QHBoxLayout()
        self.oneun_type_quiz = QRadioButton("Quiz")
        self.oneun_type_worksheet = QRadioButton("Worksheet")
        self.oneun_type_quiz.setChecked(True)
        self.oneun_type_quiz.toggled.connect(self._oneun_update_generate_btn)
        self.oneun_type_worksheet.toggled.connect(self._oneun_update_generate_btn)
        doctype_layout.addWidget(self.oneun_type_quiz)
        doctype_layout.addWidget(self.oneun_type_worksheet)
        doctype_layout.addStretch()
        params_layout.addRow("Document type:", doctype_layout)

        # Module selection
        module_layout = QHBoxLayout()
        self.oneun_module_num = QComboBox()
        self.oneun_module_num.addItem("None selected")
        for i in range(1, NUM_MODULES + 1):
            self.oneun_module_num.addItem(str(i))
        self.oneun_module_num.currentIndexChanged.connect(
            lambda index: self._oneun_update_input_folder())
        module_layout.addWidget(self.oneun_module_num)
        module_layout.addStretch()
        params_layout.addRow("Module:", module_layout)

        seed_layout = QHBoxLayout()
        self.oneun_use_seed = QCheckBox("Use base seed:")
        self.oneun_seed = QSpinBox()
        self.oneun_seed.setRange(0, 999999)
        self.oneun_seed.setValue(42)
        self.oneun_seed.setEnabled(False)
        self.oneun_use_seed.toggled.connect(self.oneun_seed.setEnabled)
        seed_layout.addWidget(self.oneun_use_seed)
        seed_layout.addWidget(self.oneun_seed)
        seed_layout.addStretch()
        params_layout.addRow("Reproducibility:", seed_layout)

        # Group packet option
        self.oneun_packet_checkbox = QCheckBox("Create group packet PDF")
        self.oneun_packet_checkbox.setChecked(False)
        self.oneun_packet_checkbox.setToolTip(
            "Convert generated ODTs to PDFs and merge into group packets, "
            "sorted by student last name. Answer keys are produced as a separate packet."
        )
        params_layout.addRow("", self.oneun_packet_checkbox)

        layout.addWidget(params_group)

        # --- Plot Controls Group ---
        plot_group = QGroupBox("Plot / Graph")
        plot_layout = QFormLayout()
        plot_group.setLayout(plot_layout)

        # Include graph checkbox (master toggle)
        self.oneun_include_graph = QCheckBox("Include graph in quiz")
        self.oneun_include_graph.setChecked(False)
        self.oneun_include_graph.setEnabled(False)
        self.oneun_include_graph.setToolTip("Graphs require an equation source and are unavailable with variables/constants-only definitions.")
        self.oneun_include_graph.toggled.connect(self._oneun_toggle_plot_controls)
        plot_layout.addRow("", self.oneun_include_graph)

        # Equation number
        self.oneun_eq_spinbox = QSpinBox()
        self.oneun_eq_spinbox.setRange(1, 20)
        self.oneun_eq_spinbox.setValue(1)
        self.oneun_eq_spinbox.setToolTip("Equation number to use for the plot (1-based)")
        self.oneun_eq_spinbox.valueChanged.connect(self._oneun_eq_changed)
        plot_layout.addRow("Equation #:", self.oneun_eq_spinbox)

        # X variable combobox
        self.oneun_x_var_combo = QComboBox()
        self.oneun_x_var_combo.setToolTip("Variable to plot on X axis")
        plot_layout.addRow("X variable:", self.oneun_x_var_combo)

        # Y variable combobox
        self.oneun_y_var_combo = QComboBox()
        self.oneun_y_var_combo.setToolTip("Variable to plot on Y axis")
        plot_layout.addRow("Y variable:", self.oneun_y_var_combo)

        # Gridlines and log-scale options
        axes_layout = QHBoxLayout()
        self.oneun_gridlines = QCheckBox("Gridlines")
        self.oneun_gridlines.setChecked(True)
        self.oneun_log_x = QCheckBox("Log X")
        self.oneun_log_y = QCheckBox("Log Y")
        axes_layout.addWidget(self.oneun_gridlines)
        axes_layout.addWidget(self.oneun_log_x)
        axes_layout.addWidget(self.oneun_log_y)
        axes_layout.addStretch()
        plot_layout.addRow("Axes options:", axes_layout)

        layout.addWidget(plot_group)
        self._oneun_toggle_plot_controls(False)  # disabled initially

        # Student Codes are managed in the shared Student Codes tab
        oneun_sc_note = QLabel("Student codes are set in the \u2018Student Codes\u2019 tab.")
        oneun_sc_note.setStyleSheet("color: #666; font-style: italic;")
        layout.addWidget(oneun_sc_note)

        # --- Input Files Group ---
        files_group = QGroupBox("Input Files")
        files_layout = QFormLayout()
        files_group.setLayout(files_layout)

        # Auto-set, read-only input folder
        self.oneun_input_folder = QLineEdit()
        self.oneun_input_folder.setPlaceholderText("Select a module and document type")
        self.oneun_input_folder.setReadOnly(True)
        self.oneun_input_folder.setMinimumWidth(380)
        files_layout.addRow("Input Folder:", self.oneun_input_folder)

        # Matched definition set names
        self.oneun_def_path = QLineEdit()
        self.oneun_def_path.setReadOnly(True)
        self.oneun_def_path.setPlaceholderText("No matching template sets found")
        self.oneun_def_path.setMinimumWidth(380)
        self.oneun_def_path.textChanged.connect(self._oneun_def_loaded)
        files_layout.addRow("Definition Files:", self.oneun_def_path)

        layout.addWidget(files_group)

        # --- Generate button — lower right ---
        layout.addStretch()
        gen_btn_row = QHBoxLayout()
        gen_btn_row.addStretch()
        self.oneun_generate_btn = QPushButton("Generate Quiz")  # label updated dynamically
        self.oneun_generate_btn.setStyleSheet(
            "font-size: 12px; font-weight: bold; padding: 6px 16px;"
            "background-color: #4CAF50; color: white;")
        self.oneun_generate_btn.clicked.connect(self._oneun_generate)
        gen_btn_row.addWidget(self.oneun_generate_btn)
        layout.addLayout(gen_btn_row)

        self._oneun_update_input_folder()
        return tab

    def _oneun_update_generate_btn(self):
        """Update the generate button label to match the selected document type."""
        doc_type = 'Worksheet' if self.oneun_type_worksheet.isChecked() else 'Quiz'
        self.oneun_generate_btn.setText(f"Generate {doc_type}")
        self._oneun_update_input_folder()

    def _oneun_update_input_folder(self, folder=None):
        """Set the standardized input folder and reload template sets."""
        if folder is None:
            module_text = self.oneun_module_num.currentText()
            if module_text == "None selected":
                self.oneun_input_folder.setText("Select a module to see input folder")
                return
            try:
                module = int(module_text)
            except ValueError:
                self.oneun_input_folder.setText("Invalid module selection")
                return
            subdir = 'worksheet_templates' if self.oneun_type_worksheet.isChecked() else 'quiz_templates'
            try:
                course_info = get_course_info(self.engine)
                course_folder = (course_info.get('course_folder') or '').strip()
            except Exception:
                course_folder = ''
            if not course_folder:
                self.oneun_input_folder.setText("Configure Course Folder in Course Info first")
                return
            folder = os.path.join(os.path.expanduser(course_folder),
                                  f"module{module}", subdir)
            self.oneun_input_folder.setText(folder)
        self._oneun_resolve_input_folder(folder)

    def _oneun_toggle_plot_controls(self, enabled: bool):
        """Enable/disable plot control widgets based on the include-graph checkbox."""
        for w in (self.oneun_eq_spinbox, self.oneun_x_var_combo,
                  self.oneun_y_var_combo, self.oneun_gridlines,
                  self.oneun_log_x, self.oneun_log_y):
            w.setEnabled(enabled)

    def _oneun_first_def_path(self) -> str:
        """Return the .txt path of the first matched template set, if any."""
        sets = getattr(self, 'oneun_definition_sets', None)
        if sets:
            return sets[0].get('def_path', '')
        return ''

    def _oneun_def_loaded(self):
        """Called when the definition file list changes; repopulate variable combos."""
        path = self._oneun_first_def_path()
        if path and os.path.exists(path):
            self._oneun_eq_changed(self.oneun_eq_spinbox.value())

    def _oneun_eq_changed(self, eq_num: int):
        """Reparse the variables for equation eq_num and populate X/Y combos."""
        path = self._oneun_first_def_path()
        if not path or not os.path.exists(path):
            return
        try:
            definition = load_problem_definition(path)
        except Exception:
            return

        # Build display list: "short_name ($var)" sorted
        var_items = []
        for vname in definition.variables:
            vdef = definition.variables.get(vname)
            short = (vdef.var_name_short_list[0] if vdef and vdef.var_name_short_list
                     else vname.lstrip('$'))
            var_items.append((short, vname))
        var_items.sort(key=lambda t: t[0])

        prev_x = self.oneun_x_var_combo.currentData()
        prev_y = self.oneun_y_var_combo.currentData()

        self.oneun_x_var_combo.blockSignals(True)
        self.oneun_y_var_combo.blockSignals(True)
        self.oneun_x_var_combo.clear()
        self.oneun_y_var_combo.clear()
        for short, vname in var_items:
            self.oneun_x_var_combo.addItem(f"{short}  ({vname})", vname)
            self.oneun_y_var_combo.addItem(f"{short}  ({vname})", vname)
        self.oneun_x_var_combo.blockSignals(False)
        self.oneun_y_var_combo.blockSignals(False)

        # Restore previous selection if still valid
        for combo, prev in ((self.oneun_x_var_combo, prev_x),
                             (self.oneun_y_var_combo, prev_y)):
            if prev:
                idx = combo.findData(prev)
                if idx >= 0:
                    combo.setCurrentIndex(idx)

    def _oneun_resolve_input_folder(self, folder=None):
        """Load matched template sets (Name_Template.odt, Name_AnswerTemplate.odt, optional Name.txt)."""
        if folder is None:
            folder = self.oneun_input_folder.text().strip()
        if not folder:
            return
        if not os.path.isdir(folder):
            print(f"[ERROR] OneUn input folder does not exist: {folder}")
            QMessageBox.warning(self, 'Input Folder Not Found',
                                f'Input folder does not exist: {folder}')
            self.oneun_definition_sets = []
            self.oneun_def_path.setText('')
            return

        try:
            files = [f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))]
        except OSError as e:
            print(f"[ERROR] Cannot read input folder {folder}: {e}")
            self.oneun_definition_sets = []
            self.oneun_def_path.setText('')
            return

        template_re = re.compile(r'^(.*?)__?Template\.odt$', re.IGNORECASE)
        answer_re = re.compile(r'^(.*?)__?AnswerTemplate\.odt$', re.IGNORECASE)
        txt_re = re.compile(r'^(.*?)\.txt$', re.IGNORECASE)

        templates: Dict[str, str] = {}
        answers: Dict[str, str] = {}
        txts: Dict[str, str] = {}
        for f in files:
            m = template_re.match(f)
            if m:
                templates[m.group(1)] = f
                continue
            m = answer_re.match(f)
            if m:
                answers[m.group(1)] = f
                continue
            m = txt_re.match(f)
            if m:
                txts[m.group(1)] = f

        candidate_names = set(templates) | set(answers) | set(txts)
        matched_names = set(templates) & set(answers)

        if not matched_names:
            err = (f"No complete template sets found in {folder}. "
                   "Each set needs Name_Template.odt and Name_AnswerTemplate.odt; Name.txt is optional.")
            print(f"[ERROR] {err}")
            QMessageBox.warning(self, 'No Template Sets', err)
            self.oneun_definition_sets = []
            self.oneun_def_path.setText('')
            return

        for name in sorted(candidate_names - matched_names):
            if name in matched_names:
                continue
            missing = []
            if name not in templates:
                missing.append('Name_Template.odt')
            if name not in answers:
                missing.append('Name_AnswerTemplate.odt')
            have = []
            if name in templates:
                have.append('Name_Template.odt')
            if name in answers:
                have.append('Name_AnswerTemplate.odt')
            if name in txts:
                have.append('Name.txt')
            warn = (f"Template set '{name}' is incomplete (missing {', '.join(missing)}; "
                    f"has {', '.join(have)}). It will be ignored.")
            print(f"[WARNING] {warn}")

        if self.oneun_type_worksheet.isChecked() and len(matched_names) > 1:
            chosen = sorted(matched_names)[0]
            warn = (f"{len(matched_names)} template sets found for worksheets; "
                    f"only the first ({chosen}) will be used. Place unused sets in a subfolder.")
            print(f"[WARNING] {warn}")
            QMessageBox.warning(self, 'Multiple Template Sets', warn)

        self.oneun_definition_sets = []
        for name in sorted(matched_names):
            self.oneun_definition_sets.append({
                'name': name,
                'template_path': os.path.join(folder, templates[name]),
                'answer_key_template_path': os.path.join(folder, answers[name]),
                'def_path': os.path.join(folder, txts[name]) if name in txts else None,
            })

        display_names = []
        for name in sorted(matched_names):
            suffix = ' (+txt)' if name in txts else ' (no txt)'
            display_names.append(name + suffix)
        self.oneun_def_path.setText('; '.join(display_names))
        self._oneun_def_loaded()

    def _oneun_get_params(self):
        """Gather all OneUn parameters from the UI.

        Returns:
            Tuple of (definition_sets, base_seed, metadata, plot_config,
                      module_number, doc_type, course_folder)
            or None if validation fails.  Errors are printed to terminal.
        """
        if not getattr(self, 'oneun_definition_sets', []):
            print("[ERROR] No template sets loaded. Select a module and document type.")
            QMessageBox.warning(self, 'No Template Sets',
                                'No template sets loaded. Check the input folder.')
            return None

        module_text = self.oneun_module_num.currentText()
        if module_text == "None selected":
            print("[ERROR] Please select a module number.")
            return None
        try:
            module = int(module_text)
        except ValueError:
            print("[ERROR] Invalid module number selected.")
            return None

        try:
            course_info = get_course_info(self.engine)
            course_folder = (course_info.get('course_folder') or '').strip()
        except Exception:
            course_folder = ''
        if not course_folder:
            print("[ERROR] No course folder configured. Set it in Course Info.")
            return None

        base_seed = self.oneun_seed.value() if self.oneun_use_seed.isChecked() else None

        doc_type = 'Worksheet' if self.oneun_type_worksheet.isChecked() else 'Quiz'
        instructors = self.instructors_value or ''
        if not instructors:
            try:
                course_info = get_course_info(self.engine)
                instructors = course_info.get('instructors', '')
            except Exception:
                instructors = ''
        metadata = {
            'doc_type': doc_type,
            'course': self.course_value or '',
            'instructors': instructors if isinstance(
                instructors, str) else ', '.join(instructors or []),
            'quiz_date': self.quiz_date_edit.date().toString("yyyy-MM-dd"),
        }

        plot_config = {
            'include_graph': self.oneun_include_graph.isChecked(),
            'equation_index': self.oneun_eq_spinbox.value(),
            'x_var': self.oneun_x_var_combo.currentData() or '',
            'y_var': self.oneun_y_var_combo.currentData() or '',
            'use_gridlines': self.oneun_gridlines.isChecked(),
            'log_x': self.oneun_log_x.isChecked(),
            'log_y': self.oneun_log_y.isChecked(),
        }

        definition_sets = []
        for s in self.oneun_definition_sets:
            if s['def_path']:
                try:
                    definition = load_problem_definition(s['def_path'])
                except Exception as e:
                    print(f"[ERROR] Could not load definition {s['def_path']}: {e}")
                    QMessageBox.warning(self, 'Definition Error',
                                        f"Could not load {s['def_path']}: {e}")
                    return None
            else:
                definition = ProblemDefinition(equations=[], variables={}, constants={})
            definition_sets.append({
                'name': s['name'],
                'definition': definition,
                'template_path': s['template_path'],
                'answer_key_template_path': s['answer_key_template_path'],
                'def_path': s['def_path'],
            })

        return (definition_sets, base_seed, metadata, plot_config,
                module, doc_type, course_folder)

    def _oneun_generate(self):
        """Generate ODT file(s) for the selected student codes."""
        params = self._oneun_get_params()
        if params is None:
            return

        (definition_sets, base_seed, metadata, plot_config,
         module, doc_type, course_folder) = params

        set_info = definition_sets[0]

        # Parse student codes
        raw_codes = self.student_codes_text.toPlainText().strip()
        student_codes = [c.strip() for c in raw_codes.split(',') if c.strip()]
        if not student_codes:
            student_codes = ['']

        # Build module-based worksheet IDs when a module is selected
        output_ids: dict = {}
        answer_key_output_ids: dict = {}
        if module is not None:
            for code in student_codes:
                base = format_quiz_id(code, module, 1)
                output_ids[code] = f"{base}WS"
                answer_key_output_ids[code] = f"{base}WA"

        # Resolve student codes to real names/section numbers for the ODT header
        student_names: dict = {}
        student_section_codes: dict = {}
        try:
            for row in get_all_students_as_dicts(self.engine):
                if row.get('student_code') in student_codes:
                    code = row['student_code']
                    student_names[code] = row.get('name') or code
                    section_number = row.get('section_number')
                    student_section_codes[code] = str(section_number) if section_number is not None else ''
        except Exception as e:
            print(f"Warning: could not load student names: {e}")

        subdir = 'worksheets' if doc_type == 'Worksheet' else 'quizzes'
        output_dir = os.path.join(course_folder, f'module{module}', subdir)
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, set_info['name'] + '.odt')

        try:
            odt_gen = OneUnODTGenerator()
            main_files, answer_key_files, _ = odt_gen.generate_quiz(
                definition=set_info['definition'],
                template_path=set_info['template_path'],
                output_path=output_path,
                student_codes=student_codes,
                quiz_metadata=metadata,
                plot_config=plot_config,
                output_ids=output_ids or None,
                answer_key_template_path=set_info['answer_key_template_path'] or None,
                answer_key_output_ids=answer_key_output_ids or None,
                student_names=student_names or None,
                student_section_codes=student_section_codes or None,
                base_seed=base_seed,
                return_values=True,
            )
            generated = main_files + answer_key_files
            log_path = os.path.join(output_dir, set_info['name'] + '_summary.txt')
            odt_gen._write_summary_log(
                log_path=log_path,
                definition_path=set_info['def_path'],
                template_path=set_info['template_path'],
                output_files=generated,
                student_seeds={
                    (sc or 'generic'): (base_seed + i if base_seed is not None else None)
                    for i, sc in enumerate(student_codes)
                },
                metadata=metadata,
                plot_config=plot_config,
                generated_at=datetime.now().isoformat(timespec='seconds')
            )

            if self.oneun_packet_checkbox.isChecked():
                try:
                    packet_title = 'Worksheet Packet' if doc_type == 'Worksheet' else 'Quant Quiz Packet'
                    self._create_quant_packets(
                        main_files, answer_key_files, student_codes, module, output_dir,
                        output_ids=output_ids,
                        answer_key_output_ids=answer_key_output_ids,
                        title=packet_title,
                        doc_type=doc_type,
                    )
                except Exception as pkt_err:
                    print(f"[ERROR] Failed to create quant packet: {pkt_err}")
                    import traceback
                    traceback.print_exc()

            QMessageBox.information(
                self, "Success",
                f"Generated {len(generated)} {doc_type.lower()} file(s).\n\n"
                f"Set: {set_info['name']}\n"
                f"Equations (questions): {len(set_info['definition'].equations)}\n"
                f"Students: {len(student_codes)}\n"
                f"Base seed: {base_seed}\n"
                f"Output folder: {output_dir}\n\n"
                f"Summary log written alongside output files.")
            self.statusBar().showMessage(
                f"OneUn: {len(generated)} {doc_type.lower()}(s) saved", 5000)

        except Exception as e:
            print(f"\n[ERROR] OneUn generation failed: {e}")
            import traceback
            traceback.print_exc()


    # ------------------------------------------------------------------
    # Shared Student Codes Tab
    # ------------------------------------------------------------------

    def create_student_codes_tab(self):
        """Create the shared Student Codes tab used by all generator tabs."""
        tab = QWidget()
        layout = QVBoxLayout()
        tab.setLayout(layout)

        desc = QLabel(
            "Enter the student codes to use for Quiz Creator and Quant ODT.")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self.student_codes_text = QTextEdit()
        self.student_codes_text.setPlaceholderText("Enter comma-separated student codes here")
        self.student_codes_text.setPlainText(
            self.settings.value('studentCodes', 'StA'))
        self.student_codes_text.textChanged.connect(lambda: self.toggle_quiz_buttons(True))
        self.student_codes_text.textChanged.connect(
            lambda: self.settings.setValue(
                'studentCodes', self.student_codes_text.toPlainText()))
        layout.addWidget(self.student_codes_text)

        sel_row = QHBoxLayout()
        self.student_combo = QComboBox()
        self.populate_student_combo()
        sel_row.addWidget(self.student_combo)

        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self.add_selected_student)
        sel_row.addWidget(add_btn)

        all_btn = QPushButton("All")
        all_btn.clicked.connect(self.add_all_students)
        sel_row.addWidget(all_btn)

        self.section_filter_combo = QComboBox()
        self._populate_section_filter_combo()
        sel_row.addWidget(self.section_filter_combo)

        section_btn = QPushButton("Add Section")
        section_btn.setToolTip("Add all students in the selected section")
        section_btn.clicked.connect(self.add_section_students)
        sel_row.addWidget(section_btn)

        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(lambda: self.student_codes_text.clear())
        sel_row.addWidget(clear_btn)

        layout.addLayout(sel_row)
        layout.addStretch()
        return tab

def main():
    """Main function to run the Generator application."""
    app = QApplication(sys.argv)
    
    # Set application style
    app.setStyle('Fusion')
    
    # Create and show the main window
    window = MCQGeneratorGUI()
    window.show()
    
    # Run the application
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
