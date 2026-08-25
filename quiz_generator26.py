"""
Quiz generation backend for MCQ26.

This module re-implements the PDF-based quiz generation routines from the old
MCQ system, adapted for the new integrated question bank format and the new
module-folder storage layout:

    ~/textProcessing/NBIO140_2026/moduleX/quizzes/

It relies on the local MCQ26 modules:
    - quiz_bank_parser26.load_integrated_bank / load_question_banks
    - qr_code26.generate_qr_code
    - database26 for course info and Quiz records
"""
import io
import os
import random
import re
import json
import tempfile
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from sqlalchemy.orm import Session

from fpdf import FPDF
from pypdf import PdfReader, PdfWriter

from database26 import (
    Student, Quiz, get_course_info, get_student_by_code, get_section,
    add_generated_quiz_attempt, get_student_module_question_ids, quiz_attempt_exists,
)
from document_ids26 import artifact_id, format_quiz_id
from quiz_bank_parser26 import load_question_banks
from qr_code26 import generate_qr_code


# ---------------------------------------------------------------------------
# PDF helpers (adapted from bubbleSheet/MCQ/quiz_generator.py)
# ---------------------------------------------------------------------------

def sanitize_text_for_pdf(text):
    """Sanitize text for PDF output by replacing problematic Unicode characters."""
    if not text:
        return text

    replacements = {
        '\u2013': '-',    # en-dash
        '\u2014': '--',   # em-dash
        '\u2018': "'",   # left single quote
        '\u2019': "'",   # right single quote
        '\u201c': '"',   # left double quote
        '\u201d': '"',   # right double quote
        '\u2022': '*',    # bullet
        '\u2026': '...',  # ellipsis
        '\u00a0': ' ',    # non-breaking space
        '\u00b0': 'deg',  # degree sign
        '\u00b1': '+/-',  # plus-minus sign
        '\u00d7': 'x',    # multiplication sign
        '\u00f7': '/',    # division sign
        # Greek letters
        '\u03b1': '[alpha]', '\u03b2': '[beta]', '\u03b3': '[gamma]',
        '\u03b4': '[delta]', '\u03b5': '[epsilon]', '\u03b6': '[zeta]',
        '\u03b7': '[eta]', '\u03b8': '[theta]', '\u03b9': '[iota]',
        '\u03ba': '[kappa]', '\u03bb': '[lambda]', '\u03bc': '[mu]',
        '\u03bd': '[nu]', '\u03be': '[xi]', '\u03bf': '[omicron]',
        '\u03c0': '[pi]', '\u03c1': '[rho]', '\u03c2': '[sigma]',
        '\u03c3': '[sigma]', '\u03c4': '[tau]', '\u03c5': '[upsilon]',
        '\u03c6': '[phi]', '\u03c7': '[chi]', '\u03c8': '[psi]',
        '\u03c9': '[omega]',
        '\u0391': '[Alpha]', '\u0392': '[Beta]', '\u0393': '[Gamma]',
        '\u0394': '[Delta]', '\u0395': '[Epsilon]', '\u0396': '[Zeta]',
        '\u0397': '[Eta]', '\u0398': '[Theta]', '\u0399': '[Iota]',
        '\u039a': '[Kappa]', '\u039b': '[Lambda]', '\u039c': '[Mu]',
        '\u039d': '[Nu]', '\u039e': '[Xi]', '\u039f': '[Omicron]',
        '\u03a0': '[Pi]', '\u03a1': '[Rho]', '\u03a3': '[Sigma]',
        '\u03a4': '[Tau]', '\u03a5': '[Upsilon]', '\u03a6': '[Phi]',
        '\u03a7': '[Chi]', '\u03a8': '[Psi]', '\u03a9': '[Omega]',
    }

    for unicode_char, replacement in replacements.items():
        text = text.replace(unicode_char, replacement)

    try:
        text.encode('latin-1')
    except UnicodeEncodeError:
        text = text.encode('latin-1', errors='replace').decode('latin-1')
        text = text.replace('\ufffd', '[?]')

    return text


