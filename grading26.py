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
import threading
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
from PIL import Image
from fpdf import FPDF
from pypdf import PdfReader, PdfWriter

from database26 import (
    create_db_engine,
    get_course_info,
    get_quiz_by_id,
    get_quiz_questions,
    get_quizzes_for_student,
    get_student_by_code,
    record_quiz_attempt,
)
from document_ids26 import artifact_id, format_quiz_id
import bubble_scoring26

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Test mode
# ---------------------------------------------------------------------------
# When True, grading completes but no database writes are performed. Feedback
# emails are queued for the test recipient, and quant pages are still
# extracted. Set to False when moving to production runs.
TEST_MODE = False


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
# Transfer review
# ---------------------------------------------------------------------------
# Codes of placeholder 'students' whose printed quizzes are spares handed to
# someone else (e.g. 'SaQ' = "Sample Questions"). During grading these are
# treated like quizzes of non-enrolled students: the grader is asked whether
# to transfer the quiz to the student who actually took it.
SPARE_STUDENT_CODES = {'saq'}


def is_spare_student_code(student_code: Optional[str]) -> bool:
    return (student_code or '').strip().lower() in SPARE_STUDENT_CODES


def needs_transfer_review(engine, student_code: Optional[str]) -> bool:
    """True if a quiz printed for *student_code* should prompt a transfer."""
    if not student_code:
        return False
    if is_spare_student_code(student_code):
        return True
    student = get_student_by_code(engine, student_code)
    return student is None or not student.enrolled


def _save_first_page_image(pages, page_images, grading_dir, quiz_id) -> Optional[str]:
    """Save the quiz's printed page 1 (or its first scanned page) as a PNG."""
    if not page_images:
        return None
    index = next(
        (i for i, page in enumerate(pages or [])
         if getattr(page, 'quiz_page_number', None) == 1 and i < len(page_images)),
        0,
    )
    output_dir = Path(grading_dir) / 'transfer_review'
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / f'{quiz_id}_page_1.png'
    try:
        Image.fromarray(page_images[index]).save(image_path)
    except Exception as e:
        logger.warning(f'Could not save first-page image for {quiz_id}: {e}')
        return None
    return str(image_path)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class ComponentScore:
    """Score for one quiz component (e.g. MCQ section or quant section)."""
    component_type: str  # 'mcq' or 'quant'
    correct: float
    total: float
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
    grading_dir: Optional[str] = None
    grading_session_id: Optional[int] = None
    date_taken: Optional[str] = None
    unresolved_image_path: Optional[str] = None
    # Saved image of the quiz's first page, written only for quizzes that
    # need a transfer review (see needs_transfer_review).
    first_page_image_path: Optional[str] = None
    ambiguous_image_paths: Dict[int, str] = field(default_factory=dict)
    missing_image_paths: Dict[int, str] = field(default_factory=dict)
    overwrite_declined: bool = False
    feedback_sent: bool = False
    # Set when the quiz is transferred to a different student: the new owner
    # is owed a feedback email even if the recorded score is unchanged.
    feedback_pending: bool = False
    # Raw scanned page images for this quiz (all pages, printed order). Kept
    # around only while a quiz is held up, so bubble-position detection can
    # be deferred until the quiz_id (and thus its calibration metadata) is
    # known - see resolve_held_up_result. Cleared once no longer needed.
    page_images: List[Any] = field(default_factory=list, repr=False)

    # Set when a no-QR page was manually attributed to a quiz whose question
    # pages were already all present - the page is a Quant/ODT (or blank)
    # page of that quiz, not an MCQ page, so nothing is scored or recorded
    # for this result (its scan index is merged into the sibling result's
    # quant_scan_pages).
    resolved_as_quant: bool = False
    # A no-QR page that is probably but not certainly blank: the grader is
    # asked to confirm rather than auto-dropping, since it might be a faintly
    # written Quant/ODT page.
    maybe_blank: bool = False
    # The grader confirmed the scan is a stray blank page - not a quiz at all.
    resolved_as_blank: bool = False

    # Quant/ODT extraction metadata (populated during parse_scan_file)
    has_quant: bool = False
    quant_total_points: float = 0.0
    mcq_page_count: int = 0
    expected_total_pages: int = 0
    quant_scan_pages: List[int] = field(default_factory=list)
    section_number: Optional[int] = None
    last_name: str = ''
    first_name: str = ''
    student_name: str = ''
    # Name the quiz was associated with when the scan was identified. Left
    # unchanged if the quiz is later transferred to another student so the
    # replacement list can show both names.
    original_student_name: str = ''
    quiz_questions: List[Dict] = field(default_factory=list, repr=False)

    @property
    def total_score(self) -> float:
        """Return the overall percentage score (0-100)."""
        if not self.components:
            return 0.0
        mcq = next((c for c in self.components if c.component_type == 'mcq'), None)
        quant = next((c for c in self.components if c.component_type == 'quant'), None)
        if mcq is None:
            return 0.0
        quant_achieved = quant.correct if quant else 0
        total_points = mcq.total + (self.quant_total_points if self.has_quant else 0)
        if total_points == 0:
            return 0.0
        return 100.0 * (mcq.correct + quant_achieved) / total_points

    @property
    def mcq_score(self) -> Optional[float]:
        mcq = [c for c in self.components if c.component_type == 'mcq']
        if not mcq:
            return None
        correct = sum(c.correct for c in mcq)
        mcq_total = sum(c.total for c in mcq)
        total = mcq_total + (self.quant_total_points if self.has_quant else 0)
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
        pdf_to_images,
    )
    return process_scan_file, process_block_scans, ScannedQuiz, pdf_to_images


def _scanner_classes():
    """Return the lightweight scanner classes/helpers needed for in-process parsing."""
    _ensure_scanner_ready()
    from quiz_scanner26 import (
        QuizPage,
        ScannedQuiz,
        group_pages_by_quiz_id,
        extract_quiz_id_and_page_from_qr,
        derive_student_code_from_quiz_id,
    )
    return QuizPage, ScannedQuiz, group_pages_by_quiz_id, extract_quiz_id_and_page_from_qr, derive_student_code_from_quiz_id


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
    questions: Optional[List[Dict]] = None,
) -> Tuple[List[ComponentScore], Dict[int, Optional[str]], List[Dict]]:
    """Score scanned answers against the quiz's own stored correct answers.

    Correct answers (and the question count) come from the QuizQuestion rows
    saved in the database when the quiz was generated
    (database26.get_quiz_questions), so no separate answer-key file needs to
    be located on disk - the quiz_id alone is sufficient.

    If *questions* is already provided it is reused, avoiding a second DB
    lookup for the same quiz.
    """
    if questions is None:
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
    return components, correct_answers, questions


def _report_answer_reading_gaps(
    quiz_id: str,
    questions: List[Dict],
    student_answers: Dict[int, List[str]],
) -> None:
    """Warn the grader (terminal) about MCQ questions with no detected mark
    or with multiple marks. Unanswered questions are absent from
    *student_answers* and count as incorrect in scoring, so listing them
    here is the only signal that a mark may have been missed by the reader
    (e.g. a check mark instead of a filled bubble)."""
    blank = sorted(
        q['question_number'] for q in questions
        if q['question_number'] not in student_answers
    )
    multiple = sorted(
        number for number, letters in student_answers.items() if len(letters) > 1
    )
    if blank:
        logger.warning(
            f'{quiz_id}: no answer detected for {len(blank)} MCQ question(s): '
            f'{", ".join(map(str, blank))}'
        )
    if multiple:
        logger.warning(
            f'{quiz_id}: multiple answers marked for MCQ question(s): '
            + ', '.join(f'{n} ({"/".join(student_answers[n])})' for n in multiple)
        )


def rescore_result(
    result: GradingResult,
    forced_correct_question_ids: Optional[set] = None,
) -> GradingResult:
    forced_correct_question_ids = forced_correct_question_ids or set()
    correct_count = 0
    for question in result.quiz_questions:
        q_num = question['question_number']
        letter = result.correct_answers.get(q_num)
        given = result.student_answers.get(q_num, [])
        forced_correct = (
            question.get('question_id') in forced_correct_question_ids or
            (question.get('question_text') or '').strip() in forced_correct_question_ids
        )
        if forced_correct or (letter is not None and letter in given):
            correct_count += 1
    total = len(result.quiz_questions)
    result.components = [ComponentScore(
        component_type='mcq',
        correct=correct_count,
        total=total,
        score_pct=100.0 * correct_count / total if total else 0.0,
    )]
    return result


