"""
MCQ Generator Application - Main GUI for generating questions and quizzes.
"""
import sys
import os
import shutil
import glob
from typing import Dict, List
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
    NUM_MODULES,
)
from shared_gui26 import BaseMCQApp
from course_info_panel26 import CourseInfoPanel
from llm_converter26 import LLMConverter
from quiz_generator26 import (
    create_quizzes_for_students, get_quiz_page_count,
    stamp_page_numbers_to_pdf, COURSE_FOLDER,
)
from document_ids26 import artifact_id, format_quiz_id
from OneUn import (ProblemDefinitionParser, ProblemGenerator, OneUnODTGenerator,
                  load_problem_definition, generate_problems_for_student,
                  get_odt_template_page_count)



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
        total_questions_layout.addStretch()
        form_layout.addRow("Total Questions:", total_questions_layout)

        # Append One Unknown quiz option
        self.append_oneun_checkbox = QCheckBox("Append Quant ODT")
        self.append_oneun_checkbox.setChecked(False)
        self.append_oneun_checkbox.setToolTip(
            "Also generate One Unknown ODTs appended to the MCQ quiz "
            "using settings from the One Unknown tab."
        )
        form_layout.addRow("", self.append_oneun_checkbox)
        
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
        
        # Create quizzes button with Dev Mode checkbox
        quiz_btn_layout = QHBoxLayout()
        
        # Create Quizzes button
        self.create_quiz_btn = QPushButton("Create Quizzes")
        self.create_quiz_btn.setStyleSheet("font-size: 14px; font-weight: bold; padding: 8px;")
        self.create_quiz_btn.clicked.connect(self.create_quiz_set_from_gui)
        quiz_btn_layout.addWidget(self.create_quiz_btn)
        
        # Add stretch to push the Dev Mode checkbox to the right
        quiz_btn_layout.addStretch()
        
        # Dev Mode checkbox
        self.dev_mode_checkbox = QCheckBox("Dev Mode")
        self.dev_mode_checkbox.setChecked(False)  # Default to BE UNchecked
        self.dev_mode_checkbox.setToolTip("When checked, quiz databases will not be updated")
        quiz_btn_layout.addWidget(self.dev_mode_checkbox)
        
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
            dev_mode = self.dev_mode_checkbox.isChecked()

            created = create_quizzes_for_students(
                engine=self.engine,
                module_number=module_number,
                student_codes=student_codes,
                bank_paths=bank_paths,
                questions_per_bank=questions_per_bank,
                quiz_date=quiz_date,
                attempts=None,  # Use course default max_attempts_per_module
                dev_mode=dev_mode,
            )

            total_quizzes = sum(len(v) for v in created.values())
            created_codes = ', '.join(sorted(created.keys()))
            quiz_folder = os.path.join(COURSE_FOLDER, f'module{module_number}', 'quizzes')

            # Optionally append Quant ODT, reusing the One Unknown tab settings
            if self.append_oneun_checkbox.isChecked():
                self._append_oneun_to_quiz(
                    module_number=module_number,
                    student_codes=student_codes,
                    total_mcq_questions=total_bank_questions,
                    created=created,
                )
            else:
                for code, quiz_ids in created.items():
                    for quiz_id in quiz_ids:
                        quiz_pdf = os.path.join(quiz_folder, f'{artifact_id(quiz_id, "Q")}.pdf')
                        if os.path.exists(quiz_pdf):
                            stamp_page_numbers_to_pdf(quiz_pdf)

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
                              created: Dict[str, List[str]]):
        """Generate One Unknown ODTs appended to the MCQ PDFs.

        Validates that the One Unknown tab's settings are compatible with the
        current MCQ quiz, then generates ODTs whose question/page numbering
        continues after the MCQ portion.
        """
        params = self._oneun_get_params()
        if params is None:
            raise ValueError("One Unknown settings are incomplete; cannot append.")

        (definition, mode, base_seed, metadata,
         _output_path, template_path, answer_key_template_path, plot_config, def_path) = params

        # Document type must be Quiz when appending to an MCQ quiz
        if metadata.get('doc_type') != 'Quiz':
            raise ValueError(
                "One Unknown document type must be 'Quiz' when appending to an MCQ quiz."
            )

        # Module must match the MCQ module (skip when no definition file is used)
        if def_path:
            oneun_module = self._extract_module_number(def_path)
            if oneun_module is None:
                raise ValueError(
                    f"Cannot determine module from One Unknown definition filename: {def_path!r}. "
                    "Use an M#_ prefix (e.g. M1_nernst.txt) to enable module matching."
                )
            if oneun_module != module_number:
                raise ValueError(
                    f"Module mismatch: MCQ module is {module_number}, "
                    f"but One Unknown definition is for module {oneun_module}."
                )

        # Determine MCQ page count from the first generated metadata JSON
        quiz_folder = os.path.join(
            os.path.expanduser('~/textProcessing/NBIO140_2026'),
            f'module{module_number}', 'quizzes')
        mcq_pages = 0
        first_code = student_codes[0]
        pattern = os.path.join(
            quiz_folder, f"{first_code}_{module_number:02d}_0001QM.json")
        matches = sorted(glob.glob(pattern))
        if matches:
            mcq_pages = get_quiz_page_count(matches[0])
        else:
            fallback = sorted(glob.glob(
                os.path.join(quiz_folder, f"{first_code}_*QM.json")))
            if fallback:
                mcq_pages = get_quiz_page_count(fallback[0])

        odt_pages = get_odt_template_page_count(template_path)
        if odt_pages is None:
            raise ValueError(f"Could not determine page count from ODT template {template_path!r}")
        total_pages = mcq_pages + odt_pages
        metadata['total_pages'] = total_pages

        # Output into the same module quizzes folder as the PDFs
        out_stem = os.path.join(quiz_folder, 'oneunknown_quiz.odt')

        odt_gen = OneUnODTGenerator()
        generated_odts = odt_gen.generate_quiz(
            definition=definition,
            template_path=template_path,
            output_path=out_stem,
            student_codes=student_codes,
            quiz_metadata=metadata,
            mode=mode,
            answer_key_template_path=answer_key_template_path or None,
            base_seed=base_seed,
            start_question=total_mcq_questions + 1,
            start_page=mcq_pages + 1,
        )

        # Stamp combined page numbers onto the existing MCQ PDFs
        for code, quiz_ids in created.items():
            for quiz_id in quiz_ids:
                quiz_pdf = os.path.join(quiz_folder, f'{artifact_id(quiz_id, "Q")}.pdf')
                if os.path.exists(quiz_pdf):
                    stamp_page_numbers_to_pdf(quiz_pdf, total_pages=total_pages)

        # Write a summary log alongside the generated ODTs
        odt_gen._write_summary_log(
            log_path=os.path.splitext(out_stem)[0] + '_summary.txt',
            definition_path=def_path,
            template_path=template_path,
            output_files=generated_odts,
            student_seeds={
                (sc or 'generic'): (base_seed + i if base_seed is not None else None)
                for i, sc in enumerate(student_codes)
            },
            metadata=metadata,
            mode=mode,
            plot_config=plot_config,
            generated_at=__import__('datetime').datetime.now().isoformat(
                timespec='seconds')
        )

        QMessageBox.information(
            self,
            "One Unknown Appended",
            f"Generated {len(generated_odts)} One Unknown ODT file(s) "
            f"appended to the MCQ quiz.\n"
            f"Quant questions start at Q{total_mcq_questions + 1}, "
            f"page {mcq_pages + 1}.\n"
            f"Output folder: {quiz_folder}"
        )

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
    
        # Define the pattern for MX_YYYQD_datestr.txt
        # M followed by 1-2 digits, underscore, 3 letters, Q/A/F/T, 0-2, underscore, date, .txt
        pattern = r'^M\d{1,2}_[A-Za-z]{3}[QATF][0-2]_\w+\.txt$'
        
        # Help text for error messages
        error_msg = (
            "Selected file must match the pattern M#_XXXQ#_date.txt where:\n"
            "- M# is the module number (1-2 digits)\n"
            "- XXX is a 3-letter topic code\n"
            "- Q is the file type (Q=question, A=answer, F=feedback, T=type)\n"
            "- # is the difficulty (0-2)\n"
            "- date is the date in format like May1525\n"
            "Example: M1_ABCQ1_15May25.txt"
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

        # --- Input Files Group ---
        files_group = QGroupBox("Input Files")
        files_layout = QFormLayout()
        files_group.setLayout(files_layout)

        # Input folder that contains the .txt definition and .odt templates
        folder_layout = QHBoxLayout()
        self.oneun_input_folder = QLineEdit()
        self.oneun_input_folder.setPlaceholderText("Select folder containing .txt, ODT template, and answer-key ODT")
        self.oneun_input_folder.setMinimumWidth(380)
        self.oneun_input_folder.editingFinished.connect(self._oneun_resolve_input_folder)
        folder_layout.addWidget(self.oneun_input_folder)
        self.oneun_browse_folder_btn = QPushButton("Browse…")
        self.oneun_browse_folder_btn.clicked.connect(self._oneun_browse_input_folder)
        folder_layout.addWidget(self.oneun_browse_folder_btn)
        files_layout.addRow("Input Folder:", folder_layout)

        # Auto-detected definition file (.txt)
        self.oneun_def_path = QLineEdit()
        self.oneun_def_path.setReadOnly(True)
        self.oneun_def_path.setPlaceholderText("Auto-detected .txt file")
        self.oneun_def_path.setMinimumWidth(380)
        self.oneun_def_path.textChanged.connect(self._oneun_def_loaded)
        files_layout.addRow("Definition File:", self.oneun_def_path)

        # Auto-detected ODT template file (required)
        self.oneun_tpl_path = QLineEdit()
        self.oneun_tpl_path.setReadOnly(True)
        self.oneun_tpl_path.setPlaceholderText("Auto-detected ODT template")
        self.oneun_tpl_path.setMinimumWidth(380)
        files_layout.addRow("ODT Template (required):", self.oneun_tpl_path)

        # Auto-detected answer key template file (optional)
        self.oneun_answer_key_tpl_path = QLineEdit()
        self.oneun_answer_key_tpl_path.setReadOnly(True)
        self.oneun_answer_key_tpl_path.setPlaceholderText("Auto-detected answer-key ODT (optional)")
        self.oneun_answer_key_tpl_path.setMinimumWidth(380)
        files_layout.addRow("Answer Key ODT:", self.oneun_answer_key_tpl_path)

        layout.addWidget(files_group)

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
        self.oneun_module_num.currentIndexChanged.connect(self._oneun_update_output_preview)
        module_layout.addWidget(self.oneun_module_num)
        module_layout.addStretch()
        params_layout.addRow("Module:", module_layout)

        mode_layout = QHBoxLayout()
        self.oneun_mode_random = QRadioButton("Random")
        self.oneun_mode_pseudo_random = QRadioButton("Pseudo Random")
        self.oneun_mode_random.setChecked(True)
        self.oneun_mode_random.setToolTip("Values chosen randomly from allowed values")
        self.oneun_mode_pseudo_random.setToolTip(
            "Value combinations chosen in random order without reuse across students"
        )
        mode_layout.addWidget(self.oneun_mode_random)
        mode_layout.addWidget(self.oneun_mode_pseudo_random)
        mode_layout.addStretch()
        params_layout.addRow("Mode:", mode_layout)

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

        # --- Output Group ---
        output_group = QGroupBox("Output")
        output_layout = QFormLayout()
        output_group.setLayout(output_layout)

        out_file_layout = QHBoxLayout()
        self.oneun_output_path = QLineEdit()
        self.oneun_output_path.setPlaceholderText("Optional base file name; auto-filled if blank")
        self.oneun_output_path.setMinimumWidth(380)
        out_file_layout.addWidget(self.oneun_output_path)
        self.oneun_output_browse_btn = QPushButton("Browse…")
        self.oneun_output_browse_btn.clicked.connect(self._oneun_browse_output)
        out_file_layout.addWidget(self.oneun_output_browse_btn)
        output_layout.addRow("Output File (optional):", out_file_layout)

        self.oneun_output_preview = QLabel("Select a module to see output folder")
        self.oneun_output_preview.setStyleSheet("color: #666;")
        output_layout.addRow("Output Folder:", self.oneun_output_preview)

        layout.addWidget(output_group)

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

        return tab

    def _oneun_update_generate_btn(self):
        """Update the generate button label to match the selected document type."""
        doc_type = 'Worksheet' if self.oneun_type_worksheet.isChecked() else 'Quiz'
        self.oneun_generate_btn.setText(f"Generate {doc_type}")
        self._oneun_update_output_preview()

    def _oneun_update_output_preview(self):
        """Display the auto-computed output folder for the selected module/doc type."""
        module_text = self.oneun_module_num.currentText()
        if module_text == "None selected":
            self.oneun_output_preview.setText("Select a module to see output folder")
            return
        try:
            module = int(module_text)
        except ValueError:
            self.oneun_output_preview.setText("Invalid module selection")
            return
        subdir = 'worksheets' if self.oneun_type_worksheet.isChecked() else 'quizzes'
        try:
            course_info = get_course_info(self.engine)
            course_folder = (course_info.get('course_folder') or '').strip()
        except Exception:
            course_folder = ''
        if not course_folder:
            self.oneun_output_preview.setText("Configure Course Folder in Course Info first")
            return
        folder = os.path.join(os.path.expanduser(course_folder),
                              f"module{module}", subdir)
        self.oneun_output_preview.setText(folder)

    def _oneun_toggle_plot_controls(self, enabled: bool):
        """Enable/disable plot control widgets based on the include-graph checkbox."""
        for w in (self.oneun_eq_spinbox, self.oneun_x_var_combo,
                  self.oneun_y_var_combo, self.oneun_gridlines,
                  self.oneun_log_x, self.oneun_log_y):
            w.setEnabled(enabled)

    def _oneun_def_loaded(self):
        """Called when the definition file path changes; repopulate variable combos."""
        path = self.oneun_def_path.text().strip()
        if path and os.path.exists(path):
            self._oneun_eq_changed(self.oneun_eq_spinbox.value())

    def _oneun_eq_changed(self, eq_num: int):
        """Reparse the variables for equation eq_num and populate X/Y combos."""
        path = self.oneun_def_path.text().strip()
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

    def _oneun_browse_input_folder(self):
        """Browse for a folder containing the definition .txt and ODT templates."""
        folder = QFileDialog.getExistingDirectory(
            self, "Select Input Folder",
            os.path.expanduser('~'))
        if folder:
            self.oneun_input_folder.setText(folder)
            self._oneun_resolve_input_folder(folder)

    def _oneun_resolve_input_folder(self, folder=None):
        """Auto-detect definition .txt, main .odt template, and optional answer-key .odt."""
        if folder is None:
            folder = self.oneun_input_folder.text().strip()
        if not folder or not os.path.isdir(folder):
            return
        try:
            files = os.listdir(folder)
        except OSError:
            return
        txt_files = [f for f in files if f.lower().endswith('.txt') and 'summary' not in f.lower()]
        odt_files = [f for f in files if f.lower().endswith('.odt')]
        if not txt_files:
            print("[ERROR] No .txt definition file found in the selected folder.")
            return
        if len(txt_files) > 1:
            print("[ERROR] Multiple .txt files found; the folder should contain exactly one definition file.")
            return
        if not odt_files:
            print("[ERROR] No .odt template files found in the selected folder.")
            return
        answer_odts = [f for f in odt_files if 'answer' in f.lower() or 'key' in f.lower()]
        non_answer_odts = [f for f in odt_files if f not in answer_odts]
        if len(non_answer_odts) == 1 and len(answer_odts) == 1:
            main_template = non_answer_odts[0]
            answer_key_template = answer_odts[0]
        elif len(non_answer_odts) == 1 and not answer_odts:
            main_template = non_answer_odts[0]
            answer_key_template = ''
        else:
            print("[ERROR] Cannot determine the ODT template and answer-key template. "
                  "Expected one main .odt and optionally one whose name contains 'answer' or 'key'.")
            return
        self.oneun_def_path.setText(os.path.join(folder, txt_files[0]))
        self.oneun_tpl_path.setText(os.path.join(folder, main_template))
        self.oneun_answer_key_tpl_path.setText(
            os.path.join(folder, answer_key_template) if answer_key_template else '')

    def _oneun_browse_output(self):
        """Browse for output ODT file location."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Quiz As",
            os.path.expanduser('~/oneun_quiz.odt'),
            "ODT Files (*.odt);;All Files (*)")
        if path:
            self.oneun_output_path.setText(path)

    def _oneun_get_params(self):
        """Gather all OneUn parameters from the UI.

        Returns:
            Tuple of (definition, mode, base_seed, metadata,
                      output_path, template_path, plot_config, def_path)
            or None if validation fails.  Errors are printed to terminal.
        """
        # Definition file is optional (template can have no variables/constants)
        def_path = self.oneun_def_path.text().strip()
        if def_path and not os.path.exists(def_path):
            print("[ERROR] Problem definition file not found.")
            return None

        definition = None
        if def_path:
            try:
                definition = load_problem_definition(def_path)
            except Exception as e:
                print(f"[ERROR] Problem definition has errors: {e}")
                import traceback
                traceback.print_exc()
                return None

        # Validate template (required)
        template_path = self.oneun_tpl_path.text().strip()
        if not template_path or not os.path.exists(template_path):
            print("[ERROR] Please select a valid ODT template file.")
            return None

        mode = 'pseudo_random' if self.oneun_mode_pseudo_random.isChecked() else 'random'
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

        output_path = self.oneun_output_path.text().strip()
        if not output_path:
            module_text = self.oneun_module_num.currentText()
            if module_text == "None selected":
                print("[ERROR] Please select a module number or enter an output file path.")
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
            subdir = 'worksheets' if self.oneun_type_worksheet.isChecked() else 'quizzes'
            doc_type = 'Worksheet' if self.oneun_type_worksheet.isChecked() else 'Quiz'
            date_str = datetime.now().strftime('%b%d%y')
            output_path = os.path.join(
                os.path.expanduser(course_folder),
                f'module{module}',
                subdir,
                f'OneUn_{doc_type}_{date_str}.odt'
            )

        answer_key_template_path = self.oneun_answer_key_tpl_path.text().strip()
        if answer_key_template_path and not os.path.exists(answer_key_template_path):
            print("[ERROR] Please select a valid answer-key ODT template.")
            return None

        plot_config = {
            'include_graph': self.oneun_include_graph.isChecked(),
            'equation_index': self.oneun_eq_spinbox.value(),
            'x_var': self.oneun_x_var_combo.currentData() or '',
            'y_var': self.oneun_y_var_combo.currentData() or '',
            'use_gridlines': self.oneun_gridlines.isChecked(),
            'log_x': self.oneun_log_x.isChecked(),
            'log_y': self.oneun_log_y.isChecked(),
        }

        return (definition, mode, base_seed, metadata,
                output_path, template_path, answer_key_template_path, plot_config, def_path)

    def _oneun_generate(self):
        """Generate one ODT quiz per student code."""
        params = self._oneun_get_params()
        if params is None:
            return

        (definition, mode, base_seed, metadata,
         output_path, template_path, answer_key_template_path, plot_config, def_path) = params

        # Parse student codes
        raw_codes = self.student_codes_text.toPlainText().strip()
        student_codes = [c.strip() for c in raw_codes.split(',') if c.strip()]
        if not student_codes:
            student_codes = ['']

        # Build module-based worksheet IDs when a module is selected
        output_ids: dict = {}
        answer_key_output_ids: dict = {}
        module_text = self.oneun_module_num.currentText()
        if module_text != "None selected":
            module = int(module_text)
            for code in student_codes:
                base = format_quiz_id(code, module, 1)
                output_ids[code] = f"{base}WS"
                answer_key_output_ids[code] = f"{base}WA"

        # Resolve student codes to real names for the ODT header
        student_names: dict = {}
        try:
            for row in get_all_students_as_dicts(self.engine):
                if row.get('student_code') in student_codes:
                    student_names[row['student_code']] = row.get('name') or row['student_code']
        except Exception as e:
            print(f"Warning: could not load student names: {e}")

        base_dir = os.path.dirname(os.path.abspath(output_path))

        try:
            odt_gen = OneUnODTGenerator()
            generated = odt_gen.generate_quiz(
                definition=definition,
                template_path=template_path,
                output_path=output_path,
                student_codes=student_codes,
                quiz_metadata=metadata,
                plot_config=plot_config,
                mode=mode,
                output_ids=output_ids or None,
                answer_key_template_path=answer_key_template_path or None,
                answer_key_output_ids=answer_key_output_ids or None,
                student_names=student_names or None,
                base_seed=base_seed
            )
            # Pass definition path into the summary log retroactively
            odt_gen._write_summary_log(
                log_path=os.path.splitext(output_path)[0] + '_summary.txt',
                definition_path=def_path,
                template_path=template_path,
                output_files=generated,
                student_seeds={
                    (sc or 'generic'): (base_seed + i if base_seed is not None else None)
                    for i, sc in enumerate(student_codes)
                },
                metadata=metadata,
                mode=mode,
                plot_config=plot_config,
                generated_at=__import__('datetime').datetime.now().isoformat(
                    timespec='seconds')
            )

            QMessageBox.information(
                self, "Success",
                f"Generated {len(generated)} quiz file(s).\n\n"
                f"Equations (questions): {len(definition.equations)}\n"
                f"Students: {len(student_codes)}\n"
                f"Mode: {mode}\n"
                f"Output folder: {base_dir}\n\n"
                f"Summary log written alongside output files.")
            self.statusBar().showMessage(
                f"OneUn: {len(generated)} quiz(zes) saved", 5000)

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