class QuizPDF(FPDF):
    """PDF document for a multiple-choice quiz or answer key."""

    def circle(self, x, y, r, style='D'):
        """Draw a circle with center (x, y) and radius r."""
        self.ellipse(x - r, y - r, 2 * r, 2 * r, style)

    def draw_answer_circle(self, x, y, filled=False, question_idx=None, answer_idx=None):
        """Draw an answer bubble, optionally filled, and record its position."""
        page = str(self.page_no())
        if page not in self.metadata:
            self.metadata[page] = {"qr_code": None, "questions": [], "calibration_points": {}}
        while len(self.metadata[page]["questions"]) <= question_idx:
            self.metadata[page]["questions"].append({"stem": None, "number": None, "answers": [], "correct_answer": None})
        self.metadata[page]["questions"][question_idx]["answers"].append({
            "x": x, "y": y, "radius": self.circle_radius, "answer_idx": answer_idx
        })
        if filled:
            self.set_fill_color(0)
            self.set_draw_color(0)
            self.circle(x, y, self.circle_radius, 'F')
            self.circle(x, y, self.circle_radius, 'D')
        else:
            self.set_fill_color(255)
            self.set_draw_color(0)
            self.circle(x, y, self.circle_radius, 'D')

    def __init__(self, course=None, instructors=None, student=None, date=None, quiz_id=None, quiz_type=None, module_num=None, section_code=''):
        # Letter, not A4: quizzes are printed on US Letter paper in practice.
        # Authoring at A4 and printing on Letter causes most print drivers to
        # silently "shrink to fit + center" the content (different aspect
        # ratios), which shifts every recorded coordinate in the calibration
        # metadata relative to what's actually on the physical/scanned page.
        super().__init__(orientation='P', unit='mm', format='Letter')
        self.unifontsubset = False
        self.set_auto_page_break(auto=True, margin=15)
        self.course = course
        self.instructors = instructors
        self.student = student
        self.quiz_date = date
        self.quiz_id = quiz_id
        self.quiz_type = quiz_type
        self.module_num = module_num
        self.section_code = section_code
        self.circle_radius = 2.16
        self.set_title('Multiple Choice Quiz')
        self.first_page = True
        self.metadata = {
            "quiz_id": quiz_id,
            "module_num": module_num,
            "page_dimensions": {"width": self.w, "height": self.h}
        }
        self._last_page = False
        self.add_page()
        self.set_font('Helvetica', '', 12)

    def header(self):
        quiz_type = getattr(self, 'quiz_type', 'Quiz')
        if quiz_type != 'Extra Page':
            page_width = self.w
            page_height = self.h
            square_size = 5
            left_x = 20 - square_size / 2
            right_x = page_width - 20 - square_size / 2
            top_y = 18 - square_size / 2
            bottom_y = page_height - 18 - square_size / 2
            self.set_fill_color(0)
            self.rect(left_x, top_y, square_size, square_size, 'F')
            self.rect(right_x, top_y, square_size, square_size, 'F')
            self.rect(left_x, bottom_y, square_size, square_size, 'F')

            page = str(self.page_no())
            if page not in self.metadata:
                self.metadata[page] = {"qr_code": None, "questions": [], "calibration_points": {}}
            self.metadata[page]["calibration_points"] = {
                "top_left": {"x": left_x + square_size/2, "y": top_y + square_size/2},
                "top_right": {"x": right_x + square_size/2, "y": top_y + square_size/2},
                "bottom_left": {"x": left_x + square_size/2, "y": bottom_y + square_size/2}
            }
        else:
            page = str(self.page_no())
            if page not in self.metadata:
                self.metadata[page] = {"qr_code": None, "questions": [], "calibration_points": {}}

        self.set_font('Helvetica', 'B', 12)
        y_title = self.get_y() + 2
        printable_w = self.w - self.l_margin - self.r_margin
        title_w = self.get_string_width(quiz_type)
        title_left_x = self.l_margin + max((printable_w - title_w) / 2, 0)
        label_x = title_left_x + title_w + 3

        if self.page_no() == 1 and self.course is not None:
            self.ln(2)
            self.cell(0, 8, quiz_type, align='C', ln=1)
            if getattr(self, 'quiz_id', None):
                prev_x, prev_y = self.get_x(), self.get_y()
                self.set_font('Helvetica', '', 8)
                self.set_xy(label_x, y_title)
                self.cell(self.get_string_width(self.quiz_id) + 1, 8, self.quiz_id, ln=0)
                self.set_xy(prev_x, prev_y)
            self.set_font('Helvetica', '', 11)
            instructors_clean = self.instructors
            if isinstance(instructors_clean, str):
                instructors_clean = instructors_clean.strip('[]\'\" ')
            student_name = self.student
            if hasattr(self.student, 'name'):
                student_name = self.student.name
            section_part = f"Section: {self.section_code} ________" if self.section_code else "Section: ________"
            self.multi_cell(
                0, 8,
                f"Course: {self.course}    Instructor: {instructors_clean}    Student: {student_name}",
                align='L', ln=1
            )
            self.multi_cell(
                0, 8,
                f"{section_part}    Created: {self.quiz_date}",
                align='L', ln=1
            )
            self.ln(4)
            if quiz_type != 'Extra Page':
                self.cell(0, 8, 'Signature: _______________________________    Date: ______________________________', ln=1)
                self.ln(2)
        else:
            if getattr(self, 'quiz_id', None):
                prev_x, prev_y = self.get_x(), self.get_y()
                self.set_font('Helvetica', '', 8)
                self.set_xy(label_x, y_title)
                self.cell(self.get_string_width(self.quiz_id) + 1, 8, self.quiz_id, ln=0)
                self.set_xy(prev_x, prev_y)
            self.ln(15)

    def get_student_code(self, student_name):
        if hasattr(self, 'metadata') and 'student_code' in self.metadata and self.metadata['student_code']:
            return self.metadata['student_code']
        return None

    def footer(self):
        self.set_font('Helvetica', '', 8)
        quiz_type = getattr(self, 'quiz_type', 'Quiz')
        square_size = 5
        right_x = self.w - 20 - square_size / 2
        bottom_y = self.h - 18 - square_size / 2
        qr_w = 16.875
        qr_h = 16.875
        x_pos = right_x - 10
        y_pos = bottom_y - 11

        student_code = self.get_student_code(self.student) if self.student else ""
        img = generate_qr_code(student_code, self.quiz_id, self.page_no())
        with tempfile.NamedTemporaryFile(suffix='.png', delete=True) as tmpfile:
            img.save(tmpfile, format='PNG')
            tmpfile.flush()
            self.image(tmpfile.name, x=x_pos, y=y_pos, w=qr_w, h=qr_h)
            page = str(self.page_no())
            if page not in self.metadata:
                self.metadata[page] = {"qr_code": None, "questions": []}
            self.metadata[page]["qr_code"] = {
                "x": x_pos, "y": y_pos, "w": qr_w, "h": qr_h, "content": self.quiz_id
            }

    def add_question(self, number, stem, answers, correct_answer=None, is_answer_key=False, question_id=None):
        """Add one question to the PDF."""
        try:
            if stem:
                stem = sanitize_text_for_pdf(stem).lstrip(': ').strip()
            if not stem:
                stem = ''
            # If the stem contains only the question id, replace it with a placeholder
            if stem and question_id is not None and stem == question_id:
                stem = f'[Question text missing; ID: {question_id}]'
            sanitized_answers = [sanitize_text_for_pdf(ans) if ans else ans for ans in answers]
            if len(sanitized_answers) == len(answers):
                answers = sanitized_answers

            self.set_font('Helvetica', 'B', 11)
            self.ln(2)

            stem_lines = stem.split('\n')
            stem_height = len(stem_lines) * 5
            base_answer_height = 6
            line_height = 4
            circle_x = self.get_x() + 3
            text_x = circle_x + 7
            max_width = self.w - text_x - 10

            answer_heights = []
            total_answer_height = 0
            for ans in answers:
                self.set_font('Helvetica', '', 11)
                text_width = self.get_string_width(ans)
                num_lines = max(1, int(text_width / max_width) + 1)
                height = base_answer_height + (num_lines - 1) * line_height
                answer_heights.append(height)
                total_answer_height += height

            total_needed = stem_height + total_answer_height + 3
            square_size = 5
            bottom_y = self.h - 18 - square_size / 2
            qr_top_y = bottom_y - 11
            safe_bottom = qr_top_y - 2
            if self.get_y() + total_needed > safe_bottom:
                self.add_page()

            page = str(self.page_no())
            if page not in self.metadata:
                self.metadata[page] = {"qr_code": None, "questions": [], "calibration_points": {}}
            if not self.metadata[page].get("first_question"):
                self.metadata[page]["first_question"] = {
                    "number": number, "x": 10, "y": self.get_y(), "page_number": page
                }
                self.metadata[page]["page_identifier"] = {
                    "first_question_number": number, "page_number": page
                }

            self.set_x(10)
            if stem_lines:
                try:
                    self.cell(10, 5, f"{number}.", ln=0)
                    self.multi_cell(0, 5, stem_lines[0], align='L')
                    for line in stem_lines[1:]:
                        self.set_x(20)
                        self.multi_cell(0, 5, line, align='L')
                except Exception as e:
                    print(f"Error displaying question {number}: {e}")
                    self.set_x(10)
                    self.cell(10, 5, f"{number}.", ln=1)
                    self.set_x(20)
                    self.multi_cell(0, 5, "[Error displaying question text]", align='L')

            self.set_font('Helvetica', '', 11)
            y_start = self.get_y()
            y_positions = [y_start]
            for i in range(1, len(answers)):
                y_positions.append(y_positions[i-1] + answer_heights[i-1])

            for idx, ans in enumerate(answers):
                self.set_y(y_positions[idx])
                self.set_x(circle_x)
                filled = is_answer_key and correct_answer == idx
                self.draw_answer_circle(self.get_x() + self.circle_radius, self.get_y() + line_height/2,
                                        filled=filled, question_idx=number-1, answer_idx=idx)
                self.set_x(text_x)
                self.multi_cell(0, line_height, ans, align='L')

            self.ln(1)

            page = str(self.page_no())
            if page in self.metadata and len(self.metadata[page]["questions"]) > 0:
                question_idx = len(self.metadata[page]["questions"]) - 1
                self.metadata[page]["questions"][question_idx]["stem"] = stem
                self.metadata[page]["questions"][question_idx]["number"] = number
                self.metadata[page]["questions"][question_idx]["correct_answer"] = correct_answer
                self.metadata[page]["questions"][question_idx]["question_id"] = question_id

        except Exception as e:
            print(f"Error adding question {number}: {e}")
            import traceback
            traceback.print_exc()


