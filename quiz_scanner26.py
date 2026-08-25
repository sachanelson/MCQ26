"""
Module for parsing and grading scanned quizzes.

This module uses a functional programming style to:
1. Identify qsession folders and scan files based on block selection
2. Parse scanned quiz PDFs
3. Classify pages as first, middle, or last pages
4. Extract quiz answers from scanned pages
"""
import os
import re
import logging
import argparse
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Callable, Any, NamedTuple
from dataclasses import dataclass
import json

import fitz  # PyMuPDF
import numpy as np
from PIL import Image
import cv2
import time

# Note: this module previously imported `database`/`qsession_manager` from
# the legacy bubbleSheet/MCQ package at the top level. MCQ26 doesn't use the
# qsession-folder/block-scanning flow (process_block_scans/grade_block_scans)
# that those support, so that import is now deferred into
# process_block_scans itself (see below) rather than being a hard dependency
# for importing this module at all - MCQ26's own grading flow only uses
# process_scan_file, which never touches it.

# Set up logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)  # Default to WARNING level

# Define page types
class PageType:
    FIRST = "first"
    MIDDLE = "middle"
    LAST = "last"

class QuizPage:
    """
    Class to represent a page of a quiz.
    """
    def __init__(self, page_number, page_type, image=None, quiz_id=None, student_code=None,
                 answers=None, quiz_page_number=None):
        self.page_number = page_number  # position of this page within the scanned PDF
        self.page_type = page_type  # 'first', 'middle', 'last'
        self.image = image
        self.quiz_id = quiz_id
        self.student_code = student_code
        self.answers = answers if answers is not None else {}
        # 1-based page number *within its quiz*, decoded from the QR code
        # (see qr_code26.py's '{quiz_id}|{page_number}' content). Used to
        # reconstruct each quiz's correct page order even if the physical
        # scan interleaves pages from different quizzes out of order.
        self.quiz_page_number = quiz_page_number
        
    def __str__(self):
        """String representation of the quiz page without showing the image array."""
        return f"QuizPage(page_number={self.page_number}, page_type='{self.page_type}', quiz_id={self.quiz_id}, student_code={self.student_code}, answers={self.answers})"
    
    def __repr__(self):
        """Representation of the quiz page without showing the image array."""
        return self.__str__()

@dataclass
class ScannedQuiz:
    """Represents a complete scanned quiz with all its pages."""
    quiz_id: str
    student_code: str
    pages: List[QuizPage]
    answers: Dict[int, str] = None
    score: Optional[float] = None

def get_qsession_path_for_block(block_id: int, course_info: Optional[Dict[str, Any]] = None) -> Optional[Path]:
    """
    Get the qsession directory path for a specific block.
    
    Args:
        block_id: ID of the quiz block
        course_info: Optional course info dictionary. If provided, avoids an extra database query.
        
    Returns:
        Path to the qsession directory or None if not found
    """
    try:
        # Deferred import: only the (unused by MCQ26) qsession-folder/block
        # scanning flow needs this legacy dependency.
        from qsession_manager import ensure_qsession_directory_exists
        qsession_dir = ensure_qsession_directory_exists(block_id, course_info)
        if not qsession_dir:
            logger.error(f"Could not find or create qsession directory for block {block_id}")
            return None
            
        return qsession_dir
    except Exception as e:
        logger.error(f"Error getting qsession path for block {block_id}: {str(e)}")
        return None

def get_scan_files(qsession_dir: Path) -> List[Path]:
    """
    Get all scan files in the scans subdirectory of the qsession directory.
    
    Args:
        qsession_dir: Path to the qsession directory
        
    Returns:
        List of paths to scan files
    """
    scan_dir = qsession_dir / "scans"
    if not scan_dir.exists():
        logger.warning(f"Scans directory does not exist: {scan_dir}")
        scan_dir.mkdir(exist_ok=True)
        return []
        
    # Get all PDF files in the scans directory
    scan_files = list(scan_dir.glob("*.pdf"))
    scan_files.sort()  # Sort files alphabetically
    
    logger.info(f"Found {len(scan_files)} scan files in {scan_dir}")
    return scan_files

def get_answer_key_files(qsession_dir: Path) -> List[Path]:
    """
    Get all answer key files in the qsession directory.
    The answer keys are stored in a subfolder with 'A' suffix (for Answer key).
    
    Args:
        qsession_dir: Path to the qsession directory
        
    Returns:
        List of paths to answer key files
    """
    # Look for a subfolder with 'A' suffix (Answer key folder)
    answer_key_dirs = [d for d in qsession_dir.iterdir() if d.is_dir() and d.name.endswith('A')]
    
    if not answer_key_dirs:
        # If no 'A' suffix folder found, try 'answer_keys' as fallback
        answer_key_dir = qsession_dir / "answer_keys"
        if answer_key_dir.exists():
            answer_key_dirs = [answer_key_dir]
        else:
            logger.warning(f"No answer key directory found in: {qsession_dir}")
            return []
    
    answer_key_files = []
    
    # Get all PDF and JSON files in the answer key directories
    for answer_key_dir in answer_key_dirs:
        pdf_files = list(answer_key_dir.glob("*.pdf"))
        json_files = list(answer_key_dir.glob("*.json"))
        answer_key_files.extend(pdf_files)
        answer_key_files.extend(json_files)
        
        logger.info(f"Found {len(pdf_files)} answer key PDFs and {len(json_files)} metadata files in {answer_key_dir}")
    
    return answer_key_files

def pdf_to_images(pdf_path: Path) -> List[np.ndarray]:
    """
    Convert a PDF file to a list of images.
    
    Args:
        pdf_path: Path to the PDF file
        
    Returns:
        List of images as numpy arrays
    """
    try:
        doc = fitz.open(pdf_path)
        images = []
        
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            pix = page.get_pixmap(matrix=fitz.Matrix(300/72, 300/72))
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            img_np = np.array(img)
            images.append(img_np)
            
        return images
    except Exception as e:
        logger.error(f"Error converting PDF to images: {str(e)}")
        return []

def has_header(image: np.ndarray) -> bool:
    """
    Detect if a page has a header (indicating it's a first page).
    
    The header contains course info, instructor, student name, and date.
    
    Args:
        image: Image as numpy array
        
    Returns:
        True if the page has a header, False otherwise
    """
    # The header is typically in the top 15% of the page
    height, width = image.shape if len(image.shape) == 2 else image.shape[:2]
    header_region = image[:int(height * 0.15), :]
    
    # Use OCR to detect text in the header region that would indicate a first page
    import pytesseract
    header_text = pytesseract.image_to_string(header_region)
    
    # Check for keywords that would appear in a header
    header_keywords = ['name', 'instructor', 'course', 'date', 'student']
    for keyword in header_keywords:
        if keyword.lower() in header_text.lower():
            return True
    
    return False

def has_footer(image: np.ndarray) -> bool:
    """
    Detect if a page has a footer with review boxes (indicating it's a last page).
    
    Args:
        image: Image as numpy array
        
    Returns:
        True if the page has a footer with review boxes, False otherwise
    """
    # Convert to grayscale
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    
    # The footer with review boxes is typically in the bottom 20% of the page
    height, width = gray.shape
    footer_region = gray[int(height * 0.8):, :]
    
    # Check for squares in the footer region (review boxes)
    # Apply adaptive thresholding to handle different lighting conditions
    thresh = cv2.adaptiveThreshold(footer_region, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 11, 2)
    
    # Find contours
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    # Look for square-like contours
    squares = 0
    for contour in contours:
        # Approximate the contour
        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.04 * peri, True)
        
        # If the contour has 4 points, it might be a square
        if len(approx) == 4:
            x, y, w, h = cv2.boundingRect(approx)
            # Check if it's square-like (width and height are similar)
            if 0.8 < w/h < 1.2 and w > 20:  # Minimum size to avoid noise
                squares += 1
    
    # If we find at least 2 squares in the footer region, it's likely a last page
    return squares >= 2