def _ambiguous_page_images(
    metadata: Dict[str, Any], page_images: List[Any], student_answers: Dict[int, List[str]],
    grading_dir: str, quiz_id: str,
) -> Dict[int, str]:
    ambiguous = {number for number, answers in student_answers.items() if len(answers) > 1}
    if not ambiguous:
        return {}
    output_dir = Path(grading_dir) / 'ambiguous_answers'
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    page_keys = sorted(
        (key for key in metadata if isinstance(key, str) and key.isdigit() and metadata.get(key, {}).get('questions')),
        key=int,
    )
    for page_index, page_key in enumerate(page_keys):
        if page_index >= len(page_images):
            break
        numbers = {
            question.get('number') for question in metadata[page_key].get('questions', [])
        } & ambiguous
        if not numbers:
            continue
        image_path = output_dir / f'{quiz_id}_page_{page_key}.png'
        Image.fromarray(page_images[page_index]).save(image_path)
        for number in numbers:
            paths[number] = str(image_path)
    return paths


def _missing_page_images(
    metadata: Dict[str, Any], page_images: List[Any], questions: List[Dict],
    student_answers: Dict[int, List[str]], grading_dir: str, quiz_id: str,
) -> Dict[int, str]:
    """Save PNG snapshots of MCQ pages that contain questions with no
    detected answer, so they can be reviewed in MissingAnswerDialog."""
    missing = {
        q['question_number'] for q in questions
        if q['question_number'] not in student_answers
    }
    if not missing:
        return {}
    output_dir = Path(grading_dir) / 'missing_answers'
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    page_keys = sorted(
        (key for key in metadata if isinstance(key, str) and key.isdigit() and metadata.get(key, {}).get('questions')),
        key=int,
    )
    for page_index, page_key in enumerate(page_keys):
        if page_index >= len(page_images):
            break
        numbers = {
            question.get('number') for question in metadata[page_key].get('questions', [])
        } & missing
        if not numbers:
            continue
        image_path = output_dir / f'{quiz_id}_page_{page_key}.png'
        Image.fromarray(page_images[page_index]).save(image_path)
        for number in numbers:
            paths[number] = str(image_path)
    return paths


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


# ---------------------------------------------------------------------------
# Generated-quiz pagination helpers
# ---------------------------------------------------------------------------
def _find_generated_quiz_pdf(course_folder: str, quiz_id: str) -> Optional[Path]:
    """Return the path to the generated combined MCQ+Quant PDF for *quiz_id*,
    searching the module quiz folder and any section subfolders.
    """
    document = bubble_scoring26.parse_document_id(quiz_id)
    module_folder = Path(course_folder).expanduser() / f'module{document.module_number}' / 'quizzes'
    if not module_folder.is_dir():
        return None
    target = f'{quiz_id}Q.pdf'
    for root, _dirs, files in os.walk(module_folder):
        if target in files:
            return Path(root) / target
    return None


def _get_mcq_page_count(course_folder: str, quiz_id: str) -> int:
    """Return the number of MCQ pages from the quiz's generation metadata.

    The metadata JSON records one entry per printed MCQ page; the highest
    numbered key is therefore the MCQ page count.
    """
    metadata = bubble_scoring26.load_quiz_metadata(course_folder, quiz_id)
    if not metadata:
        return 0
    page_nums = [
        int(k) for k in metadata.keys()
        if isinstance(k, str) and k.isdigit() and isinstance(metadata[k], dict)
    ]
    return max(page_nums) if page_nums else 0


def _get_generated_pdf_page_count(course_folder: str, quiz_id: str) -> int:
    """Return the total page count of the generated combined MCQ+Quant PDF."""
    path = _find_generated_quiz_pdf(course_folder, quiz_id)
    if path is None or not path.is_file():
        return 0
    try:
        return len(PdfReader(str(path)).pages)
    except Exception as e:
        logger.warning(f'Could not read page count from {path}: {e}')
        return 0


# ---------------------------------------------------------------------------
# Quant page grouping
# ---------------------------------------------------------------------------
def _group_key_for_quiz(page) -> Optional[str]:
    """Return the quiz_id for a classified page, or None if not decodable."""
    return getattr(page, 'quiz_id', None) or None


def _scan_index(page) -> int:
    return getattr(page, 'page_number', 0) or 0


def _reconcile_quant_pages(
    scanned_quizzes: List[Any],
    expected_info: Dict[str, Dict[str, Any]],
) -> List[Any]:
    """Re-attribute unresolved (no-QR) pages to the MCQ quiz they belong to.

    The generated PDF tells us the total expected number of pages for each
    quiz.  Pages without a QR code (blank backs and Quant/ODT pages) are
    assigned to the nearest resolved group that still needs pages to reach its
    expected total.  This handles the common case where Quant/ODT pages are
    appended after the MCQ section but occasionally appear out of order in the
    scanned batch.
    """
    resolved = [sq for sq in scanned_quizzes if not _is_unknown_id(sq.quiz_id)]
    unresolved = [sq for sq in scanned_quizzes if _is_unknown_id(sq.quiz_id)]

    if not unresolved or not resolved:
        return scanned_quizzes

    # unresolved entries from process_scan_file are one page each
    unresolved_pages = []
    for sq in unresolved:
        if sq.pages:
            unresolved_pages.extend(sq.pages)

    # initialise capacity for each resolved group
    capacities: Dict[str, int] = {}
    quiz_pages: Dict[str, List[Any]] = {}
    quiz_indices: Dict[str, List[int]] = {}
    mcq_incomplete: set = set()
    for sq in resolved:
        info = expected_info.get(sq.quiz_id, {})
        expected = info.get('expected_total_pages', 0) or len(sq.pages)
        capacities[sq.quiz_id] = max(0, expected - len(sq.pages))
        quiz_pages[sq.quiz_id] = list(sq.pages)
        quiz_indices[sq.quiz_id] = [_scan_index(p) for p in sq.pages]
        mcq_count = info.get('mcq_page_count', 0)
        if mcq_count:
            present = {getattr(p, 'quiz_page_number', None) for p in sq.pages}
            if not set(range(1, mcq_count + 1)) <= present:
                mcq_incomplete.add(sq.quiz_id)
                # Only quizzes whose MCQ pages are all present may absorb
                # no-QR pages: every missing page is then a Quant/ODT (or
                # blank) page, which carries no QR.  A quiz still missing
                # MCQ pages might be the true owner of a no-QR page whose QR
                # simply failed to decode, so attributing other pages to it
                # would misfile them as Quant pages.
                capacities[sq.quiz_id] = 0

    # assign each unresolved page to the nearest group that still needs pages
    for page in sorted(unresolved_pages, key=_scan_index):
        page_idx = _scan_index(page)
        # If the physically nearest resolved quiz is missing MCQ pages, this
        # no-QR page could be one of them (its QR failed to decode).  Leave
        # it unresolved for manual resolution instead of letting a more
        # distant quiz absorb it as a Quant page - which would both misfile
        # the page and consume capacity meant for that quiz's own pages.
        nearest = min(
            (qid for qid in quiz_indices if quiz_indices[qid]),
            key=lambda qid: min(abs(page_idx - idx) for idx in quiz_indices[qid]),
            default=None,
        )
        if nearest is not None and nearest in mcq_incomplete:
            continue
        candidates = [
            (qid, min(abs(_scan_index(page) - idx) for idx in quiz_indices[qid]) if quiz_indices[qid] else float('inf'))
            for qid, cap in capacities.items() if cap > 0
        ]
        # prefer a group whose pages appear before this unresolved page
        def _sort_key(item):
            qid, dist = item
            indices = quiz_indices[qid]
            before = [idx for idx in indices if idx <= _scan_index(page)]
            after = [idx for idx in indices if idx > _scan_index(page)]
            # groups whose pages are before this page take priority; otherwise distance
            has_before = 1 if not before else 0
            return (has_before, dist)

        candidates.sort(key=_sort_key)
        if candidates:
            chosen = candidates[0][0]
            quiz_pages[chosen].append(page)
            capacities[chosen] -= 1
            quiz_indices[chosen].append(_scan_index(page))

    # rebuild scanned_quizzes preserving resolved order and appending any
    # still-unassigned unresolved pages at the end
    new_quizzes = []
    assigned_page_ids = set()
    for sq in resolved:
        sq.pages = quiz_pages[sq.quiz_id]
        assigned_page_ids.update(id(p) for p in sq.pages)
        new_quizzes.append(sq)

    for sq in unresolved:
        unassigned = [p for p in (sq.pages or []) if id(p) not in assigned_page_ids]
        if unassigned:
            new_quizzes.append(type(sq)(
                quiz_id=sq.quiz_id,
                student_code=sq.student_code,
                pages=unassigned,
                answers=sq.answers,
                score=sq.score,
            ))

    return new_quizzes if new_quizzes else scanned_quizzes