# ---------------------------------------------------------------------------
# Quiz creation API
# ---------------------------------------------------------------------------

def _module_quiz_folder(module_number: int, course_folder: str) -> str:
    """Return and create the module quizzes folder."""
    folder = os.path.join(course_folder, f'module{module_number}', 'quizzes')
    os.makedirs(folder, exist_ok=True)
    return folder


def _module_answer_key_folder(module_number: int, course_folder: str) -> str:
    """Return and create the module answer keys folder."""
    folder = os.path.join(course_folder, f'module{module_number}', 'answer_keys')
    os.makedirs(folder, exist_ok=True)
    return folder


def _find_start_attempt(
    engine,
    student_id: int,
    student_code: str,
    module_number: int,
    needed: int,
) -> int:
    """Return the first attempt number for which `needed` consecutive attempts are free."""
    attempt = 1
    while True:
        if all(
            not quiz_attempt_exists(
                engine, student_id, module_number,
                format_quiz_id(student_code, module_number, attempt + i)
            )
            for i in range(needed)
        ):
            return attempt
        attempt += 1


def _select_questions(
    bank_questions: Dict[str, List[Dict]],
    questions_per_bank: Dict[str, int],
    excluded_ids: Optional[set] = None,
) -> Tuple[List[Dict], int]:
    """Select questions from each bank, preferring not-yet-used ones.

    The selection is database-driven: `excluded_ids` contains the question IDs
    already assigned to this student in this module.  If a bank has been
    exhausted, the remaining required questions are drawn from the full bank,
    allowing reuse.  The number of questions that were already in
    `excluded_ids` is returned so the caller can report how many were reused.
    """
    if excluded_ids is None:
        excluded_ids = set()

    initial_excluded = set(excluded_ids)
    selected: List[Dict] = []
    reused_count = 0

    for bank_path, questions in bank_questions.items():
        n = questions_per_bank.get(bank_path, 0)
        if n <= 0:
            continue
        if n > len(questions):
            raise ValueError(
                f"Bank {os.path.basename(bank_path)} requested {n} questions "
                f"but only has {len(questions)}"
            )

        not_used = [q for q in questions if q['id'] not in excluded_ids]
        chosen: List[Dict] = []

        if len(not_used) >= n:
            chosen = random.sample(not_used, n)
        else:
            # Use all not-yet-used questions, then top up from the rest of the bank.
            random.shuffle(not_used)
            chosen = not_used[:]
            chosen_ids = {q['id'] for q in chosen}
            while len(chosen) < n:
                remaining = [q for q in questions if q['id'] not in chosen_ids]
                if not remaining:
                    break
                random.shuffle(remaining)
                q = remaining[0]
                chosen.append(q)
                chosen_ids.add(q['id'])

        for q in chosen:
            if q['id'] in initial_excluded:
                reused_count += 1
            excluded_ids.add(q['id'])
            for oid in q.get('overlap', []):
                excluded_ids.add(oid)
        selected.extend(chosen)

    random.shuffle(selected)
    return selected, reused_count