def classify_page(image: np.ndarray, page_number: int) -> QuizPage:
    """
    Classify a page as first, middle, or last.

    Every page of an MCQ quiz carries a QR code encoding '{quiz_id}|{page}',
    where 'page' is 1-based *within that quiz* (see qr_code26.py) - not just
    the first page. So a page is classified as FIRST when its QR decodes to
    page number 1, not merely "a QR was found" (which would misclassify
    every page of a multi-page quiz as a new quiz's first page). This is
    also more reliable than OCR-matching generic header keywords ('name',
    'date', 'course', ...), which used to be the primary signal but produced
    false positives on non-MCQ pages sharing similar header text (e.g.
    quantitative/ODT pages appended after the MCQ section for manual
    grading, which do NOT carry a QR code at all) - a false FIRST
    classification would incorrectly split one physical quiz into two
    groups. The header heuristic is kept only as a fallback for the very
    first page of the scan file (there is no earlier group it could wrongly
    get merged into), so a genuinely unreadable QR on an actual first page
    still starts a group (to be resolved manually) instead of being merged
    into whatever preceded it.

    Args:
        image: Image as numpy array
        page_number: Page number in the PDF

    Returns:
        QuizPage object with the classified page type
    """
    quiz_id, qr_page_number = extract_quiz_id_and_page_from_qr(image)
    if quiz_id and qr_page_number == 1:
        page_type = PageType.FIRST
    elif quiz_id:
        page_type = PageType.MIDDLE
    elif page_number == 0:
        page_type = PageType.FIRST
    elif has_footer(image):
        page_type = PageType.LAST
    else:
        page_type = PageType.MIDDLE

    return QuizPage(
        page_number=page_number,
        page_type=page_type,
        image=image,
        quiz_id=quiz_id,
        quiz_page_number=qr_page_number,
        answers={}
    )

def extract_quiz_id(image: np.ndarray) -> Optional[str]:
    """
    Extract the quiz ID from a first page.
    
    The quiz ID is typically displayed in the lower right corner above the QR code.
    First tries to extract from QR code, then falls back to OCR if QR code detection fails.
    
    Args:
        image: Image as numpy array
        
    Returns:
        Quiz ID string or None if not found
    """
    try:
        # First try to extract from QR code
        quiz_id_from_qr = extract_quiz_id_from_qr(image)
        if quiz_id_from_qr:
            logger.debug(f"Quiz ID from QR code: {quiz_id_from_qr}")
            return quiz_id_from_qr
            
        # If QR code detection fails, fall back to OCR
        # Convert to grayscale if it's a color image
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image
        
        # Focus on the bottom portion of the image where the footer is
        height, width = gray.shape
        footer_region = gray[int(height * 0.8):, int(width * 0.6):]  # Bottom 20%, right 40%
        
        # Apply OCR to the footer region
        import pytesseract
        text = pytesseract.image_to_string(footer_region)
        
        # Look for patterns that match quiz IDs
        # First try the standard format: ABC1_001 (student code + module number + quiz number)
        quiz_id_match = re.search(r'[A-Z]{2,3}\d*_\d{3,4}', text)
        if quiz_id_match:
            return quiz_id_match.group(0)
        
        # Try formats like AKn00_0002, NKico_0002, etc. that we see in the scans
        alt_match = re.search(r'([A-Z]{2,3}[a-z]*\d*_\d{4})', text)
        if alt_match:
            return alt_match.group(1)
            
        # Try formats with lowercase letters like ico, ado, etc.
        alt_match2 = re.search(r'([A-Z][A-Za-z]{2,4}\d*_\d{4})', text)
        if alt_match2:
            return alt_match2.group(1)
        
        # If we still don't have a match, try the entire image
        full_text = pytesseract.image_to_string(gray)
        
        # Try all the patterns again on the full text
        full_match = re.search(r'[A-Z]{2,3}\d*_\d{3,4}', full_text)
        if full_match:
            return full_match.group(0)
            
        full_alt_match = re.search(r'([A-Z]{2,3}[a-z]*\d*_\d{4})', full_text)
        if full_alt_match:
            return full_alt_match.group(1)
            
        full_alt_match2 = re.search(r'([A-Z][A-Za-z]{2,4}\d*_\d{4})', full_text)
        if full_alt_match2:
            return full_alt_match2.group(1)
            
        # If we still can't find a match, look for any pattern that might be a quiz ID
        last_resort = re.search(r'([A-Z][A-Za-z0-9]{2,5}_\d{2,4})', full_text)
        if last_resort:
            return last_resort.group(1)
            
        return None
        
    except Exception as e:
        logger.error(f"Error extracting quiz ID: {str(e)}")
        return None

def extract_quiz_id_from_qr(image: np.ndarray) -> Optional[str]:
    """
    Extract quiz ID from QR code in the image.

    Delegates to MCQ.qr_detector.extract_quiz_id_from_qr, which crops to the
    lower-right corner where the QR is actually printed (rather than
    searching the whole, much larger page image) and validates decoded data
    with regexes that tolerate mixed-case student codes (e.g. 'AdK', 'StA').
    This module's own from-scratch implementation previously searched the
    entire image and required all-uppercase student-code prefixes, both of
    which made it unreliable on real scans.

    Args:
        image: Image as numpy array

    Returns:
        Quiz ID string or None if QR code not found or couldn't be decoded
    """
    try:
        from qr_detector26 import extract_quiz_id_from_qr as _qr_detector_extract
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if len(image.shape) == 3 else image
        return _qr_detector_extract(gray)
    except Exception as e:
        logger.error(f"Error extracting quiz ID from QR code: {str(e)}")
        return None


def extract_quiz_id_and_page_from_qr(image: np.ndarray) -> Tuple[Optional[str], Optional[int]]:
    """Decode a QR code and split its raw content into (quiz_id, page_number).

    MCQ26 encodes every page's QR as '{quiz_id}|{page_number}' (see
    qr_code26.py) - every page of a quiz carries a QR, not just the first -
    so the page number is needed to tell "the first page of this quiz"
    apart from "a later page of the same quiz". Uses
    MCQ.qr_detector.decode_raw_qr_data for the underlying crop/threshold/
    decode logic (shared with extract_quiz_id_from_qr).

    Returns (None, None) if no QR could be decoded or its content doesn't
    match the expected '{quiz_id}|{page_number}' format.
    """
    try:
        from qr_detector26 import decode_raw_qr_data
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if len(image.shape) == 3 else image
        raw = decode_raw_qr_data(gray)
    except Exception as e:
        logger.error(f"Error decoding QR code: {str(e)}")
        return None, None

    if not raw or '|' not in raw:
        return None, None

    quiz_id_part, _, page_part = raw.partition('|')
    id_match = re.search(r'([A-Za-z0-9]+_\d{2,5})', quiz_id_part)
    quiz_id = id_match.group(1) if id_match else (quiz_id_part or None)
    try:
        page_number = int(page_part.strip())
    except ValueError:
        page_number = None
    return quiz_id, page_number


def extract_student_code(image: np.ndarray) -> Optional[str]:
    """
    Extract the student code from a first page.
    
    The student code is typically displayed in the header.
    
    Args:
        image: Image as numpy array
        
    Returns:
        Student code string or None if not found
    """
    try:
        # Convert to grayscale if it's a color image
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image
        
        # Focus on the top portion of the image where the header is
        height, width = gray.shape
        header_region = gray[0:int(height * 0.15), :]
        
        # Apply OCR to the header region
        import pytesseract
        text = pytesseract.image_to_string(header_region)
        
        # Look for patterns that match student codes
        # Student codes are typically 3 uppercase letters
        student_code_match = re.search(r'Student Code:?\s*([A-Z]{3})', text, re.IGNORECASE)
        if student_code_match:
            return student_code_match.group(1)
        
        # Try to find any 3-letter code that might be a student code
        alt_match = re.search(r'\b([A-Z]{3})\b', text)
        if alt_match:
            return alt_match.group(1)
            
        return None
        
    except Exception as e:
        logger.error(f"Error extracting student code: {str(e)}")
        return None

def derive_student_code_from_quiz_id(quiz_id: Optional[str]) -> Optional[str]:
    """Derive the student code from an MCQ26 quiz_id.

    MCQ26 quiz IDs are {student_code}{module_number:02d}_{attempt:04d},
    where student_code is alphanumeric and may be mixed-case (e.g. 'AdK').
    """
    if not quiz_id:
        return None
    id_match = re.match(r'^([A-Za-z0-9]+?)\d{2}_\d{4}$', quiz_id)
    return id_match.group(1) if id_match else None


