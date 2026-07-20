"""
Qt-based GUI for Quantitative Quiz Generation

This module provides a user interface for creating and configuring quantitative
quizzes with the new ODT-based system.
"""

import sys
import os
from typing import Dict, List, Optional
from datetime import datetime

# Qt imports
try:
    from PyQt6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QGridLayout, QLabel, QLineEdit, QTextEdit, QPushButton, QComboBox,
        QSpinBox, QDoubleSpinBox, QGroupBox, QCheckBox, QTabWidget,
        QMessageBox, QFileDialog, QProgressBar, QSplitter, QScrollArea,
        QFormLayout, QFrame
    )
    from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
    from PyQt6.QtGui import QFont, QIcon, QPixmap
    QT_AVAILABLE = True
except ImportError:
    print("Warning: PyQt6 not available. Install with: pip install PyQt6")
    QT_AVAILABLE = False
    
# Define dummy classes if Qt not available
if not QT_AVAILABLE:
    class QThread:
        def __init__(self):
            pass
        def start(self):
            pass
            
    def pyqtSignal(*args):
        def dummy_signal(*args, **kwargs):
            pass
        return dummy_signal
        
    # Dummy Qt classes for basic functionality
    class QWidget:
        def __init__(self):
            pass
        def setLayout(self, layout):
            pass
            
    class QMainWindow:
        def __init__(self):
            pass
        def setCentralWidget(self, widget):
            pass
        def setWindowTitle(self, title):
            pass
        def setGeometry(self, x, y, w, h):
            pass
        def statusBar(self):
            return DummyStatusBar()
        def show(self):
            pass
            
    class DummyStatusBar:
        def showMessage(self, msg):
            pass
            
    class QApplication:
        def __init__(self, args):
            pass
        def setStyle(self, style):
            pass
        def exec(self):
            return 0
            
    class QVBoxLayout:
        def __init__(self):
            pass
        def addWidget(self, widget):
            pass
        def addLayout(self, layout):
            pass
        def addStretch(self):
            pass
            
    class QHBoxLayout:
        def __init__(self):
            pass
        def addWidget(self, widget):
            pass
        def addLayout(self, layout):
            pass
        def addStretch(self):
            pass
            
    class QLabel:
        def __init__(self, text=""):
            pass
        def setText(self, text):
            pass
        def setFont(self, font):
            pass
            
    class QLineEdit:
        def __init__(self):
            pass
        def text(self):
            return ""
        def setText(self, text):
            pass
            
    class QTextEdit:
        def __init__(self):
            pass
        def setPlainText(self, text):
            pass
        def clear(self):
            pass
        def setReadOnly(self, readonly):
            pass
        def setMaximumHeight(self, height):
            pass
            
    class QPushButton:
        def __init__(self, text):
            pass
        def setFont(self, font):
            pass
        def setStyleSheet(self, style):
            pass
        def setEnabled(self, enabled):
            pass
        def clicked(self):
            return DummySignal()
            
    class DummySignal:
        def connect(self, func):
            pass
            
    class QComboBox:
        def __init__(self):
            pass
        def addItems(self, items):
            pass
        def currentText(self):
            return ""
        def setCurrentText(self, text):
            pass
            
    class QSpinBox:
        def __init__(self):
            pass
        def setRange(self, min_val, max_val):
            pass
        def setValue(self, value):
            pass
        def value(self):
            return 1
            
    class QGroupBox:
        def __init__(self, title):
            pass
        def setLayout(self, layout):
            pass
            
    class QCheckBox:
        def __init__(self, text):
            pass
        def isChecked(self):
            return False
        def setChecked(self, checked):
            pass
            
    class QTabWidget:
        def __init__(self):
            pass
        def addTab(self, widget, title):
            pass
            
    class QFormLayout:
        def __init__(self):
            pass
        def addRow(self, label, widget):
            pass
            
    class QProgressBar:
        def __init__(self):
            pass
        def setValue(self, value):
            pass
        def setVisible(self, visible):
            pass
            
    class QMessageBox:
        @staticmethod
        def critical(parent, title, message):
            print(f"CRITICAL: {title} - {message}")
        @staticmethod
        def warning(parent, title, message):
            print(f"WARNING: {title} - {message}")
        @staticmethod
        def information(parent, title, message):
            print(f"INFO: {title} - {message}")
            
    class QFileDialog:
        @staticmethod
        def getSaveFileName(parent, title, filter_str):
            return ("", "")
            
    class QFont:
        def __init__(self, family, size, weight=None):
            pass
        Bold = 1

# Local imports
try:
    from odt_quiz_generator import ODTQuizGenerator
    from quantitative_question_bank import QuantitativeQuestionBank
    LOCAL_MODULES_AVAILABLE = True
