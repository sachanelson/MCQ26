"""
Utility functions for handling LLM question bank operations.
"""
import os
import re
from typing import List, Dict, Optional, Tuple
from PyQt6.QtWidgets import QMessageBox

def parse_question_bank_filename(filename):
    """
    Parse a question bank filename to extract module, topic code, and difficulty.
    
    Args:
        filename (str): The question bank filename (e.g., "M1_ABCQ1_30May25.txt")
        
    Returns:
        dict: Parsed components (module, topic_code, difficulty) or None if invalid format
    """
    # Expected format: MX_YYYQD_datestr.txt
    pattern = r'^M(\d+)_([A-Za-z]{3})Q(\d)_.*\.txt$'
    match = re.match(pattern, filename)
    
    if not match:
        return None
        
    return {
        'module': int(match.group(1)),
        'topic_code': match.group(2).upper(),
        'difficulty': int(match.group(3))
    }

def get_question_bank_files(question_file):
    """
    Get the corresponding answer and feedback files for a question file.
    
    Args:
        question_file (str): Path to the question file
        
    Returns:
        tuple: (answer_file_path, feedback_file_path) or (None, None) if files not found
    """
    # Get the parent directory of the Questions directory (Question_banks)
    question_dir = os.path.dirname(question_file)  # Should be .../Question_banks/Questions
    question_banks_dir = os.path.dirname(question_dir)  # Should be .../Question_banks
    
    filename = os.path.basename(question_file)
    
    # Create answer and feedback filenames by replacing Q with A and F
    answer_filename = filename.replace("Q", "A", 1)
    feedback_filename = filename.replace("Q", "F", 1)
    
    # Look for answer and feedback files in the sibling directories
    answer_file = os.path.join(question_banks_dir, "Answers", answer_filename)
    feedback_file = os.path.join(question_banks_dir, "Feedback", feedback_filename)
    
    # Verify both files exist
    if not (os.path.exists(answer_file) and os.path.exists(feedback_file)):
        return None, None
        
    return answer_file, feedback_file

