import os
import json
import re
from datetime import datetime
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, 
    QMessageBox, QComboBox, QTextEdit, QFileDialog, QFormLayout, QGroupBox
)
from PyQt6.QtCore import Qt

print("LLM Converter module loaded - DEBUG VERSION")

# Import local modules
from llm_question_utils26 import parse_llm_questions_file


class LLMConverter:
    """LLM Question Converter module for the MCQ Generator application."""
    
    def __init__(self, parent=None):
        """Initialize the LLM Converter.
        
        Args:
            parent: The parent MCQGeneratorGUI instance
        """
        self.parent = parent
        self.questions = []
        self.current_question_index = -1
        self.current_topic_code = ''
        self.current_difficulty = 0
        self._full_basepath = None
        self.tab = None  # Will store the tab widget reference
        
    def get_selected_module(self):
        """Get the currently selected module number.
        
        Returns:
            int or None: The selected module number, or None if no valid selection.
        """
        current_text = self.llm_module_num.currentText()
        if current_text == "None selected":
            return None
        try:
            return int(current_text)
        except ValueError:
            return None
        
    def create_llm_converter_tab(self):
        """Create the LLM question converter tab."""
        from PyQt6.QtGui import QShortcut, QKeySequence
        
        tab = QWidget()
        layout = QVBoxLayout()
        tab.setLayout(layout)
        
        # Store the tab reference
        self.tab = tab
        
        # Add keyboard shortcuts for left/right arrow keys
        # These will be set up when the tab is added to the main window
        # We'll do this in a separate method called from the main GUI
        
        # Course info group
        course_group = QGroupBox("Course Information")
        course_layout = QFormLayout()
        course_group.setLayout(course_layout)
        
        # Create course inputs for this tab
        self.llm_course = QLineEdit(self.parent.course_value)
        self.llm_title = QLineEdit(self.parent.course_title_value)
        self.llm_instructors = QLineEdit(', '.join(self.parent.instructors_value) if isinstance(self.parent.instructors_value, list) else (self.parent.instructors_value or ''))
        
        # Set minimum width and font for all fields
        for field in [self.llm_course, self.llm_title, self.llm_instructors]:
            field.setMinimumWidth(400)
            font = field.font()
            font.setPointSize(10)
            field.setFont(font)
        
        # Connect signals
        self.llm_course.textChanged.connect(self.parent.sync_course)
        self.llm_title.textChanged.connect(self.parent.sync_course_title)
        self.llm_instructors.textChanged.connect(self.parent.sync_instructors)
        
        # Add to layout
        course_layout.addRow("Course:", self.llm_course)
        course_layout.addRow("Course Title:", self.llm_title)
        course_layout.addRow("Instructors:", self.llm_instructors)
        layout.addWidget(course_group)
        
        # LLM Converter parameters
        param_group = QGroupBox("LLM Question Parameters")
        param_layout = QFormLayout()
        param_group.setLayout(param_layout)
        
        # Input file selection
        input_file_layout = QHBoxLayout()
        self.llm_input_file = QLineEdit()
        self.llm_input_file.setPlaceholderText("Select LLM-generated questions file")
        self.llm_input_file.setMinimumWidth(300)
        input_file_btn = QPushButton("Browse...")
        input_file_btn.clicked.connect(self.select_llm_input_file)
        input_file_layout.addWidget(self.llm_input_file)
        input_file_layout.addWidget(input_file_btn)
        param_layout.addRow("Input File:", input_file_layout)
        
    # Topic code input
        self.llm_topic_code = QLineEdit()
        self.llm_topic_code.setMaxLength(3)
        self.llm_topic_code.setFixedWidth(50)
        self.llm_topic_code.textChanged.connect(self.update_output_directory)
        param_layout.addRow("Topic Code (3 letters):", self.llm_topic_code)
        
    # Topic name
        self.llm_topic_name = QLineEdit()
        param_layout.addRow("Topic Name:", self.llm_topic_name)
        
    # Module number (ComboBox with 'None selected')
        self.llm_module_num = QComboBox()
        self.llm_module_num.addItem("None selected")
        for i in range(1, 31):
            self.llm_module_num.addItem(str(i))
        self.llm_module_num.currentIndexChanged.connect(self.update_output_directory)
        param_layout.addRow("Module:", self.llm_module_num)

    # Difficulty level
        self.llm_difficulty_combo = QComboBox()
        self.llm_difficulty_combo.addItems(["0 (Easy)", "1 (Medium)", "2 (Hard)"])
        param_layout.addRow("Difficulty Level:", self.llm_difficulty_combo)
        
    # Output directory display
        self.output_dir_label = QLabel()
        param_layout.addRow("Output Directory:", self.output_dir_label)
        self.update_output_directory()  # Initialize the output directory display
        
    # Add widgets to layout
        layout.addWidget(param_group)
        
    # QBank management buttons
        qbank_btn_layout = QHBoxLayout()
        
        self.new_qbank_btn = QPushButton("New QBank")
        self.add_to_qbank_btn = QPushButton("Add to QBank")
        self.load_qbank_btn = QPushButton("Load QBank")
        
        # Connect buttons to their handlers
        self.new_qbank_btn.clicked.connect(self.new_qbank)
        self.add_to_qbank_btn.clicked.connect(self.add_to_qbank)
        self.load_qbank_btn.clicked.connect(self.load_qbank)
                
        # Add buttons to layout
        qbank_btn_layout.addWidget(self.new_qbank_btn)
        qbank_btn_layout.addWidget(self.add_to_qbank_btn)
        qbank_btn_layout.addWidget(self.load_qbank_btn)
        
        layout.addLayout(qbank_btn_layout)
        
    # Question navigation and editing area
        editor_widget = QWidget()
        editor_layout = QHBoxLayout()
        editor_widget.setLayout(editor_layout)
        
    # Left side: Question editor (2/3 width)
        question_editor_widget = QWidget()
        question_editor_layout = QVBoxLayout()
        question_editor_widget.setLayout(question_editor_layout)
    # Navigation buttons
        nav_layout = QHBoxLayout()
        self.prev_button = QPushButton("<")
        self.prev_button.setFixedWidth(40)
        self.next_button = QPushButton(">")
        self.next_button.setFixedWidth(40)
        self.delete_button = QPushButton("Delete")
        self.similarity_button = QPushButton("Compute Similarity")
        
        self.prev_button.clicked.connect(self.show_previous_question)
        self.next_button.clicked.connect(self.show_next_question)
        self.delete_button.clicked.connect(self.delete_current_question)
        self.similarity_button.clicked.connect(self.compute_context_similarity)
        
        nav_layout.addWidget(self.prev_button)
        nav_layout.addWidget(self.next_button)
        nav_layout.addSpacing(20)  # Add some space before delete button
        nav_layout.addWidget(self.delete_button)
        nav_layout.addSpacing(10)  # Add some space between delete and similarity buttons
        nav_layout.addWidget(self.similarity_button)
        nav_layout.addStretch()  # Push buttons to the left
        
        question_editor_layout.addLayout(nav_layout)
        
    # Question ID label
        self.question_id_label = QLabel("Question ID: ")
        self.question_id_label.setStyleSheet("font-weight: bold; color: #2c3e50;")
        question_editor_layout.addWidget(self.question_id_label)
        
    # Question editor
        self.question_editor = QTextEdit()
        self.question_editor.setPlaceholderText("Question text will appear here...")
        question_editor_layout.addWidget(self.question_editor)
        
    # Right side: Feedback editor (1/3 width)
        feedback_widget = QWidget()
        feedback_layout = QVBoxLayout()
        feedback_widget.setLayout(feedback_layout)
        
    # Feedback section header
        feedback_header = QHBoxLayout()
        feedback_label = QLabel("<b>Feedback:</b>")
        feedback_header.addWidget(feedback_label)
        
    # Add chapter and section selectors
        self.chapter_combo = QComboBox()
        self.chapter_combo.addItems([str(i) for i in range(1, 15)])  # Chapters 1-14
        self.chapter_combo.setFixedWidth(60)
        
        self.section_combo = QComboBox()
        self.section_combo.addItems([str(i) for i in range(1, 36)])  # Sections 1-35
        self.section_combo.setFixedWidth(60)
        
    # Add Feedback button
        add_feedback_btn = QPushButton("Add Feedback")
        add_feedback_btn.clicked.connect(self.add_section_feedback)
        add_feedback_btn.setFixedWidth(100)
        
        feedback_header.addStretch()
        feedback_header.addWidget(QLabel("Ch:"))
        feedback_header.addWidget(self.chapter_combo)
        feedback_header.addWidget(QLabel("Sec:"))
        feedback_header.addWidget(self.section_combo)
        feedback_header.addWidget(add_feedback_btn)
        
        feedback_layout.addLayout(feedback_header)
        
    # Feedback editor
        self.feedback_editor = QTextEdit()
        self.feedback_editor.setPlaceholderText("Enter feedback for this question...")
        feedback_layout.addWidget(self.feedback_editor)
        
    # Context section header
        context_header = QHBoxLayout()
        context_label = QLabel("<b>Context:</b>")
        context_header.addWidget(context_label)
        context_header.addStretch()
        feedback_layout.addLayout(context_header)
        
    # Context display
        self.context_display = QTextEdit()
        self.context_display.setReadOnly(True)
        self.context_display.setPlaceholderText("Context for this question will appear here...")
        feedback_layout.addWidget(self.context_display)
        
    # Add editors to the main editor widget
        editor_layout.addWidget(question_editor_widget, 2)  # 2/3 width
        editor_layout.addWidget(feedback_widget, 1)         # 1/3 width
        
    # Status area
        self.llm_status = QTextEdit()
        self.llm_status.setReadOnly(True)
        self.llm_status.setPlaceholderText("Conversion status will appear here...")
        self.llm_status.setMinimumHeight(80)  # Reduced height to accommodate the context display
        
    # Add to main layout
        layout.addWidget(editor_widget)
        layout.addWidget(self.llm_status)
        
        return tab
        
    def delete_current_question(self):
        """Delete the current question and show the next one."""
        if not self.questions or self.current_question_index < 0:
            return
                
        # Remove current question
        del self.questions[self.current_question_index]
            
        if not self.questions:
            # No more questions
            self.current_question_index = -1
            self.question_editor.clear()
            self.feedback_editor.clear()
        else:
            # Adjust index if we deleted the last question
            if self.current_question_index >= len(self.questions):
                self.current_question_index = len(self.questions) - 1
            self.display_current_question()
    
    def show_previous_question(self):
        """Save current question and show the previous one."""
        self.save_current_question()
        if not self.questions:
            return
                
        self.current_question_index = (self.current_question_index - 1) % len(self.questions)
        self.display_current_question()
            
    def show_next_question(self):
        """Save current question and show the next one."""
        self.save_current_question()
        if not self.questions:
            return
                
        self.current_question_index = (self.current_question_index + 1) % len(self.questions)
        self.display_current_question()
            
    def save_current_question(self):
        """Save the current question's text and feedback back to the questions list."""
        if not self.questions or self.current_question_index < 0:
            return
            
        question = self.questions[self.current_question_index]
        
        # Get the current text from the editor
        current_text = self.question_editor.toPlainText()
        
        # Check if the text contains an answer section (starts with "Correct Answer:")
        correct_answer_pos = current_text.find("\n\nCorrect Answer:")
        
        if correct_answer_pos > 0:
            # Split the text into question and answer parts
            question_text = current_text[:correct_answer_pos].rstrip()
            answer_text = current_text[correct_answer_pos + 2:].strip()  # +2 to skip the double newline
            
            # Save the question text
            question['text'] = question_text
            
            # Update the answer
            if isinstance(question.get('answer'), dict):
                question['answer']['answer_text'] = answer_text
            else:
                question['answer'] = answer_text
        else:
            # No answer section found, save the whole text as the question
            question['text'] = current_text
            
        question['feedback'] = self.feedback_editor.toPlainText()

    def display_current_question(self):
        """Display the current question and its feedback in the editors."""
        if not self.questions or self.current_question_index < 0:
            self.question_editor.clear()
            self.feedback_editor.clear()
            self.context_display.clear()
            self.question_id_label.setText("Question ID: ")
            return
                
        question = self.questions[self.current_question_index]

        # Get question text and ensure it's a string
        question_text = question.get('text', '')
        if not isinstance(question_text, str):
            question_text = str(question_text)
            
        # Get answer text from the answer dictionary
        answer = question.get('answer', {})
        answer_text = answer.get('answer_text', '')
        
        # Display both question and answer text together
        display_text = question_text
        if answer_text:
            display_text = question_text.rstrip() + "\n\n" + answer_text
            
        self.question_editor.setPlainText(display_text)

        feedback = question.get('feedback', '')
        if not isinstance(feedback, str):
            feedback = str(feedback)
        self.feedback_editor.setPlainText(feedback)
        
        # Display context if available
        context = question.get('context', '')
        if not isinstance(context, str):
            context = str(context)
        self.context_display.setPlainText(context)

        # Update question ID label if available
        question_id = question.get('id', '')
        if question_id:
            self.question_id_label.setText(f"Question ID: {question_id}")
        else:
            # Require topic code for ID generation
            topic_code = self.llm_topic_code.text().strip().upper()
            if len(topic_code) != 3:
                QMessageBox.warning(self.parent, "Missing Topic Code", "A valid 3-letter topic code is required to generate a Question ID.")
                self.question_id_label.setText("Question ID: ")
                return
            difficulty = self.llm_difficulty_combo.currentIndex()
            question_id = f"{topic_code}{difficulty}_{self.current_question_index + 1}"
            self.question_id_label.setText(f"Question ID: {question_id}")
            question['id'] = question_id

    # Update window title to show current position
        if self.parent:
            self.parent.setWindowTitle(f"MCQ Generator - Question {self.current_question_index + 1} of {len(self.questions)}")

    def update_question_numbers(self):
        """Update question numbers and IDs based on current topic code and difficulty."""
        print("Starting update_question_numbers method")
        if not self.questions:
            print("No questions to update")
            return
            
        # Get current topic code and difficulty
        topic_code = self.current_topic_code
        difficulty = self.current_difficulty
        print(f"Topic code: {topic_code}, Difficulty: {difficulty}")
        
        try:
            # Update each question's ID
            print(f"Updating IDs for {len(self.questions)} questions")
            for i, question in enumerate(self.questions, 1):
                # Format: TOPICCODEDIFFICULTY_NUMBER (e.g., BIO12 for Biology, difficulty 1, question 2)
                question_id = f"{topic_code}{difficulty}_{i}"
                question['id'] = question_id
                question['number'] = i  # Also update the question number
                
            print("Question IDs updated successfully")
                
            # Update the display if we're currently showing a question
            if hasattr(self, 'current_question_index') and 0 <= self.current_question_index < len(self.questions):
                print("Updating display for current question")
                self.display_current_question()
        except Exception as e:
            print(f"Error in update_question_numbers: {str(e)}")
            import traceback
            traceback.print_exc()
        
        
    def select_llm_input_file(self):
        """Open a file dialog to select and load the LLM-generated questions file, requiring topic code and module selection."""
        topic_code = self.llm_topic_code.text().strip().upper()
        module = self.get_selected_module()
        if len(topic_code) != 3 or module is None:
            QMessageBox.warning(self.parent, "Missing Information", "Please enter topic code and module number first")
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self.parent,
            "Select LLM Questions File",
            "",
            "Text Files (*.txt);;All Files (*)"
        )
        if file_path:
            self.llm_input_file.setText(file_path)
            self.load_llm_output(file_path)
    
    def load_llm_output(self, file_path):
        """Load questions from an LLM output file."""
        # Reset context_file_path to ensure we don't reuse context from a previously loaded qBank
        if hasattr(self, 'context_file_path'):
            self.context_file_path = None
            
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # Parse the JSON content
        try:
            data = json.loads(content)
            questions = data.get('questions', [])
        except json.JSONDecodeError:
            # If not JSON, try to parse as text using llm_question_utils26
            from llm_question_utils26 import parse_llm_questions_file
            questions = parse_llm_questions_file(file_path)
            
        if not questions:
            QMessageBox.warning(self.parent, "No Questions Found", "No valid questions found in the file.")
            return
            
        # Get topic code and difficulty from GUI (no fallback to UNK or 0)
        topic_code = self.llm_topic_code.text().strip().upper()
        if len(topic_code) != 3:
            QMessageBox.warning(self.parent, "Missing Topic Code", "A valid 3-letter topic code is required to generate Question IDs.")
            return
        difficulty = self.llm_difficulty_combo.currentIndex()
    
        # Store the questions with IDs
        self.questions = []
        for i, q in enumerate(questions, 1):
            question_text = q.get('text', '')
            # Handle both old and new format answers
            answer = q.get('answer', '')
            answer_letter = ''
            full_answer_text = ''
            
            # Extract the answer letter
            if isinstance(answer, dict):
                # Dictionary format
                answer_letter = answer.get('letter', '') or answer.get('choice', '')
            elif isinstance(answer, str) and len(answer) == 1 and answer in 'ABCDE':
                # Single letter format
                answer_letter = answer
            elif isinstance(answer, str) and answer.startswith("Correct Answer:"):
                # Already formatted as "Correct Answer: X"
                match = re.search(r"Correct Answer:\s*([A-E])", answer)
                if match:
                    answer_letter = match.group(1)
            
            # Extract the answer text from the question if we have a valid letter
            if answer_letter and answer_letter in 'ABCDE' and question_text:
                # Look for the answer choice in the question text
                pattern = f"{answer_letter}\. ([^\n]+)"
                match = re.search(pattern, question_text)
                if match:
                    # Get the full text of the answer choice
                    answer_choice_text = match.group(1).strip()
                    full_answer_text = f"Correct Answer: {answer_letter}. {answer_choice_text}"
                else:
                    # If we can't find the answer text, just use the letter
                    full_answer_text = f"Correct Answer: {answer_letter}"
            elif isinstance(answer, str) and answer.startswith("Correct Answer:"):
                # Use the existing formatted answer
                full_answer_text = answer
            elif answer_letter:
                # Just use the letter if that's all we have
                full_answer_text = f"Correct Answer: {answer_letter}"
            
            # Create the answer dictionary
            answer_dict = {
                'choice': answer_letter,
                'answer_text': full_answer_text
            }
            
            self.questions.append({
                'text': question_text,
                'answer': answer_dict,
                'feedback': q.get('feedback', ''),
                'context': q.get('context', ''),
                'number': i,
                'id': f"{topic_code}{difficulty}_{i}"
            })
        self.current_topic_code = topic_code
        self.current_difficulty = difficulty
            
        # Display the first question
        self.current_question_index = 0
        self.display_current_question()           
        # Update status
        self.llm_status.append(f"Loaded {len(self.questions)} questions from {os.path.basename(file_path)}")
    
    def _load_questions_from_file(self, file_path):
        """Load questions from a file and return as a list of question dictionaries.
            
            This method handles files with a JSON header followed by questions in the format:
            ID. Question stem
            A. Answer choice A
            B. Answer choice B
            etc.
                
            Questions are separated by blank lines.
            """
        questions = []
        if not file_path or not os.path.exists(file_path):
            return questions
                
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                
            # Skip JSON header if present (enclosed in # { ... })
            if '#' in content and '}' in content:
                header_end = content.find('}') + 1
                content = content[header_end:].strip()
                
            # Use regex to find question blocks that start with an ID pattern like "RST0_1."
            # The pattern specifically looks for question IDs (like RST0_1) and not answer choices
            # Each block includes everything up to the next question ID or end of content
            question_pattern = r'([A-Z0-9]+_[0-9]+)\.(.*?)(?=\n\s*\n+[A-Z0-9]+_[0-9]+\.|$)'  
            question_matches = re.findall(question_pattern, content, re.DOTALL)
                
            # Process each question match
            for q_id, q_text in question_matches:
                # Clean up the text
                q_text = q_text.strip()
                if q_text:  # Only add if we have text
                    questions.append({
                        'id': q_id,
                        'text': q_text
                    })
                
            # Log the number of questions found
            print(f"Found {len(questions)} questions in {file_path}")
            self.llm_status.append(f"Found {len(questions)} questions in {os.path.basename(file_path)}")
                    
        except Exception as e:
            print(f"Error loading questions from {file_path}: {str(e)}")
            self.llm_status.append(f"Error loading questions: {str(e)}")
            import traceback
            traceback.print_exc()
            
        return questions
            
    def _load_answers_from_file(self, file_path):
        """Load answers from a file and return as a dictionary keyed by question ID.
        
        This method handles files with a JSON header followed by answers in the format:
        ID. Correct Answer: X. Explanation
        """
        answers = {}
        if not file_path or not os.path.exists(file_path):
            return answers
                
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                
            # Skip JSON header if present (enclosed in # { ... })
            if '#' in content and '}' in content:
                header_end = content.find('}') + 1
                content = content[header_end:].strip()
                
            # Split content into question blocks (separated by blank lines)
            blocks = re.split(r'\n\s*\n', content)
            
            for block in blocks:
                block = block.strip()
                if not block:
                    continue
                    
                # Extract question ID from the beginning of the block
                id_match = re.match(r'([A-Z0-9]+_[0-9]+)\.', block)
                if not id_match:
                    continue
                    
                q_id = id_match.group(1)
                
                # Try to extract answer (Pattern: "Correct Answer: B" or "Correct Answer: B. Text")
                # Use a single pattern that works for both cases
                answer_match = re.search(r'Correct Answer:\s*([A-E])(?:\.?\s*(.*))?', block)
                
                if answer_match:
                    choice = answer_match.group(1)
                    explanation = ""
                    if len(answer_match.groups()) > 1 and answer_match.group(2):
                        explanation = answer_match.group(2).strip()
                    
                    # Create the answer_text field with the full formatted answer
                    answer_text = f"Correct Answer: {choice}"
                    if explanation:
                        answer_text += f". {explanation}"
                    
                    answers[q_id] = {
                        'choice': choice.strip(),
                        'answer_text': answer_text
                    }
                else:
                    # Try to extract just the letter (Pattern: "B")
                    letter_match = re.search(r'([A-E])\s*$', block)
                    if letter_match:
                        choice = letter_match.group(1)
                        answers[q_id] = {
                            'choice': choice.strip(),
                            'answer_text': f"Correct Answer: {choice}"
                        }
            
            # Log the number of answers found
            self.llm_status.append(f"Found {len(answers)} answers in {os.path.basename(file_path)}")
                    
        except Exception as e:
            print(f"Error loading answers from {file_path}: {str(e)}")
            self.llm_status.append(f"Error loading answers: {str(e)}")
            import traceback
            traceback.print_exc()
            
        return answers
    
    def _load_feedback_from_file(self, file_path):
        """Load feedback from a file and return as a dictionary keyed by question ID."""
        feedback = {}
        if not file_path or not os.path.exists(file_path):
            return feedback
                
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                
            # Skip JSON header if present (enclosed in # { ... })
            if '#' in content and '}' in content:
                header_end = content.find('}') + 1
                content = content[header_end:].strip()
                
            # Split content into lines and process each line
            for line in content.split('\n'):
                line = line.strip()
                if not line:
                    continue
                    
                # Match question ID and feedback (format: "ID. Feedback text")
                match = re.match(r'^([A-Z0-9_]+)\.\s*(.*)', line)
                if match:
                    q_id = match.group(1)
                    feedback_text = match.group(2).strip()
                    feedback[q_id] = feedback_text
            
            # Log the number of feedback entries found
            self.llm_status.append(f"Found {len(feedback)} feedback entries in {os.path.basename(file_path)}")
                    
        except Exception as e:
            print(f"Error loading feedback from {file_path}: {str(e)}")
            self.llm_status.append(f"Error loading feedback: {str(e)}")
            import traceback
            traceback.print_exc()
            
        return feedback
        
    def _load_context_from_file(self, file_path):
        """Load context from a file and return as a dictionary keyed by question ID."""
        context = {}
        if not file_path or not os.path.exists(file_path):
            return context
                
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                
            # Skip JSON header if present (enclosed in # { ... })
            if '#' in content and '}' in content:
                header_end = content.find('}') + 1
                content = content[header_end:].strip()
                
            # Use regex to find context lines that match the pattern "ID. Context text"
            context_pattern = r'([A-Z0-9]+_[0-9]+)\. (.*?)(?=\n[A-Z0-9]+_[0-9]+\.|$)'
            context_matches = re.findall(context_pattern, content, re.DOTALL)
                
            # Process each context match
            for q_id, text in context_matches:
                context[q_id] = text.strip()
                
            # Log the number of context entries found
            print(f"Found {len(context)} context entries in {file_path}")
                
        except Exception as e:
            print(f"Error loading context file: {str(e)}")
            import traceback
            traceback.print_exc()
            
        return context
        
    def new_qbank(self):
        """Create a new QBank with the current questions."""
        print("Starting new_qbank method")
        
        if not self.questions:
            print("No questions to save")
            QMessageBox.warning(self.parent, "No Questions", "No questions to save. Please load questions first.")
            return False
            
        # Get module, topic code, and difficulty with validation
        module = self.get_selected_module()
        print(f"Selected module: {module}")
        if module is None:
            print("Module is None, showing warning")
            QMessageBox.warning(self.parent, "Missing Module", "Please select a module number.")
            return False
                
        topic_code = self.llm_topic_code.text().strip().upper()
