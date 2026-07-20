"""
Shared functionality for MCQ26 Generator GUI.
Forked from bubbleSheet/MCQ/shared_gui.py for standalone MCQ26 development.
"""
from PyQt6.QtWidgets import (
    QApplication, QMainWindow,
    QLineEdit, QPushButton, QMessageBox, QTabWidget, QComboBox, QTableWidget,
)
from PyQt6.QtCore import Qt, QTimer, QSettings
from sqlalchemy import create_engine, text, event, Engine
from sqlalchemy.orm import sessionmaker, Session
from pathlib import Path
import os
import traceback
from typing import List, Dict, Any, Optional, Tuple, Union

from database26 import create_db_engine, get_db_session


class BaseMCQApp(QMainWindow):
    """Base class for MCQ26 Generator application."""

    def __init__(self):
        """Initialize the base application with common functionality."""
        super().__init__()

        # Window setup
        self.setWindowTitle("MCQ System")
        self.setGeometry(100, 100, 1000, 700)

        # Application settings
        self.settings = QSettings('Codeium', 'MCQ System')

        # Database setup
        self.engine = create_db_engine()
        self.Session = get_db_session(self.engine)

        # Rate limiting for updates (in seconds)
        self.last_module_update = 0
        self.MODULE_UPDATE_COOLDOWN = 1.0

        # Initialize default parameters and load course info
        self.load_default_parameters()
        self._init_course_info()

        # Initialize questions list for LLM converter
        self.questions = []
        self.current_question_index = -1

        # UI components
        self._init_ui()

    def _init_course_info(self):
        """Initialize course-related attributes from database26 or defaults."""
        from database26 import get_course_info as _get_course_info
        # Set default values first
        self.course_value = self.defaults.get('course', '')
        self.course_title_value = self.defaults.get('course_title', '')
        instructors = self.defaults.get('instructors', [])
        self.instructors_value = instructors if isinstance(instructors, str) else ', '.join(instructors)
        self.course_folder_value = self.defaults.get('course_folder', '')
        self.min_signup_time = self.defaults.get('min_signup_time', 24)
        self.min_cancel_time = self.defaults.get('min_cancel_time', 24)

        # Load from database26
        try:
            info = _get_course_info(self.engine)
            self.course_value        = info.get('course',          self.course_value)
            self.course_title_value  = info.get('course_title',    self.course_title_value)
            self.course_folder_value = info.get('course_folder',   self.course_folder_value)
            self.instructors_value   = info.get('instructors',     self.instructors_value)
            self.min_signup_time     = info.get('min_signup_time', self.min_signup_time)
            self.min_cancel_time     = info.get('min_cancel_time', self.min_cancel_time)
        except Exception as e:
            print(f"Warning: Could not load course info from database26: {e}")

        # Ensure these attributes are always set
        self.module_value = self.defaults.get('module', 0)
        self.quiz_data_value = None
        self.quiz_date_value = None

    def load_default_parameters(self):
        """Load default parameters."""
        self.defaults = {
            'course': 'NBIO140',
            'course_title': 'Neuroscience',
            'instructors': ['Instructor'],
            'module': 0,
            'questions_per_quiz': 20,
            'basePath': os.path.expanduser('~/textProcessing'),
            'qType': 'permuted eq MC5',
        }

        # Create necessary directories if they don't exist
        Path(self.defaults['basePath']).mkdir(parents=True, exist_ok=True)

    def _handle_error(self, context, error, show_message=True):
        """Handle errors by logging to terminal and optionally showing a message box."""
        import traceback
        error_type = type(error).__name__
        error_msg = f"{error_type} in {context}: {str(error)}"

        print(f"\n[ERROR] {error_msg}")
        traceback.print_exc()

        if show_message:
            QMessageBox.critical(
                self,
                "Error",
                f"An error occurred in {context}.\n\n"
                f"Error: {error_type}: {str(error)[:200]}"
            )

        return error_msg

    def sync_course(self, value):
        """Synchronize course value across all tabs."""
        self.course_value = value
        if hasattr(self, 'gen_course') and self.gen_course.text() != value:
            self.gen_course.setText(value)

    def sync_course_title(self, value):
        """Synchronize course title across all tabs."""
        self.course_title_value = value
        if hasattr(self, 'gen_title') and self.gen_title.text() != value:
            self.gen_title.setText(value)

    def sync_instructors(self, value):
        """Synchronize instructors across all tabs."""
        self.instructors_value = value
        if hasattr(self, 'gen_instructors') and self.gen_instructors.text() != value:
            self.gen_instructors.setText(value)

    def load_blocks_to_combo(self, combo_box):
        """Load quiz blocks into the specified combo box."""
        try:
            combo_box.clear()
            from MCQ.signup_manager import get_all_blocks
            blocks = get_all_blocks()

            if not blocks:
                combo_box.addItem("No blocks available", None)
                return

            for block in blocks:
                try:
                    date_str = block.get('date', '')
                    if hasattr(date_str, 'strftime'):
                        date_str = date_str.strftime('%Y-%m-%d')
                    time_slot = block.get('time_slot', '')
                    room = block.get('room', 'No room')
                    block_id = block.get('block_id')
                    display_text = f"{date_str} {time_slot} - {room} (ID: {block_id})"
                    combo_box.addItem(display_text, block_id)
                except Exception as e:
                    print(f"DEBUG: Error formatting block: {e}")
                    continue

        except Exception as e:
            print(f"DEBUG: Error loading blocks: {e}")
            combo_box.addItem("Error loading blocks", None)