def _is_unknown_id(quiz_id: Optional[str]) -> bool:
    return not quiz_id or quiz_id.startswith('unknown_')


# ---------------------------------------------------------------------------
# Main parsing
# ---------------------------------------------------------------------------
def parse_scan_file(
    engine,
    scan_path: str,
    grading_dir: str,
    course_folder: str,
    single_sides: bool = False,
) -> List[GradingResult]:
    """Parse a single scan PDF into a list of GradingResult objects.

    Quant/ODT pages are extracted before any scoring happens.  We first do a
    silent QR pass over the whole scan to identify the MCQ pages for each
    quiz, then separate the MCQ and Quant pages, write a copy of the MCQ
    pages and a copy of the Quant pages to the grading session directory,
    and grade only the MCQ copy.  No QR decoding is attempted on the Quant
    pages during the grading pass.
    """
    _, _, _, pdf_to_images = _import_scanner()
    QuizPage, ScannedQuiz, group_pages_by_quiz_id, extract_quiz_id_and_page_from_qr, derive_student_code_from_quiz_id = _scanner_classes()

    images = pdf_to_images(Path(scan_path))
    if not images:
        return []

    unresolved_dir = Path(grading_dir) / 'unresolved'

    # First pass: decode QR on every page, but silence the detector's
    # "no QR found" errors because Quant/ODT pages legitimately have none.
    # Each scan file gets a fresh binarisation threshold - batches scanned at
    # different times can differ in brightness/contrast.
    from qr_detector26 import reset_qr_threshold
    reset_qr_threshold()
    qr_logger = logging.getLogger('qr_detector26')
    old_qr_level = qr_logger.level
    qr_logger.setLevel(logging.CRITICAL)
    try:
        pages = []
        for i, img in enumerate(images):
            quiz_id, qr_page_number = extract_quiz_id_and_page_from_qr(img)
            page_type = 'first' if qr_page_number == 1 else 'middle'
            pages.append(QuizPage(
                page_number=i,
                page_type=page_type,
                image=img,
                quiz_id=quiz_id,
                quiz_page_number=qr_page_number,
                answers={},
            ))
    finally:
        qr_logger.setLevel(old_qr_level)

    # Classify no-QR pages by ink coverage.  Clearly blank pages (stray
    # scanner blanks, blank backs) are dropped outright - they hold no
    # student work and must not consume a quiz's spare-page capacity or
    # prompt for manual resolution.  Borderline pages are kept but flagged
    # so the grader can confirm 'blank' instead of being forced to identify
    # them; at this ink level a page could equally be a faint Quant/ODT page.
    maybe_blank_scan_indices = set()
    kept_pages = []
    dropped_blank = 0
    for page in pages:
        if page.quiz_id or page.image is None:
            kept_pages.append(page)
            continue
        if _is_blank_page(page.image, max_dark_fraction=_BLANK_DARK_FRACTION_MAX):
            dropped_blank += 1
            continue
        if _is_blank_page(page.image, max_dark_fraction=_MAYBE_BLANK_DARK_FRACTION_MAX):
            maybe_blank_scan_indices.add(_scan_index(page))
        kept_pages.append(page)
    if dropped_blank:
        logger.info(f'Dropped {dropped_blank} blank page(s) from scan {scan_path}')
    pages = kept_pages

    # Note any in-quiz page numbers that were scanned more than once for a
    # quiz - group_pages_by_quiz_id keeps only the first occurrence of each
    # page number, and the grader should be told that extra copies existed.
    page_counts = Counter(
        (p.quiz_id, p.quiz_page_number) for p in pages
        if p.quiz_id and p.quiz_page_number is not None
    )
    duplicate_scan_pages: Dict[str, List[int]] = {}
    for (qid, page_num), count in page_counts.items():
        if count > 1:
            duplicate_scan_pages.setdefault(qid, []).append(page_num)

    # Group pages by the quiz_id decoded from their QR code.  Pages without
    # a QR code become single-page unresolved groups for now.
    quiz_page_groups = group_pages_by_quiz_id(pages)
    scanned_quizzes = []
    for group in quiz_page_groups:
        quiz_id = group[0].quiz_id
        student_code = derive_student_code_from_quiz_id(quiz_id) if quiz_id else None
        scanned_quizzes.append(ScannedQuiz(
            quiz_id=quiz_id or f"unknown_{Path(scan_path).stem}_{len(scanned_quizzes)}",
            student_code=student_code,
            pages=group,
            answers={},
        ))

    # Pre-load pagination and student information once per resolved quiz
    expected_info: Dict[str, Dict[str, Any]] = {}
    for sq in scanned_quizzes:
        if _is_unknown_id(sq.quiz_id):
            continue
        quiz = get_quiz_by_id(engine, sq.quiz_id)
        if not quiz:
            continue
        total = _get_generated_pdf_page_count(course_folder, sq.quiz_id)
        mcq_pages = _get_mcq_page_count(course_folder, sq.quiz_id)
        if total == 0:
            total = mcq_pages
        student = quiz.get('student') or {}
        expected_info[sq.quiz_id] = {
            'expected_total_pages': total,
            'mcq_page_count': mcq_pages,
            'has_odt': quiz.get('has_odt', False),
            'quant_total_points': quiz.get('quant_total_points', 0),
            'section_number': student.get('section_number'),
            'student_name': student.get('name', ''),
            'student_code': student.get('student_code', ''),
            'last_name': _last_name(student.get('name', '')),
            'first_name': _first_name(student.get('name', '')),
        }

    # Re-attribute unresolved (no-QR) pages to the nearest resolved quiz that
    # still needs pages to reach its generated total.  These are Quant/ODT
    # pages (or blank backs) that have no QR of their own.
    scanned_quizzes = _reconcile_quant_pages(scanned_quizzes, expected_info)

    # Separate MCQ pages from Quant/ODT pages, and write separate copies of
    # each before any grading occurs.  The MCQ images are then graded as a
    # copy of the original scan with the Quant pages removed.
    reader = PdfReader(str(scan_path))
    mcq_writer = PdfWriter()
    quant_writer = PdfWriter()
    quant_scan_pages_by_quiz: Dict[str, List[int]] = {}
    for sq in scanned_quizzes:
        info = expected_info.get(sq.quiz_id, {})
        mcq_count = info.get('mcq_page_count', 0)
        mcq_pages = []
        quant_pages = []
        for page in (sq.pages or []):
            if _is_unknown_id(sq.quiz_id) or mcq_count == 0:
                # Unknown quiz or missing metadata: keep all pages with the
                # unresolved/MCQ copy so manual resolution or metadata errors
                # can be reported as before.
                mcq_pages.append(page)
                continue
            page_num = getattr(page, 'quiz_page_number', None)
            if page_num is not None and isinstance(page_num, int) and page_num <= mcq_count:
                mcq_pages.append(page)
            else:
                # In single-sided mode, drop blank back pages so they are not
                # treated as Quant pages and do not trigger further QR work.
                if single_sides and page.image is not None and _is_blank_page(page.image):
                    continue
                quant_pages.append(page)
        sq.pages = mcq_pages
        quant_indices = [getattr(p, 'page_number', None) for p in quant_pages]
        quant_indices = [i for i in quant_indices if i is not None and 0 <= i < len(reader.pages)]
        if quant_indices:
            quant_scan_pages_by_quiz[sq.quiz_id] = quant_indices
            for scan_idx in quant_indices:
                quant_writer.add_page(reader.pages[scan_idx])
    for sq in scanned_quizzes:
        for page in (sq.pages or []):
            scan_idx = getattr(page, 'page_number', None)
            if scan_idx is not None and 0 <= scan_idx < len(reader.pages):
                mcq_writer.add_page(reader.pages[scan_idx])

    mcq_pdf_path = Path(grading_dir) / 'mcq_only.pdf'
    quant_pdf_path = Path(grading_dir) / 'quant_extracted.pdf'
    try:
        with open(mcq_pdf_path, 'wb') as f:
            mcq_writer.write(f)
    except Exception as e:
        logger.warning(f'Could not write MCQ-only copy: {e}')
    try:
        with open(quant_pdf_path, 'wb') as f:
            quant_writer.write(f)
    except Exception as e:
        logger.warning(f'Could not write extracted Quant PDF: {e}')

    # Grade the MCQ-only copy (in-memory page set, equivalent to the file copy).
    results = []
    for index, sq in enumerate(scanned_quizzes, 1):
        page_info = []
        page_images = []
        for page in (sq.pages or []):
            page_info.append({
                'page_number': getattr(page, 'page_number', None),
                'page_type': getattr(page, 'page_type', None),
                'quiz_page_number': getattr(page, 'quiz_page_number', None),
            })
            if getattr(page, 'image', None) is not None:
                page_images.append(page.image)
        image_pages = [p for p in (sq.pages or []) if getattr(p, 'image', None) is not None]

        is_unresolved = not sq.quiz_id or sq.quiz_id.startswith('unknown_') or not sq.student_code

        result = GradingResult(
            quiz_id=sq.quiz_id,
            student_code=sq.student_code,
            module_number=_extract_module_number(sq.quiz_id) if not is_unresolved else 0,
            pages=page_info,
            scan_file=str(scan_path),
        )

        dup_pages = duplicate_scan_pages.get(sq.quiz_id)
        if dup_pages:
            result.validation_issues.append(
                f"Page(s) {', '.join(str(p) for p in sorted(dup_pages))} of this "
                'quiz were scanned more than once; only the first occurrence of '
                'each was graded.'
            )

        if not is_unresolved:
            info = expected_info.get(sq.quiz_id, {})
            result.has_quant = bool(info.get('has_odt'))
            result.quant_total_points = float(info.get('quant_total_points') or 0)
            result.mcq_page_count = info.get('mcq_page_count', 0)
            result.expected_total_pages = info.get('expected_total_pages', 0)
            result.section_number = info.get('section_number')
            result.student_name = info.get('student_name', '')
            result.original_student_name = result.student_name
            result.last_name = info.get('last_name', '')
            result.first_name = info.get('first_name', '')
            result.student_code = info.get('student_code') or sq.student_code

            # Quant pages were already separated out and their original scan
            # indices are stored for later collation.
            result.quant_scan_pages = list(quant_scan_pages_by_quiz.get(sq.quiz_id, []))

            if needs_transfer_review(engine, result.student_code):
                result.first_page_image_path = _save_first_page_image(
                    image_pages, page_images, grading_dir, sq.quiz_id)

        if is_unresolved:
            result.held_up = True
            result.maybe_blank = any(
                _scan_index(p) in maybe_blank_scan_indices for p in (sq.pages or []))
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
                metadata = bubble_scoring26.load_quiz_metadata(course_folder, sq.quiz_id) or {}
                result.ambiguous_image_paths = _ambiguous_page_images(
                    metadata, page_images, student_answers, grading_dir, sq.quiz_id,
                )
                components, correct_answers, questions = _score_against_database(
                    engine, sq.quiz_id, student_answers, questions=result.quiz_questions or None,
                )
                result.components = components
                result.correct_answers = correct_answers
                result.quiz_questions = questions
                result.missing_image_paths = _missing_page_images(
                    metadata, page_images, questions, student_answers, grading_dir, sq.quiz_id,
                )
                _report_answer_reading_gaps(sq.quiz_id, questions, student_answers)
            except ValueError as e:
                result.held_up = True
                result.page_images = page_images
                result.validation_issues.append(str(e))

        results.append(result)
    return results


