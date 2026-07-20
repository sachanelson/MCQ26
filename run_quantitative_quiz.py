#!/usr/bin/env python3
"""
Main Entry Point for Quantitative Quiz System

This script provides the main interface for the ODT-based quantitative quiz
generation system, complementing the existing MCQ PDF-based system.
"""

import sys
import os
import argparse
from typing import Dict, List

# Local imports
from odt_quiz_generator import ODTQuizGenerator
from quantitative_question_bank import QuantitativeQuestionBank
from quantitative_quiz_gui import QuantitativeQuizGUI, QT_AVAILABLE
from quiz_integration import SharedQuizComponents


def create_sample_quiz():
    """Create a sample quiz for demonstration."""
    print("Creating sample quantitative quiz...")
    
    # Initialize components
    shared = SharedQuizComponents()
    question_bank = QuantitativeQuestionBank()
    generator = ODTQuizGenerator()
    
    # Create quiz metadata
    metadata = shared.create_quiz_metadata(
        student_name="Sample Student",
        course_code="BIOL26",
        quiz_type="Quiz"
    )
    
    # Generate questions
    questions = question_bank.generate_question_set(
        num_questions=3,
        question_types=['nernst_equation'],
        difficulty='medium'
    )
    
    # Create document
    generator.create_document(**metadata)
    
    # Add questions
    for i, question in enumerate(questions, 1):
        generator.add_question(
            number=i,
            stem=question['stem'],
            subquestions=question['subquestions'],
            given_data={'all_ions': question['all_ions']}
        )
    
    # Save document
    filename = generator.save_document("sample_quantitative_quiz")
    print(f"Sample quiz created: {filename}")
    
    return filename


def create_quiz_from_config(config_file: str):
    """Create quiz from configuration file."""
    print(f"Creating quiz from config: {config_file}")
    
    # This would read a JSON/YAML config file and create a quiz
    # Implementation would go here
    print("Config file support not yet implemented")


def run_gui():
    """Run the graphical user interface."""
    if not QT_AVAILABLE:
        print("GUI not available. PyQt6 is required.")
        print("Install with: pip install PyQt6")
        return
        
    print("Starting Quantitative Quiz GUI...")
    
    app = None
    try:
        from PyQt6.QtWidgets import QApplication
        
        app = QApplication(sys.argv)
        app.setStyle('Fusion')
        
        window = QuantitativeQuizGUI()
        window.show()
        
        sys.exit(app.exec())
        
    except ImportError:
        print("PyQt6 is required for the GUI.")
        print("Install with: pip install PyQt6")
    except Exception as e:
        print(f"Error running GUI: {e}")
        if app:
            app.quit()


def check_dependencies():
    """Check if optional GUI dependencies are available."""
    print("Checking dependencies...")

    missing = []

    if not QT_AVAILABLE:
        missing.append("PyQt6")

    if missing:
        print("Missing dependencies:")
        for dep in missing:
            print(f"  - {dep}")
        print("\nInstall with:")
        print(f"  pip install {' '.join(missing)}")
        return False

    print("All dependencies are available.")
    return True


def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description="Quantitative Quiz Generator - ODT-based quiz system"
    )
    
    parser.add_argument(
        "--gui", action="store_true",
        help="Launch the graphical user interface"
    )
    parser.add_argument(
        "--sample", action="store_true",
        help="Create a sample quiz"
    )
    parser.add_argument(
        "--config", type=str,
        help="Create quiz from configuration file"
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Check dependencies"
    )
    
    args = parser.parse_args()
    
    # If no arguments, show help
    if len(sys.argv) == 1:
        parser.print_help()
        print("\nExamples:")
        print("  python run_quantitative_quiz.py --gui")
        print("  python run_quantitative_quiz.py --sample")
        print("  python run_quantitative_quiz.py --check")
        return
    
    # Handle arguments
    if args.check:
        check_dependencies()
    elif args.gui:
        if check_dependencies():
            run_gui()
    elif args.sample:
        create_sample_quiz()
    elif args.config:
        create_quiz_from_config(args.config)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
