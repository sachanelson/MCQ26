"""MCQ26 scan grading orchestration.

This module ports the high-level grading flow from
bubbleSheet/MCQ/grade_quiz_new.py while keeping scan parsing and answer
extraction where possible.  It intentionally avoids the legacy monolithic
_grade_single_quiz function: parsing, scoring, persistence, and email are
separated so that MCQ26 can mix MCQ and quantitative (ODT) components.

Scan parsing uses quiz_scanner26.py/qr_detector26.py, MCQ26's own copies of
the (adapted) legacy scan-parsing routines - MCQ26 doesn't depend on the
separate bubbleSheet/MCQ repo for grading.
"""
import os
import re
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from PIL import Image

from database26 import (
    create_db_engine,
    get_quiz_questions,
    get_student_by_code,
    record_quiz_attempt,
)
from document_ids26 import format_quiz_id
import bubble_scoring26

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scanner setup
# ---------------------------------------------------------------------------
def _ensure_scanner_ready() -> None:
    """One-time setup needed before importing quiz_scanner26.

    quiz_scanner26.py/qr_detector26.py are MCQ26's own copies of the scan-
    parsing routines (no dependency on the separate bubbleSheet/MCQ repo);
    this just makes sure pyzbar can find the zbar shared library first.
    """
    _ensure_zbar_library_path()


