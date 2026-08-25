"""Reads scanned bubble-sheet answers using per-quiz calibration metadata.

Every generated quiz PDF has a matching `{quiz_id}QM.json` metadata file
(written by quiz_generator26.create_quiz_pdf) that records, for each page:
  - 'calibration_points': the three fixed-position black squares' nominal
    mm coordinates ('top_left', 'top_right', 'bottom_left'). These squares
    are drawn at the same page-margin-relative position on every page,
    independent of quiz content.
  - 'questions': for each question on that page, its absolute question
    number (matching database26.QuizQuestion.question_number) and the mm
    coordinates of each answer bubble (index matching the position in
    QuizQuestion.answer_choices_json / correct_answer_index).

Printing and scanning introduce translation/scale/rotation error, so before
trusting the recorded (nominal) bubble coordinates, we re-locate the three
calibration squares in the actual scanned image and compute the affine
transform from nominal mm coordinates to observed pixel coordinates. That
same transform is then applied to every bubble coordinate before sampling
for fill, so grading stays accurate even when the physical page didn't scan
in at exactly the same position/scale/rotation as the original PDF.

Correctness itself (which bubble *should* be filled) is never derived here -
that comes from database26.QuizQuestion.correct_answer_index. This module
only answers "which bubble did the student actually fill in".
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from document_ids26 import parse_document_id

FILL_THRESHOLD = 0.5     # fraction of a bubble's sampled pixels that must be dark to count as filled
LETTERS = 'ABCDE'


def quiz_metadata_path(course_folder: str, quiz_id: str) -> Path:
    """Return the deterministic path to a quiz's {quiz_id}QM.json metadata file.

    This mirrors the layout written by quiz_generator26.create_quizzes_for_students:
    <course_folder>/module{module_number}/quizzes/attempt{attempt}/JSON/{quiz_id}QM.json
    """
    document = parse_document_id(quiz_id)
    return (
        Path(course_folder).expanduser() / f'module{document.module_number}' / 'quizzes'
        / f'attempt{document.sequence}' / 'JSON' / f'{quiz_id}QM.json'
    )


def load_quiz_metadata(course_folder: str, quiz_id: str) -> Optional[Dict[str, Any]]:
    """Load a quiz's generation-time page/bubble-position metadata, or None if missing."""
    path = quiz_metadata_path(course_folder, quiz_id)
    if not path.is_file():
        return None
    with open(path, 'r') as f:
        return json.load(f)