def _last_name(full_name: str) -> str:
    parts = (full_name or '').strip().split()
    return parts[-1].lower() if parts else ''


def _first_name(full_name: str) -> str:
    parts = (full_name or '').strip().split()
    return parts[0].lower() if parts else ''


def _last_first(full_name: str) -> str:
    parts = (full_name or '').split()
    return f'{parts[-1]}, {" ".join(parts[:-1])}' if parts else ''


def _question_page_numbers(metadata: Dict[str, Any]) -> set:
    """1-based numbers of metadata pages that carry MCQ questions."""
    if not metadata:
        return set()
    return {
        int(k) for k, v in metadata.items()
        if isinstance(k, str) and k.isdigit() and isinstance(v, dict) and v.get('questions')
    }


def _mcq_pages_accounted_for(
    quiz_id: str,
    metadata: Dict[str, Any],
    result: GradingResult,
    all_results: List[GradingResult],
) -> Optional[GradingResult]:
    """Return a resolved sibling result for *quiz_id* that already contains
    every question-bearing MCQ page, or None.

    When a no-QR page is attributed to a quiz that already has all of its
    question pages, the page can't be an unreadable MCQ page - it must be a
    Quant/ODT or blank back page (those carry no QR code).
    """
    question_pages = _question_page_numbers(metadata)
    if not question_pages:
        return None
    for sibling in all_results:
        if sibling is result or sibling.held_up or sibling.quiz_id != quiz_id:
            continue
        scanned_pages = {
            p.get('quiz_page_number') for p in sibling.pages
        }
        if question_pages <= scanned_pages:
            return sibling
    return None


def resolve_held_up_result(
    engine,
    result: GradingResult,
    module_number: int,
    student_code: str,
    quiz_number: int,
    course_folder: str,
    all_results: Optional[List[GradingResult]] = None,
) -> GradingResult:
    """Manually resolve a held-up scan using grader-supplied identifiers.

    Builds the quiz_id from *module_number*, *student_code*, and
    *quiz_number* (the 4-digit attempt sequence after the underscore),
    verifies the student exists, then locates that quiz's calibration
    metadata (now resolvable since quiz_id is known) to read its scanned
    answers and scores them against the database. Mutates and returns
    *result*. Raises ValueError if the student, quiz metadata, or generated
    quiz can't be found.

    If *all_results* is given and the named quiz's MCQ question pages are
    already all present in a resolved sibling result, the held-up page(s)
    can't be MCQ pages of that quiz - they are Quant/ODT (or blank) pages,
    which carry no QR code. In that case their scan indices are merged into
    the sibling's quant_scan_pages, the result is flagged resolved_as_quant,
    and no answer reading or scoring is performed for it.
    """
    student = get_student_by_code(engine, student_code)
    if student is None:
        raise ValueError(f'No enrolled student found with code {student_code!r}')

    quiz_id = format_quiz_id(student_code, module_number, quiz_number)

    if all_results is not None:
        metadata = bubble_scoring26.load_quiz_metadata(course_folder, quiz_id)
        if metadata is None:
            path = bubble_scoring26.quiz_metadata_path(course_folder, quiz_id)
            raise ValueError(f'No quiz metadata found for {quiz_id} (expected at {path})')
        sibling = _mcq_pages_accounted_for(quiz_id, metadata, result, all_results)
        if sibling is not None:
            for page_info in result.pages:
                scan_idx = page_info.get('page_number')
                if scan_idx is not None and scan_idx not in sibling.quant_scan_pages:
                    sibling.quant_scan_pages.append(scan_idx)
            result.quiz_id = quiz_id
            result.student_code = student_code
            result.module_number = module_number
            result.section_number = student.section_number
            result.student_name = student.name or ''
            result.original_student_name = result.student_name
            result.last_name = _last_name(result.student_name)
            result.first_name = _first_name(result.student_name)
            result.resolved_as_quant = True
            result.held_up = False
            result.page_images = []
            result.validation_issues.append(
                f'Page carries no QR code and all MCQ question pages of {quiz_id} '
                'were already present; treated as a Quant page of that quiz.'
            )
            return result

    student_answers, issues = _read_answers_for_quiz_id(course_folder, quiz_id, result.page_images)
    components, correct_answers, questions = _score_against_database(engine, quiz_id, student_answers)
    _report_answer_reading_gaps(quiz_id, questions, student_answers)

    result.quiz_id = quiz_id
    result.student_code = student_code
    result.module_number = module_number
    result.student_answers = student_answers
    result.validation_issues.extend(issues)
    result.components = components
    result.correct_answers = correct_answers
    result.quiz_questions = questions
    quiz = get_quiz_by_id(engine, quiz_id) or {}
    result.has_quant = bool(quiz.get('has_odt'))
    result.quant_total_points = float(quiz.get('quant_total_points') or 0)
    result.section_number = student.section_number
    result.student_name = student.name or ''
    result.original_student_name = result.student_name
    result.last_name = _last_name(result.student_name)
    result.first_name = _first_name(result.student_name)
    result.held_up = False
    result.page_images = []
    return result


def grade_block_scans(block_id: int, course_info: Optional[Dict] = None) -> List[GradingResult]:
    """Process all scans for a quiz block and return graded results.

    Routine used: quiz_scanner26.py:process_block_scans
    """
    _, process_block_scans, _, _ = _import_scanner()
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
# Blank-page detection
# ---------------------------------------------------------------------------
# Ink coverage limits for classifying no-QR pages: below the first a page is
# certainly blank and dropped automatically; below the second it is probably
# blank but might be a faintly written page, so the grader confirms.
_BLANK_DARK_FRACTION_MAX = 0.005
_MAYBE_BLANK_DARK_FRACTION_MAX = 0.02