def _prepare_quiz_questions(selected: List[Dict]) -> List[Dict]:
    """Convert integrated-bank questions into the PDF-ready format and randomize choices."""
    quiz_questions = []
    for q in selected:
        choices = list(q['choices'])
        correct_idx = q['correct_idx']
        if correct_idx is not None and 0 <= correct_idx < len(choices):
            correct_text = choices[correct_idx]
            random.shuffle(choices)
            correct_idx = choices.index(correct_text)

        answers_with_prefixes = [f"{chr(65+i)}. {choices[i]}" for i in range(len(choices))]
        quiz_questions.append({
            'id': q['id'],
            'stem': q['stem'],
            'answers': answers_with_prefixes,
            'correct_idx': correct_idx,
            'feedback': q.get('feedback', ''),
            'context': q.get('context', ''),
        })
    return quiz_questions


def create_quiz_pdf(questions, output_file, is_answer_key=False, course=None, instructors=None,
                    student=None, quiz_date=None, base_quiz_id=None, module_num=None,
                    ensure_even_pages=True, section_code=''):
    """Create a quiz PDF (or answer-key PDF) from prepared questions."""
    instructors_str = instructors
    if isinstance(instructors, list):
        instructors_str = ", ".join(instructors)

    student_name = student
    if hasattr(student, 'name'):
        student_name = student.name

    quiz_type = 'Answer Key' if is_answer_key else 'Quiz'
    pdf = QuizPDF(course, instructors_str, student_name, quiz_date, base_quiz_id, quiz_type, module_num, section_code)
    pdf.metadata.update({
        'course': course,
        'instructors': instructors_str,
        'quiz_date': quiz_date,
        'student': student_name,
        'student_code': student.student_code if hasattr(student, "student_code") else None,
    })

    pdf.ln(15)
    for i, question in enumerate(questions, 1):
        answers = question['answers']
        if not isinstance(answers, list):
            continue
        correct_idx = None
        if is_answer_key:
            correct_idx = question.get('correct_idx')
            if correct_idx is not None:
                try:
                    correct_idx = int(correct_idx)
                    if not (0 <= correct_idx < len(answers)):
                        correct_idx = None
                except (TypeError, ValueError):
                    correct_idx = None
        pdf.add_question(
            i,
            question['stem'],
            answers,
            correct_answer=correct_idx,
            is_answer_key=is_answer_key,
            question_id=question.get('id'),
        )

    pdf._last_page = True
    if ensure_even_pages and pdf.page_no() % 2 != 0:
        original_quiz_type = pdf.quiz_type
        pdf.quiz_type = 'Extra Page'
        pdf.add_page()
        pdf.quiz_type = original_quiz_type
        pdf._last_page = False

    pdf.output(output_file)

    output_dir = os.path.dirname(output_file)
    if is_answer_key:
        metadata_dir = output_dir
    else:
        metadata_dir = os.path.normpath(os.path.join(output_dir, os.pardir, 'JSON'))
    os.makedirs(metadata_dir, exist_ok=True)
    letter = 'A' if is_answer_key else 'Q'
    basename = os.path.basename(output_file)
    basename_no_ext = os.path.splitext(basename)[0]
    metadata_path = os.path.join(metadata_dir, f"{basename_no_ext}M.json")
    for page_num, page_data in pdf.metadata.items():
        if isinstance(page_num, str) and page_num.isdigit():
            page_data["page_number"] = int(page_num)
            if "questions" in page_data:
                page_data["questions"] = [q for q in page_data["questions"] if q.get("number") is not None]
    pdf.metadata["is_answer_key"] = is_answer_key
    with open(metadata_path, "w", encoding="utf-8") as metaf:
        json.dump(pdf.metadata, metaf, indent=2)

    return output_file