def _ensure_zbar_library_path() -> None:
    """Help pyzbar find the Homebrew-installed zbar shared library.

    On Apple Silicon, `ctypes.util.find_library` (used by pyzbar) doesn't
    search /opt/homebrew/lib by default, so pyzbar.pyzbar.decode() fails to
    import with "Unable to find zbar shared library" even when `brew install
    zbar` has been run. Setting DYLD_LIBRARY_PATH before pyzbar is imported
    fixes this without requiring any shell/venv configuration.
    """
    homebrew_lib = '/opt/homebrew/lib'
    if not os.path.isdir(homebrew_lib):
        return
    existing = os.environ.get('DYLD_LIBRARY_PATH', '')
    if homebrew_lib not in existing.split(os.pathsep):
        os.environ['DYLD_LIBRARY_PATH'] = (
            f'{homebrew_lib}{os.pathsep}{existing}' if existing else homebrew_lib
        )


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
    unresolved_image_path: Optional[str] = None
    overwrite_declined: bool = False
    # Raw scanned page images for this quiz (all pages, printed order). Kept
    # around only while a quiz is held up, so bubble-position detection can
    # be deferred until the quiz_id (and thus its calibration metadata) is
    # known - see resolve_held_up_result. Cleared once no longer needed.
    page_images: List[Any] = field(default_factory=list, repr=False)

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
def _import_scanner():
    _ensure_scanner_ready()
    from quiz_scanner26 import (
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


def _score_against_database(
    engine,
    quiz_id: str,
    student_answers: Dict[int, List[str]],
) -> Tuple[List[ComponentScore], Dict[int, Optional[str]]]:
    """Score scanned answers against the quiz's own stored correct answers.

    Correct answers (and the question count) come from the QuizQuestion rows
    saved in the database when the quiz was generated
    (database26.get_quiz_questions), so no separate answer-key file needs to
    be located on disk - the quiz_id alone is sufficient.
    """
    questions = get_quiz_questions(engine, quiz_id)
    if not questions:
        raise ValueError(f'No generated quiz found in the database for {quiz_id}')

    correct_answers: Dict[int, Optional[str]] = {}
    correct_count = 0
    for question in questions:
        q_num = question['question_number']
        letter = _letter_for_index(question['correct_answer_index'])
        correct_answers[q_num] = letter
        given = student_answers.get(q_num)
        if letter is not None and given and letter in given:
            correct_count += 1

    total = len(questions)
    components = [ComponentScore(
        component_type='mcq',
        correct=correct_count,
        total=total,
        score_pct=100.0 * correct_count / total if total else 0.0,
    )]
    return components, correct_answers


def _read_answers_for_quiz_id(
    course_folder: str, quiz_id: str, page_images: List[Any],
) -> Tuple[Dict[int, List[str]], List[str]]:
    """Locate a quiz's calibration metadata and read its scanned bubble answers.

    Raises ValueError if the metadata file (written at generation time)
    can't be found - this happens if the quiz_id is wrong (e.g. a manual
    resolution typo) or its files were never generated/have been moved.
    """
    metadata = bubble_scoring26.load_quiz_metadata(course_folder, quiz_id)
    if metadata is None:
        path = bubble_scoring26.quiz_metadata_path(course_folder, quiz_id)
        raise ValueError(f'No quiz metadata found for {quiz_id} (expected at {path})')
    return bubble_scoring26.read_quiz_answers(page_images, metadata)


def parse_scan_file(engine, scan_path: str, grading_dir: str, course_folder: str) -> List[GradingResult]:
    """Parse a single scan PDF into a list of GradingResult objects.

    Quiz identity (quiz_id, student_code) is read from the QR code on each
    quiz's first page. For quizzes whose identity is readable, answers are
    read immediately by locating that quiz's own generation-time bubble
    position metadata (see bubble_scoring26) and scoring against the
    database. Any quiz whose ID or student code cannot be read is marked
    `held_up=True`; its first-page image is saved under
    `<grading_dir>/unresolved/` for display, and *all* of its page images are
    kept on the result so bubble-position detection can be deferred until
    the quiz_id becomes known via manual resolution (see
    `resolve_held_up_result`) - the calibration metadata can't be located
    without first knowing which quiz this is.

    Scan-parsing routine used: quiz_scanner26.py:process_scan_file
    """
    process_scan_file, _, _ = _import_scanner()
    scanned_quizzes = process_scan_file(Path(scan_path))
    unresolved_dir = Path(grading_dir) / 'unresolved'

    results = []
    for index, sq in enumerate(scanned_quizzes, 1):
        page_info = []
        page_images = []
        for page in (sq.pages or []):
            page_info.append({
                'page_number': getattr(page, 'page_number', None),
                'page_type': getattr(page, 'page_type', None),
            })
            if getattr(page, 'image', None) is not None:
                page_images.append(page.image)

        is_unresolved = not sq.quiz_id or sq.quiz_id.startswith('unknown_') or not sq.student_code

        result = GradingResult(
            quiz_id=sq.quiz_id,
            student_code=sq.student_code,
            module_number=_extract_module_number(sq.quiz_id) if not is_unresolved else 0,
            pages=page_info,
            scan_file=str(scan_path),
        )

        if is_unresolved:
            result.held_up = True
            result.page_images = page_images
            result.validation_issues.append('Could not read quiz ID / student code from QR code.')
            if page_images:
                unresolved_dir.mkdir(parents=True, exist_ok=True)
                image_path = unresolved_dir / f'unresolved_{index}.png'
                try:
                    Image.fromarray(page_images[0]).save(image_path)
                    result.unresolved_image_path = str(image_path)
                except Exception as e:
                    logger.warning(f"Could not save unresolved scan image: {e}")
        else:
            try:
                student_answers, issues = _read_answers_for_quiz_id(course_folder, sq.quiz_id, page_images)
                result.student_answers = student_answers
                result.validation_issues.extend(issues)
                components, correct_answers = _score_against_database(engine, sq.quiz_id, student_answers)
                result.components = components
                result.correct_answers = correct_answers
            except ValueError as e:
                result.held_up = True
                result.page_images = page_images
                result.validation_issues.append(str(e))

        results.append(result)
    return results


def resolve_held_up_result(
    engine,
    result: GradingResult,
    module_number: int,
    student_code: str,
    quiz_number: int,
    course_folder: str,
) -> GradingResult:
    """Manually resolve a held-up scan using grader-supplied identifiers.

    Builds the quiz_id from *module_number*, *student_code*, and
    *quiz_number* (the 4-digit attempt sequence after the underscore),
    verifies the student exists, then locates that quiz's calibration
    metadata (now resolvable since quiz_id is known) to read its scanned
    answers and scores them against the database. Mutates and returns
    *result*. Raises ValueError if the student, quiz metadata, or generated
    quiz can't be found.
    """
    student = get_student_by_code(engine, student_code)
    if student is None:
        raise ValueError(f'No enrolled student found with code {student_code!r}')

    quiz_id = format_quiz_id(student_code, module_number, quiz_number)
    student_answers, issues = _read_answers_for_quiz_id(course_folder, quiz_id, result.page_images)
    components, correct_answers = _score_against_database(engine, quiz_id, student_answers)

    result.quiz_id = quiz_id
    result.student_code = student_code
    result.module_number = module_number
    result.student_answers = student_answers
    result.validation_issues.extend(issues)
    result.components = components
    result.correct_answers = correct_answers
    result.held_up = False
    result.page_images = []
    return result


def grade_block_scans(block_id: int, course_info: Optional[Dict] = None) -> List[GradingResult]:
    """Process all scans for a quiz block and return graded results.

    Routine used: quiz_scanner26.py:process_block_scans
    """
    _, process_block_scans, _ = _import_scanner()
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
def _parsed_quizzes_dir(grading_dir: str) -> Path:
    return Path(grading_dir) / 'parsed_quizzes26'


def save_grading_artifacts(result: GradingResult, grading_dir: str) -> Path:
    """Save JSON artifacts that the manual regrade dialog can reload.

    Legacy equivalent: bubbleSheet/MCQ/grade_quiz_new.py writes
    {quiz_id}_results.json and {quiz_id}_correct_answers.json under
    parsed_quizzes/.  MCQ26 keeps the same idea but stores everything in one
    file per quiz under `<grading_dir>/parsed_quizzes26/`, where *grading_dir*
    is the grading session's own directory (see grading_session26.py).
    """
    out_dir = _parsed_quizzes_dir(grading_dir)
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


def load_grading_artifact(grading_dir: str, quiz_id: str) -> Optional[Dict[str, Any]]:
    """Load a previously saved grading artifact."""
    results_file = _parsed_quizzes_dir(grading_dir) / f"{quiz_id}_results.json"
    if not results_file.exists():
        return None
    with open(results_file, 'r') as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Detailed feedback
# ---------------------------------------------------------------------------
def generate_detailed_feedback(engine, quiz_id: str, incorrect_questions: List[int]) -> str:
    """Build detailed feedback text for incorrect questions.

    Feedback is read directly from the QuizQuestion rows saved in the
    database when the quiz was generated (database26.get_quiz_questions),
    keyed by question_number - no feedback text file needs to be located.
    """
    if not incorrect_questions:
        return "No specific feedback available for your incorrect answers."

    questions = {q['question_number']: q for q in get_quiz_questions(engine, quiz_id)}

    parts = ["\nDETAILED FEEDBACK:\n"]
    for q_num in sorted(incorrect_questions):
        feedback = (questions.get(q_num) or {}).get('feedback_text', '').strip()
        parts.append(f"Question {q_num}: {feedback or 'No specific feedback available for this question.'}")
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
    grading_session_id: Optional[int] = None,
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
        grading_session_id=grading_session_id,
    )