def _is_blank_page(image: np.ndarray, dark_threshold: int = 250, max_dark_fraction: float = 0.005) -> bool:
    """Return True if *image* looks like a blank back page.

    Blank backs inserted by the generator are essentially white with at most
    a small page-number stamp.  Content pages contain enough text/diagrams
    that they exceed the threshold.
    """
    if image is None:
        return True
    if len(image.shape) == 3:
        gray = np.mean(image, axis=2).astype(np.uint8)
    else:
        gray = image
    dark_fraction = np.mean(gray < dark_threshold)
    return dark_fraction < max_dark_fraction


# ---------------------------------------------------------------------------
# Quant page extraction
# ---------------------------------------------------------------------------
def _extract_and_collate_quant_pages(
    results: List[GradingResult],
    scan_path: str,
    grading_dir: str,
    single_sides: bool = False,
) -> List[str]:
    """Extract Quant/ODT pages from the scan into one combined PDF.

    When *single_sides* is True, blank back pages are detected and ignored
    (condition 4/5).  When False, both fronts and backs are kept (condition
    3).  All resolved results' Quant pages are written to a single
    `extracted_quant/quant_pages.pdf`, ordered by last name.
    """
    if not scan_path or not os.path.isfile(scan_path):
        return []

    scan_path = Path(scan_path)
    try:
        from quiz_scanner26 import pdf_to_images
        all_images = pdf_to_images(scan_path)
    except Exception as e:
        logger.warning(f'Could not render scan images for quant extraction: {e}')
        return []

    group = [
        result for result in results
        if not result.held_up and result.quant_scan_pages
    ]
    group.sort(key=lambda r: (r.last_name or '', r.first_name or '', r.quiz_id or ''))

    writer = PdfWriter()
    reader = PdfReader(str(scan_path))

    for result in group:
        for scan_idx in result.quant_scan_pages:
            if scan_idx < 0 or scan_idx >= len(reader.pages):
                continue
            if single_sides and scan_idx < len(all_images) and _is_blank_page(all_images[scan_idx]):
                continue
            writer.add_page(reader.pages[scan_idx])

    if not writer.pages:
        return []
    out_dir = Path(grading_dir) / 'extracted_quant'
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / 'quant_pages.pdf'
    with open(out_path, 'wb') as f:
        writer.write(f)
    return [str(out_path)]


def _start_quant_transcription(results: List[GradingResult], scan_path: str,
                               grading_dir: str) -> Optional[threading.Thread]:
    """Transcribe the extracted Quant handwriting in a background thread.

    Writes `extracted_quant/quant_transcription.md` (one section per quiz)
    when an LLM API key is configured; see quant_transcription26.py.

    CURRENTLY UNUSED: the calls in record_results are commented out.  Quant
    pages are read by the instructor from extracted_quant/quant_pages.pdf.
    Re-enable those calls (and configure ~/.mcq26/llm_config.json) to get an
    LLM-generated transcription of the handwritten answers as a reading aid.
    """
    try:
        from quant_transcription26 import (
            OUTPUT_NAME, quizzes_from_results, resolve_backend, transcribe_quizzes,
        )
        provider, _, api_key = resolve_backend()
        if not api_key:
            logger.info(f'Quant transcription skipped: no {provider} API key configured')
            return None
        quizzes = quizzes_from_results(results, scan_path)
        if not quizzes:
            return None
        out_path = Path(grading_dir) / 'extracted_quant' / OUTPUT_NAME

        def run():
            try:
                transcribe_quizzes(quizzes, out_path, title=Path(grading_dir).name)
            except Exception:
                logger.exception('Quant transcription failed')

        thread = threading.Thread(target=run, name='quant-transcription')
        thread.start()
        return thread
    except Exception:
        logger.exception('Could not start Quant transcription')
        return None


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
        'has_quant': result.has_quant,
        'quant_total_points': result.quant_total_points,
        'quant_scan_pages': result.quant_scan_pages,
        'section_number': result.section_number,
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
# Quant/ODT feedback
# ---------------------------------------------------------------------------
def _odt_definition_path(odt_template_path: str) -> Optional[str]:
    """Derive the .txt definition file path from an ODT template path.

    E.g. '.../ODTquiz1b_Template.odt' -> '.../ODTquiz1b.txt'
    """
    if not odt_template_path:
        return None
    base = odt_template_path
    if base.lower().endswith('.odt'):
        base = base[:-4]
    if base.endswith('_Template'):
        base = base[:-len('_Template')]
    txt_path = base + '.txt'
    return txt_path if os.path.isfile(txt_path) else None


def _load_quant_feedback(engine, quiz_id: str) -> Dict[str, str]:
    """Load the [FEEDBACK] section from the ODT definition file for a quiz.

    Returns a dict keyed by question number string (e.g. '1', '2') or 'All',
    with feedback text as values. Returns an empty dict if no feedback is
    available.
    """
    quiz = get_quiz_by_id(engine, quiz_id)
    if not quiz or not quiz.get('has_odt'):
        return {}
    odt_template_path = quiz.get('odt_template_path', '')
    txt_path = _odt_definition_path(odt_template_path)
    if not txt_path:
        return {}
    try:
        from OneUn import load_problem_definition
        definition = load_problem_definition(txt_path)
        return definition.feedback or {}
    except Exception as e:
        logger.warning(f"Could not load quant feedback from {txt_path}: {e}")
        return {}


def generate_quant_feedback_text(
    engine,
    result: GradingResult,
) -> Optional[str]:
    """Build quant/ODT feedback text based on the student's quant score.

    Always returns a line showing the quant score.  When the student earned
    full marks the line reads "(10/10) No Feedback".  When the score is below
    maximum, applicable feedback entries from the template's [FEEDBACK]
    section are appended after the score.

    Returns None only when the result has no quant component at all.
    """
    if not result.has_quant:
        return None

    quant = next((c for c in result.components if c.component_type == 'quant'), None)
    if quant is None:
        return None

    earned = quant.correct
    total = result.quant_total_points
    score_prefix = f"({earned:g}/{total:g})"

    # Full marks — no remedial feedback needed
    if earned >= total:
        return f"Quantitative problems: {score_prefix} No Feedback"

    feedback_entries = _load_quant_feedback(engine, result.quiz_id)

    if not feedback_entries:
        return f"Quantitative problems: {score_prefix}"

    parts = []
    if 'All' in feedback_entries:
        parts.append(f"Quantitative problems: {score_prefix} {feedback_entries['All']}")
    else:
        # Without per-question scoring, present all feedback entries as
        # general quant-section advice.
        for key in sorted(feedback_entries.keys(), key=lambda k: (not k.isdigit(), int(k) if k.isdigit() else 0)):
            parts.append(f"Quantitative problems: {score_prefix} {feedback_entries[key]}")

    return "\n".join(parts) if parts else f"Quantitative problems: {score_prefix}"


# ---------------------------------------------------------------------------
# Detailed feedback
# ---------------------------------------------------------------------------
def generate_detailed_feedback(
    engine,
    quiz_id: str,
    incorrect_questions: List[int],
    questions: Optional[List[Dict]] = None,
) -> str:
    """Build detailed feedback text for incorrect questions.

    Feedback is read directly from the QuizQuestion rows saved in the
    database when the quiz was generated (database26.get_quiz_questions),
    keyed by question_number - no feedback text file needs to be located.

    If *questions* is already provided it is reused, avoiding a second DB query.
    """
    if not incorrect_questions:
        return "No specific feedback available for your incorrect answers."

    if questions is None:
        questions = {q['question_number']: q for q in get_quiz_questions(engine, quiz_id)}
    else:
        questions = {q['question_number']: q for q in questions}

    parts = []
    for q_num in sorted(incorrect_questions):
        feedback = (questions.get(q_num) or {}).get('feedback_text', '').strip()
        if feedback:
            parts.append(f"Question {q_num}: Please review {feedback}")
        else:
            parts.append(f"Question {q_num}: No specific feedback available for this question.")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Test-mode email output