def create_quizzes_for_students(
    engine,
    module_number: int,
    student_codes: List[str],
    bank_paths: List[str],
    questions_per_bank: Dict[str, int],
    quiz_date: str,
    course_folder: str,
    attempts: Optional[int] = None,
    has_odt: bool = False,
    odt_template_paths: Optional[List[str]] = None,
    odt_variable_names_list: Optional[List[List[str]]] = None,
    odt_values: Optional[Dict[str, List[Dict]]] = None,
    bank_questions: Optional[Dict[str, List[Dict]]] = None,
) -> Dict[str, List[str]]:
    """Create PDF quizzes for selected students in the module's quizzes folder."""
    if not bank_paths:
        raise ValueError("At least one question bank is required")

    course_info = get_course_info(engine)
    if attempts is None:
        attempts = course_info.get('max_attempts_per_module', 4)

    course = course_info.get('course', 'NBIO 140b')
    instructors = course_info.get('instructors', 'Sacha Nelson and Christine Grienberger')

    if bank_questions is None:
        bank_questions = load_question_banks(bank_paths)
    for bank_path, questions in bank_questions.items():
        if not questions:
            raise ValueError(f"No usable questions in bank: {bank_path}")
        n = questions_per_bank.get(bank_path, 0)
        if n > len(questions):
            raise ValueError(
                f"Bank {os.path.basename(bank_path)} requested {n} questions but only has {len(questions)}"
            )

    quiz_folder = _module_quiz_folder(module_number, course_folder)
    created: Dict[str, List[str]] = {}
    first_bank_path = next(iter(bank_questions)) if bank_questions else ''
    first_bank_name = os.path.basename(first_bank_path) if first_bank_path else ''
    first_bank_size = len(bank_questions[first_bank_path]) if first_bank_path in bank_questions else 0
    first_bank_n = questions_per_bank.get(first_bank_path, 0)

    for code in student_codes:
        code = code.strip()
        if not code:
            continue

        student = get_student_by_code(engine, code)
        if student is None:
            print(f"[WARNING] Student code not found: {code}")
            continue

        excluded_ids = get_student_module_question_ids(
            engine, student.student_id, module_number
        )

        created[code] = []
        student_odt_values = (odt_values or {}).get(code, []) if has_odt else []
        start_attempt = _find_start_attempt(
            engine, student.student_id, student.student_code, module_number, attempts
        )

        for attempt in range(start_attempt, start_attempt + attempts):
            quiz_id = format_quiz_id(student.student_code, module_number, attempt)
            selected, reused = _select_questions(
                bank_questions,
                questions_per_bank,
                excluded_ids=excluded_ids,
            )
            quiz_questions = _prepare_quiz_questions(selected)

            section_code = ''
            if student.section_number is not None:
                sec = get_section(engine, student.section_number)
                if sec:
                    section_code = sec.get('code', '')

            attempt_folder = os.path.join(quiz_folder, f'attempt{attempt}')
            attempt_quiz_folder = os.path.join(attempt_folder, 'questions')
            attempt_key_folder = os.path.join(attempt_folder, 'answers')
            attempt_json_folder = os.path.join(attempt_folder, 'JSON')
            os.makedirs(attempt_quiz_folder, exist_ok=True)
            os.makedirs(attempt_key_folder, exist_ok=True)
            os.makedirs(attempt_json_folder, exist_ok=True)

            quiz_pdf_path = os.path.join(attempt_quiz_folder, f"{artifact_id(quiz_id, 'Q')}.pdf")
            key_pdf_path = os.path.join(attempt_key_folder, f"{artifact_id(quiz_id, 'A')}.pdf")

            create_quiz_pdf(
                quiz_questions,
                quiz_pdf_path,
                is_answer_key=False,
                course=course,
                instructors=instructors,
                student=student,
                quiz_date=quiz_date,
                base_quiz_id=quiz_id,
                module_num=module_number,
                ensure_even_pages=True,
                section_code=section_code,
            )
            create_quiz_pdf(
                quiz_questions,
                key_pdf_path,
                is_answer_key=True,
                course=course,
                instructors=instructors,
                student=student,
                quiz_date=quiz_date,
                base_quiz_id=quiz_id,
                module_num=module_number,
                ensure_even_pages=True,
                section_code=section_code,
            )

            created[code].append(quiz_id)

            odt_index = attempt - start_attempt
            odt_value_for_attempt = (
                student_odt_values[odt_index]
                if odt_index < len(student_odt_values)
                else None
            )
            odt_template_path_for_attempt = ''
            if has_odt and odt_template_paths and odt_index < len(odt_template_paths):
                odt_template_path_for_attempt = odt_template_paths[odt_index]
            odt_variable_names_for_attempt = None
            if has_odt and odt_variable_names_list and odt_index < len(odt_variable_names_list):
                odt_variable_names_for_attempt = odt_variable_names_list[odt_index]
            add_generated_quiz_attempt(
                engine=engine,
                student_id=student.student_id,
                module_number=module_number,
                quiz_id=quiz_id,
                date_taken=quiz_date,
                questions=quiz_questions,
                has_odt=has_odt,
                odt_template_path=odt_template_path_for_attempt,
                odt_variable_names=odt_variable_names_for_attempt,
                odt_variable_values=odt_value_for_attempt,
            )

            if reused > 0:
                print(
                    f"[WARNING] {reused} question(s) reused for quiz {attempt} of module {module_number} "
                    f"for student {code} (qbank {first_bank_name} has only {first_bank_size} "
                    f"questions but {first_bank_n} are needed per quiz for {attempts} quizzes)"
                )

    return created


