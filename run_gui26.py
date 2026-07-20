"""
Minimal launcher for the MCQ26 Generator GUI.

Run from any directory with:
    python /Users/sacha/textProcessing/MCQ26/run_gui26.py
"""
import sys
import os

# Ensure MCQ26/ is on sys.path so local 26-suffixed modules resolve
MCQ26_DIR = os.path.dirname(os.path.abspath(__file__))
if MCQ26_DIR not in sys.path:
    sys.path.insert(0, MCQ26_DIR)

# Ensure textProcessing/ is on sys.path so 'MCQ' package resolves
TEXT_PROCESSING_DIR = os.path.dirname(MCQ26_DIR)
if TEXT_PROCESSING_DIR not in sys.path:
    sys.path.insert(0, TEXT_PROCESSING_DIR)

# Ensure bubbleSheet/ is on sys.path so 'MCQ' package resolves
BUBBLE_SHEET_DIR = os.path.join(TEXT_PROCESSING_DIR, 'bubbleSheet')
if BUBBLE_SHEET_DIR not in sys.path:
    sys.path.insert(0, BUBBLE_SHEET_DIR)

from PyQt6.QtWidgets import QApplication
from generator_gui26 import MCQGeneratorGUI

def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    window = MCQGeneratorGUI()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