# ---------------------------------------------------------------------------
def _queue_test_feedback_email(
    engine,
    result: GradingResult,
    is_regrade: bool = False,
) -> None:
    from email26 import queue_test_quiz_feedback

    def _normalize(d):
        if not d:
            return {}
        out = {}
        for k, v in d.items():
            try:
                out[int(k)] = v
            except (ValueError, TypeError):
                out[k] = v
        return out

    student_answers = _normalize(result.student_answers)
    correct_answers = _normalize(result.correct_answers)
    question_numbers = {
        int(q['question_number']) for q in result.quiz_questions
    } if result.quiz_questions else set(correct_answers.keys())

    incorrect = []
    for q in sorted(question_numbers):
        correct = correct_answers.get(q)
        given = student_answers.get(q, [])
        if correct is not None and correct not in given:
            incorrect.append(q)

    logger.info(f'{result.quiz_id}: {len(incorrect)} incorrect MCQ question(s) for test feedback: {incorrect}')
    detailed = None
    if incorrect:
        try:
            detailed = generate_detailed_feedback(engine, result.quiz_id, incorrect, questions=result.quiz_questions)
        except Exception as e:
            logger.warning(f"Could not generate detailed feedback: {e}")

    # Append quant/ODT feedback if applicable
    quant_feedback = None
    if result.has_quant:
        try:
            quant_feedback = generate_quant_feedback_text(engine, result)
        except Exception as e:
            logger.warning(f"Could not generate quant feedback: {e}")
    if quant_feedback:
        if detailed:
            detailed += f"\n\n{quant_feedback}"
        else:
            detailed = quant_feedback

    try:
        queued = queue_test_quiz_feedback(
            student_code=result.student_code,
            quiz_score=round(result.total_score),
            module_number=result.module_number,
            date_taken=result.date_taken or datetime.now().strftime('%Y-%m-%d'),
            detailed_feedback=detailed,
            is_regrade=is_regrade,
            engine=engine,
        )
        if not queued.get('queued'):
            logger.warning(f"Could not queue test feedback email for {result.quiz_id}")
    except Exception as e:
        logger.warning(f"Could not queue test feedback email for {result.quiz_id}: {e}")


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
    finalize: bool = True,
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

    mcq = next((c for c in result.components if c.component_type == 'mcq'), None)
    quant = next((c for c in result.components if c.component_type == 'quant'), None)
    total_questions = (mcq.total if mcq else 0) + (result.quant_total_points if result.has_quant else 0)
    score_int = round(result.total_score) if finalize else None

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
        mcq_points_earned=mcq.correct if mcq else None,
        mcq_points_possible=mcq.total if mcq else None,
        quant_points_earned=quant.correct if quant else None,
        quant_total_points=result.quant_total_points if result.has_quant else 0,
        preserve_score=not finalize,
    )


# ---------------------------------------------------------------------------
# High-level flow
# ---------------------------------------------------------------------------
def _collate_quant_answer_keys(
    results: List[GradingResult], course_folder: str, grading_dir: str,
) -> List[Path]:
    """Collate all Quant answer-key PDFs into one file, ordered by last name."""
    entries = []
    for result in results:
        if not result.has_quant or result.section_number is None:
            continue
        attempt = int(result.quiz_id.split('_')[-1])
        answer_path = (
            Path(course_folder) / f'module{result.module_number}' / 'quizzes'
            / f'section{result.section_number}' / f'attempt{attempt}' / 'answers'
            / 'QUANT' / f'{result.quiz_id}_quant_answer.pdf'
        )
        if answer_path.is_file():
            last = result.last_name or _last_name(result.student_name)
            first = result.first_name or _first_name(result.student_name)
            entries.append((last, first, result.quiz_id, answer_path))
        else:
            logger.warning(f'Quant answer key not found for {result.quiz_id}: {answer_path}')

    if not entries:
        return []
    output_dir = Path(grading_dir) / 'quant_answer_keys'
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / 'quant_answer_keys.pdf'
    writer = PdfWriter()
    for _last, _first, _qid, path in sorted(entries):
        for page in PdfReader(path).pages:
            writer.add_page(page)
    with open(output_path, 'wb') as output:
        writer.write(output)
    return [output_path]


def _write_replacement_list(results: List[GradingResult], grading_dir: str,
                            engine=None) -> Path:
    """Write a checklist of the quizzes graded in this session.

    Grouped by module, then alphabetically by last name, so it can be used to
    assemble the set of replacement quizzes for the next quiz session.
    Written to `<grading_dir>/replacement_list.txt`.
    """
    name_cache: Dict[str, str] = {}

    def _full_name(r: GradingResult) -> str:
        if r.student_name:
            return r.student_name
        if r.student_code and engine is not None:
            if r.student_code not in name_cache:
                student = get_student_by_code(engine, r.student_code)
                name_cache[r.student_code] = (
                    student.name if student and student.name else '')
            return name_cache[r.student_code]
        return ''

    def _printed_name(r: GradingResult) -> str:
        # Name the quiz was associated with before any transfer.
        return r.original_student_name or _full_name(r)

    usable = [
        r for r in results
        if not r.held_up and not r.overwrite_declined
        and not r.resolved_as_quant and not r.resolved_as_blank
    ]
    by_module: Dict[int, List[GradingResult]] = {}
    for r in usable:
        module = r.module_number if r.module_number is not None else 0
        by_module.setdefault(module, []).append(r)

    lines = [
        f'Replacement List — {Path(grading_dir).name}',
        f'Generated: {datetime.now():%Y-%m-%d %H:%M}',
        '',
    ]
    for module in sorted(by_module):
        lines.append(f'Module {module or "?"}')
        entries = sorted(
            by_module[module],
            key=lambda r: (_last_name(_printed_name(r)),
                           _first_name(_printed_name(r)),
                           r.quiz_id or ''))
        for r in entries:
            name = _last_first(_printed_name(r))
            transferred_to = _last_first(_full_name(r))
            if transferred_to and transferred_to.lower() != name.lower():
                name = f'{name} ({transferred_to})' if name else transferred_to
            lines.append(f'    [ ] {r.quiz_id:<14} {name}')
        lines.append('')

    path = Path(grading_dir) / 'replacement_list.txt'
    path.write_text('\n'.join(lines).rstrip() + '\n')
    logger.info(f'Replacement list written to {path}')
    return path


def _attempt_number(quiz_id: str) -> int:
    """Attempt sequence suffix of a quiz_id ('AbC02_0007' -> 7); 9999 if unparsable."""
    try:
        return int(str(quiz_id).rsplit('_', 1)[-1])
    except (IndexError, ValueError):
        return 9999


def _quiz_attempt_taken(quiz) -> bool:
    """True if a Quiz row represents a consumed attempt.

    Generated-but-never-graded quizzes have no score, grading session, or
    grading marks, so any of those being set means the physical quiz was used.
    Transferred quizzes count toward their current owner's total.
    """
    return (quiz.score is not None or quiz.grading_session_id is not None
            or quiz.mcq_points_earned is not None or quiz.date_graded is not None)


def _quiz_dict_taken(quiz: Dict[str, Any]) -> bool:
    """_quiz_attempt_taken() equivalent for a get_quiz_by_id() dict."""
    return (quiz.get('score') is not None
            or quiz.get('grading_session_id') is not None
            or quiz.get('mcq_points_earned') is not None
            or quiz.get('date_graded') is not None)


def _next_free_quiz_id(engine, student, module_number: int,
                       own_quiz_ids: set) -> Tuple[str, List[str]]:
    """Return the first attempt quiz_id not yet created for this student.

    Attempt slots are scanned in order and a slot is occupied when its quiz_id
    exists in the database under ANY student - transferred quizzes keep the
    originally printed quiz_id, so the student's own Quiz rows alone can miss
    blocked slots.  This mirrors the quiz generator's _find_start_attempt with
    a single needed quiz.  Returns (quiz_id, notes) where notes describe
    occupied slots blocked by something other than this student's own attempts.
    """
    notes: List[str] = []
    attempt = 1
    while True:
        quiz_id = format_quiz_id(student.student_code, module_number, attempt)
        if quiz_id in own_quiz_ids:
            attempt += 1
            continue
        row = get_quiz_by_id(engine, quiz_id)
        if row is None:
            return quiz_id, notes
        owner = (row.get('student') or {}).get('student_code') or '?'
        state = 'already graded' if _quiz_dict_taken(row) else 'already created'
        notes.append(f'{quiz_id} {state} ({owner})')
        attempt += 1


def _quiz_pdf_path(course_folder: str, module_number: int, section_number: int,
                   attempt: int, quiz_id: str) -> Path:
    """Canonical storage path of a generated quiz PDF."""
    return (Path(course_folder) / f'module{module_number}' / 'quizzes'
            / f'section{section_number}' / f'attempt{attempt}' / 'questions'
            / f'{artifact_id(quiz_id, "Q")}.pdf')