#        print(f"Topic code: {topic_code}")
#        print(f"Topic code length: {len(topic_code)}")
        
        # Check if questions exist
#        print(f"self.questions exists: {hasattr(self, 'questions')}")
#        if hasattr(self, 'questions'):
#            print(f"Number of questions: {len(self.questions)}")
#            if self.questions:
#                print(f"First question ID: {self.questions[0].get('id', 'No ID')}")
        
        difficulty = self.llm_difficulty_combo.currentIndex()
#        print(f"Difficulty: {difficulty}")
                
        # Update class variables for question ID generation
        self.current_topic_code = topic_code
        self.current_difficulty = difficulty
#       print("Updated class variables for question ID generation")
                
        # Check if we have questions to process
#        print(f"Number of questions: {len(self.questions) if hasattr(self, 'questions') and self.questions else 0}")
        if not hasattr(self, 'questions') or not self.questions:
            print("No questions available to save")
            QMessageBox.warning(self.parent, "No Questions", "No questions loaded or available to save.")
            return False
                
        # Skip JSON header if present (enclosed in # { ... })
        if '#' in content and '}' in content:
            header_end = content.find('}') + 1
            content = content[header_end:].strip()
                
        # Use regex to find answer lines that match the pattern "ID. Answer"
        # For example: "RST0_1. A The hydrophobic interior"
        answer_pattern = r'([A-Z0-9]+_[0-9]+)\. ([A-E])\s+(.*?)(?=\n[A-Z0-9]+_[0-9]+\.|$)'
        answer_matches = re.findall(answer_pattern, content, re.DOTALL)
                
        # Process each answer match
        for q_id, choice, explanation in answer_matches:
            # Store the answer choice and explanation
            answers[q_id] = {
                'choice': choice.strip(),
                'explanation': explanation.strip()
            }
                
        # Log the number of answers found
        print(f"Found {len(answers)} answers in {file_path}")
        self.llm_status.append(f"Found {len(answers)} answers in {os.path.basename(file_path)}")
                    
        return answers
        
    def new_qbank(self):
        """Create a new QBank with the current questions."""
