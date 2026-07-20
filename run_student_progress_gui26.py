"""
Minimal launcher for the MCQ26 Student Progress GUI.

Run from any directory with:
    python /Users/sacha/textProcessing/MCQ26/run_student_progress_gui26.py
"""
import sys
import os

# Ensure MCQ26/ is on sys.path so local 26-suffixed modules resolve
MCQ26_DIR = os.path.dirname(os.path.abspath(__file__))
if MCQ26_DIR not in sys.path:
    sys.path.insert(0, MCQ26_DIR)

from PyQt6.QtWidgets import QApplication
from student_progress_gui26 import StudentProgressGUI


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    window = StudentProgressGUI()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