except ImportError:
    print("Warning: Local modules not available")
    LOCAL_MODULES_AVAILABLE = False


class QuizGenerationThread(QThread):
    """Background thread for quiz generation."""
    progress_updated = pyqtSignal(int)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    
    def __init__(self, quiz_config: Dict, questions: List[Dict]):
        super().__init__()
        self.quiz_config = quiz_config
        self.questions = questions
        
    def run(self):
        """Generate the quiz in background."""
        try:
            self.progress_updated.emit(10)
            
            # Create generator
            generator = ODTQuizGenerator()
            self.progress_updated.emit(20)
            
            # Create document
            generator.create_document(**self.quiz_config)
            self.progress_updated.emit(40)
            
            # Add questions
            total_questions = len(self.questions)
            for i, question in enumerate(self.questions):
                generator.add_question(
                    number=i + 1,
                    stem=question['stem'],
                    subquestions=question['subquestions']
                )
                progress = 40 + int((i + 1) / total_questions * 40)
                self.progress_updated.emit(progress)
            
            # Save document
            filename = f"quantitative_quiz_{datetime.now().strftime('%Y%m%d_%H%M%S')}.odt"
            generator.save_document(filename)
            self.progress_updated.emit(90)
            
            self.progress_updated.emit(100)
            self.finished.emit(filename)
            
        except Exception as e:
            self.error.emit(str(e))


class QuestionPreviewWidget(QWidget):
    """Widget for previewing generated questions."""
    
    def __init__(self):
        super().__init__()
        self.init_ui()
        
    def init_ui(self):
        """Initialize the UI."""
        layout = QVBoxLayout()
        
        # Title
        title = QLabel("Question Preview")
        title.setFont(QFont("Arial", 12, QFont.Bold))
        layout.addWidget(title)
        
        # Preview text area
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setMaximumHeight(300)
        layout.addWidget(self.preview_text)
        
        self.setLayout(layout)
        
    def update_preview(self, question: Dict):
        """Update the preview with a new question."""
        if not question:
            self.preview_text.clear()
            return
            
        preview_text = f"Question: {question.get('stem', '')}\n\n"
        preview_text += f"Given: {question.get('given_info', '')}\n\n"
        
        for subq in question.get('subquestions', []):
            preview_text += f"{subq.get('letter', '')}) {subq.get('text', '')}\n"
            if subq.get('has_answer_box', False):
                preview_text += "   [Answer Box]\n"
                
        preview_text += f"\nAnswer: {question.get('answer', '')}"
        
        self.preview_text.setPlainText(preview_text)


class QuizConfigWidget(QWidget):
    """Widget for configuring quiz parameters."""
    
    def __init__(self):
        super().__init__()
        self.init_ui()
        
    def init_ui(self):
        """Initialize the UI."""
        layout = QVBoxLayout()
        
        # Quiz Information Group
        quiz_group = QGroupBox("Quiz Information")
        quiz_layout = QFormLayout()
        
        self.quiz_type_combo = QComboBox()
        self.quiz_type_combo.addItems(["Quiz", "Answer Key", "Extra Page"])
        quiz_layout.addRow("Quiz Type:", self.quiz_type_combo)
        
        self.course_edit = QLineEdit()
        quiz_layout.addRow("Course:", self.course_edit)
        
        self.instructors_edit = QLineEdit()
        quiz_layout.addRow("Instructors:", self.instructors_edit)
        
        self.student_edit = QLineEdit()
        quiz_layout.addRow("Student:", self.student_edit)
        
        self.date_edit = QLineEdit()
        self.date_edit.setText(datetime.now().strftime("%Y-%m-%d"))
        quiz_layout.addRow("Date:", self.date_edit)
        
        self.quiz_id_edit = QLineEdit()
        quiz_layout.addRow("Quiz ID:", self.quiz_id_edit)
        
        quiz_group.setLayout(quiz_layout)
        layout.addWidget(quiz_group)
        
        # Question Generation Group
        question_group = QGroupBox("Question Generation")
        question_layout = QFormLayout()
        
        self.num_questions_spin = QSpinBox()
        self.num_questions_spin.setRange(1, 50)
        self.num_questions_spin.setValue(5)
        question_layout.addRow("Number of Questions:", self.num_questions_spin)
        
        self.question_type_combo = QComboBox()
        self.question_type_combo.addItems(["Nernst Equation", "Mixed Types"])
        question_layout.addRow("Question Type:", self.question_type_combo)
        
        self.difficulty_combo = QComboBox()
        self.difficulty_combo.addItems(["Easy", "Medium", "Hard"])
        self.difficulty_combo.setCurrentText("Medium")
        question_layout.addRow("Difficulty:", self.difficulty_combo)
        
        question_group.setLayout(question_layout)
        layout.addWidget(question_group)
        
        # Output Options Group
        output_group = QGroupBox("Output Options")
        output_layout = QVBoxLayout()
        
        self.auto_save_check = QCheckBox("Auto-save to default location")
        self.auto_save_check.setChecked(True)
        output_layout.addWidget(self.auto_save_check)
        
        self.open_after_check = QCheckBox("Open document after generation")
        self.open_after_check.setChecked(True)
        output_layout.addWidget(self.open_after_check)
        
        output_group.setLayout(output_layout)
        layout.addWidget(output_group)
        
        layout.addStretch()
        self.setLayout(layout)
        
    def get_config(self) -> Dict:
        """Get the current configuration."""
        return {
            'quiz_type': self.quiz_type_combo.currentText(),
            'course': self.course_edit.text(),
            'instructors': self.instructors_edit.text(),
            'student': self.student_edit.text(),
            'quiz_date': self.date_edit.text(),
            'quiz_id': self.quiz_id_edit.text()
        }
        
    def get_question_config(self) -> Dict:
        """Get the question generation configuration."""
        return {
            'num_questions': self.num_questions_spin.value(),
            'question_type': self.question_type_combo.currentText(),
            'difficulty': self.difficulty_combo.currentText().lower()
        }


