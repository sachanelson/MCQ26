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
LIGHT_FILL_DARKNESS_THRESHOLD = 0.09
LIGHT_FILL_MARGIN = 0.03
# Signature of an unmarked printed bubble, used to verify a fitted transform
# before trusting the light-fill fallback: the circle outline alone yields a
# small fill fraction while the interior stays white. If most expected bubble
# positions look like this, the transform is correct even when no bubble is
# filled strongly (e.g. light pencil scribbles).
EMPTY_CIRCLE_FILL_RANGE = (0.04, 0.45)
EMPTY_CIRCLE_DARKNESS_MAX = 0.05
EMPTY_CIRCLE_COVER_FRACTION = 0.6
LETTERS = 'ABCDE'


def quiz_metadata_path(course_folder: str, quiz_id: str, section: Optional[int] = None) -> Path:
    """Return the deterministic path to a quiz's {quiz_id}QM.json metadata file.

    Mirrors the layout written by quiz_generator26.create_quizzes_for_students.
    When *section* is provided the path includes a section{N} subdirectory:
      <course_folder>/module{module_number}/quizzes/section{section}/attempt{attempt}/JSON/{quiz_id}QM.json
    """
    document = parse_document_id(quiz_id)
    parts = [Path(course_folder).expanduser(), f'module{document.module_number}', 'quizzes']
    if section is not None:
        parts.append(f'section{section}')
    parts.extend([f'attempt{document.sequence}', 'JSON', f'{quiz_id}QM.json'])
    return Path(*parts)


def load_quiz_metadata(course_folder: str, quiz_id: str) -> Optional[Dict[str, Any]]:
    """Load a quiz's generation-time page/bubble-position metadata, or None if missing.

    First tries the legacy (no-section) location for backward compatibility, then
    searches section{N} subdirectories under the module quizzes folder.
    """
    # Legacy/non-sectioned layout
    path = quiz_metadata_path(course_folder, quiz_id)
    if path.is_file():
        with open(path, 'r') as f:
            return json.load(f)

    # Search section subdirectories
    document = parse_document_id(quiz_id)
    module_quizzes = (
        Path(course_folder).expanduser() / f'module{document.module_number}' / 'quizzes'
    )
    candidate = module_quizzes / f'attempt{document.sequence}' / 'JSON' / f'{quiz_id}QM.json'
    if candidate.is_file():
        with open(candidate, 'r') as f:
            return json.load(f)
    if module_quizzes.is_dir():
        for section_dir in sorted(module_quizzes.iterdir()):
            if section_dir.is_dir() and section_dir.name.startswith('section'):
                candidate = section_dir / f'attempt{document.sequence}' / 'JSON' / f'{quiz_id}QM.json'
                if candidate.is_file():
                    with open(candidate, 'r') as f:
                        return json.load(f)
    return None


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


# Maximum plausible candidates evaluated per corner region.
_MAX_CORNER_CANDIDATES = 5
# A solid calibration square or filled answer bubble has a large inscribed
# circle; handwriting strokes and empty circle outlines are only a few px
# thick.  Distance-transform peaks above this radius mark solid shapes even
# when the shape's contour merged with surrounding handwriting.
_MIN_INSCRIBED_RADIUS_PX = 12.0