#        print("Starting new_qbank method")
        
        if not self.questions:
            print("No questions to save")
            QMessageBox.warning(self.parent, "No Questions", "No questions to save. Please load questions first.")
            return False
            
        # Get module, topic code, and difficulty with validation
        module = self.get_selected_module()
#        print(f"Selected module: {module}")
        if module is None:
            print("Module is None, showing warning")
            QMessageBox.warning(self.parent, "Missing Module", "Please select a module number.")
            return False
                
        topic_code = self.llm_topic_code.text().strip().upper()
                
        difficulty = self.llm_difficulty_combo.currentIndex()
                
        # Update class variables for question ID generation
        self.current_topic_code = topic_code
        self.current_difficulty = difficulty
        self.update_question_numbers()
                    
        # Get the base directory from course info panel if available
        course_folder = ""
        if hasattr(self, 'parent') and hasattr(self.parent, 'course_info_panel') and \
           hasattr(self.parent.course_info_panel, 'course_folder_input'):
            course_folder = self.parent.course_info_panel.course_folder_input.text().strip()
            print(f"Course folder from panel: '{course_folder}'")
        if not course_folder:
            course_folder = os.path.expanduser("~/textProcessing/NBIO 140B")
            print(f"Using default course folder: '{course_folder}'")
                
        # Create the output directory structure
        output_dir = os.path.join(course_folder, f"module{module}", topic_code, "QBanks")
        questions_dir = os.path.join(output_dir, "Questions")
        answers_dir = os.path.join(output_dir, "Answers")
        feedback_dir = os.path.join(output_dir, "Feedback")
        context_dir = os.path.join(output_dir, "Context")
            
        # Create directories if they don't exist
        try:
            os.makedirs(questions_dir, exist_ok=True)
            os.makedirs(answers_dir, exist_ok=True)
            os.makedirs(feedback_dir, exist_ok=True)
            os.makedirs(context_dir, exist_ok=True)
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.warning(self.parent, "Directory Error", f"Could not create output directories: {str(e)}")
            return False
        
        # Now generate the file paths
        try:
            # Generate base filename with date
            date_str = datetime.now().strftime('%b%d%y')
            
            questions_path = os.path.join(questions_dir, f"M{module}_{topic_code}Q{difficulty}_{date_str}.txt")
            answers_path = os.path.join(answers_dir, f"M{module}_{topic_code}A{difficulty}_{date_str}.txt")
            feedback_path = os.path.join(feedback_dir, f"M{module}_{topic_code}F{difficulty}_{date_str}.txt")
            context_path = os.path.join(context_dir, f"M{module}_{topic_code}C{difficulty}_{date_str}.txt")
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.warning(self.parent, "Path Error", f"Could not generate file paths: {str(e)}")
            return False
                
        # Check if files already exist
        questions_exists = os.path.exists(questions_path)
        answers_exists = os.path.exists(answers_path)
        feedback_exists = os.path.exists(feedback_path)
        context_exists = os.path.exists(context_path)
        
        # If any files exist, ask user if they want to overwrite or create new files
        if any([questions_exists, answers_exists, feedback_exists, context_exists]):
            # Show a dialog explaining the situation and offering options
            try:
                msg_box = QMessageBox(self.parent)
                msg_box.setWindowTitle('Files Exist')
            except Exception as e:
                import traceback
                traceback.print_exc()
                return False
            
            msg_box.setText('One or more QBank files already exist.')
            msg_box.setInformativeText('Would you like to overwrite the existing files or create new files?')
                    
            # Add custom buttons
            overwrite_btn = msg_box.addButton('Overwrite', QMessageBox.ButtonRole.AcceptRole)
            new_file_btn = msg_box.addButton('New File', QMessageBox.ButtonRole.ActionRole)
            cancel_btn = msg_box.addButton('Cancel', QMessageBox.ButtonRole.RejectRole)
                        
            msg_box.exec()
                        
            # Handle the user's choice
            if msg_box.clickedButton() == cancel_btn:
                return False
            elif msg_box.clickedButton() == new_file_btn:
                # Generate a new unique filename by adding a counter
                counter = 1
                # Define base_name for the file naming pattern
                base_name = f"M{module}_{topic_code}"
                while any(os.path.exists(p) for p in [questions_path, answers_path, feedback_path, context_path]):
                    questions_path = os.path.join(questions_dir, f"{base_name}Q{difficulty}_{date_str}_{counter}.txt")
                    answers_path = os.path.join(answers_dir, f"{base_name}A{difficulty}_{date_str}_{counter}.txt")
                    feedback_path = os.path.join(feedback_dir, f"{base_name}F{difficulty}_{date_str}_{counter}.txt")
                    context_path = os.path.join(context_dir, f"{base_name}C{difficulty}_{date_str}_{counter}.txt")
                    counter += 1
                # Show file dialog to select a new location for the questions file
                # The answers and feedback files will be saved in corresponding directories
                new_path, _ = QFileDialog.getSaveFileName(
                    self.parent,
                    'Save QBank Questions File',
                    questions_path,
                    'Text Files (*.txt);;All Files (*)'
                )
                
                if not new_path:
                    return False  # User canceled the file dialog
            else:
                # If we're overwriting, just use the existing paths
                new_path = questions_path
                        
            # Update paths based on the new location
            questions_path = new_path
                        
            # Extract directory and filename components
            new_dir = os.path.dirname(new_path)
            new_filename = os.path.basename(new_path)
                        
            # Create a proper directory structure for the new file location
            # If the user selected a path that doesn't end with 'Questions', create a Questions directory
            if not new_dir.endswith('Questions'):
                # Create a new Questions directory at the selected location
                parent_dir = new_dir
                new_dir = os.path.join(parent_dir, 'Questions')
                os.makedirs(new_dir, exist_ok=True)
                            
                # Update the questions path to use the new Questions directory
                questions_path = os.path.join(new_dir, os.path.basename(new_path))
            else:
                parent_dir = os.path.dirname(new_dir)
                        
                # Create new Answers, Feedback, and Context directories at the same level as Questions
                answers_dir = os.path.join(parent_dir, 'Answers')
                feedback_dir = os.path.join(parent_dir, 'Feedback')
                context_dir = os.path.join(parent_dir, 'Context')
                        
                os.makedirs(answers_dir, exist_ok=True)
                os.makedirs(feedback_dir, exist_ok=True)
                os.makedirs(context_dir, exist_ok=True)
                        
                # Always update the answers and feedback paths when using a new location
                # Extract the base name and extension from the new questions path
                new_basename = os.path.basename(new_path)
                base_name, ext = os.path.splitext(new_basename)
                        
                # Check if the filename follows our standard format with Q{difficulty}
                if 'Q' in base_name and '_' in base_name:
                    # Extract the base part before 'Q' and the suffix after difficulty
                    parts = base_name.split('Q', 1)
                    base_part = parts[0]
                    suffix_part = parts[1].split('_', 1)[1] if '_' in parts[1] else ''
                            
                    # Create corresponding answer, feedback, and context filenames
                    answers_path = os.path.join(answers_dir, f"{base_part}A{difficulty}_{suffix_part}{ext}")
                    feedback_path = os.path.join(feedback_dir, f"{base_part}F{difficulty}_{suffix_part}{ext}")
                    context_path = os.path.join(context_dir, f"{base_part}C{difficulty}_{suffix_part}{ext}")
                else:
                    # If the user completely changed the filename format, adapt accordingly
                    answers_path = os.path.join(answers_dir, f"{base_name}_answers{ext}")
                    feedback_path = os.path.join(feedback_dir, f"{base_name}_feedback{ext}")
                    context_path = os.path.join(context_dir, f"{base_name}_context{ext}")
                            
                    # Update the questions path to the new path
                    questions_path = new_path
        
        # Create headers for each file
        print("Creating file headers")
        llm_topic_name = self.llm_topic_name.text().strip()
        print(f"Topic name: {llm_topic_name}")
        header = {
            'author': 'Sacha Nelson',
            'instructors': 'Sacha Nelson and Christine Grienberger',
            'course number': 'NBIO 140b',
            'course title': 'Principles of Neuroscience',
            'topic': llm_topic_name,
            'qTop': topic_code,
            'module': module,
            'qType': 'LLM Generated MCQ',
            'numQ': len(self.questions),
            'qIDstrt': self.questions[0]['id'],
            'qIDend': self.questions[-1]['id'],
            'element': 'qbank',
            'date': datetime.now().strftime('%m%d-%y'),
            'difficulty': difficulty,
            'input file': self.llm_input_file.text().strip(),
            'timestamp': datetime.now().isoformat()
        }
        print("Header created successfully")
        
        # Write questions file
        print(f"Writing questions file to: {questions_path}")
        try:
            with open(questions_path, 'w', encoding='utf-8') as f:
                print("Questions file opened successfully")
                f.write(f"# {json.dumps(header, indent=2)}\n\n")
                print(f"Writing {len(self.questions)} questions")
                for q in self.questions:
                    # Get the question text with answer choices included
                    question_text = q.get('text', '')
                    
                    # Only remove the "Correct Answer:" pattern if present
                    # This removes the answer key but preserves the answer choices
                    question_text = re.sub(r'\n\s*Correct Answer:.*$', '', question_text, flags=re.DOTALL)

                    # Clean up any extra newlines
                    question_text = re.sub(r'\n{3,}', '\n\n', question_text).strip()
                            
                    # Write the clean question text
                    f.write(f"{q['id']}. {question_text}\n\n")
                print("Questions file written successfully")
        except Exception as e:
            print(f"Error writing questions file: {str(e)}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self.parent, "Error Creating QBank", f"Failed to write questions file: {str(e)}")
            return False
        
        # Write answers file
        print(f"Writing answers file to: {answers_path}")
        try:
            with open(answers_path, 'w', encoding='utf-8') as f:
                print("Answers file opened successfully")
                f.write(f"# {json.dumps(header, indent=2)}\n\n")
                # First, extract answer text from questions if needed
                self._enhance_answers_with_text()
                
                for q in self.questions:
                    if 'answer' in q:
                        answer = q['answer']
                        # Write the answer_text field directly
                        f.write(f"{q['id']}. {answer.get('answer_text', '')}\n\n")
                    else:
                        f.write(f"{q['id']}. \n\n")
                print("Answers file written successfully")
        except Exception as e:
            print(f"Error writing answers file: {str(e)}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self.parent, "Error Creating QBank", f"Failed to write answers file: {str(e)}")
            return False
        
        # Write feedback file
        print(f"Writing feedback file to: {feedback_path}")
        try:
            with open(feedback_path, 'w', encoding='utf-8') as f:
                print("Feedback file opened successfully")
                f.write(f"# {json.dumps(header, indent=2)}\n\n")
                for q in self.questions:
                    feedback = q.get('feedback', '')
                    f.write(f"{q['id']}. {feedback}\n\n")
                print("Feedback file written successfully")
        except Exception as e:
            print(f"Error writing feedback file: {str(e)}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self.parent, "Error Creating QBank", f"Failed to write feedback file: {str(e)}")
            return False
            
        # Write context file
        print(f"Writing context file to: {context_path}")
        try:
            with open(context_path, 'w', encoding='utf-8') as f:
                print("Context file opened successfully")
                f.write(f"# {json.dumps(header, indent=2)}\n\n")
                for q in self.questions:
                    context = q.get('context', '')
                    f.write(f"{q['id']}. {context}\n\n")
                print("Context file written successfully")
        except Exception as e:
            print(f"Error writing context file: {str(e)}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self.parent, "Error Creating QBank", f"Failed to write context file: {str(e)}")
            return False
        
        # Update status
        print("Updating status in UI")
        self.llm_status.append(f"Saved {len(self.questions)} questions to {os.path.basename(questions_path)}")
        
        # Determine the actual save location to display in the message
        # If the user chose a different location, use that instead of the original output_dir
        display_location = os.path.dirname(questions_path)
        print(f"Display location: {display_location}")
        if 'Questions' in display_location:
            display_location = os.path.dirname(display_location)  # Go up one level from Questions directory
            print(f"Adjusted display location: {display_location}")
        
        print("Showing success message dialog")
        try:
            QMessageBox.information(
                self.parent,
                'QBank Created',
                f'Successfully created QBank with {len(self.questions)} questions.\n\n'
                f'Files saved to:\n{display_location}'
            )
            print("Success message shown")
        except Exception as e:
            print(f"Error showing success message: {str(e)}")
            import traceback
            traceback.print_exc()
            
        print("new_qbank method completed successfully")
        return True
        
    def load_qbank(self):
        """Load questions from a QBank."""
        # Get the base directory for QBanks
        base_dir = os.path.expanduser('~')
        if hasattr(self, 'current_output_dir') and self.current_output_dir:
            base_dir = self.current_output_dir
        
        # Show file dialog to select Questions file
        default_dir = os.path.join(base_dir, 'QBanks', 'Questions')
        questions_dir = os.path.join(default_dir, 'Questions')
            
        # Use the questions directory if it exists, otherwise use the default directory
        # If neither exists, fall back to the user's home directory
        start_dir = questions_dir if os.path.exists(questions_dir) else default_dir
        if not os.path.exists(start_dir):
            start_dir = os.path.expanduser('~')
                
        # Show file dialog to select a question file
        file_path, _ = QFileDialog.getOpenFileName(
            self.parent,  # Use the parent widget instead of self
            "Open QBank Questions File",
            start_dir,
            "Text Files (*.txt);;All Files (*)"
        )
        
        if not file_path:
            return  # User cancelled
            
        # Get the base directory (one level up from the Questions directory)
        questions_dir = os.path.dirname(file_path)
        qbank_dir = os.path.dirname(questions_dir)  # Parent of Questions directory
        answers_dir = os.path.join(qbank_dir, 'Answers')
        feedback_dir = os.path.join(qbank_dir, 'Feedback')
        context_dir = os.path.join(qbank_dir, 'Context')
            
        # Ensure required directories exist
        os.makedirs(answers_dir, exist_ok=True)
        os.makedirs(feedback_dir, exist_ok=True)
        os.makedirs(context_dir, exist_ok=True)
            
        # Get the base filename (without path)
        file_name = os.path.basename(file_path)
            
        # Find the position of the first underscore
        answers_file = None
        feedback_file = None
        context_file = None
            
        try:
            underscore_pos = file_name.find('_')
            
            if underscore_pos != -1 and len(file_name) > underscore_pos + 4:
                # Calculate the position where 'Q' should be (4 chars after first underscore)
                q_pos = underscore_pos + 4
                
                if q_pos < len(file_name) and file_name[q_pos] == 'Q':
                    # Replace the Q at the calculated position with A/F/C
                    answers_file = file_name[:q_pos] + 'A' + file_name[q_pos+1:]
                    feedback_file = file_name[:q_pos] + 'F' + file_name[q_pos+1:]
                    context_file = file_name[:q_pos] + 'C' + file_name[q_pos+1:]
                
            if not answers_file or not feedback_file:
                raise ValueError("Could not determine answer/feedback filenames from pattern")                    
            # Build full paths
            answers_path = os.path.join(answers_dir, answers_file)
            feedback_path = os.path.join(feedback_dir, feedback_file)
            context_path = os.path.join(context_dir, context_file)
            
            # Store the context path as a class attribute for later use
            self.context_file_path = context_path
                
        except Exception as e:
            QMessageBox.critical(
                self.parent,
                'Error Loading QBank',
                f'Could not determine answer/feedback filenames. Expected format: M*_*Q*_*.txt\n\nError: {str(e)}'
            )
            return
            
        # Load questions using the robust method
        questions = self._load_questions_from_file(file_path)

        # Load answers, feedback, and context if available
        answers = self._load_answers_from_file(answers_path) if os.path.exists(answers_path) else {}
        feedback = self._load_feedback_from_file(feedback_path) if os.path.exists(feedback_path) else {}
        context = self._load_context_from_file(context_path) if os.path.exists(context_path) else {}

        # Attach answers, feedback, and context by question ID
        for q in questions:
            q_id = q.get('id')
            if q_id and q_id in answers:
                q['answer'] = answers[q_id]
            if q_id and q_id in feedback:
                q['feedback'] = feedback[q_id]
            if q_id and q_id in context:
                q['context'] = context[q_id]
            
        # Update the questions list
        self.questions = questions
        self.current_question_index = 0 if questions else -1
            
        if not questions:
            QMessageBox.warning(self.parent, 'No Questions', 'The selected file does not contain any questions.')
            return
            
        # Display the first question
        self.display_current_question()
            
        # Update status with more detailed information
        num_with_answers = sum(1 for q in questions if 'answer' in q)
        num_with_feedback = sum(1 for q in questions if 'feedback' in q)
            
        status_msg = f"Loaded {len(questions)} questions from {os.path.basename(file_path)}\n"
        status_msg += f"Questions with answers: {num_with_answers}, with feedback: {num_with_feedback}"
        self.llm_status.append(status_msg)
            
        # Extract module, topic code, and difficulty from filename
        filename = os.path.basename(file_path)
        # Initialize variables with default values
        module_num = None
        topic_code = ""
        difficulty = 0
        
        # Look for pattern like M1_ABC_...
        match = re.match(r'M(\d+)_([A-Za-z]{3})Q?(\d+)_', filename)
        if match:
            module_num = int(match.group(1))
            topic_code = match.group(2).upper()
            difficulty = int(match.group(3))
        else:
            self.llm_status.append(f"Warning: Could not extract module, topic code, and difficulty from filename: {filename}")
            print(f"Warning: Could not extract module, topic code, and difficulty from filename: {filename}")
                
        # Update UI elements if we have a valid module number
        if module_num is not None:
            # Find the index that corresponds to the module number
            module_idx = self.llm_module_num.findText(str(module_num))
            if module_idx >= 0:
                self.llm_module_num.setCurrentIndex(module_idx)
                    
        # Block signals to prevent triggering events while updating
        self.llm_topic_code.blockSignals(True)
        self.llm_topic_code.setText(topic_code)
        self.llm_topic_code.blockSignals(False)
                
        self.llm_difficulty_combo.setCurrentIndex(difficulty)  # Set the difficulty in the UI
                
        # Update internal state
        self.current_topic_code = topic_code
        self.current_difficulty = difficulty
                
        # Store the base path for saving
        qbank_base_dir = os.path.dirname(os.path.dirname(file_path))
        if hasattr(self, '_full_basepath'):
            self._full_basepath = qbank_base_dir
                
        # Update the output directory based on the parsed values
        if hasattr(self, 'update_output_directory'):
            self.update_output_directory()
                
        # Force update the UI to reflect the new values
        self.llm_topic_code.editingFinished.emit()
                    
        # Ensure the UI is updated by forcing a repaint
        self.llm_topic_code.update()
        self.llm_topic_code.repaint()
                
        # Debug output
        print(f"UI Topic Code: {self.llm_topic_code.text()}")
        print(f"Current Topic Code: {self.current_topic_code}")
                
        # Update question IDs with the loaded topic code and difficulty
        self.update_question_numbers()
                
        # Update output directory
        self.update_output_directory()
                
        # Debug output
        print(f"Loaded topic code: {topic_code}, difficulty: {difficulty}")
        print(f"Current UI values - topic: {self.llm_topic_code.text()}, difficulty: {self.llm_difficulty_combo.currentIndex()}")                       
        # If we get here, loading was successful
        self.llm_status.append(f"Successfully loaded QBank from {os.path.dirname(os.path.dirname(file_path))}")    

    def add_to_qbank(self):
        """Add current questions to an existing QBank."""
        if not self.questions:
            QMessageBox.warning(self.parent, "No Questions", "No questions to add. Please load or create questions first.")
            return False
            
        try:
            # Set default directory for file dialog
            default_dir = os.path.join(os.path.expanduser('~'), 'MCQ_QuestionBanks')
            questions_dir = os.path.join(default_dir, 'Questions')
            
            # Use the questions directory if it exists, otherwise use the default directory
            # If neither exists, fall back to the user's home directory
            start_dir = questions_dir if os.path.exists(questions_dir) else default_dir
            if not os.path.exists(start_dir):
                start_dir = os.path.expanduser('~')
                
            # Show file dialog to select a question file
            file_path, _ = QFileDialog.getOpenFileName(
                self.parent,  # Use parent widget instead of self
                "Select QBank Questions File",
                start_dir,
                "Text Files (*.txt);;All Files (*)"
            )
            
            if not file_path:
                return False  # User cancelled
                
            # Get the base directory (one level up from the Questions directory)
            qbank_dir = os.path.dirname(os.path.dirname(file_path))
            file_name = os.path.basename(file_path)
            
            # Parse module, topic code, and difficulty from filename
            match = re.match(r'M(\d+)_([A-Za-z]{3})Q?(\d+)', file_name)
            if not match:
                QMessageBox.warning(self.parent, "Invalid Filename", 
                                  "Could not parse module, topic code, and difficulty from filename.")
                return False
                
            module = int(match.group(1))
            topic_code = match.group(2).upper()
            difficulty = int(match.group(3))
            
            # Find corresponding answer and feedback files
            answers_dir = os.path.join(qbank_dir, 'Answers')
            feedback_dir = os.path.join(qbank_dir, 'Feedback')
            context_dir = os.path.join(qbank_dir, 'Context')
            
            # Ensure required directories exist
            os.makedirs(answers_dir, exist_ok=True)
            os.makedirs(feedback_dir, exist_ok=True)
            os.makedirs(context_dir, exist_ok=True)
            
            # Extract base name and date from the question file
            base_match = re.match(r'(M\d+_[A-Za-z]{3})Q?\d+_(.*)\.txt', file_name)
            if base_match:
                base_prefix = base_match.group(1)
                date_suffix = base_match.group(2)
                
                # Construct answer, feedback, and context file paths
                answers_path = os.path.join(answers_dir, f"{base_prefix}A{difficulty}_{date_suffix}.txt")
                feedback_path = os.path.join(feedback_dir, f"{base_prefix}F{difficulty}_{date_suffix}.txt")
                context_path = os.path.join(context_dir, f"{base_prefix}C{difficulty}_{date_suffix}.txt")
            else:
                # Fallback: try to find any matching answer and feedback files
                def get_latest_file(prefix, pattern):
                    dir_path = os.path.join(qbank_dir, prefix)
                    if not os.path.exists(dir_path):
                        return None
                            
                    files = [f for f in os.listdir(dir_path) 
                                if re.match(pattern, f) and f.endswith('.txt')]
                    if not files:
                        return None
                            
                    # Sort by modification time, newest first
                    files.sort(key=lambda x: os.path.getmtime(os.path.join(dir_path, x)), reverse=True)
                    return os.path.join(dir_path, files[0])
                
                # Pattern to match corresponding answer, feedback, and context files
                file_pattern = f"M{module}_{topic_code}[AFC]{difficulty}.*\.txt"
                    
                answers_path = get_latest_file('Answers', file_pattern)
                feedback_path = get_latest_file('Feedback', file_pattern)
                context_path = get_latest_file('Context', file_pattern)
            
            if not (answers_path and feedback_path and os.path.exists(answers_path) and os.path.exists(feedback_path)):
                QMessageBox.warning(self.parent, "Missing Files", 
                                  "Could not find matching answer and feedback files. Please ensure they exist.")
                return False
                
        # Load existing questions, answers, feedback, and context
            existing_questions = self._load_questions_from_file(file_path)
            existing_answers = self._load_answers_from_file(answers_path)
            existing_feedback = self._load_feedback_from_file(feedback_path)
            existing_context = self._load_context_from_file(context_path) if os.path.exists(context_path) else {}
            
            if not existing_questions:
                QMessageBox.warning(self.parent, "No Questions Found", 
                                  "No questions found in the selected file.")
                return False
            
            # Find the highest question number in the existing IDs
            highest_num = 0
            id_pattern = re.compile(r'[A-Z]{3}\d+_(\d+)')
            
            for q in existing_questions:
                q_id = q['id']
                match = id_pattern.match(q_id)
                if match:
                    num = int(match.group(1))
                    highest_num = max(highest_num, num)
                else:
                    # Try alternative pattern without topic code
                    alt_match = re.match(r'\d+_(\d+)', q_id)
                    if alt_match:
                        num = int(alt_match.group(1))
                        highest_num = max(highest_num, num)
            
            # Update existing questions with answers, feedback, and context
            for q in existing_questions:
                q_id = q['id']
                if q_id in existing_answers:
                    q['answer'] = existing_answers[q_id]
                if q_id in existing_feedback:
                    q['feedback'] = existing_feedback[q_id]
                if q_id in existing_context:
                    q['context'] = existing_context[q_id]
            
            # Add new questions with consecutive IDs
            start_num = highest_num + 1
            for i, question in enumerate(self.questions, start=start_num):
                # Create a copy to avoid modifying the original
                new_q = question.copy()
                # Generate new ID with the correct format
                new_q['id'] = f"{topic_code}{difficulty}_{i}"
                existing_questions.append(new_q)
            
            # Write questions back to files
            # Create headers for each file
            llm_topic_name = self.llm_topic_name.text().strip()
            header = {
                'author': 'Sacha Nelson',
                'instructors': 'Sacha Nelson and Christine Grienberger',
                'course number': 'NBIO 140b',
                'course title': 'Principles of Neuroscience',
                'topic': llm_topic_name,
                'qTop': topic_code,
                'module': module,
                'qType': 'LLM Generated MCQ',
                'numQ': len(existing_questions),
                'qIDstrt': existing_questions[0]['id'],
                'qIDend': existing_questions[-1]['id'],
                'element': 'qbank',
                'date': datetime.now().strftime('%m%d-%y'),
                'difficulty': difficulty,
                'input file': self.llm_input_file.text().strip(),
                'timestamp': datetime.now().isoformat()
            }
            
            # Write questions file
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(f"# {json.dumps(header, indent=2)}\n\n")
                    for q in existing_questions:
                        # Get the question text with answer choices included
                        question_text = q.get('text', '')
                            
                        # Only remove the "Correct Answer:" pattern if present
                        # This removes the answer key but preserves the answer choices
                        question_text = re.sub(r'\n\s*Correct Answer:.*$', '', question_text, flags=re.DOTALL)

                        # Clean up any extra newlines
                        question_text = re.sub(r'\n{3,}', '\n\n', question_text).strip()
                            
                        # Write the clean question text
                        f.write(f"{q['id']}. {question_text}\n\n")
            except Exception as e:
                QMessageBox.critical(
                    self.parent,
                    'Error',
                    f'Failed to write questions file: {str(e)}'
                )
                return False
            
            # Write answers file
            try:
                with open(answers_path, 'w', encoding='utf-8') as f:
                    f.write(f"# {json.dumps(header, indent=2)}\n\n")
                    # We don't need to enhance answers here as they've already been processed
                    # when we added the new questions to existing_questions
                    # This prevents duplicate processing that could lead to answer duplication
                    
                    # Make sure all questions have enhanced answers
                    self._enhance_answers_with_text()
                    
                    for q in existing_questions:
                        if 'answer' in q:
                            answer = q['answer']
                            # Write the answer_text field directly
                            f.write(f"{q['id']}. {answer.get('answer_text', '')}\n\n")
                        else:
                            f.write(f"{q['id']}. \n\n")
            except Exception as e:
                QMessageBox.critical(
                    self.parent,
                    'Error',
                    f'Failed to write answers file: {str(e)}'
                )
                return False
            
            # Write feedback file
            try:
                with open(feedback_path, 'w', encoding='utf-8') as f:
                    f.write(f"# {json.dumps(header, indent=2)}\n\n")
                    for q in existing_questions:
                        feedback = q.get('feedback', '')
                        f.write(f"{q['id']}. {feedback}\n\n")
            except Exception as e:
                QMessageBox.critical(
                    self.parent,
                    'Error',
                    f'Failed to write feedback file: {str(e)}'
                )
                return False
                
            # Write context file
            try:
                with open(context_path, 'w', encoding='utf-8') as f:
                    f.write(f"# {json.dumps(header, indent=2)}\n\n")
                    for q in existing_questions:
                        context = q.get('context', '')
                        f.write(f"{q['id']}. {context}\n\n")
            except Exception as e:
                QMessageBox.critical(
                    self.parent,
                    'Error',
                    f'Failed to write context file: {str(e)}'
                )
                return False
            
            QMessageBox.information(
                self.parent,  # Use parent widget instead of self
                'Questions Added',
                f'Successfully added {len(self.questions)} questions to QBank.\n\n'
                f'Total questions: {len(existing_questions)}\n'
                f'Files updated: \n{os.path.basename(file_path)}\n{os.path.basename(answers_path)}\n{os.path.basename(feedback_path)}\n{os.path.basename(context_path)}'
            )
            return True
    
        except Exception as e:
            QMessageBox.critical(
                self.parent,  # Use parent widget instead of self
                'Error',
                f'Failed to add questions to QBank: {str(e)}'
            )
            import traceback
            traceback.print_exc()
            return False
    
    def _find_qbank_files(self, base_dir, module, topic_code, difficulty):
        """Find QBank files matching the given parameters."""
        questions_dir = os.path.join(base_dir, 'Questions')
        answers_dir = os.path.join(base_dir, 'Answers')
        feedback_dir = os.path.join(base_dir, 'Feedback')
        context_dir = os.path.join(base_dir, 'Context')
        
        pattern = f"M{module}_{topic_code}*{difficulty}_*.txt"
        
        try:
            questions_files = [f for f in os.listdir(questions_dir) if f.startswith(f"M{module}_{topic_code}") and f.endswith(f"{difficulty}_.txt")]
            answers_files = [f for f in os.listdir(answers_dir) if f.startswith(f"M{module}_{topic_code}") and f.endswith(f"{difficulty}_.txt")]
            feedback_files = [f for f in os.listdir(feedback_dir) if f.startswith(f"M{module}_{topic_code}") and f.endswith(f"{difficulty}_.txt")]
            
            # Context files are optional, so we don't check if they exist
            context_files = []
            if os.path.exists(context_dir):
                context_files = [f for f in os.listdir(context_dir) if f.startswith(f"M{module}_{topic_code}") and f.endswith(f"{difficulty}_.txt")]
            
            if not questions_files or not answers_files or not feedback_files:
                return None
                
            # Sort by date (newest first) and get the most recent
            questions_files.sort(reverse=True)
            answers_files.sort(reverse=True)
            feedback_files.sort(reverse=True)
            
            result = {
                'questions': os.path.join(questions_dir, questions_files[0]),
                'answers': os.path.join(answers_dir, answers_files[0]),
                'feedback': os.path.join(feedback_dir, feedback_files[0])
            }
            
            # Add context file path if available
            if context_files:
                context_files.sort(reverse=True)
                result['context'] = os.path.join(context_dir, context_files[0])
                
            return result
        except Exception as e:
            print(f"Error finding QBank files: {str(e)}")
            return None
    
    def select_base_path(self):
        """Open file dialog to select base path."""
        current_path = self.base_path_edit.text()
        path = QFileDialog.getExistingDirectory(
            self, 
            "Select Base Directory",
            current_path,
            QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontUseNativeDialog
        )
        if path:
            self.base_path_edit.setText(path)
            self.defaults['basePath'] = path   
    
    def update_question_id_preview(self):
        """Update the preview of question IDs based on current topic code and module."""
        try:
            topic_code = self.llm_topic_code.text().strip().upper()
            module = self.get_selected_module()
            
            if topic_code and module is not None:
                # Generate a sample question ID
                sample_id = f"{topic_code}{module}_1"
                if hasattr(self, 'question_id_preview'):
                    self.question_id_preview.setText(f"Sample ID: {sample_id}")
        except Exception as e:
            print(f"Error updating question ID preview: {str(e)}")
    
    def update_output_directory(self):
        """Build and display the output directory path based on module, topic code, and difficulty."""
        try:
            # Get topic code and module
            topic_code = self.llm_topic_code.text().strip().upper()
            module = self.get_selected_module()
            
            if not topic_code or module is None:
                self.output_dir_label.setText("(Please enter topic code and select module)")
                return
                
            # Get difficulty
            difficulty_text = self.llm_difficulty_combo.currentText()
            difficulty = difficulty_text[0]  # Extract the first character (the number)
            
            # Get the course folder from the course info panel if available
            course_folder = ""
            if hasattr(self, 'parent') and hasattr(self.parent, 'course_info_panel') and \
               hasattr(self.parent.course_info_panel, 'course_folder_input'):
                course_folder = self.parent.course_info_panel.course_folder_input.text().strip()
            
            # Build the output path: course_folder/module{module}/{topic_code}/QBanks
            # This matches the expected structure: ~/course_folder/module{module}/{topic_code}/QBanks/
            module_dir = f"module{module}"
            base_path = os.path.join(module_dir, topic_code, "QBanks")
            
            # Store the full path including course folder
            if course_folder:
                self._full_basepath = os.path.join(course_folder, base_path)
                # Display the full path including course folder
                self.output_dir_label.setText(self._full_basepath)
            else:
                self._full_basepath = base_path
                # Display the path without course folder since it's not available
                self.output_dir_label.setText("(Course folder not set) " + base_path)
            
            # Update the question ID preview
            self.update_question_id_preview()
            
        except Exception as e:
            print(f"Error updating output directory: {str(e)}")
            self.output_dir_label.setText(f"(Error: {str(e)})")
            
        # Update the output directory edit field if it exists
        if hasattr(self, 'output_dir_edit') and self._full_basepath:
            self.output_dir_edit.setText(self._full_basepath)

    def _get_context_file_path(self):
        """Determine the context file path based on the current state.
        
        There are two cases to handle:
        1. Questions loaded from qBank - use the same context path used to load the context
        2. Questions loaded from LLM input file - use the same logic as new_qbank
        
        Returns:
            str: The context file path, or None if it cannot be determined
        """
        # Case 1: Context file path already set (from loading a qBank)
        # Note: context_file_path is reset to None when loading a new LLM input file
        # so this will only be used if we're working with a previously loaded qBank
        if hasattr(self, 'context_file_path') and self.context_file_path:
            print(f"Using existing context file path: {self.context_file_path}")
            return self.context_file_path
        
        # Case 2: Need to determine context path using new_qbank logic
        try:
            # Get the module, topic code, and difficulty
            module = self.llm_module_num.currentText().strip()
            topic_code = self.llm_topic_code.text().strip().upper()
            difficulty = self.llm_difficulty_combo.currentIndex()
            
            # Check if we have all required information
            if not module or not topic_code:
                print("Missing required information for context path: module or topic code")
                return None
            
            # Get the base directory from course info panel if available - same as in new_qbank
            course_folder = ""
            if hasattr(self, 'parent') and hasattr(self.parent, 'course_info_panel') and \
               hasattr(self.parent.course_info_panel, 'course_folder_input'):
                course_folder = self.parent.course_info_panel.course_folder_input.text().strip()
                print(f"Course folder from panel: '{course_folder}'")
            if not course_folder:
                course_folder = os.path.expanduser("~/textProcessing/NBIO 140B")
                print(f"Using default course folder: '{course_folder}'")
                
            # Create the output directory structure - same as in new_qbank
            output_dir = os.path.join(course_folder, f"module{module}", topic_code, "QBanks")
            context_dir = os.path.join(output_dir, "Context")
            
            # Create directory if it doesn't exist
            try:
                os.makedirs(context_dir, exist_ok=True)
            except Exception as e:
                print(f"Could not create context directory: {str(e)}")
                return None
            
            # Generate the context file name using the same logic as new_qbank
            date_str = datetime.now().strftime('%b%d%y')
            context_path = os.path.join(context_dir, f"M{module}_{topic_code}C{difficulty}_{date_str}.txt")
            
            print(f"Generated context file path: {context_path}")
            return context_path
            
        except Exception as e:
            print(f"Error determining context file path: {str(e)}")
            import traceback
            traceback.print_exc()
            return None
    
    def compute_context_similarity(self):
        """Compute similarity between context texts of all question pairs.
        
        This method compares the context text of each pair of questions and identifies
        overlaps where >50% of one context is contained within the other. Results are
        saved to a text file in the context folder.
        
        Pairs that share common members are combined into larger groupings.
        For example, if questions 1 and 5 overlap, and questions 5 and 8 overlap,
        they will be combined into a 1:5:8 group rather than separate 1:5 and 5:8 pairs.
        """
        if not self.questions or len(self.questions) < 2:
            QMessageBox.warning(self.parent, "No Questions", "Need at least two questions to compute similarity.")
            return
        
        # Save current question if needed
        self.save_current_question()
        
        # Get the context file path
        context_path = self._get_context_file_path()
        if not context_path:
            QMessageBox.critical(self.parent, "Path Error", 
                               "Could not determine context file path. Please ensure a valid question file is loaded or all required fields are filled.")
            return
        
        # Get context directory and file name
        context_dir = os.path.dirname(context_path)
        context_file = os.path.basename(context_path)
        
        # Create the overlap file name by prepending "Overlap_"
        overlap_file_name = f"Overlap_{context_file}"
        output_file = os.path.join(context_dir, overlap_file_name)
        
        # Print paths for debugging
        print(f"Context path: {context_path}")
        print(f"Overlap output path: {output_file}")
        
        try:
            # Find overlapping contexts
            overlaps = []
            total_comparisons = 0
            
            # Update status
            self.llm_status.setText("Computing context similarity...")
            
            # Compare each unique pair of questions
            for i, q1 in enumerate(self.questions):
                for j, q2 in enumerate(self.questions[i+1:], i+1):
                    context1 = q1.get('context', '')
                    context2 = q2.get('context', '')
                    
                    # Convert to string if needed
                    if not isinstance(context1, str):
                        context1 = str(context1)
                        
                    # Skip empty contexts
                    if not context1.strip():
                        continue
                        
                    if not isinstance(context2, str):
                        context2 = str(context2)
                    
                    # Skip empty contexts
                    if not context2.strip():
                        continue
                        
                    total_comparisons += 1
                    
                    # Check for overlap in either direction
                    overlap_found = False
                    overlap_direction = ""
                    
                    # Check if context1 is mostly contained in context2
                    if len(context1) > 0 and len(context2) > 0:
                        # Split into words for word-based comparison
                        words1 = context1.split()
                        words2 = context2.split()
                        
                        if len(words1) == 0 or len(words2) == 0:
                            continue
                        
                        # Check for contiguous word overlap in either direction
                        # Convert lists to strings with a unique separator for exact matching
                        words1_str = ' '.join(words1)
                        words2_str = ' '.join(words2)
                        
                        # Check if one context's words are a contiguous substring of the other
                        # and represent more than 50% of the smaller context
                        if words1_str in words2_str and len(words1) / len(words2) > 0.5:
                            overlap_found = True
                            overlap_direction = f"{q1['id']} is contained in {q2['id']} (>50% word overlap)"
                        elif words2_str in words1_str and len(words2) / len(words1) > 0.5:
                            overlap_found = True
                            overlap_direction = f"{q2['id']} is contained in {q1['id']} (>50% word overlap)"
                    
                    if overlap_found:
                        overlaps.append({
                            'q1_id': q1['id'],
                            'q1_context': context1,
                            'q2_id': q2['id'],
                            'q2_context': context2,
                            'direction': overlap_direction
                        })
            
            # Group overlapping pairs that share common members
            overlap_groups = []
            overlap_relations = []
            
            # First, extract all the overlap relations
            for overlap in overlaps:
                overlap_relations.append((overlap['q1_id'], overlap['q2_id'], overlap['direction']))
            
            # Create a dictionary to track which group each question belongs to
            question_to_group = {}
            
            # Process each overlap relation
            for q1_id, q2_id, direction in overlap_relations:
                # Check if either question is already in a group
                group1 = question_to_group.get(q1_id)
                group2 = question_to_group.get(q2_id)
                
                if group1 is None and group2 is None:
                    # Create a new group with both questions
                    new_group = {'question_ids': {q1_id, q2_id}, 'relations': [(q1_id, q2_id, direction)]}
                    group_index = len(overlap_groups)
                    overlap_groups.append(new_group)
                    
                    # Update the mapping
                    question_to_group[q1_id] = group_index
                    question_to_group[q2_id] = group_index
                    
                elif group1 is not None and group2 is None:
                    # Add q2 to q1's group
                    overlap_groups[group1]['question_ids'].add(q2_id)
                    overlap_groups[group1]['relations'].append((q1_id, q2_id, direction))
                    question_to_group[q2_id] = group1
                    
                elif group1 is None and group2 is not None:
                    # Add q1 to q2's group
                    overlap_groups[group2]['question_ids'].add(q1_id)
                    overlap_groups[group2]['relations'].append((q1_id, q2_id, direction))
                    question_to_group[q1_id] = group2
                    
                elif group1 != group2:
                    # Merge the two groups
                    # Add all questions from group2 to group1
                    overlap_groups[group1]['question_ids'].update(overlap_groups[group2]['question_ids'])
                    overlap_groups[group1]['relations'].extend(overlap_groups[group2]['relations'])
                    overlap_groups[group1]['relations'].append((q1_id, q2_id, direction))
                    
                    # Update the mapping for all questions in group2
                    for qid in overlap_groups[group2]['question_ids']:
                        question_to_group[qid] = group1
                    
                    # Mark group2 as merged (will be filtered out later)
                    overlap_groups[group2] = None
                    
                else:  # Both in the same group already
                    # Just add the relation
                    overlap_groups[group1]['relations'].append((q1_id, q2_id, direction))
            
            # Filter out None groups (those that were merged)
            overlap_groups = [group for group in overlap_groups if group is not None]
            
            # Write results to file
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(f"Context Overlap Analysis\n")
                f.write(f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Total questions analyzed: {len(self.questions)}\n")
                f.write(f"Total comparisons made: {total_comparisons}\n")
                f.write(f"Overlapping pairs found: {len(overlaps)}\n")
                f.write(f"Consolidated groups found: {len(overlap_groups)}\n\n")
                
                if not overlap_groups:
                    f.write("No overlapping contexts found.\n")
                else:
                    for i, group in enumerate(overlap_groups, 1):
                        question_ids = sorted(group['question_ids'], key=lambda x: str(x))
                        f.write(f"Group #{i}: Questions {', '.join(str(qid) for qid in question_ids)}\n\n")
                        
                        # List all overlap relations in this group
                        f.write("Overlap relations in this group:\n")
                        for q1_id, q2_id, direction in group['relations']:
                            f.write(f"- {direction}\n")
                        f.write("\n")
                        
                        # Write each question in the group with its full text and context
                        for qid in question_ids:
                            # Find the full question in the questions list
                            question = next((q for q in self.questions if q.get('id') == qid), None)
                            
                            if question:
                                # Write question with full text
                                f.write(f"Question {qid}:\n")
                                
                                # Get question text and ensure it's a string
                                question_text = question.get('text', '')
                                if not isinstance(question_text, str):
                                    question_text = str(question_text)
                                    
                                # Get answer text from the answer dictionary
                                answer = question.get('answer', {})
                                answer_text = ''
                                if isinstance(answer, dict):
                                    answer_text = answer.get('answer_text', '')
                                elif isinstance(answer, str):
                                    answer_text = answer
                                
                                # Display both question and answer text together
                                display_text = question_text
                                if answer_text:
                                    display_text = question_text.rstrip() + "\n\n" + answer_text
                                    
                                f.write(f"{display_text}\n\n")
                                
                                # Write context
                                context = question.get('context', '')
                                if not isinstance(context, str):
                                    context = str(context)
                                f.write(f"Context:\n{context}\n\n")
                            else:
                                f.write(f"Question {qid}: Not found\n\n")
                        
                        f.write("-" * 80 + "\n\n")
            
            # Show success message
            message = f"Context similarity analysis complete!\n\n"
            message += f"Total questions: {len(self.questions)}\n"
            message += f"Overlapping pairs found: {len(overlaps)}\n"
            message += f"Consolidated groups: {len(overlap_groups)}\n\n"
            message += f"Results saved to: {output_file}"
            
            QMessageBox.information(self.parent, "Similarity Analysis Complete", message)
            self.llm_status.setText(f"Similarity analysis complete. Found {len(overlap_groups)} question groups with overlapping contexts.")
            
        except Exception as e:
            print(f"Error computing similarity: {str(e)}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self.parent, "Error", f"Failed to compute similarity: {str(e)}")
            self.llm_status.setText("Error computing similarity.")
    
    def _enhance_answers_with_text(self):
        """Extract answer text from questions for answers that only have a letter.
        
        This method looks for answers in the format 'A. Answer text' in the question content
        and enhances the answer dictionary with the full text of the answer.
        """
        for q in self.questions:
            # Skip if no answer
            if 'answer' not in q:
                continue
                
            answer = q['answer']
            answer_choice = ''
            
            # Get the answer choice letter
            if isinstance(answer, dict) and 'choice' in answer:
                # Already has a dictionary format
                if answer.get('answer_text', ''):
                    # Already has full answer text, skip
                    continue
                answer_choice = answer['choice']
            elif isinstance(answer, str):
                # Just a letter
                answer_choice = answer
            else:
                # Unknown format
                continue
                
            # Only proceed if we have a valid answer choice
            if not answer_choice or answer_choice not in 'ABCDE':
                continue
                
            # Try to find the answer text in the question
            question_text = q.get('text', '')
            if not question_text:
                continue
                
            # Look for the answer choice in the question text
            pattern = f"{answer_choice}\. ([^\n]+)"
            match = re.search(pattern, question_text)
            if match:
                # Get the full text of the answer choice
                answer_choice_text = match.group(1).strip()
                
                # Create the full answer text in the format "Correct Answer: X. Full text"
                full_answer_text = f"Correct Answer: {answer_choice}. {answer_choice_text}"
                
                # Update the answer with the full text
                if isinstance(answer, dict):
                    answer['answer_text'] = full_answer_text
                else:
                    q['answer'] = {
                        'choice': answer_choice,
                        'answer_text': full_answer_text
                    }
                print(f"Enhanced answer for {q['id']} with text: {full_answer_text}")
    
    def add_section_feedback(self):
        """Add 'Review section X.Y' to the feedback field."""
        if not hasattr(self, 'current_question_index') or self.current_question_index < 0:
            return
                
        chapter = self.chapter_combo.currentText()
        section = self.section_combo.currentText()
        feedback_text = f"Review section {chapter}.{section}"
            
        # Update the current question's feedback
        self.questions[self.current_question_index]['feedback'] = feedback_text
        # Update the display
        self.feedback_editor.setPlainText(feedback_text)
        
    def setup_shortcuts(self):
        """Set up keyboard shortcuts for the LLM Converter tab.
        
        This method adds keyboard shortcuts for left/right arrow keys
        to navigate between questions when the LLM Converter tab is active.
        
        Note: This should be called after the tab is added to the main window.
        """
        from PyQt6.QtGui import QShortcut, QKeySequence
        from PyQt6.QtCore import Qt
        
        if not self.tab or not self.parent:
            return
        
        # Create shortcuts for left/right arrow keys
        left_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Left), self.tab)
        right_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Right), self.tab)
        
        # Connect shortcuts to navigation methods
        left_shortcut.activated.connect(self.show_previous_question)
        right_shortcut.activated.connect(self.show_next_question)
        
        print("LLM Converter shortcuts set up successfully")
        return True