class QuantitativeQuizGUI(QMainWindow):
    """Main GUI window for quantitative quiz generation."""
    
    def __init__(self):
        super().__init__()
        if not QT_AVAILABLE:
            raise ImportError("PyQt5 is required")
        if not LOCAL_MODULES_AVAILABLE:
            raise ImportError("Local modules are required")
            
        self.question_bank = QuantitativeQuestionBank()
        self.current_questions = []
        self.generation_thread = None
        
        self.init_ui()
        self.init_connections()
        
    def init_ui(self):
        """Initialize the user interface."""
        self.setWindowTitle("Quantitative Quiz Generator")
        self.setGeometry(100, 100, 1000, 700)
        
        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Main layout
        main_layout = QHBoxLayout()
        
        # Left panel - Configuration
        left_panel = QWidget()
        left_layout = QVBoxLayout()
        
        # Configuration widget
        self.config_widget = QuizConfigWidget()
        left_layout.addWidget(self.config_widget)
        
        # Control buttons
        button_layout = QVBoxLayout()
        
        self.generate_btn = QPushButton("Generate Questions")
        self.generate_btn.setFont(QFont("Arial", 10, QFont.Bold))
        self.generate_btn.setStyleSheet("QPushButton { background-color: #4CAF50; color: white; }")
        button_layout.addWidget(self.generate_btn)
        
        self.create_quiz_btn = QPushButton("Create Quiz Document")
        self.create_quiz_btn.setFont(QFont("Arial", 10, QFont.Bold))
        self.create_quiz_btn.setStyleSheet("QPushButton { background-color: #2196F3; color: white; }")
        self.create_quiz_btn.setEnabled(False)
        button_layout.addWidget(self.create_quiz_btn)
        
        self.save_btn = QPushButton("Save Quiz")
        self.save_btn.setEnabled(False)
        button_layout.addWidget(self.save_btn)
        
        self.clear_btn = QPushButton("Clear All")
        button_layout.addWidget(self.clear_btn)
        
        left_layout.addLayout(button_layout)
        left_layout.addStretch()
        
        left_panel.setLayout(left_layout)
        left_panel.setMaximumWidth(400)
        
        # Right panel - Preview and Progress
        right_panel = QWidget()
        right_layout = QVBoxLayout()
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        right_layout.addWidget(self.progress_bar)
        
        # Tab widget for different views
        self.tab_widget = QTabWidget()
        
        # Question preview tab
        self.preview_widget = QuestionPreviewWidget()
        self.tab_widget.addTab(self.preview_widget, "Question Preview")
        
        # Generated questions list
        self.questions_list = QTextEdit()
        self.questions_list.setReadOnly(True)
        self.tab_widget.addTab(self.questions_list, "All Questions")
        
        right_layout.addWidget(self.tab_widget)
        
        right_panel.setLayout(right_layout)
        
        # Add panels to main layout
        main_layout.addWidget(left_panel)
        main_layout.addWidget(right_panel)
        
        central_widget.setLayout(main_layout)
        
        # Status bar
        self.statusBar().showMessage("Ready")
        
    def init_connections(self):
        """Initialize signal connections."""
        self.generate_btn.clicked.connect(self.generate_questions)
        self.create_quiz_btn.clicked.connect(self.create_quiz)
        self.save_btn.clicked.connect(self.save_quiz)
        self.clear_btn.clicked.connect(self.clear_all)
        
    def generate_questions(self):
        """Generate questions based on configuration."""
        try:
            # Get configuration
            q_config = self.config_widget.get_question_config()
            
            # Generate questions
            self.statusBar().showMessage("Generating questions...")
            
            if q_config['question_type'] == "Nernst Equation":
                question_types = ['nernst_equation']
            else:
                question_types = ['nernst_equation']  # Expand as more types are added
                
            self.current_questions = self.question_bank.generate_question_set(
                num_questions=q_config['num_questions'],
                question_types=question_types,
                difficulty=q_config['difficulty']
            )
            
            # Update UI
            self.update_questions_display()
            self.create_quiz_btn.setEnabled(True)
            
            self.statusBar().showMessage(f"Generated {len(self.current_questions)} questions")
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to generate questions: {str(e)}")
            
    def update_questions_display(self):
        """Update the display with generated questions."""
        if not self.current_questions:
            return
            
        # Update preview with first question
        self.preview_widget.update_preview(self.current_questions[0])
        
        # Update questions list
        questions_text = ""
        for i, question in enumerate(self.current_questions, 1):
            questions_text += f"Question {i}:\n"
            questions_text += f"{question['stem']}\n"
            questions_text += f"Given: {question['given_info']}\n"
            questions_text += f"Answer: {question['answer']}\n\n"
            
        self.questions_list.setPlainText(questions_text)
        
    def create_quiz(self):
        """Create the ODT quiz document."""
        if not self.current_questions:
            QMessageBox.warning(self, "Warning", "No questions generated yet")
            return
            
        try:
            # Get configuration
            quiz_config = self.config_widget.get_config()
            
            # Validate required fields
            if not quiz_config['course'] or not quiz_config['quiz_id']:
                QMessageBox.warning(self, "Warning", "Please fill in Course and Quiz ID")
                return
                
            # Start generation in background
            self.generation_thread = QuizGenerationThread(quiz_config, self.current_questions)
            self.generation_thread.progress_updated.connect(self.update_progress)
            self.generation_thread.finished.connect(self.quiz_generation_finished)
            self.generation_thread.error.connect(self.quiz_generation_error)
            
            # Show progress bar
            self.progress_bar.setVisible(True)
            self.progress_bar.setValue(0)
            
            # Disable buttons during generation
            self.generate_btn.setEnabled(False)
            self.create_quiz_btn.setEnabled(False)
            
            # Start thread
            self.generation_thread.start()
            
            self.statusBar().showMessage("Creating quiz document...")
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to create quiz: {str(e)}")
            
    def update_progress(self, value):
        """Update progress bar."""
        self.progress_bar.setValue(value)
        
    def quiz_generation_finished(self, filename):
        """Handle quiz generation completion."""
        self.progress_bar.setVisible(False)
        self.generate_btn.setEnabled(True)
        self.create_quiz_btn.setEnabled(True)
        self.save_btn.setEnabled(True)
        
        self.statusBar().showMessage(f"Quiz saved as: {filename}")
        
        QMessageBox.information(self, "Success", f"Quiz saved as: {filename}")
        
        # Open document if option is checked
        if self.config_widget.open_after_check.isChecked():
            try:
                os.system(f"open {filename}")
            except:
                pass
                
    def quiz_generation_error(self, error_msg):
        """Handle quiz generation error."""
        self.progress_bar.setVisible(False)
        self.generate_btn.setEnabled(True)
        self.create_quiz_btn.setEnabled(True)
        
        self.statusBar().showMessage("Error generating quiz")
        QMessageBox.critical(self, "Error", f"Failed to generate quiz: {error_msg}")
        
    def save_quiz(self):
        """Save quiz to specified location."""
        filename, _ = QFileDialog.getSaveFileName(
            self, "Save Quiz", "", "ODT Files (*.odt);;All Files (*)"
        )
        
        if filename:
            try:
                # This would need to be implemented to save with a different name
                self.statusBar().showMessage(f"Quiz saved as: {filename}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save quiz: {str(e)}")
                
    def clear_all(self):
        """Clear all generated data."""
        self.current_questions = []
        self.preview_widget.update_preview(None)
        self.questions_list.clear()
        self.create_quiz_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.statusBar().showMessage("Cleared")


def main():
    """Main function to run the GUI."""
    if not QT_AVAILABLE:
        print("PyQt5 is required. Install with: pip install PyQt5")
        return
        
    if not LOCAL_MODULES_AVAILABLE:
        print("Local modules are required")
        return
        
    app = QApplication(sys.argv)
    
    # Set application style
    app.setStyle('Fusion')
    
    # Create and show main window
    window = QuantitativeQuizGUI()
    window.show()
    
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