def _find_quiz_pdf(course_folder: str, module_number: int, section_number: int,
                   attempt: int, quiz_id: str) -> Optional[Path]:
    """Return the generated quiz PDF's path, or None if it isn't stored correctly.

    Group-packet creation moves individual PDFs into questions/Individual/, so
    both locations are valid storage.
    """
    canonical = _quiz_pdf_path(course_folder, module_number, section_number, attempt, quiz_id)
    for candidate in (canonical, canonical.parent / 'Individual' / canonical.name):
        if candidate.is_file():
            return candidate
    return None


def _write_replacement_coversheet(entries: List[Dict[str, Any]], title: str,
                                  output_path: Path) -> None:
    """Checklist coversheet for the replacement packet, padded to even pages."""
    pdf = FPDF(orientation='P', unit='mm', format='Letter')  # matches printer paper
    pdf.set_auto_page_break(auto=True, margin=25)
    pdf.add_page()

    pdf.set_font('Helvetica', 'B', 16)
    pdf.cell(0, 10, title, ln=1, align='C')
    pdf.ln(4)

    pdf.set_font('Helvetica', '', 11)
    pdf.cell(0, 8, 'Date quizzes taken: ____________________', ln=1)
    pdf.ln(4)

    line_h = 7
    box_size = 4
    for entry in entries:
        if pdf.get_y() + line_h + 5 > pdf.h - 25:
            pdf.add_page()
        pdf.set_font('Helvetica', '', 11)
        start_x = pdf.get_x()
        y = pdf.get_y()
        pdf.rect(start_x, y + 1.5, box_size, box_size)
        pdf.set_xy(start_x + box_size + 3, y)
        replaces = ''
        if entry['taken_ids']:
            replaces = f"   (replaces {', '.join(entry['taken_ids'])})"
        pdf.cell(0, line_h, f"{entry['quiz_id']}   {entry['display']}{replaces}", ln=1)

    pdf.ln(6)
    pdf.cell(0, 6, 'Sorted by: _____________________________________', ln=1)

    # Keep the coversheet an even number of sides so it forms whole physical
    # sheets separate from the quizzes that follow.
    if pdf.page_no() % 2 != 0:
        pdf.add_page()
    pdf.output(str(output_path))


def build_replacement_packet(engine, results: List[GradingResult], grading_dir: str,
                             course_folder: str) -> Optional[Path]:
    """Assemble the next-attempt quizzes that replace this session's graded set.

    For each quiz on the replacement list, the owning student's next unused
    attempt for that module is selected from generated quizzes - the database
    is authoritative for which quiz_ids exist, and the lowest untaken attempt
    sequence is preferred (attempts are sometimes taken out of order).  Found
    PDFs are fused behind a checklist coversheet into
    `<grading_dir>/replacement_packet.pdf`; the rest are itemized in
    `<grading_dir>/unfound_replacements.txt` as either existing-but-missing
    files, students who need a new attempt generated, or students at the
    attempt limit who require an instructor exception.  For the latter two
    groups the report prints the specific quiz_id of the needed attempt -
    the first attempt slot whose quiz_id does not exist in the database under
    any student - along with notes on slots that are blocked by quizzes that
    were already created or already graded under another student.
    """
    max_attempts = int(get_course_info(engine).get('max_attempts_per_module', 4) or 4)
    session_quiz_ids = {r.quiz_id for r in results if r.quiz_id}

    # One replacement per student per module, regardless of how many of their
    # quizzes appear on the list.
    groups: Dict[Tuple[str, int], List[GradingResult]] = {}
    for r in results:
        if (r.held_up or r.overwrite_declined or r.resolved_as_quant
                or r.resolved_as_blank or not r.student_code or not r.module_number):
            continue
        groups.setdefault((r.student_code, r.module_number), []).append(r)

    found: List[Dict[str, Any]] = []
    missing: List[Dict[str, Any]] = []
    to_generate: Dict[int, List[Dict[str, Any]]] = {}
    limit_reached: Dict[int, List[Dict[str, Any]]] = {}

    for (code, module), group in sorted(groups.items()):
        student = get_student_by_code(engine, code)
        if student is None:
            logger.warning(f'Replacement packet: student code {code} not found; skipped')
            continue
        session_taken = sorted({r.quiz_id for r in group if r.quiz_id})
        quizzes = [q for q in get_quizzes_for_student(engine, student.student_id)
                   if q.module_number == module]
        taken_ids = {q.quiz_id for q in quizzes if _quiz_attempt_taken(q)}
        taken_ids |= set(session_taken)
        untaken = sorted(
            (q for q in quizzes
             if q.quiz_id not in taken_ids and q.quiz_id not in session_quiz_ids),
            key=lambda q: (_attempt_number(q.quiz_id), q.quiz_id))
        name = student.name or code
        base_entry = {
            'module': module, 'code': code,
            'last': _last_name(name), 'first': _first_name(name),
            'display': _last_first(name), 'taken_ids': session_taken,
        }
        if untaken:
            candidate = untaken[0]
            attempt = _attempt_number(candidate.quiz_id)
            section = student.section_number if student.section_number is not None else 0
            entry = dict(base_entry, quiz_id=candidate.quiz_id)
            try:
                expected = _quiz_pdf_path(course_folder, module, section,
                                          attempt, candidate.quiz_id)
                pdf_path = _find_quiz_pdf(course_folder, module, section, attempt,
                                          candidate.quiz_id)
            except Exception as e:
                logger.warning(f'Replacement packet: could not locate {candidate.quiz_id}: {e}')
                expected = '(could not derive expected path)'
                pdf_path = None
            if pdf_path:
                found.append(dict(entry, pdf_path=pdf_path))
            else:
                missing.append(dict(entry, expected=expected))
        else:
            needed_id, slot_notes = _next_free_quiz_id(
                engine, student, module,
                {q.quiz_id for q in quizzes} | session_quiz_ids)
            entry = dict(base_entry, needed=needed_id, notes=slot_notes)
            if len(taken_ids) >= max_attempts:
                limit_reached.setdefault(module, []).append(entry)
            else:
                to_generate.setdefault(module, []).append(entry)

    # The packet and its checklist coversheet are ordered alphabetically by
    # last name for distribution; only the unfound report stays grouped by
    # module since quiz generation proceeds per module.
    found.sort(key=lambda e: (e['last'], e['first'], e['quiz_id']))
    missing.sort(key=lambda e: (e['module'], e['last'], e['first'], e['quiz_id']))

    packet_path = Path(grading_dir) / 'replacement_packet.pdf'
    if found:
        title = f'Replacement Quiz Packet - {Path(grading_dir).name}'
        cover_path = Path(grading_dir) / '_replacement_coversheet.pdf'
        _write_replacement_coversheet(found, title, cover_path)
        writer = PdfWriter()
        for source in [cover_path] + [e['pdf_path'] for e in found]:
            for page in PdfReader(str(source)).pages:
                writer.add_page(page)
        tmp_path = packet_path.with_suffix('.pdf.tmp')
        with open(tmp_path, 'wb') as f:
            writer.write(f)
        os.replace(tmp_path, packet_path)
        try:
            cover_path.unlink()
        except OSError:
            pass
        logger.info(f'Replacement packet written to {packet_path} '
                    f'({len(found)} quiz(es))')
    elif packet_path.exists():
        # A rebuild (e.g. after a transfer) may leave nothing to include.
        try:
            packet_path.unlink()
        except OSError:
            pass

    lines = [
        f'Unfound Replacement Quizzes - {Path(grading_dir).name}',
        f'Generated: {datetime.now():%Y-%m-%d %H:%M}',
        '',
    ]
    if missing:
        lines.append('Should exist but were not found on disk:')
        for module in sorted({e['module'] for e in missing}):
            lines.append(f'Module {module}')
            for e in [x for x in missing if x['module'] == module]:
                lines.append(f"    {e['quiz_id']:<14} {e['display']:<24} expected: {e['expected']}")
        lines.append('')
    if to_generate:
        lines.append('Need to be generated (paste the codes into the quiz generator):')
        for module in sorted(to_generate):
            entries = sorted(to_generate[module], key=lambda e: e['code'])
            lines.append(f'Module {module}')
            lines.append(f"    {', '.join(e['code'] for e in entries)}")
            for e in entries:
                note = f"   ({'; '.join(e['notes'])})" if e['notes'] else ''
                lines.append(f"        {e['code']:<8} needs {e['needed']}{note}")
        lines.append('')
    if limit_reached:
        lines.append(f'At the {max_attempts}-attempt limit (instructor exception required):')
        for module in sorted(limit_reached):
            lines.append(f'Module {module}')
            for e in sorted(limit_reached[module], key=lambda e: e['code']):
                note = f"   ({'; '.join(e['notes'])})" if e['notes'] else ''
                lines.append(f"    {e['code']:<8} next: {e['needed']}{note}")
        lines.append('')
    if not (missing or to_generate or limit_reached):
        lines.append('All replacement quizzes were found and are in the packet.')
    unfound_path = Path(grading_dir) / 'unfound_replacements.txt'
    unfound_path.write_text('\n'.join(lines).rstrip() + '\n')
    logger.info(f'Unfound replacement list written to {unfound_path}')

    return packet_path if found else None


