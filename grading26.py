"""MCQ26 scan grading orchestration.

This module ports the high-level grading flow from
bubbleSheet/MCQ/grade_quiz_new.py while keeping scan parsing and answer
extraction where possible.  It intentionally avoids the legacy monolithic
_grade_single_quiz function: parsing, scoring, persistence, and email are
separated so that MCQ26 can mix MCQ and quantitative (ODT) components.

Scan parsing currently delegates to the legacy bubbleSheet/MCQ/quiz_scanner.py
routines.  Those routines can be copied and adapted to MCQ26 later without
changing this orchestration layer.
"""
import os
import re
import sys
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from database26 import (
    create_db_engine,
    get_student_by_code,
    record_quiz_attempt,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Legacy MCQ package path setup
# ---------------------------------------------------------------------------
def _ensure_legacy_mcq_path() -> None:
    """Make bubbleSheet/MCQ importable as the 'MCQ' package.

    Some legacy modules use bare imports such as 'from database import ...'
    instead of 'from MCQ.database import ...', so we also add bubbleSheet/MCQ
    itself to sys.path as a compatibility shim.
    """
    text_processing_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bubble_sheet_dir = os.path.join(text_processing_dir, 'bubbleSheet')
    mcq_pkg_dir = os.path.join(bubble_sheet_dir, 'MCQ')
    for p in (bubble_sheet_dir, mcq_pkg_dir):
        if p not in sys.path:
            sys.path.insert(0, p)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class ComponentScore:
    """Score for one quiz component (e.g. MCQ section or quant section)."""
    component_type: str  # 'mcq' or 'quant'
    correct: int
    total: int
    score_pct: float   # 0-100


@dataclass
class GradingResult:
    """Result of grading a single scanned quiz."""
    quiz_id: str
    student_code: Optional[str]
    module_number: int
    pages: List[Dict[str, Any]] = field(default_factory=list)
    student_answers: Dict[int, List[str]] = field(default_factory=dict)
    correct_answers: Dict[int, Optional[str]] = field(default_factory=dict)
    components: List[ComponentScore] = field(default_factory=list)
    validation_issues: List[str] = field(default_factory=list)
    held_up: bool = False
    scan_file: Optional[str] = None
    qsession_folder: Optional[str] = None

    @property
    def total_score(self) -> float:
        """Return the overall percentage score (0-100)."""
        if not self.components:
            return 0.0
        # Weight each component by its number of questions.
        total_correct = sum(c.correct for c in self.components)
        total_questions = sum(c.total for c in self.components)
        if total_questions == 0:
            return 0.0
        return 100.0 * total_correct / total_questions

    @property
    def mcq_score(self) -> Optional[float]:
        mcq = [c for c in self.components if c.component_type == 'mcq']
        if not mcq:
            return None
        correct = sum(c.correct for c in mcq)
        total = sum(c.total for c in mcq)
        return 100.0 * correct / total if total else None

    @property
    def quant_score(self) -> Optional[float]:
        quant = [c for c in self.components if c.component_type == 'quant']
        if not quant:
            return None
        correct = sum(c.correct for c in quant)
        total = sum(c.total for c in quant)
        return 100.0 * correct / total if total else None


# ---------------------------------------------------------------------------
# Scan parsing (delegated to legacy routines for now)
# ---------------------------------------------------------------------------
def _import_legacy_scanner():
    _ensure_legacy_mcq_path()
    from MCQ.quiz_scanner import (
        process_scan_file,
        process_block_scans,
        ScannedQuiz,
    )
    return process_scan_file, process_block_scans, ScannedQuiz


def _letter_for_index(idx: Optional[int]) -> Optional[str]:
    if idx is None:
        return None
    return chr(65 + idx)


def _index_for_letter(letter: str) -> Optional[int]:
    if not letter or len(letter) != 1:
        return None
    letter = letter.upper()
    if 'A' <= letter <= 'Z':
        return ord(letter) - 65
    return None


def parse_scan_file(scan_path: str) -> List[GradingResult]:
    """Parse a single scan PDF into a list of GradingResult objects.

    Legacy routine used: bubbleSheet/MCQ/quiz_scanner.py:process_scan_file
    """
    process_scan_file, _, _ = _import_legacy_scanner()
    scanned_quizzes = process_scan_file(Path(scan_path))
    results = []
    for sq in scanned_quizzes:
        # Convert legacy letter answers to index lists.
        student_answers: Dict[int, List[str]] = {}
        correct_answers: Dict[int, Optional[str]] = {}
        for q_num, letter in (sq.answers or {}).items():
            student_answers[int(q_num)] = [letter] if isinstance(letter, str) else list(letter)

        # Legacy ScannedQuiz.score is already a percentage (0-100) or None.
        score_pct = sq.score if sq.score is not None else 0.0
        total_questions = len(sq.answers or {})
        correct_count = round(score_pct * total_questions / 100.0) if total_questions else 0

        components = []
        if total_questions > 0:
            components.append(ComponentScore(
                component_type='mcq',
                correct=correct_count,
                total=total_questions,
                score_pct=score_pct,
            ))

        page_info = []
        for page in (sq.pages or []):
            page_info.append({
                'page_number': getattr(page, 'page_number', None),
                'page_type': getattr(page, 'page_type', None),
            })

        results.append(GradingResult(
            quiz_id=sq.quiz_id,
            student_code=sq.student_code,
            module_number=_extract_module_number(sq.quiz_id),
            pages=page_info,
            student_answers=student_answers,
            correct_answers=correct_answers,
            components=components,
            scan_file=str(scan_path),
        ))
    return results


def grade_block_scans(block_id: int, course_info: Optional[Dict] = None) -> List[GradingResult]:
    """Process all scans for a quiz block and return graded results.

    Legacy routine used: bubbleSheet/MCQ/quiz_scanner.py:process_block_scans
    """
    _, process_block_scans, _ = _import_legacy_scanner()
    scanned_quizzes = process_block_scans(block_id, course_info)
    # Reuse parse logic by converting ScannedQuiz objects.
    results = []
    for sq in scanned_quizzes:
        score_pct = sq.score if sq.score is not None else 0.0
        total_questions = len(sq.answers or {})
        correct_count = round(score_pct * total_questions / 100.0) if total_questions else 0
        components = []
        if total_questions > 0:
            components.append(ComponentScore(
                component_type='mcq',
                correct=correct_count,
                total=total_questions,
                score_pct=score_pct,
            ))
        page_info = []
        for page in (sq.pages or []):
            page_info.append({
                'page_number': getattr(page, 'page_number', None),
                'page_type': getattr(page, 'page_type', None),
            })
        results.append(GradingResult(
            quiz_id=sq.quiz_id,
            student_code=sq.student_code,
            module_number=_extract_module_number(sq.quiz_id),
            pages=page_info,
            student_answers={int(k): ([v] if isinstance(v, str) else list(v))
                             for k, v in (sq.answers or {}).items()},
            correct_answers={},
            components=components,
            scan_file=None,
        ))
    return results


def _extract_module_number(quiz_id: str) -> int:
    """Best-effort extraction of module number from a quiz ID."""
    if not quiz_id:
        return 0
    m = re.search(r'[Mm](\d+)', quiz_id)
    if m:
        return int(m.group(1))
    # Fallback: look for any leading digits.
    m = re.search(r'(\d+)', quiz_id)
    return int(m.group(1)) if m else 0


# ---------------------------------------------------------------------------
# Artifact persistence for regrade
# ---------------------------------------------------------------------------
def _parsed_quizzes_dir(qsession_folder: str) -> Path:
    return Path(qsession_folder) / 'parsed_quizzes26'


def save_grading_artifacts(result: GradingResult, qsession_folder: str) -> Path:
    """Save JSON artifacts that the manual regrade dialog can reload.

    Legacy equivalent: bubbleSheet/MCQ/grade_quiz_new.py writes
    {quiz_id}_results.json and {quiz_id}_correct_answers.json under
    parsed_quizzes/.  MCQ26 keeps the same idea but stores everything in one
    file per quiz under parsed_quizzes26/.
    """
    out_dir = _parsed_quizzes_dir(qsession_folder)
    out_dir.mkdir(parents=True, exist_ok=True)
    results_file = out_dir / f"{result.quiz_id}_results.json"

    payload = {
        'quiz_id': result.quiz_id,
        'student_code': result.student_code,
        'module_number': result.module_number,
        'scan_file': result.scan_file,
        'score': round(result.total_score),
        'components': [
            {'component_type': c.component_type,
             'correct': c.correct,
             'total': c.total,
             'score_pct': c.score_pct}
            for c in result.components
        ],
        'student_answers': result.student_answers,
        'correct_answers': result.correct_answers,
        'validation_issues': result.validation_issues,
        'held_up': result.held_up,
        'timestamp': datetime.now().isoformat(),
    }
    with open(results_file, 'w') as f:
        json.dump(payload, f, indent=2)
    logger.info(f"Saved grading artifacts to {results_file}")
    return results_file


def load_grading_artifact(qsession_folder: str, quiz_id: str) -> Optional[Dict[str, Any]]:
    """Load a previously saved grading artifact."""
    results_file = _parsed_quizzes_dir(qsession_folder) / f"{quiz_id}_results.json"
    if not results_file.exists():
        return None
    with open(results_file, 'r') as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Detailed feedback
# ---------------------------------------------------------------------------
def generate_detailed_feedback(
    qsession_folder: str,
    module_number: int,
    incorrect_questions: List[int],
    quiz_id: str,
    wrong_answers: Optional[Dict[int, str]] = None,
) -> str:
    """Build detailed feedback text for incorrect questions.

    Legacy equivalent: bubbleSheet/MCQ/generate_signup_email.py:generate_detailed_feedback
    MCQ26 reads from the same feedback folder layout.
    """
    feedback_dir = Path(qsession_folder) / 'feedback'
    if not feedback_dir.exists():
        return "No detailed feedback available."

    feedback_file = feedback_dir / f"{quiz_id}F.txt"
    if not feedback_file.exists():
        alt_dir = Path(qsession_folder) / quiz_id / f"{quiz_id}F"
        alt_file = alt_dir / f"{quiz_id}F.txt"
        if alt_file.exists():
            feedback_file = alt_file
        else:
            return "No detailed feedback available for this quiz."

    try:
        lines = [line.strip() for line in feedback_file.read_text().splitlines() if line.strip()]
    except Exception as e:
        logger.warning(f"Could not read feedback file {feedback_file}: {e}")
        return "No detailed feedback available."

    feedback_items: Dict[int, str] = {}
    if module_number == 0:
        # Module 0 pairs question-id lines with per-answer feedback lines.
        question_id = None
        for line in lines:
            if line.endswith('.'):
                question_id = int(line.strip('.').split()[-1]) if line.strip('.').split()[-1].isdigit() else None
            elif line and line[0].isalpha() and len(line) > 1 and line[1] == '.' and question_id is not None:
                answer_letter = line[0]
                fb_text = line[2:].strip()
                if wrong_answers and question_id in wrong_answers and wrong_answers[question_id] == answer_letter:
                    feedback_items[question_id] = fb_text
    else:
        all_pairs = []
        i = 0
        while i < len(lines):
            if lines[i].endswith('.'):
                qid = lines[i].strip('.')
                fb = lines[i + 1].strip() if i + 1 < len(lines) else ""
                all_pairs.append((qid, fb))
                i += 2
            else:
                i += 1
        for q_num in incorrect_questions:
            idx = q_num - 1
            if 0 <= idx < len(all_pairs):
                feedback_items[q_num] = all_pairs[idx][1]
            else:
                feedback_items[q_num] = "No specific feedback available for this question."

    if not feedback_items:
        return "No specific feedback available for your incorrect answers."

    parts = ["\nDETAILED FEEDBACK:\n"]
    for q_num in sorted(feedback_items):
        parts.append(f"Question {q_num}: {feedback_items[q_num]}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Database recording
# ---------------------------------------------------------------------------
def record_grading_result(
    engine,
    result: GradingResult,
    date_taken: Optional[str] = None,
    time_taken: Optional[str] = None,
    date_signed_up: Optional[str] = None,
) -> Optional[Any]:
    """Record a GradingResult in the MCQ26 database.

    Returns the created Quiz row or None if the student is not found.
    """
    if not result.student_code:
        logger.error(f"Cannot record quiz {result.quiz_id}: no student code")
        return None

    student = get_student_by_code(engine, result.student_code)
    if student is None:
        logger.error(f"Student code {result.student_code} not found in database")
        return None

    if date_taken is None:
        date_taken = datetime.now().strftime('%Y-%m-%d')
    if time_taken is None:
        time_taken = datetime.now().strftime('%H:%M:%S')

    total_questions = sum(c.total for c in result.components)
    score_int = round(result.total_score)

    return record_quiz_attempt(
        engine=engine,
        student_id=student.student_id,
        module_number=result.module_number,
        quiz_id=result.quiz_id,
        date_taken=date_taken,
        score=score_int,
        total_questions=total_questions,
        time_taken=time_taken,
        date_signed_up=date_signed_up,
    )


# ---------------------------------------------------------------------------
# High-level flow
# ---------------------------------------------------------------------------
def grade_and_record_scan_file(
    engine,
    scan_path: str,
    qsession_folder: str,
    date_taken: Optional[str] = None,
    send_feedback: bool = False,
):
    """Grade a scan file, save artifacts, record attempts, and optionally email.

    This is the MCQ26 replacement for the legacy grade_quiz_new.py single-file
    flow.
    """
    results = parse_scan_file(scan_path)
    recorded = []
    for result in results:
        if result.held_up:
            logger.warning(f"Quiz {result.quiz_id} held up: {result.validation_issues}")
        save_grading_artifacts(result, qsession_folder)
        quiz = record_grading_result(engine, result, date_taken=date_taken)
        if quiz is not None:
            recorded.append(quiz)
            if send_feedback:
                _send_feedback_if_enabled(engine, result, qsession_folder)
    return recorded


def _send_feedback_if_enabled(engine, result: GradingResult, qsession_folder: str) -> None:
    """Queue/send feedback email if autosend is enabled."""
    from email26 import generate_and_send_quiz_feedback
    incorrect = [
        q for q, ans in result.student_answers.items()
        if q in result.correct_answers and result.correct_answers[q] not in ans
    ]
    detailed = None
    if incorrect:
        try:
            detailed = generate_detailed_feedback(
                qsession_folder=qsession_folder,
                module_number=result.module_number,
                incorrect_questions=incorrect,
                quiz_id=result.quiz_id,
            )
        except Exception as e:
            logger.warning(f"Could not generate detailed feedback: {e}")

    generate_and_send_quiz_feedback(
        student_code=result.student_code,
        quiz_score=round(result.total_score),
        module_number=result.module_number,
        date_taken=datetime.now().strftime('%Y-%m-%d'),
        detailed_feedback=detailed,
        is_regrade=False,
    )