def extract_quiz_id_and_student_code(image: np.ndarray) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract both quiz ID and student code from a quiz page.
    
    The quiz ID is typically in the footer (lower right corner),
    while the student code is in the header.
    
    Args:
        image: Image as numpy array
        
    Returns:
        Tuple of (quiz_id, student_code), either of which may be None if not found
    """
    # Extract quiz ID from footer
    quiz_id = extract_quiz_id(image)
    
    # Extract student code from header
    student_code = extract_student_code(image)
    
    # If we found a quiz ID but not a student code, derive it from the quiz ID itself.
    if quiz_id and not student_code:
        student_code = derive_student_code_from_quiz_id(quiz_id)
    
    logger.debug(f"Extracted quiz ID: {quiz_id}, student code: {student_code}")
    return quiz_id, student_code

def group_pages_into_quizzes(pages: List[QuizPage]) -> List[List[QuizPage]]:
    """
    Group pages into individual quizzes based on page types.
    
    A quiz starts with a first page (with header) and ends with a last page (with footer).
    Pages in between are grouped together with the preceding first page.
    
    Args:
        pages: List of QuizPage objects
        
    Returns:
        List of lists of QuizPage objects, where each inner list represents a complete quiz
    """
    quizzes = []
    current_quiz = []
    
    for page in pages:
        if page.page_type == PageType.FIRST:
            # If we encounter a new first page and we have pages in the current quiz,
            # finalize the current quiz and start a new one
            if current_quiz:
                quizzes.append(current_quiz)
                logger.info(f"Completed quiz: {len(current_quiz)} pages")
            
            # Start a new quiz
            current_quiz = [page]
            logger.info(f"Starting new quiz with first page")
        else:
            # Add the page to the current quiz
            current_quiz.append(page)
    
    # Add the last quiz if it's not empty
    if current_quiz:
        quizzes.append(current_quiz)
        logger.info(f"Completed quiz: {len(current_quiz)} pages")
    
    logger.info(f"Grouped pages into {len(quizzes)} quizzes")
    
    return quizzes


def group_pages_by_quiz_id(pages: List[QuizPage]) -> List[List[QuizPage]]:
    """
    Group pages into quizzes by their decoded quiz_id, tolerating pages that
    are out of physical order in the scan (e.g. one quiz's pages
    interleaved with another's, rather than each quiz's pages being
    contiguous). This replaces the sequence-based `group_pages_into_quizzes`
    (which assumes a quiz's pages always appear together in scan order) for
    single-scan-file grading, where a stack of loose papers can easily get
    scanned out of order.

    Every page carries a QR-decoded quiz_id and, when available, its 1-based
    page number *within that quiz* (QuizPage.quiz_page_number - see
    qr_code26.py's '{quiz_id}|{page_number}' QR content). Pages are bucketed
    by quiz_id and sorted by that page number to reconstruct each quiz's
    correct order regardless of scan order. Pages whose QR couldn't be
    decoded at all (quiz_id is None) can't be attributed to any quiz
    automatically - guessing based on physical adjacency would defeat the
    purpose of being order-tolerant - so each becomes its own single-page
    group, to be resolved manually like any other unreadable page.

    Args:
        pages: List of QuizPage objects, in whatever order they were scanned

    Returns:
        List of lists of QuizPage objects, one list per quiz (pages sorted
        by their in-quiz page number) plus one single-page list per page
        whose identity couldn't be decoded.
    """
    by_quiz_id: Dict[str, List[QuizPage]] = {}
    order: List[str] = []
    unresolved: List[List[QuizPage]] = []

    for page in pages:
        if not page.quiz_id:
            unresolved.append([page])
            continue
        if page.quiz_id not in by_quiz_id:
            by_quiz_id[page.quiz_id] = []
            order.append(page.quiz_id)
        by_quiz_id[page.quiz_id].append(page)

    def _sort_key(page: QuizPage):
        # Pages with an unknown in-quiz page number (shouldn't normally
        # happen once quiz_id decoded) sort after known ones, in scan order.
        return (page.quiz_page_number is None, page.quiz_page_number, page.page_number)

    groups = [sorted(by_quiz_id[qid], key=_sort_key) for qid in order]
    groups.extend(unresolved)

    logger.info(
        f"Grouped {len(pages)} pages into {len(groups)} quizzes by decoded quiz_id "
        f"({len(unresolved)} page(s) with no decodable QR)"
    )
    return groups


def extract_answers_from_page(page: QuizPage, debug_dir: Optional[Path] = None) -> Dict[int, str]:
    """
    Extract answers from a quiz page by detecting filled circles next to answer letters.
    Each question has a number (1, 2, 3...) followed by 4-5 answer choices with circles.
    The circles have a fixed size (2.4mm radius) and are positioned at consistent x-coordinates.
    
    Args:
        page: QuizPage object
        debug_dir: Optional directory to save debug images
        
    Returns:
        Dictionary mapping question numbers to answer letters (A-E)
    """
    import cv2
    import numpy as np
    import time
    from pathlib import Path
    import logging
    import os
    
    # Set up logger
    logger = logging.getLogger(__name__)
    
    # Constants
    LETTERS = ['A', 'B', 'C', 'D', 'E']
    THRESH = 0.5  # Threshold for determining if a circle is filled
    
    # Get image
    image = page.image
    h, w = image.shape[:2]
    
    # Create a debug image for visualization
    debug_image = image.copy() if len(image.shape) == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    
    # Binarize the image for better circle detection
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image
    
    # Apply thresholding to get binary image for fill detection
    _, binarized = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    
    # Save original and binarized images for debugging
    cv2.imwrite(str(debug_dir / "original_image.png"), image)
    cv2.imwrite(str(debug_dir / "binarized_image.png"), binarized)
    
    # Define parameters for circle detection based on quiz_generator.py specifications
    # The circles in the quiz have a fixed radius of 2.4mm
    # Convert mm to pixels based on A4 paper size (210mm x 297mm)
    # Assuming the image is scaled to fit the page width
    mm_to_pixel_ratio = w / 210.0  # A4 width is 210mm
    
    # Circle radius in pixels (2.4mm as specified in quiz_generator.py)
    circle_radius_px = int(2.4 * mm_to_pixel_ratio)
    
    # Expected x-position range for answer circles
    # In quiz_generator.py, circles are positioned at self.get_x() + 6
    # This is approximately 16mm from left edge (10mm margin + 6mm)
    expected_x_mm = 16  # Approximate x-position in mm from left edge
    expected_x_px = int(expected_x_mm * mm_to_pixel_ratio)
    
    # Tolerance for x-position (in pixels) - increased for better detection
    x_tolerance_px = int(10 * mm_to_pixel_ratio)  # 10mm tolerance (increased from 5mm)
    
    logger.info(f"Image dimensions: {w}x{h} pixels")
    logger.info(f"Calculated mm to pixel ratio: {mm_to_pixel_ratio:.2f}")
    logger.info(f"Expected circle radius: {circle_radius_px} pixels")
    logger.info(f"Expected circle x-position: {expected_x_px} pixels (±{x_tolerance_px})")
    
    # Create or use debug directory
    if debug_dir is None:
        debug_dir = Path("/tmp/quiz_debug")
    
    # Ensure debug directory exists
    debug_dir.mkdir(exist_ok=True, parents=True)
    
    # Save a copy of the original image for debugging
    try:
        timestamp = int(time.time())
        orig_path = debug_dir / f"original_image_{timestamp}.png"
        cv2.imwrite(str(orig_path), image)
        bin_path = debug_dir / f"binarized_{timestamp}.png"
        cv2.imwrite(str(bin_path), binarized)
        logger.info(f"Original image saved to {orig_path}")
        logger.info(f"Binarized image saved to {bin_path}")
    except Exception as e:
        logger.error(f"Error saving debug images: {e}")
    
    try:
        # Try different parameters for circle detection
        # First attempt with more sensitive parameters
        logger.info("Attempting circle detection with sensitive parameters...")
        
        # More sensitive parameters - lower param2, wider radius range
        circles = cv2.HoughCircles(
            gray, 
            cv2.HOUGH_GRADIENT, 
            dp=1.2,                                # Accumulator resolution
            minDist=int(4 * mm_to_pixel_ratio),    # Minimum distance between circles (reduced)
            param1=50,                             # Upper threshold for edge detection
            param2=20,                             # Lower threshold for center detection (more sensitive)
            minRadius=max(1, circle_radius_px-5),  # Allow smaller radius for detection
            maxRadius=circle_radius_px+5           # Allow larger radius for detection
        )
        
        if circles is None:
            logger.info("First attempt failed, trying with even more sensitive parameters...")
            
            # Try with even more sensitive parameters
            circles = cv2.HoughCircles(
                gray, 
                cv2.HOUGH_GRADIENT, 
                dp=1.5,                                # Higher dp can find more circles
                minDist=int(3 * mm_to_pixel_ratio),   # Even smaller minimum distance
                param1=40,                             # Lower edge threshold
                param2=15,                             # Even lower center threshold
                minRadius=max(1, circle_radius_px-10), # Much wider radius range
                maxRadius=circle_radius_px+10          # Much wider radius range
            )
            
            if circles is None:
                logger.info("Second attempt failed, trying with HOUGH_GRADIENT_ALT method...")
                
                # Try with HOUGH_GRADIENT_ALT which can be better for some images
                try:
                    # This method is only available in newer OpenCV versions
                    circles = cv2.HoughCircles(
                        gray, 
                        cv2.HOUGH_GRADIENT_ALT, 
                        dp=1.5,
                        minDist=int(3 * mm_to_pixel_ratio),
                        param1=100,                            # Edge gradient threshold
                        param2=0.9,                            # Circle completeness threshold (0-1)
                        minRadius=max(1, circle_radius_px-10),
                        maxRadius=circle_radius_px+10
                    )
                except Exception as e:
                    logger.error(f"HOUGH_GRADIENT_ALT not available: {e}")
                    
                    # If all else fails, try with extremely permissive parameters
                    circles = cv2.HoughCircles(
                        gray, 
                        cv2.HOUGH_GRADIENT, 
                        dp=1.5,
                        minDist=int(2 * mm_to_pixel_ratio),
                        param1=30,
                        param2=10,  # Very low threshold - will detect more false positives
                        minRadius=max(1, circle_radius_px-15),
                        maxRadius=circle_radius_px+15
                    )
        
        if circles is None:
            logger.error(f"No circles detected on page after multiple attempts")
            
            # Save debug visualization showing where we expect circles to be
            expected_y_positions = [int((50 + i*15) * mm_to_pixel_ratio) for i in range(20)]  # Approximate positions
            for y_pos in expected_y_positions:
                cv2.circle(debug_image, (expected_x_px, y_pos), circle_radius_px, (0, 0, 255), 2)
                cv2.putText(debug_image, f"Expected", (expected_x_px + circle_radius_px + 5, y_pos), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
            
            debug_path = debug_dir / f"expected_circles_{timestamp}.png"
            cv2.imwrite(str(debug_path), debug_image)
            logger.info(f"Debug image with expected circle positions saved to {debug_path}")
            
            return {}
        
        # Convert circles to integer coordinates
        circles = np.uint16(np.around(circles[0]))
        
        # Debug output - number of circles detected
        logger.info(f"Detected {len(circles)} potential answer circles")
        
        # Draw all detected circles on debug image
        for circle in circles:
            x, y, r = circle
            cv2.circle(debug_image, (x, y), r, (0, 255, 0), 2)
            # Add circle coordinates for debugging
            cv2.putText(debug_image, f"({x},{y},r={r})", (x+r, y), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
        
        # Save debug image with all detected circles
        all_circles_path = debug_dir / f"all_circles_{timestamp}.png"
        cv2.imwrite(str(all_circles_path), debug_image)
        logger.info(f"Debug image with all circles saved to {all_circles_path}")
        
        # Filter circles based on expected x-position range for answer choices
        x_min = expected_x_px - x_tolerance_px
        x_max = expected_x_px + x_tolerance_px
        
        # Filter circles to only those in the expected x-position range
        filtered_circles = [c for c in circles if x_min <= c[0] <= x_max]
        
        logger.info(f"After x-position filtering: {len(filtered_circles)} circles remain")
        
        # If no circles remain after filtering, try with a wider tolerance
        if len(filtered_circles) == 0:
            logger.info("No circles in expected x-position range, trying with wider tolerance...")
            x_tolerance_px = int(20 * mm_to_pixel_ratio)  # 20mm tolerance
            x_min = expected_x_px - x_tolerance_px
            x_max = expected_x_px + x_tolerance_px
            filtered_circles = [c for c in circles if x_min <= c[0] <= x_max]
            logger.info(f"With wider x-tolerance (±{x_tolerance_px}px): {len(filtered_circles)} circles")
        
        # If still no circles, return empty result
        if len(filtered_circles) == 0:
            logger.info("No circles in expected position after widening tolerance")
            return {}
        
        # Sort filtered circles by y-coordinate (top to bottom)
        sorted_circles = sorted(filtered_circles, key=lambda c: c[1])
        
        # Group circles into rows based on y-coordinate
        # The answer height in quiz_generator.py is 6mm
        answer_height_px = int(6 * mm_to_pixel_ratio)
        y_tolerance = answer_height_px // 2  # Half the answer height
        
        rows = []
        current_row = []
        current_y = None
        
        for circle in sorted_circles:
            x, y, r = circle
            
            if current_y is None:
                # First circle
                current_row.append((x, y, r))
                current_y = y
            elif abs(y - current_y) <= y_tolerance:
                # Same row
                current_row.append((x, y, r))
            else:
                # New row
                if current_row:
                    rows.append(current_row)
                current_row = [(x, y, r)]
                current_y = y
        
        # Add the last row if not empty
        if current_row:
            rows.append(current_row)
        
        # Debug output - number of rows detected
        logger.info(f"Grouped into {len(rows)} rows of circles")
        
        # Process rows to identify questions and answer choices
        answers = {}
        question_number = 1  # Start with question 1
        
        for i, row in enumerate(rows):
            if len(row) == 1:
                # Single circle, likely not an answer row
                continue
                
            logger.info(f"Processing question {question_number} with {len(row)} potential answer choices")
            
            # Check which circles in this row are filled
            filled_answers = []
            
            for j, (x, y, r) in enumerate(row):
                if j >= len(LETTERS):  # Skip if we have more circles than letters
                    continue
                    
                # Create a mask for this circle
                mask = np.zeros((h, w), dtype=np.uint8)
                cv2.circle(mask, (x, y), int(r * 0.8), 255, -1)  # Use slightly smaller radius for fill detection
                
                # Check if the circle is filled
                total_pixels = np.sum(mask > 0)
                if total_pixels == 0:
                    continue
                    
                filled_pixels = np.sum((binarized == 255) & (mask > 0))
                fill_ratio = filled_pixels / total_pixels
                
                # Debug output - fill ratio for each circle
                letter_label = LETTERS[j] if j < len(LETTERS) else str(j)
                logger.info(f"Q{question_number}, option {letter_label}: fill ratio = {fill_ratio:.2f}")
                
                # Draw fill ratio on debug image
                cv2.putText(debug_image, f"{fill_ratio:.2f}", (x+r, y), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
                
                # If circle is filled
                if fill_ratio > THRESH:
                    # Mark this circle as filled on debug image
                    cv2.circle(debug_image, (x, y), r, (0, 0, 255), 2)
                    filled_answers.append((j, fill_ratio))
            
            # If we found filled answers for this question
            if filled_answers:
                # Sort by fill ratio (highest first) and take the most filled one
                filled_answers.sort(key=lambda x: x[1], reverse=True)
                best_answer_idx = filled_answers[0][0]
                
                # Record the answer
                if best_answer_idx < len(LETTERS):
                    answers[question_number] = LETTERS[best_answer_idx]
                    logger.info(f"  Question {question_number}: {LETTERS[best_answer_idx]} (fill ratio: {filled_answers[0][1]:.2f})")
            
            # Move to next question
            question_number += 1
        
        # Print summary of detected answers
        logger.info(f"Detected {len(answers)} answers on this page:")
        for q_num, letter in sorted(answers.items()):
            logger.info(f"  Question {q_num}: {letter}")
        
        # Save debug image
        try:
            debug_path = debug_dir / f"page_debug_{timestamp}.png"
            cv2.imwrite(str(debug_path), debug_image)
            logger.info(f"Debug image saved to {debug_path}")
        except Exception as e:
            logger.error(f"Error saving debug image: {e}")
        
        return answers
    
    except Exception as e:
        logger.error(f"Error in extract_answers_from_page: {e}")
        import traceback
        traceback.print_exc()
        return {}  # Return empty dict on error

def process_scan_file(scan_file: Path) -> List[ScannedQuiz]:
    """
    Process a single scan file and extract all quizzes from it.

    Pages are grouped by their QR-decoded quiz_id (see
    group_pages_by_quiz_id), not by physical position in the scan, so a
    stack of loose papers scanned out of order - e.g. one page of student
    AdK's quiz followed by a page of student AuC's quiz - is still
    reconstructed correctly, as long as each page's own QR code is
    readable. Pages whose QR can't be decoded at all become their own
    single-page, identity-unknown entry for manual resolution.

    Args:
        scan_file: Path to the scan file
        
    Returns:
        List of ScannedQuiz objects
    """
    try:
        # Convert PDF to images
        images = pdf_to_images(scan_file)
        
        # Classify each page (also decodes quiz_id / in-quiz page number via QR)
        pages = [classify_page(img, i) for i, img in enumerate(images)]
        
        # Group pages by decoded quiz_id, tolerant of out-of-order scanning
        quiz_page_groups = group_pages_by_quiz_id(pages)
        
        # Create ScannedQuiz objects
        scanned_quizzes = []
        for page_group in quiz_page_groups:
            quiz_id = page_group[0].quiz_id
            student_code = derive_student_code_from_quiz_id(quiz_id) if quiz_id else None
            
            # Answer extraction is done by MCQ26 (grading26/bubble_scoring26) using
            # each quiz's own generation-time bubble-position metadata, not here.
            # `extract_answers_from_quiz` was called but never defined in this
            # module; leave `answers` empty rather than reviving that dead code.
            answers = {}
            
            # Create a ScannedQuiz object
            quiz = ScannedQuiz(
                quiz_id=quiz_id or f"unknown_{scan_file.stem}_{len(scanned_quizzes)}",
                student_code=student_code,
                pages=page_group,
                answers=answers
            )
            scanned_quizzes.append(quiz)
        
        return scanned_quizzes
    except Exception as e:
        logger.error(f"Error processing scan file {scan_file}: {str(e)}")
        return []

def process_block_scans(block_id: int, course_info: Optional[Dict[str, Any]] = None) -> List[ScannedQuiz]:
    """
    Process all scanned quizzes for a given block ID.
    
    This is the main entry point for the UI to process scanned quizzes.
    
    Args:
        block_id: The ID of the quiz block to process scans for
        course_info: Optional course info dictionary. If provided, avoids an extra database query.
        
    Returns:
        A list of ScannedQuiz objects representing the processed quizzes
    """
    logger.info(f"Processing scans for block ID: {block_id}")
    
    # Get the qsession directory for this block
    qsession_dir = get_qsession_path_for_block(block_id, course_info)
    if not qsession_dir:
        logger.error(f"Could not find qsession directory for block ID: {block_id}")
        return []
    
    # Get all scan files in the qsession directory
    scan_files = get_scan_files(qsession_dir)
    if not scan_files:
        logger.warning(f"No scan files found for block ID: {block_id} in {qsession_dir}")
        return []
    
    # Get all answer key files in the qsession directory
    logger.info("Looking for answer key files...")
    answer_key_files = get_answer_key_files(qsession_dir)
    if not answer_key_files:
        logger.warning(f"No answer key files found for block ID: {block_id} in {qsession_dir}")
    else:
        logger.info(f"Found {len(answer_key_files)} answer key files for block ID: {block_id}")
    
    # Create debug directory for this session
    debug_dir = Path("/tmp/quiz_scanner_debug")
    debug_dir.mkdir(exist_ok=True)
    session_debug_dir = debug_dir / f"block_{block_id}_{int(time.time())}"
    session_debug_dir.mkdir(exist_ok=True)
    
    logger.info(f"Found {len(scan_files)} scan files for block ID: {block_id}")
    
    # Process each scan file
    all_quizzes = []
    for scan_file in scan_files:
        try:
            # Convert PDF to images
            images = pdf_to_images(scan_file)
            
            # Classify pages
            pages = []
            for i, img in enumerate(images):
                page = classify_page(img, i)
                pages.append(page)
            
            # Group pages into quizzes
            quizzes = group_pages_into_quizzes(pages)
            
            # Extract quiz IDs and student codes using OCR
            for quiz_index, quiz in enumerate(quizzes):
                # Only process first pages for quiz ID extraction
                first_pages = [p for p in quiz if p.page_type == PageType.FIRST]
                if first_pages:
                    # Extract quiz ID and student code from the first page
                    quiz_id, student_code = extract_quiz_id_and_student_code(first_pages[0].image)
                    quiz_id = quiz_id if quiz_id else f"unknown_{len(all_quizzes)}"
                    
                    # Process the first page of the quiz to extract answers
                    if first_pages:
                        first_page = first_pages[0]
                        
                        # Create quiz-specific debug directory
                        quiz_debug_dir = session_debug_dir / f"quiz_{quiz_index}_{quiz_id}"
                        quiz_debug_dir.mkdir(exist_ok=True)
                        
                        # Find the corresponding answer key for this quiz
                        answer_key_file = find_answer_key_for_quiz(answer_key_files, quiz_id)
                        
                        if answer_key_file:
                            logger.info(f"Found answer key file: {answer_key_file}")
                            
                            # Process the answer key to get correct answers
                            answer_key_answers = process_answer_key(answer_key_file, quiz_debug_dir)
                            logger.info(f"Extracted {len(answer_key_answers)} answers from answer key")
                            
                            # Process all pages of the quiz to extract answers
                            quiz_answers = {}
                            
                            # First process the first page
                            first_page_result = process_quiz_with_answer_key(first_page, answer_key_answers, quiz_debug_dir)
                            first_page_answers, _, _ = first_page_result  # Unpack the tuple
                            quiz_answers.update(first_page_answers)
                            logger.info(f"Extracted {len(first_page_answers)} answers from first page")
                            
                            # Then process any additional pages
                            additional_pages = [p for p in quiz if p.page_type != PageType.FIRST]
                            for i, page in enumerate(additional_pages):
                                page_debug_dir = quiz_debug_dir / f"page_{i+2}"  # +2 because first page is 1
                                page_debug_dir.mkdir(exist_ok=True)
                                
                                page_result = process_quiz_with_answer_key(page, answer_key_answers, page_debug_dir)
                                page_answers, _, _ = page_result  # Unpack the tuple
                                quiz_answers.update(page_answers)
                                logger.info(f"Extracted {len(page_answers)} answers from page {i+2}")
                            
                            logger.info(f"Total answers extracted from all quiz pages: {len(quiz_answers)}")
                            
                            # Calculate score based on answers
                            score = calculate_score(quiz_answers, answer_key_answers)
                            logger.info(f"Quiz {quiz_id} score: {score}%")
                            
                            # Create a ScannedQuiz object and add it to the list
                            scanned_quiz = ScannedQuiz(
                                quiz_id=quiz_id,
                                student_code=student_code,
                                pages=quiz,
                                answers=quiz_answers,
                                score=score
                            )
                            all_quizzes.append(scanned_quiz)
                        else:
                            logger.warning(f"No answer key found for quiz {quiz_id}, extracting answers without reference")
                            quiz_answers = extract_answers_from_page(first_page.image, quiz_debug_dir)
                            score = None
                            
                            # Create a ScannedQuiz object and add it to the list
                            scanned_quiz = ScannedQuiz(
                                quiz_id=quiz_id,
                                student_code=student_code,
                                pages=quiz,
                                answers=quiz_answers,
                                score=score
                            )
                            all_quizzes.append(scanned_quiz)
                else:
                    quiz_id = f"unknown_{len(all_quizzes)}"
                    student_code = None  # Will be extracted later
                    
                    scanned_quiz = ScannedQuiz(
                        quiz_id=quiz_id,
                        student_code=student_code,
                        pages=quiz,
                        answers={},  # Empty answers dictionary
                        score=None
                    )
                    all_quizzes.append(scanned_quiz)
            
            logger.info(f"Processed {len(quizzes)} quizzes from file {scan_file}")
            
        except Exception as e:
            logger.error(f"Error processing scan file {scan_file}: {str(e)}")
            import traceback
            traceback.print_exc()
    
    # Sort quizzes by quiz_id for consistency
    all_quizzes.sort(key=lambda q: q.quiz_id)
    
    logger.info(f"Processed a total of {len(all_quizzes)} quizzes for block ID: {block_id}")
    return all_quizzes

def find_answer_key_for_module(answer_key_files: List[Path], module_number: Optional[int]) -> Optional[Path]:
    """
    Find the appropriate answer key file for a given module number.
    
    Args:
        answer_key_files: List of paths to answer key files
        module_number: Module number to find answer key for
        
    Returns:
        Path to the answer key file or None if not found
    """
    logger.info(f"find_answer_key_for_module called with module_number={module_number}")
    
    if module_number is None or not answer_key_files:
        logger.info(f"Returning None because module_number is None or no answer_key_files")
        return None
    
    # First look for JSON metadata files that might contain module information
    for file in answer_key_files:
        if file.suffix.lower() == '.json':
            try:
                with open(file, 'r') as f:
                    metadata = json.load(f)
                    if 'module_number' in metadata and metadata['module_number'] == module_number:
                        # If this JSON file has a corresponding PDF, return that
                        pdf_path = file.with_suffix('.pdf')
                        if pdf_path.exists() and pdf_path in answer_key_files:
                            logger.info(f"Found answer key PDF with matching JSON metadata: {pdf_path}")
                            return pdf_path
            except Exception as e:
                logger.error(f"Error reading JSON metadata file {file}: {str(e)}")
    
    logger.info(f"No answer key found for module {module_number}")
    return None

def find_answer_key_for_quiz(answer_key_files: List[Path], quiz_id: str) -> Optional[Path]:
    """
    Find the appropriate answer key file for a given quiz ID.
    
    Args:
        answer_key_files: List of paths to answer key files
        quiz_id: Quiz ID to find answer key for
        
    Returns:
        Path to the answer key file or None if not found
    """
    if not quiz_id or not answer_key_files:
        return None
    
    # Extract the base quiz ID (before any underscore)
    base_quiz_id = quiz_id.split('_')[0] if '_' in quiz_id else quiz_id
    
    # Try multiple approaches to find the answer key
    
    # Approach 1: Direct append 'A' to the quiz ID (e.g., 'AKn00_0002' -> 'AKn00_0002A')
    answer_key_id = quiz_id + 'A'
    logger.info(f"Looking for answer key with ID (append A): {answer_key_id}")
    
    for file in answer_key_files:
        if file.suffix.lower() == '.pdf' and answer_key_id in file.stem:
            logger.info(f"Found matching answer key file: {file}")
            return file
    
    # Approach 2: If the quiz ID ends with 'Q', replace it with 'A'
    if base_quiz_id.endswith('Q'):
        answer_key_id = base_quiz_id[:-1] + 'A'
        logger.info(f"Looking for answer key with ID (Q->A): {answer_key_id}")
        
        # Look for PDF files with the answer key ID in the name
        for file in answer_key_files:
            if file.suffix.lower() == '.pdf' and answer_key_id in file.stem:
                logger.info(f"Found matching answer key file: {file}")
                return file
    
    # Approach 3: Extract student code and replace first letter with 'A'
    # For quiz IDs like "AKn00_0002", the student code is "AKn"
    import re
    student_code_match = re.match(r'^([A-Za-z]{2,3})\d+', base_quiz_id)
    if student_code_match:
        student_code = student_code_match.group(1)
        # Replace first letter with 'A'
        answer_key_prefix = 'A' + student_code[1:]
        logger.info(f"Looking for answer key with prefix: {answer_key_prefix}")
        
        # Look for PDF files with the answer key prefix
        for file in answer_key_files:
            if file.suffix.lower() == '.pdf' and file.stem.startswith(answer_key_prefix):
                logger.info(f"Found matching answer key file by student code: {file}")
                return file
    
    # Approach 4: Try a more flexible search - any file with 'A' in the name
    logger.info("Trying flexible search for answer key files with 'A' in the name")
    for file in answer_key_files:
        if file.suffix.lower() == '.pdf' and 'A' in file.stem:
            # Check if there's a number pattern match between quiz and answer key
            quiz_numbers = re.findall(r'\d+', quiz_id)
            file_numbers = re.findall(r'\d+', file.stem)
            if quiz_numbers and file_numbers and any(qn == fn for qn in quiz_numbers for fn in file_numbers):
                logger.info(f"Found potential answer key file by number matching: {file}")
                return file
    
    logger.warning(f"No answer key file found for quiz ID: {quiz_id}")
    return None

def process_answer_key(answer_key_file: Path, debug_dir: Path) -> Dict[int, str]:
    """
    Process an answer key file to extract correct answers.
    
    Args:
        answer_key_file: Path to the answer key file
        debug_dir: Directory to save debug images
        
    Returns:
        Dictionary mapping question numbers to answer letters
    """
    logger.info(f"Processing answer key file: {answer_key_file}")
    
    # If it's a JSON file, read the answers directly
    if answer_key_file.suffix.lower() == '.json':
        try:
            with open(answer_key_file, 'r') as f:
                metadata = json.load(f)
                if 'answers' in metadata:
                    return metadata['answers']
        except Exception as e:
            logger.error(f"Error reading JSON answer key file {answer_key_file}: {str(e)}")
            return {}
    
    # If it's a PDF, process it using the answer key detector
    try:
        # Import functions from answer_key_detector
        import MCQ.answer_key_detector as answer_key_detector
        import MCQ.pdf_utils as pdf_utils        # Get the number of pages in the PDF
        num_pages = pdf_utils.get_pdf_page_count(answer_key_file)
        
        # Process all pages and combine the answers
        all_answers = {}
        
        # Define a custom detect_filled_circles function with more permissive parameters
        def enhanced_detect_filled_circles(image, page_num, debug_dir):
            """
            Enhanced version of detect_filled_circles with more permissive parameters
            to improve detection on challenging pages.
            """
            from .answer_key_detector import detect_filled_circles as original_detect
            
            # First try with original parameters
            answers, calibration_points, circle_data = original_detect(image, page_num, debug_dir)
            logger.debug(f"Original detection found {len(answers)} answers")
            
            # If we found a reasonable number of answers, return them
            if len(answers) >= 5:
                return answers, calibration_points, circle_data
            
            # Otherwise, try with more permissive parameters by modifying the image
            import cv2
            import numpy as np
            
            # Try enhancing contrast
            logger.debug("Trying enhanced contrast detection")
            enhanced = cv2.equalizeHist(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))
            enhanced_color = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)
            
            # Save enhanced image for debugging
            if debug_dir:
                enhanced_path = debug_dir / f"enhanced_contrast_page_{page_num}.png"
                cv2.imwrite(str(enhanced_path), enhanced_color)
            
            # Try detection on enhanced image
            answers2, calibration_points2, circle_data2 = original_detect(enhanced_color, page_num, debug_dir)
            logger.debug(f"Enhanced contrast detection found {len(answers2)} answers")
            
            # Return the better result
            if len(answers2) > len(answers):
                return answers2, calibration_points2, circle_data2
            return answers, calibration_points, circle_data
        
        for page_num in range(num_pages):
            logger.info(f"Processing answer key page {page_num+1}/{num_pages}")
            # Convert the PDF page to an image
            image = answer_key_detector.pdf_to_image(answer_key_file, page_num=page_num)

            
            # Create a page-specific debug directory
            page_debug_dir = debug_dir / f"page_{page_num+1}"
            page_debug_dir.mkdir(exist_ok=True)
            
            # Detect filled circles to get answers using enhanced detection
            page_answers, calibration_points, circle_data = enhanced_detect_filled_circles(image, page_num, debug_dir)
            
            # Adjust question numbers based on page
            # First page starts at question 1, second page continues from there
            if page_num > 0:
                # Calculate the offset based on the highest question number seen so far
                offset = max(all_answers.keys()) if all_answers else 0
                adjusted_answers = {k + offset: v for k, v in page_answers.items()}
                page_answers = adjusted_answers
                logger.info(f"Adjusted question numbers with offset {offset}")
            
            # Add these answers to the combined dictionary
            all_answers.update(page_answers)
        
        logger.info(f"Total answers extracted from all pages: {len(all_answers)}")
        return all_answers
        
    except Exception as e:
        logger.error(f"Error processing answer key file {answer_key_file}: {str(e)}")
        import traceback
        traceback.print_exc()
        return {}

def process_quiz_with_answer_key(page: QuizPage, answer_key_answers: Dict[int, str], debug_dir: Path) -> Tuple[Dict[int, str], Dict[int, bool], float]:
    """
    Process a quiz page using the answer key information to locate and check answers.
    
    Args:
        page: QuizPage object to process
        answer_key_answers: Dictionary of correct answers from the answer key
        debug_dir: Directory to save debug images
        
    Returns:
        Tuple of (student_answers, correct_answers, score)
    """
    import cv2
    import numpy as np
    
    # Ensure debug directory exists
    debug_dir.mkdir(exist_ok=True, parents=True)
    
    # Get image
    image = page.image
    h, w = image.shape[:2]
    
    # Create a debug image for visualization
    debug_image = image.copy() if len(image.shape) == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    
    # Convert to grayscale if needed
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        # Create a debug image for visualization (BGR)
        debug_image = image.copy()
    else:
        gray = image
        # Create a debug image for visualization (convert grayscale to BGR)
        debug_image = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    
    # Apply thresholding to get binary image for fill detection
    _, binarized = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    
    # Save original and binarized images for debugging
    orig_path = debug_dir / f"original_image.png"
    bin_path = debug_dir / f"binarized.png"
    cv2.imwrite(str(orig_path), gray)  # Save grayscale version
    cv2.imwrite(str(bin_path), binarized)
    
    # Constants
    LETTERS = ['A', 'B', 'C', 'D', 'E']
    THRESH = 0.5  # Threshold for determining if a circle is filled
    
    # Calculate mm to pixel ratio (assuming A4 page, 210x297mm)
    mm_to_pixel_ratio = w / 210.0
    
    # Circle radius in pixels (2.4mm as specified in quiz_generator.py)
    circle_radius_px = int(2.4 * mm_to_pixel_ratio)
    
    # Try to detect circles in the image
    circles = detect_circles_in_image(gray, circle_radius_px, mm_to_pixel_ratio)
    
    if circles is None or len(circles) == 0:
        logger.error("No circles detected in the quiz page")
        return {}
    
    # Draw all detected circles on debug image
    circles_debug = debug_image.copy()
    for x, y, r in circles:
        cv2.circle(circles_debug, (x, y), r, (0, 255, 0), 2)
    circles_debug_path = debug_dir / f"all_detected_circles.png"
    cv2.imwrite(str(circles_debug_path), circles_debug)
    
    # Group circles into rows (questions)
    rows = group_circles_into_rows(circles, mm_to_pixel_ratio)
    
    # Process each row to identify filled circles
    answers = {}
    for i, row in enumerate(rows):
        question_number = i + 1  # 1-indexed question numbers
        
        # Skip rows with too few circles
        if len(row) < 2:
            continue
        
        # Check which circles in this row are filled
        filled_answers = []
        
        for j, (x, y, r) in enumerate(row):
            if j >= len(LETTERS):  # Skip if we have more circles than letters
                continue
                
            # Create a mask for this circle
            mask = np.zeros((h, w), dtype=np.uint8)
            cv2.circle(mask, (x, y), int(r * 0.8), 255, -1)  # Use slightly smaller radius for fill detection
            
            # Check if the circle is filled
            total_pixels = np.sum(mask > 0)
            if total_pixels == 0:
                continue
                
            filled_pixels = np.sum((binarized == 255) & (mask > 0))
            fill_ratio = filled_pixels / total_pixels
            
            # Draw fill ratio on debug image
            cv2.putText(debug_image, f"{fill_ratio:.2f}", (x+r, y), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
            
            # If circle is filled
            if fill_ratio > THRESH:
                # Mark this circle as filled on debug image
                cv2.circle(debug_image, (x, y), r, (0, 0, 255), 2)
                filled_answers.append((j, fill_ratio))
        
        # If we found filled answers for this question
        if filled_answers:
            # Sort by fill ratio (highest first) and take the most filled one
            filled_answers.sort(key=lambda x: x[1], reverse=True)
            best_answer_idx = filled_answers[0][0]
            
            # Record the answer
            if best_answer_idx < len(LETTERS):
                answers[question_number] = LETTERS[best_answer_idx]
                
                # Check if this answer matches the answer key
                is_correct = False
                if question_number in answer_key_answers and answer_key_answers[question_number] == LETTERS[best_answer_idx]:
                    is_correct = True
                
                # Draw whether the answer is correct or not
                color = (0, 255, 0) if is_correct else (0, 0, 255)  # Green if correct, red if wrong
                cv2.circle(debug_image, (x, y), r+5, color, 3)
    
    # Save debug image
    debug_path = debug_dir / f"graded_quiz.png"
    cv2.imwrite(str(debug_path), debug_image)
    
    return answers

def detect_circles_in_image(gray: np.ndarray, circle_radius_px: int, mm_to_pixel_ratio: float) -> List[Tuple[int, int, int]]:
    """
    Detect circles in an image using multiple parameter sets for robustness.
    
    Args:
        gray: Grayscale image
        circle_radius_px: Expected circle radius in pixels
        mm_to_pixel_ratio: Conversion factor from mm to pixels
        
    Returns:
        List of (x, y, r) tuples for detected circles
    """
    # Try different parameters for circle detection
    circles = None
    
    # First attempt with more sensitive parameters
    circles = cv2.HoughCircles(
        gray, 
        cv2.HOUGH_GRADIENT, 
        dp=1.2,
        minDist=int(4 * mm_to_pixel_ratio),
        param1=50,
        param2=20,
        minRadius=max(1, circle_radius_px-5),
        maxRadius=circle_radius_px+5
    )
    
    if circles is None:
        # Try with even more sensitive parameters
        circles = cv2.HoughCircles(
            gray, 
            cv2.HOUGH_GRADIENT, 
            dp=1.5,
            minDist=int(3 * mm_to_pixel_ratio),
            param1=40,
            param2=15,
            minRadius=max(1, circle_radius_px-10),
            maxRadius=circle_radius_px+10
        )
        
        if circles is None:
            # Try with HOUGH_GRADIENT_ALT which can be better for some images
            try:
                circles = cv2.HoughCircles(
                    gray, 
                    cv2.HOUGH_GRADIENT_ALT, 
                    dp=1.5,
                    minDist=int(3 * mm_to_pixel_ratio),
                    param1=100,
                    param2=0.9,
                    minRadius=max(1, circle_radius_px-10),
                    maxRadius=circle_radius_px+10
                )
            except Exception:
                # If all else fails, try with extremely permissive parameters
                circles = cv2.HoughCircles(
                    gray, 
                    cv2.HOUGH_GRADIENT, 
                    dp=1.5,
                    minDist=int(2 * mm_to_pixel_ratio),
                    param1=30,
                    param2=10,
                    minRadius=max(1, circle_radius_px-15),
                    maxRadius=circle_radius_px+15
                )
    
    if circles is None:
        return []
    
    # Convert circles to integer coordinates
    return np.uint16(np.around(circles[0]))

def group_circles_into_rows(circles: List[Tuple[int, int, int]], mm_to_pixel_ratio: float) -> List[List[Tuple[int, int, int]]]:
    """
    Group circles into rows based on their y-coordinates.
    
    Args:
        circles: List of (x, y, r) tuples for detected circles
        mm_to_pixel_ratio: Conversion factor from mm to pixels
        
    Returns:
        List of rows, where each row is a list of (x, y, r) tuples
    """
    # Sort circles by y-coordinate (top to bottom)
    sorted_circles = sorted(circles, key=lambda c: c[1])
    
    # Group circles into rows based on y-coordinate
    # The answer height in quiz_generator.py is 6mm
    answer_height_px = int(6 * mm_to_pixel_ratio)
    y_tolerance = answer_height_px // 2  # Half the answer height
    
    rows = []
    current_row = []
    current_y = None
    
    for circle in sorted_circles:
        x, y, r = circle
        
        if current_y is None:
            # First circle
            current_row.append((x, y, r))
            current_y = y
        elif abs(y - current_y) <= y_tolerance:
            # Same row
            current_row.append((x, y, r))
        else:
            # New row
            if current_row:
                rows.append(current_row)
            current_row = [(x, y, r)]
            current_y = y
    
    # Add the last row if not empty
    if current_row:
        rows.append(current_row)
    
    return rows

def calculate_score(student_answers: Dict[int, str], answer_key_answers: Dict[int, str]) -> float:
    """
    Calculate the quiz score based on student answers and answer key.
    
    Args:
        student_answers: Dictionary mapping question numbers to student's selected answers
        answer_key_answers: Dictionary mapping question numbers to correct answers
        
    Returns:
        Score as a percentage (0-100)
    """
    if not answer_key_answers:
        return 0.0
    
    # Count correct answers
    correct = 0
    total = len(answer_key_answers)
    
    for question_num, correct_answer in answer_key_answers.items():
        if question_num in student_answers and student_answers[question_num] == correct_answer:
            correct += 1
    
    # Calculate percentage
    if total > 0:
        return (correct / total) * 100.0
    else:
        return 0.0

def extract_answers_from_page(image: np.ndarray, debug_dir: Path, timestamp: int = None) -> Dict[int, str]:
    """
    Extract answers from a quiz page image.
    
    Args:
        image: The image to extract answers from (grayscale)
        debug_dir: Directory to save debug images
        timestamp: Optional timestamp for unique debug filenames
        
    Returns:
        Dictionary mapping question numbers to answer letters (A-E)
    """
    import cv2
    import numpy as np
    
    # Create a timestamp if not provided
    if timestamp is None:
        timestamp = int(time.time() * 1000)
    
    # Constants
    LETTERS = ['A', 'B', 'C', 'D', 'E']
    THRESH = 0.5  # Threshold for determining if a circle is filled
    
    # Get image dimensions
    h, w = image.shape[:2]
    
    # Assume image is already grayscale
    gray = image
    
    # Create a debug image for visualization (convert grayscale to BGR)
    debug_image = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    
    # Apply thresholding to get binary image for fill detection
    _, binarized = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    
    # Save original and binarized images for debugging
    orig_path = debug_dir / f"original_image_{timestamp}.png"
    bin_path = debug_dir / f"binarized_{timestamp}.png"
    cv2.imwrite(str(orig_path), gray)
    cv2.imwrite(str(bin_path), binarized)
    
    # Calculate mm to pixel ratio (assuming A4 page, 210x297mm)
    mm_to_pixel_ratio = w / 210.0
    
    # Circle radius in pixels (2.4mm as specified in quiz_generator.py)
    circle_radius_px = int(2.4 * mm_to_pixel_ratio)
    
    # Try to detect circles in the image
    circles = detect_circles_in_image(gray, circle_radius_px, mm_to_pixel_ratio)
    
    if circles is None or len(circles) == 0:
        logger.info(f"No circles detected in the quiz page")
        return {}
    
    # Draw all detected circles on debug image
    circles_debug = debug_image.copy()
    for x, y, r in circles:
        cv2.circle(circles_debug, (x, y), r, (0, 255, 0), 2)
    circles_debug_path = debug_dir / f"all_detected_circles_{timestamp}.png"
    cv2.imwrite(str(circles_debug_path), circles_debug)
    
    # Group circles into rows (questions)
    rows = group_circles_into_rows(circles, mm_to_pixel_ratio)
    
    # Process each row to identify filled circles
    answers = {}
    for i, row in enumerate(rows):
        question_number = i + 1  # 1-indexed question numbers
        
        # Skip rows with too few circles
        if len(row) < 2:
            continue
        
        # Check which circles in this row are filled
        filled_answers = []
        
        for j, (x, y, r) in enumerate(row):
            if j >= len(LETTERS):  # Skip if we have more circles than letters
                continue
                
            # Create a mask for this circle
            mask = np.zeros((h, w), dtype=np.uint8)
            cv2.circle(mask, (x, y), int(r * 0.8), 255, -1)  # Use slightly smaller radius for fill detection
            
            # Check if the circle is filled
            total_pixels = np.sum(mask > 0)
            if total_pixels == 0:
                continue
                
            filled_pixels = np.sum((binarized == 255) & (mask > 0))
            fill_ratio = filled_pixels / total_pixels
            
            # Draw fill ratio on debug image
            cv2.putText(debug_image, f"{fill_ratio:.2f}", (x+r, y), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
            
            # If circle is filled
            if fill_ratio > THRESH:
                # Mark this circle as filled on debug image
                cv2.circle(debug_image, (x, y), r, (0, 0, 255), 2)
                filled_answers.append((j, fill_ratio))
        
        # If we found filled answers for this question
        if filled_answers:
            # Sort by fill ratio (highest first) and take the most filled one
            filled_answers.sort(key=lambda x: x[1], reverse=True)
            best_answer_idx = filled_answers[0][0]
            
            # Record the answer
            if best_answer_idx < len(LETTERS):
                answers[question_number] = LETTERS[best_answer_idx]
    
    # Save debug image
    debug_path = debug_dir / f"graded_quiz_{timestamp}.png"
    cv2.imwrite(str(debug_path), debug_image)
    
    return answers

def process_quiz_with_answer_key(quiz_image_path: str, answer_key: Dict[int, str], debug_dir: Path = None) -> Tuple[Dict[int, str], Dict[int, bool], float]:
    """
    Process a quiz image with a given answer key.
    
    Args:
        quiz_image_path: Path to the quiz image
        answer_key: Dictionary mapping question numbers to correct answers
        debug_dir: Directory to save debug images
        
    Returns:
        Tuple of (student_answers, correct_answers, score)
    """
    import cv2
    import numpy as np
    from pathlib import Path
    import time
    
    # Create debug directory if not provided
    if debug_dir is None:
        debug_dir = Path("debug_quiz_images")
    debug_dir.mkdir(exist_ok=True)
    
    # Generate timestamp for unique debug filenames
    timestamp = int(time.time() * 1000)
    
    # Check if the quiz is a PDF (multi-page)
    is_pdf = str(quiz_image_path).lower().endswith('.pdf')
    
    # Dictionary to store student answers
    student_answers = {}
    
    if is_pdf:
        # Process each page of the PDF
        import pdf_utils
        
        # Get number of pages in the PDF
        num_pages = pdf_utils.get_pdf_page_count(answer_key_file)
        
        # Convert PDF to images
        quiz_images = pdf_utils.convert_pdf_to_images(Path(quiz_image_path))
        
        # Process each page
        question_offset = 0
        for page_idx, image in enumerate(quiz_images):
            logger.info(f"Processing quiz page {page_idx+1}/{num_pages}")
            
            # Ensure image is grayscale
            if len(image.shape) == 3:
                image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            
            # Extract answers from this page
            page_answers = extract_answers_from_page(image, debug_dir, timestamp + page_idx)
            
            if page_answers:
                # Add question offset for multi-page quizzes
                if page_idx > 0:
                    page_answers = {k + question_offset: v for k, v in page_answers.items()}
                
                # Update student answers
                student_answers.update(page_answers)
                
                # Update question offset for next page
                question_offset = max(student_answers.keys()) if student_answers else 0
            
            logger.info(f"Found {len(page_answers)} answers on page {page_idx+1}")
    else:
        # Process single image - read as grayscale
        image = cv2.imread(str(quiz_image_path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            logger.error(f"Error: Could not read quiz image: {quiz_image_path}")
            return {}, {}, 0.0
        
        # Extract answers from the image
        student_answers = extract_answers_from_page(image, debug_dir, timestamp)
        logger.info(f"Found {len(student_answers)} answers in quiz image")
    
    # Compare student answers with answer key
    correct_answers = {}
    num_correct = 0
    
    for question_num, student_answer in student_answers.items():
        if question_num in answer_key:
            correct_answer = answer_key[question_num]
            is_correct = student_answer == correct_answer
            correct_answers[question_num] = is_correct
            
            if is_correct:
                num_correct += 1
    
    # Calculate score (percentage correct)
    total_questions = len(correct_answers)
    score = num_correct / total_questions if total_questions > 0 else 0.0
    
    logger.info(f"Quiz score: {score:.2f} ({num_correct}/{total_questions} correct)")
    
    # Create a debug image showing correct and incorrect answers
    if student_answers:
        debug_image = np.zeros((500, 800, 3), dtype=np.uint8)
        debug_image.fill(255)  # White background
        
        # Draw header
        cv2.putText(debug_image, f"Quiz Score: {score:.2f} ({num_correct}/{total_questions})", 
                   (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
        
        # Draw answers table
        y_pos = 70
        cv2.putText(debug_image, "Question | Student | Correct | Result", 
                   (20, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1)
        y_pos += 20
        cv2.line(debug_image, (20, y_pos), (780, y_pos), (0, 0, 0), 1)
        y_pos += 20
        
        # Sort questions by number
        for question_num in sorted(student_answers.keys()):
            student_answer = student_answers[question_num]
            
            if question_num in answer_key:
                correct_answer = answer_key[question_num]
                is_correct = correct_answers[question_num]
                result = "✓" if is_correct else "✗"
                color = (0, 128, 0) if is_correct else (0, 0, 255)  # Green or Red
            else:
                correct_answer = "N/A"
                result = "?"
                color = (0, 0, 255)  # Red
            
            cv2.putText(debug_image, f"{question_num:3d}      {student_answer:5s}     {correct_answer:5s}     {result}", 
                       (20, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 1)
            y_pos += 20
            
            # Add a new page if we run out of space
            if y_pos > 480:
                debug_path = debug_dir / f"quiz_results_{timestamp}.png"
                cv2.imwrite(str(debug_path), debug_image)
                
                # Create a new page
                debug_image = np.zeros((500, 800, 3), dtype=np.uint8)
                debug_image.fill(255)  # White background
                y_pos = 30
        
        # Save the debug image
        debug_path = debug_dir / f"quiz_results_{timestamp}.png"
        cv2.imwrite(str(debug_path), debug_image)
    
    return student_answers, correct_answers, score

def main():
    """Main function for testing."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Process scanned quizzes")
    parser.add_argument("block_id", type=int, help="Block ID to process")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()
    
    # Configure logging
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.WARNING)
    
    # Process the block
    quizzes = process_block_scans(args.block_id)
    
    # Print results
    logger.info(f"Found {len(quizzes)} quizzes in block {args.block_id}")
    for i, quiz in enumerate(quizzes):
        logger.info(f"Quiz {i+1}: {quiz.quiz_id}")
        logger.info(f"  Pages: {len(quiz.pages)}")
        for page in quiz.pages:
            logger.info(f"    Page {page.page_number}: {page.page_type}")
    
if __name__ == "__main__":
    main()