def get_quiz_page_count(metadata_json_path: str) -> int:
    """Return the total page count from a quiz metadata JSON file.

    Args:
        metadata_json_path: Path to the *M.json file written by create_quiz_pdf.

    Returns:
        Maximum page number found in the metadata (i.e. total pages).
    """
    with open(metadata_json_path, 'r', encoding='utf-8') as f:
        metadata = json.load(f)
    page_nums = [int(k) for k in metadata if isinstance(k, str) and k.isdigit()]
    return max(page_nums) if page_nums else 0


def pdf_page_count(pdf_path: str) -> int:
    """Return the number of pages in a PDF file."""
    return len(PdfReader(pdf_path).pages)


def stamp_page_numbers_to_pdf(pdf_path: str, total_pages: Optional[int] = None) -> int:
    """Stamp 'current:total' centered on every page of a PDF, in place.

    If total_pages is not provided, the PDF's own page count is used.
    """
    reader = PdfReader(pdf_path)
    n = len(reader.pages)
    if total_pages is None:
        total_pages = n
    labels = [f'{i}:{total_pages}' for i in range(1, n + 1)]

    # Match the overlay's page size to the actual PDF being stamped (points -> mm),
    # rather than assuming a fixed paper size, so the stamp lands in the right place
    # regardless of what format the source PDF was authored in.
    mediabox = reader.pages[0].mediabox
    pt_to_mm = 25.4 / 72.0
    page_width_mm = float(mediabox.width) * pt_to_mm
    page_height_mm = float(mediabox.height) * pt_to_mm

    overlay = FPDF(orientation='P', unit='mm', format=(page_width_mm, page_height_mm))
    overlay.set_auto_page_break(False)
    for label in labels:
        overlay.add_page()
        overlay.set_font('Helvetica', '', 10)
        overlay.set_y(overlay.h - 15)
        overlay.cell(0, 10, label, align='C')
    overlay_bytes = bytes(overlay.output())
    overlay_reader = PdfReader(io.BytesIO(overlay_bytes))

    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        page.merge_page(overlay_reader.pages[i])
        writer.add_page(page)

    tmp_path = pdf_path + '.tmp_pages'
    with open(tmp_path, 'wb') as f:
        writer.write(f)
    os.replace(tmp_path, pdf_path)
    return total_pages


def list_module_quizzes(module_number: int, course_folder: str) -> List[str]:
    """Return a list of quiz PDF paths for a module."""
    quiz_folder = _module_quiz_folder(module_number, course_folder)
    return sorted(
        os.path.join(quiz_folder, f)
        for f in os.listdir(quiz_folder)
        if f.endswith('Q.pdf')
    )