# ---------------------------------------------------------------------------
# High-level flow
# ---------------------------------------------------------------------------
def record_results(
    engine,
    results: List[GradingResult],
    grading_dir: str,
    grading_session_id: Optional[int] = None,
    date_taken: Optional[str] = None,
    send_feedback: bool = False,
) -> List[Any]:
    """Save artifacts and record quiz attempts for all resolved results.

    Results still marked `held_up` are skipped - they must be resolved first
    via `resolve_held_up_result`. Returns the list of recorded Quiz rows.
    """
    recorded = []
    for result in results:
        if result.held_up:
            logger.warning(f"Skipping unresolved scan (quiz {result.quiz_id}): {result.validation_issues}")
            continue
        save_grading_artifacts(result, grading_dir)
        quiz = record_grading_result(
            engine, result, date_taken=date_taken, grading_session_id=grading_session_id,
        )
        if quiz is not None:
            recorded.append(quiz)
            if send_feedback:
                _send_feedback_if_enabled(engine, result)
    return recorded


def grade_and_record_scan_file(
    engine,
    scan_path: str,
    grading_dir: str,
    course_folder: str,
    grading_session_id: Optional[int] = None,
    date_taken: Optional[str] = None,
    send_feedback: bool = False,
) -> Tuple[List[Any], List[GradingResult]]:
    """Grade a scan file end-to-end and record everything that's resolvable.

    Any quiz whose QR code couldn't be read is left unresolved (not recorded)
    and returned in the second element of the result tuple so it can be
    resolved interactively (see `resolve_held_up_result`) and recorded
    afterwards with `record_results`.

    Returns (recorded_quizzes, pending_results).
    """
    results = parse_scan_file(engine, scan_path, grading_dir, course_folder)
    recorded = record_results(
        engine, results, grading_dir,
        grading_session_id=grading_session_id,
        date_taken=date_taken,
        send_feedback=send_feedback,
    )
    pending = [result for result in results if result.held_up]
    return recorded, pending


def _send_feedback_if_enabled(engine, result: GradingResult) -> None:
    """Queue/send feedback email if autosend is enabled."""
    from email26 import generate_and_send_quiz_feedback
    incorrect = [
        q for q, ans in result.student_answers.items()
        if q in result.correct_answers and result.correct_answers[q] not in ans
    ]
    detailed = None
    if incorrect:
        try:
            detailed = generate_detailed_feedback(engine, result.quiz_id, incorrect)
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