def _to_gray(image: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if image.ndim == 3 else image


# Fraction of the page height/width searched for each corner's calibration
# square. Deliberately generous: printing a PDF authored for one paper size
# (e.g. A4) onto a different physical paper (e.g. Letter) commonly triggers a
# printer/driver "shrink to fit + center" transform, which shifts and scales
# every element on the page by an amount that isn't known in advance. Rather
# than compute a precise expected pixel position (which breaks whenever that
# assumption doesn't hold), we search each corner broadly and rely on the
# affine fit below to absorb whatever scale/offset actually occurred.
CORNER_REGION_FRACTION = 0.22


def _corner_regions(h: int, w: int) -> Dict[str, Tuple[int, int, int, int]]:
    """Return (y0, x0, y1, x1) search boxes for each named corner."""
    ry = int(h * CORNER_REGION_FRACTION)
    rx = int(w * CORNER_REGION_FRACTION)
    return {
        'top_left': (0, 0, ry, rx),
        'top_right': (0, w - rx, ry, w),
        'bottom_left': (h - ry, 0, h, rx),
        'bottom_right': (h - ry, w - rx, h, w),
    }


def _find_square_in_region(gray: np.ndarray, region: Tuple[int, int, int, int]) -> Optional[Tuple[float, float]]:
    """Find the largest dark, roughly-square blob within a (y0, x0, y1, x1) region."""
    y0, x0, y1, x1 = region
    if y1 <= y0 or x1 <= x0:
        return None
    window = gray[y0:y1, x0:x1]
    _, binarized = cv2.threshold(window, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(binarized, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best = None
    best_area = 0.0
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < 100:  # ignore tiny noise/specks; a calibration square is a solid filled block
            continue
        x, y, cw, ch = cv2.boundingRect(contour)
        aspect = cw / ch if ch else 0
        if not (0.6 <= aspect <= 1.6):
            continue  # not roughly square
        if area > best_area:
            best_area = area
            best = (x + cw / 2.0, y + ch / 2.0)

    if best is None:
        return None
    return (best[0] + x0, best[1] + y0)


def _page_transform(gray: np.ndarray, calibration_points: Dict[str, Dict[str, float]]) -> Optional[np.ndarray]:
    """Compute the mm -> pixel affine transform for one scanned page.

    Uses the three known calibration squares' nominal mm positions (as
    recorded at generation time) and their actual detected pixel positions,
    found by searching broad corner regions of the scan, to correct for
    print/scan misalignment - including paper-size mismatches (e.g. a quiz
    authored for A4 but printed on Letter paper, which most print drivers
    silently scale-to-fit rather than reject or truncate). Returns None if
    any square can't be found.
    """
    required = ('top_left', 'top_right', 'bottom_left')
    if not all(key in calibration_points for key in required):
        return None

    h, w = gray.shape[:2]
    regions = _corner_regions(h, w)

    src_mm = []
    dst_px = []
    for key in required:
        found = _find_square_in_region(gray, regions[key])
        if found is None:
            return None
        point = calibration_points[key]
        src_mm.append([point['x'], point['y']])
        dst_px.append(list(found))

    src = np.array(src_mm, dtype=np.float32)
    dst = np.array(dst_px, dtype=np.float32)
    return cv2.getAffineTransform(src, dst)


def _sample_fill(gray: np.ndarray, x_px: float, y_px: float, radius_px: float) -> float:
    """Return the fraction of dark pixels within a circular sample at (x_px, y_px)."""
    h, w = gray.shape[:2]
    if not (0 <= x_px < w and 0 <= y_px < h):
        return 0.0
    r = max(1, int(round(radius_px)))
    x0, x1 = max(0, int(x_px - r)), min(w, int(x_px + r) + 1)
    y0, y1 = max(0, int(y_px - r)), min(h, int(y_px + r) + 1)
    if x1 <= x0 or y1 <= y0:
        return 0.0

    window = gray[y0:y1, x0:x1]
    _, binarized = cv2.threshold(window, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    mask = np.zeros(window.shape, dtype=np.uint8)
    cv2.circle(mask, (int(x_px - x0), int(y_px - y0)), r, 255, -1)

    total = int(np.sum(mask > 0))
    if total == 0:
        return 0.0
    filled = int(np.sum((binarized == 255) & (mask > 0)))
    return filled / total


def read_quiz_answers(
    page_images: List[np.ndarray], metadata: Dict[str, Any],
) -> Tuple[Dict[int, List[str]], List[str]]:
    """Read filled-in bubble answers from a quiz's scanned pages using its metadata.

    *page_images* are the scanned page images for one quiz's page group, in
    printed order. Returns (answers, issues):
      - answers: {question_number: [answer_letter]} for every question whose
        page could be calibrated and that has a clearly filled bubble.
        Questions with no bubble filled above threshold are omitted (treated
        as blank/unanswered) rather than guessed at.
      - issues: human-readable warnings, e.g. pages that couldn't be
        calibrated (calibration squares not found in the scan).
    """
    answers: Dict[int, List[str]] = {}
    issues: List[str] = []

    page_keys = sorted(
        (key for key in metadata if isinstance(key, str) and key.isdigit() and metadata.get(key, {}).get('questions')),
        key=int,
    )
    for page_index, page_key in enumerate(page_keys):
        if page_index >= len(page_images):
            issues.append(f'Scanned quiz has fewer pages than expected (missing page {page_key}).')
            break

        page_data = metadata[page_key]
        gray = _to_gray(page_images[page_index])
        transform = _page_transform(gray, page_data.get('calibration_points', {}))
        if transform is None:
            issues.append(f'Page {page_key}: could not locate calibration squares in the scan.')
            continue

        scale = float(np.linalg.norm(transform[:, 0]))  # mm -> px scale factor from the fitted transform

        for question in page_data.get('questions', []):
            number = question.get('number')
            if number is None:
                continue
            best_letter = None
            best_fill = 0.0
            for bubble in question.get('answers', []):
                answer_idx = bubble.get('answer_idx')
                if answer_idx is None or answer_idx >= len(LETTERS):
                    continue
                point_mm = np.array([[[bubble['x'], bubble['y']]]], dtype=np.float32)
                point_px = cv2.transform(point_mm, transform)[0, 0]
                radius_px = bubble.get('radius', 2.16) * scale
                fill = _sample_fill(gray, point_px[0], point_px[1], radius_px)
                if fill > FILL_THRESHOLD and fill > best_fill:
                    best_fill = fill
                    best_letter = LETTERS[answer_idx]
            if best_letter is not None:
                answers[number] = [best_letter]

    return answers, issues