def _record_skipped_attempt(
    engine,
    result: GradingResult,
    grading_dir: str,
    grading_session_id: Optional[int] = None,
    date_taken: Optional[str] = None,
) -> None:
    """Record a scoreless attempt for a skipped quiz so it can be regraded.

    A held-up result with a known quiz_id/student (e.g. the grader skipped a
    missing-answer prompt, or metadata was missing) still gets its results
    artifact saved and its Quiz row linked to this grading session - with no
    score written - so RegradeDialog can pick it up later. Scans whose
    identity couldn't be decoded at all (no student code or an 'unknown_'
    quiz id) can't be attributed to an attempt and are skipped entirely.
    """
    if not result.student_code or _is_unknown_id(result.quiz_id):
        return
    try:
        save_grading_artifacts(result, grading_dir)
        if not TEST_MODE:
            record_grading_result(
                engine, result, date_taken=date_taken,
                grading_session_id=grading_session_id, finalize=False,
            )
        logger.info(f"Recorded scoreless attempt for skipped quiz {result.quiz_id}")
    except Exception as e:
        logger.warning(f"Could not record skipped quiz {result.quiz_id}: {e}")


def record_results(
    engine,
    results: List[GradingResult],
    grading_dir: str,
    grading_session_id: Optional[int] = None,
    date_taken: Optional[str] = None,
    send_feedback: bool = False,
    extract_quant: bool = False,
    single_sides: bool = False,
    scan_path: Optional[str] = None,
    course_folder: Optional[str] = None,
) -> List[Any]:
    """Save artifacts and record quiz attempts for all resolved results.

    Results still marked `held_up` are not scored - they must be resolved
    first via `resolve_held_up_result` - but when their identity is known a
    scoreless attempt is still recorded so they can be regraded later.
    Returns the list of recorded Quiz rows (or GradingResult objects when
    TEST_MODE is active).
    """
    recorded: List[Any] = []

    # Process in section/last-name order so feedback and extracted PDFs are
    # naturally organized.
    ordered = sorted(
        results,
        key=lambda r: (r.section_number if r.section_number is not None else 0,
                       r.last_name or '', r.first_name or '', r.quiz_id or ''),
    )

    if TEST_MODE:
        for result in ordered:
            if result.resolved_as_quant or result.resolved_as_blank:
                continue
            if result.held_up:
                _record_skipped_attempt(engine, result, grading_dir)
                continue
            if result.overwrite_declined:
                continue
            save_grading_artifacts(result, grading_dir)
            recorded.append(result)
            if send_feedback and not result.has_quant:
                _queue_test_feedback_email(engine, result)
                result.feedback_sent = True

        if extract_quant and scan_path:
            _extract_and_collate_quant_pages(ordered, scan_path, grading_dir, single_sides)
            # Disabled for now (see _start_quant_transcription):
            # if _extract_and_collate_quant_pages(ordered, scan_path, grading_dir, single_sides):
            #     _start_quant_transcription(ordered, scan_path, grading_dir)
        if course_folder:
            _collate_quant_answer_keys(ordered, course_folder, grading_dir)
        _write_replacement_list(ordered, grading_dir, engine)
        if course_folder:
            try:
                build_replacement_packet(engine, ordered, grading_dir, course_folder)
            except Exception:
                logger.exception('Could not build replacement packet')

        return recorded

    for result in ordered:
        if result.resolved_as_quant or result.resolved_as_blank:
            continue
        if result.held_up:
            logger.warning(f"Skipping unresolved scan (quiz {result.quiz_id}): {result.validation_issues}")
            _record_skipped_attempt(
                engine, result, grading_dir,
                grading_session_id=grading_session_id, date_taken=date_taken,
            )
            continue
        if result.overwrite_declined:
            continue
        save_grading_artifacts(result, grading_dir)
        if result.has_quant:
            record_grading_result(
                engine, result, date_taken=date_taken,
                grading_session_id=grading_session_id, finalize=False,
            )
            continue
        quiz = record_grading_result(
            engine, result, date_taken=date_taken, grading_session_id=grading_session_id,
        )
        if quiz is not None:
            recorded.append(quiz)
            if send_feedback:
                _send_feedback_if_enabled(engine, result)

    if extract_quant and scan_path:
        _extract_and_collate_quant_pages(ordered, scan_path, grading_dir, single_sides)
        # Disabled for now (see _start_quant_transcription):
        # if _extract_and_collate_quant_pages(ordered, scan_path, grading_dir, single_sides):
        #     _start_quant_transcription(ordered, scan_path, grading_dir)
    if course_folder:
        _collate_quant_answer_keys(ordered, course_folder, grading_dir)
    _write_replacement_list(ordered, grading_dir, engine)
    if course_folder:
        try:
            build_replacement_packet(engine, ordered, grading_dir, course_folder)
        except Exception:
            logger.exception('Could not build replacement packet')

    return recorded


def grade_and_record_scan_file(
    engine,
    scan_path: str,
    grading_dir: str,
    course_folder: str,
    grading_session_id: Optional[int] = None,
    date_taken: Optional[str] = None,
    send_feedback: bool = False,
    extract_quant: bool = False,
    single_sides: bool = False,
) -> Tuple[List[Any], List[GradingResult]]:
    """Grade a scan file end-to-end and record everything that's resolvable.

    Any quiz whose QR code couldn't be read is left unresolved (not recorded)
    and returned in the second element of the result tuple so it can be
    resolved interactively (see `resolve_held_up_result`) and recorded
    afterwards with `record_results`.

    Returns (recorded_quizzes, pending_results).
    """
    results = parse_scan_file(engine, scan_path, grading_dir, course_folder, single_sides=single_sides)
    recorded = record_results(
        engine, results, grading_dir,
        grading_session_id=grading_session_id,
        date_taken=date_taken,
        send_feedback=send_feedback,
        extract_quant=extract_quant,
        single_sides=single_sides,
        scan_path=scan_path,
    )
    pending = [result for result in results if result.held_up]
    return recorded, pending


def _send_feedback_if_enabled(engine, result: GradingResult, is_regrade: bool = False) -> None:
    """Generate and queue a feedback email (never sent automatically)."""
    from email26 import generate_and_queue_quiz_feedback

    def _normalize(d):
        if not d:
            return {}
        out = {}
        for k, v in d.items():
            try:
                out[int(k)] = v
            except (ValueError, TypeError):
                out[k] = v
        return out

    student_answers = _normalize(result.student_answers)
    correct_answers = _normalize(result.correct_answers)
    question_numbers = {
        int(q['question_number']) for q in result.quiz_questions
    } if result.quiz_questions else set(correct_answers.keys())

    incorrect = []
    for q in sorted(question_numbers):
        correct = correct_answers.get(q)
        given = student_answers.get(q, [])
        if correct is not None and correct not in given:
            incorrect.append(q)

    logger.info(f'{result.quiz_id}: {len(incorrect)} incorrect MCQ question(s) for feedback: {incorrect}')
    detailed = None
    if incorrect:
        try:
            detailed = generate_detailed_feedback(engine, result.quiz_id, incorrect, questions=result.quiz_questions)
            logger.info(f'{result.quiz_id}: generated detailed feedback of {len(detailed)} chars')
        except Exception as e:
            logger.warning(f"Could not generate detailed feedback: {e}")

    # Append quant/ODT feedback if applicable
    quant_feedback = None
    if result.has_quant:
        try:
            quant_feedback = generate_quant_feedback_text(engine, result)
        except Exception as e:
            logger.warning(f"Could not generate quant feedback: {e}")
    if quant_feedback:
        if detailed:
            detailed += f"\n\n{quant_feedback}"
        else:
            detailed = quant_feedback

    generate_and_queue_quiz_feedback(
        student_code=result.student_code,
        quiz_score=round(result.total_score),
        module_number=result.module_number,
        date_taken=result.date_taken or datetime.now().strftime('%Y-%m-%d'),
        detailed_feedback=detailed,
        is_regrade=is_regrade,
    )