def _find_square_candidates_in_region(
    gray: np.ndarray, region: Tuple[int, int, int, int]
) -> List[Tuple[float, float]]:
    """Return plausible calibration-square centers within a (y0, x0, y1, x1) region.

    Two detection passes feed one deduplicated candidate list:

    1. Contours: dark, roughly-square blobs whose bounding box is mostly
       filled (rejects sparse handwriting blobs).
    2. Distance transform: any point with an inscribed-circle radius >=
       _MIN_INSCRIBED_RADIUS_PX sits inside a solid shape.  This recovers a
       calibration square whose contour merged with a hand-drawn circle or
       other ink touching it - the merged blob fails the contour fill test,
       but the square's interior still contains a large inscribed circle.

    A filled answer bubble also survives both passes; it cannot be excluded
    locally, so callers must pick between candidates geometrically
    (_page_transforms) rather than trusting a single "best" blob.
    """
    y0, x0, y1, x1 = region
    if y1 <= y0 or x1 <= x0:
        return []
    window = gray[y0:y1, x0:x1]
    _, binarized = cv2.threshold(window, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    candidates: List[Tuple[float, float]] = []

    # Distance-transform peaks first: a large inscribed circle is stronger
    # evidence of a solid calibration square than a contour that merely has a
    # filled bounding box, and it also survives a square merged into a larger
    # sparse blob (e.g. a hand-drawn circle overlapping the square).
    dist = cv2.distanceTransform(binarized, cv2.DIST_L2, 5)
    if dist.size and float(dist.max()) >= _MIN_INSCRIBED_RADIUS_PX:
        dilated = cv2.dilate(dist, np.ones((21, 21), np.uint8))
        peak_ys, peak_xs = np.nonzero(
            (dist == dilated) & (dist >= _MIN_INSCRIBED_RADIUS_PX))
        ranked = sorted(
            ((float(dist[py, px]), float(px), float(py))
             for py, px in zip(peak_ys, peak_xs)),
            key=lambda t: -t[0],
        )
        for _d, px, py in ranked:
            candidates.append((px, py))

    contours, _ = cv2.findContours(binarized, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contour_cands = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < 100:  # ignore tiny noise/specks; a calibration square is a solid filled block
            continue
        x, y, cw, ch = cv2.boundingRect(contour)
        aspect = cw / ch if ch else 0
        if not (0.6 <= aspect <= 1.6):
            continue  # not roughly square
        # The square is a solid filled block; handwriting (e.g. a signature on
        # page 1 that reaches into the corner search region) forms a larger but
        # sparse blob, so require most of the bounding box to be filled.
        if area / (cw * ch) < 0.6:
            continue  # sparse blob, e.g. handwriting
        contour_cands.append((area, (x + cw / 2.0, y + ch / 2.0)))
    candidates.extend(c for _area, c in sorted(contour_cands, key=lambda t: -t[0]))

    # Deduplicate nearby centers (the same square found by both passes) and
    # convert region-relative coordinates back to full-page coordinates.
    deduped: List[Tuple[float, float]] = []
    for cx, cy in candidates:
        if all(abs(cx - ex) > 20 or abs(cy - ey) > 20 for ex, ey in deduped):
            deduped.append((cx, cy))
    return [(cx + x0, cy + y0) for cx, cy in deduped[:_MAX_CORNER_CANDIDATES]]


def _find_square_in_region(gray: np.ndarray, region: Tuple[int, int, int, int]) -> Optional[Tuple[float, float]]:
    """Find the largest dark, roughly-square blob within a (y0, x0, y1, x1) region."""
    candidates = _find_square_candidates_in_region(gray, region)
    return candidates[0] if candidates else None


def _page_transforms(gray: np.ndarray, calibration_points: Dict[str, Dict[str, float]]) -> List[np.ndarray]:
    """Candidate mm -> pixel affine transforms for one scanned page, best first.

    Uses the three known calibration squares' nominal mm positions (as
    recorded at generation time) and their actual detected pixel positions,
    found by searching broad corner regions of the scan, to correct for
    print/scan misalignment - including paper-size mismatches (e.g. a quiz
    authored for A4 but printed on Letter paper, which most print drivers
    silently scale-to-fit rather than reject or truncate).

    A corner region can hold several square-ish blobs (e.g. a filled answer
    bubble near the top-left square), so every corner contributes a small
    candidate list.  Combinations are ranked by geometric sanity - the
    top-left/top-right and top-left/bottom-left vectors should stay near
    their nominal orientation (horizontal/vertical) and keep the nominal
    aspect ratio - and callers verify the sampled bubbles before trusting a
    transform.  Returns an empty list if any square can't be found.
    """
    required = ('top_left', 'top_right', 'bottom_left')
    if not all(key in calibration_points for key in required):
        return []

    h, w = gray.shape[:2]
    regions = _corner_regions(h, w)

    src_mm = []
    candidate_lists = []
    for key in required:
        candidates = _find_square_candidates_in_region(gray, regions[key])
        if not candidates:
            return []
        candidate_lists.append(candidates)
        point = calibration_points[key]
        src_mm.append([point['x'], point['y']])

    src = np.array(src_mm, dtype=np.float32)
    nominal_w = float(np.linalg.norm(src[1] - src[0]))   # top_left -> top_right
    nominal_h = float(np.linalg.norm(src[2] - src[0]))   # top_left -> bottom_left
    nominal_ratio = nominal_w / nominal_h if nominal_h else 1.0

    combos = []
    for tl in candidate_lists[0]:
        for tr in candidate_lists[1]:
            for bl in candidate_lists[2]:
                vec_w = np.array([tr[0] - tl[0], tr[1] - tl[1]], dtype=np.float64)
                vec_h = np.array([bl[0] - tl[0], bl[1] - tl[1]], dtype=np.float64)
                len_w = float(np.linalg.norm(vec_w))
                len_h = float(np.linalg.norm(vec_h))
                if len_w < 50 or len_h < 50:
                    continue  # degenerate triple
                ratio_penalty = abs(len_w / len_h - nominal_ratio) / nominal_ratio
                # Nominal top edge is horizontal and left edge vertical;
                # measured angles should stay small for an upright scan.
                rotation = abs(np.arctan2(vec_w[1], vec_w[0]))
                tilt = abs(np.arctan2(vec_h[0], vec_h[1]))
                combos.append((ratio_penalty + rotation + tilt, tl, tr, bl))

    combos.sort(key=lambda combo: combo[0])
    transforms = []
    for _score, tl, tr, bl in combos[:8]:
        dst = np.array([tl, tr, bl], dtype=np.float32)
        transforms.append(cv2.getAffineTransform(src, dst))
    return transforms


def _page_transform(gray: np.ndarray, calibration_points: Dict[str, Dict[str, float]]) -> Optional[np.ndarray]:
    """Compute the best mm -> pixel affine transform for one scanned page."""
    transforms = _page_transforms(gray, calibration_points)
    return transforms[0] if transforms else None


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


def _sample_darkness(gray: np.ndarray, x_px: float, y_px: float, radius_px: float) -> float:
    h, w = gray.shape[:2]
    r = max(1, int(round(radius_px * 0.7)))
    x0, x1 = max(0, int(x_px - r)), min(w, int(x_px + r) + 1)
    y0, y1 = max(0, int(y_px - r)), min(h, int(y_px + r) + 1)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    window = gray[y0:y1, x0:x1]
    yy, xx = np.ogrid[y0:y1, x0:x1]
    mask = (xx - x_px) ** 2 + (yy - y_px) ** 2 <= r ** 2
    return float(np.mean(255 - window[mask]) / 255) if np.any(mask) else 0.0


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
        transforms = _page_transforms(gray, page_data.get('calibration_points', {}))
        if not transforms:
            issues.append(f'Page {page_key}: could not locate calibration squares in the scan.')
            continue

        def _sample_page(transform):
            scale = float(np.linalg.norm(transform[:, 0]))  # mm -> px scale from the fitted transform
            samples_out: List[Tuple[int, List[Tuple[str, float, float]]]] = []
            for question in page_data.get('questions', []):
                number = question.get('number')
                if number is None:
                    continue
                samples = []
                for bubble in question.get('answers', []):
                    answer_idx = bubble.get('answer_idx')
                    if answer_idx is None or answer_idx >= len(LETTERS):
                        continue
                    point_mm = np.array([[[bubble['x'], bubble['y']]]], dtype=np.float32)
                    point_px = cv2.transform(point_mm, transform)[0, 0]
                    radius_px = bubble.get('radius', 2.16) * scale
                    samples.append((
                        LETTERS[answer_idx],
                        _sample_fill(gray, point_px[0], point_px[1], radius_px),
                        _sample_darkness(gray, point_px[0], point_px[1], radius_px),
                    ))
                if samples:
                    samples_out.append((number, samples))
            return samples_out

        # Before trusting the light-fill (darkness) fallback, verify the fitted
        # transform actually lands on the printed circles: an unmarked bubble's
        # outline yields a small, consistent fill fraction while its interior
        # stays white. If a wrong transform was fit (e.g. a signature was picked
        # as a calibration square, or a filled bubble stood in for a square
        # whose contour merged with handwriting), most sampled positions land
        # on blank paper or text, few look like empty circles, and the
        # darkness fallback would fabricate answers from noise.  Try the
        # geometrically ranked candidate transforms in order and keep the
        # first that verifies; if none do, fall back to the best candidate
        # with the light-fill path disabled (the questions are routed to the
        # missing-answer dialog for manual resolution).  Any single strong
        # fill is also sufficient evidence.
        def _is_trustworthy(page_samples) -> bool:
            all_samples = [
                sample for _number, samples in page_samples for sample in samples
            ]
            if any(sample[1] > FILL_THRESHOLD for sample in all_samples):
                return True
            if not all_samples:
                return False
            empty_circles = sum(
                1 for _letter, fill, darkness in all_samples
                if EMPTY_CIRCLE_FILL_RANGE[0] <= fill <= EMPTY_CIRCLE_FILL_RANGE[1]
                and darkness <= EMPTY_CIRCLE_DARKNESS_MAX
            )
            return empty_circles >= EMPTY_CIRCLE_COVER_FRACTION * len(all_samples)

        page_samples = _sample_page(transforms[0])
        page_trustworthy = _is_trustworthy(page_samples)
        for transform in transforms[1:]:
            if page_trustworthy:
                break
            alt_samples = _sample_page(transform)
            if _is_trustworthy(alt_samples):
                page_samples = alt_samples
                page_trustworthy = True

        untrusted_page_reported = False
        for number, samples in page_samples:
            marked_letters = [sample[0] for sample in samples if sample[1] > FILL_THRESHOLD]
            if marked_letters:
                answers[number] = marked_letters
                continue
            if not page_trustworthy:
                if not untrusted_page_reported:
                    issues.append(
                        f'Page {page_key}: could not verify bubble positions after '
                        'calibration; light-fill detection disabled for this page.'
                    )
                    untrusted_page_reported = True
                continue
            by_darkness = sorted(samples, key=lambda sample: sample[2], reverse=True)
            best_letter, _, best_darkness = by_darkness[0]
            next_darkness = by_darkness[1][2] if len(by_darkness) > 1 else 0.0
            if (best_darkness >= LIGHT_FILL_DARKNESS_THRESHOLD and
                    best_darkness - next_darkness >= LIGHT_FILL_MARGIN):
                answers[number] = [best_letter]

    return answers, issues