def get_last_question_number(question_file):
    """
    Get the last question number from a question file.
    
    Args:
        question_file (str): Path to the question file
        
    Returns:
        int: The last question number, or 0 if no questions found
    """
    last_num = 0
    try:
        with open(question_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and line[0].isdigit():
                    try:
                        num = int(line.split('.')[0])
                        last_num = max(last_num, num)
                    except (ValueError, IndexError):
                        continue
    except Exception:
        return 0
        
    return last_num

def parse_llm_questions_file(file_path: str) -> List[Dict[str, any]]:
    """
    Parse an LLM-generated questions file into a list of question dictionaries.
    
    Each question dictionary contains:
    - 'text': The full question text including stem and choices
    - 'answer': Dictionary with 'letter' and 'text' of the correct answer
    - 'feedback': Empty string (to be filled in later)
    
    Args:
        file_path (str): Path to the input file with LLM-generated questions
        
    Returns:
        List[Dict]: List of question dictionaries
    """
    questions = []
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # Split content into individual questions (assuming they're separated by a line of dashes)
        raw_questions = [q.strip() for q in re.split(r'\n-{4,}\n', content) if q.strip()]
        
        for raw_q in raw_questions:
            if not raw_q.strip():
                continue
                
            # Initialize question dictionary
            question = {
                'text': '',
                'answer': {'letter': '', 'text': ''},
                'feedback': ''
            }
            
            # Check if this is the new format with "Question X:" header
            q_match = re.search(r'(?i)Question\s+(\d+)[:.]?\s*(.*?)(?=\n+[A-E][:.)]|$)', 
                              raw_q, re.DOTALL)
                              
            if q_match:
                question_stem = q_match.group(2).strip()
                
                # Extract all choices (A-E)
                choices = []
                
                # Try different patterns for choices
                # Pattern 1: A. Choice text - match lines that start with a single letter A-E followed by period
                choice_pattern1 = r'\n^\s*([A-E])[\.:]\s*(.*?)(?=\n^\s*[A-E][\.:\)]|\n\s*Correct Answer:|\n\s*Context:|$)'
                
                # Pattern 2: A) Choice text - match lines that start with a single letter A-E followed by parenthesis
                choice_pattern2 = r'\n^\s*([A-E])\)\s*(.*?)(?=\n^\s*[A-E][\.:\)]|\n\s*Correct Answer:|\n\s*Context:|$)'
                
                # Try first pattern
                for match in re.finditer(choice_pattern1, raw_q, re.MULTILINE | re.DOTALL):
                    letter = match.group(1).upper()
                    text = match.group(2).strip()
                    choices.append((letter, text))
                
                # If no choices found with first pattern, try the second
                if not choices:
                    for match in re.finditer(choice_pattern2, raw_q, re.MULTILINE | re.DOTALL):
                        letter = match.group(1).upper()
                        text = match.group(2).strip()
                        choices.append((letter, text))
                
                # Build the question text with choices
                question_text = [question_stem, '']  # Add empty line after stem
                for letter, text in choices:
                    question_text.append(f"{letter}. {text}")
                
                # Build the question text with just the stem and choices
                question['text'] = '\n'.join(question_text)
                
                # Find and store the correct answer
                # Make sure we're not looking for these patterns within the choices
                answer_section = re.search(r'(?i)\n+Correct\s*Answer:\s*([A-E])', raw_q)
                if answer_section:
                    correct_letter = answer_section.group(1).upper()
                    # Find the text of the correct answer
                    correct_text = next((text for l, text in choices if l == correct_letter), '')
                    # Store answer in the format: 'Correct Answer: X'
                    question['answer'] = f"Correct Answer: {correct_letter}"
                
                # Extract section reference from context if available - get the LAST parenthetical expression
                # First find the Context line
                context_line_match = re.search(r'(?i)\n+Context:(.*?)(?=\n+Question|$)', raw_q, re.DOTALL)
                if context_line_match:
                    context_text = context_line_match.group(1).strip()
                    
                    # Store the raw context text in the question dictionary
                    # Trim the leading double quotes (there are two sets) and trailing quote (one set)
                    if context_text.startswith('""') and context_text.endswith('"'):
                        context_text = context_text[2:-1].strip()
                    question['context'] = context_text
                    
                    # Find all parenthetical expressions
                    parenthetical_matches = list(re.finditer(r'\(([^)]+)\)', context_text))
                    # Get the last one (which should be the section reference)
                    if parenthetical_matches:
                        last_match = parenthetical_matches[-1]
                        section_ref = last_match.group(1).strip()
                        # Format as 'Review: X'
                        question['feedback'] = f"Review: {section_ref}"
                
                questions.append(question)
            else:
                # Fall back to the original parsing logic for older formats
                # Extract question stem (anything before the first choice)
                q_match = re.search(r'(.*?)(?=\n+[A-E][:.)]|$)', raw_q, re.DOTALL)
                if q_match:
                    question_stem = q_match.group(1).strip()
                    
                    # Extract all choices (A-E)
                    choices = []
                    choice_pattern = r'\n^\s*([A-E])[\.:]\s*(.*?)(?=\n^\s*[A-E][\.:\)]|\n\s*Correct Answer:|\n\s*Context:|$)'
                    for match in re.finditer(choice_pattern, raw_q, re.MULTILINE | re.DOTALL):
                        letter = match.group(1).upper()
                        text = match.group(2).strip()
                        choices.append((letter, text))
                    
                    # If no choices found with A-E, try a different pattern
                    if not choices:
                        choice_pattern = r'\n^\s*([A-E])\)\s*(.*?)(?=\n^\s*[A-E][\.:\)]|\n\s*Correct Answer:|\n\s*Context:|$)'
                        for match in re.finditer(choice_pattern, raw_q, re.MULTILINE | re.DOTALL):
                            letter = match.group(1).upper()
                            text = match.group(2).strip()
                            choices.append((letter, text))
                    
                    # Build the question text with choices
                    question_text = [question_stem, '']  # Add empty line after stem
                    for letter, text in choices:
                        question_text.append(f"{letter}. {text}")
                    
                    # Build the question text with just the stem and choices
                    question['text'] = '\n'.join(question_text)
                    
                    # Find and store the correct answer separately
                    # Make sure we're not looking for these patterns within the choices
                    ans_match = re.search(r'(?i)\n+correct\s*(?:answer|choice)[:.]?\s*([A-E])', raw_q)
                    if ans_match:
                        correct_letter = ans_match.group(1).upper()
                        # Find the text of the correct answer
                        correct_text = next((text for l, text in choices if l == correct_letter), '')
                        # Store answer in the format: 'Correct Answer: X'
                        question['answer'] = f"Correct Answer: {correct_letter}"
                    
                    questions.append(question)
                    
    except Exception as e:
        print(f"Error parsing LLM questions file: {e}")
        return []
    
    return questions

def get_header_from_file(file_path):
    """
    Extract the header from a question/answer/feedback file.
    
    Args:
        file_path (str): Path to the file
        
    Returns:
        dict: The header as a dictionary, or None if not found
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            header_lines = []
            for line in f:
                line = line.strip()
                if not line and header_lines:
                    break
                header_lines.append(line)
            
            if not header_lines:
                return None
                
            # Join header lines and evaluate as dictionary
            header_str = '\n'.join(header_lines)
            try:
                return eval(header_str)
            except (SyntaxError, NameError, TypeError):
                return None
                
    except Exception:
        return None
