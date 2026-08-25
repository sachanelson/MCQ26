#!/usr/bin/env python3
"""
Module for detecting QR codes in quiz images and extracting quiz IDs.
This module was extracted from grade_quiz_new.py to make it more maintainable.
"""

import os
import re
import time
import logging
import traceback
import ctypes.util
import warnings
import ctypes
import signal
from typing import Optional, List

# Get our module logger
logger = logging.getLogger(__name__)

# Define a custom signal handler to ignore SIGABRT signals from zbar assertions
def ignore_sigabrt(signum, frame):
    # Just return without raising an exception
    return

# Add Homebrew lib paths to help find zbar shared library
brew_lib_paths = [
    '/opt/homebrew/lib',  # Apple Silicon Macs
    '/usr/local/lib'      # Intel Macs
]

# Try to preload the zbar library
zbar_found = False
for lib_path in brew_lib_paths:
    zbar_path = os.path.join(lib_path, 'libzbar.dylib')
    if os.path.exists(zbar_path):
        try:
            # Set the library path in environment
            os.environ['DYLD_LIBRARY_PATH'] = lib_path
            # Load the library explicitly
            zbar_lib = ctypes.CDLL(zbar_path)
            # Set the library path for ctypes
            ctypes.util._findLib_dyld = lambda name: zbar_path
            zbar_found = True
            logger.info(f"Found zbar library at {zbar_path}")
            
            # Try to disable zbar debug output at the C level
            try:
                # Some zbar libraries expose a set_verbosity function
                if hasattr(zbar_lib, 'zbar_set_verbosity'):
                    zbar_lib.zbar_set_verbosity(0)  # Set verbosity to 0 (silent)
                    logger.info("Successfully disabled zbar verbosity")
            except Exception as e:
                logger.debug(f"Could not set zbar verbosity: {e}")
                
            break
        except Exception as e:
            logger.warning(f"Failed to load zbar from {zbar_path}: {e}")

if not zbar_found:
    logger.warning("Could not find zbar library. QR code detection may be limited.")
    
# Install the custom signal handler for SIGABRT
# This will prevent the program from crashing due to zbar assertions
try:
    original_sigabrt = signal.getsignal(signal.SIGABRT)
    signal.signal(signal.SIGABRT, ignore_sigabrt)
    logger.info("Installed custom SIGABRT handler to prevent zbar assertion crashes")
except Exception as e:
    logger.warning(f"Could not install SIGABRT handler: {e}")

# Import required libraries
try:
    import numpy as np
    import cv2
    import os
    import sys
    import contextlib
    import tempfile
    import warnings
    import subprocess
    from pyzbar.pyzbar import decode as original_pyzbar_decode
    
    # Define a custom warning filter to ignore zbar databar warnings
    warnings.filterwarnings("ignore", message=".*decoder/databar.*")
    
    # Create a wrapper for pyzbar_decode that completely suppresses zbar warnings
    def pyzbar_decode(image):
        """Wrapper for pyzbar.pyzbar.decode that suppresses zbar warnings.
        
        This wrapper uses multiple techniques to ensure zbar warnings are completely
        suppressed, including stderr redirection and warning filters.
        
        Args:
            image: The image to decode
            
        Returns:
            The decoded objects from pyzbar
        """
        # Temporarily disable stderr
        old_stderr = sys.stderr
        devnull = open(os.devnull, 'w')
        sys.stderr = devnull
        
        try:
            # Use warnings.catch_warnings to suppress any Python warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                # Call the original function
                result = original_pyzbar_decode(image)
                return result
        except Exception as e:
            logger.warning(f"Error in pyzbar_decode wrapper: {e}")
            # Return empty list on error
            return []
        finally:
            # Restore stderr
            sys.stderr = old_stderr
            devnull.close()
    
    # Define a context manager to suppress zbar warnings
    @contextlib.contextmanager
    def suppress_stderr():
        """Context manager to suppress stderr output temporarily.
        
        This completely redirects stderr to /dev/null to prevent zbar warnings
        from being displayed. This is particularly important for suppressing
        the 'decoder/databar.c:1211: _zbar_decode_databar: Assertion "seg->finder >= 0" failed'
        warnings that occur during QR code detection.
        
        Combined with warnings.filterwarnings, this provides two layers of protection
        against the zbar assertion warnings.
        """
        # Save the current stderr
        old_stderr = sys.stderr
        # Redirect stderr to /dev/null
        with open(os.devnull, 'w') as devnull:
            sys.stderr = devnull
            try:
                # Also suppress warnings during execution
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    yield
            finally:
                # Restore stderr
                sys.stderr = old_stderr
                
except ImportError as e:
    logger.error(f"Error importing required libraries: {e}")
    raise

def _extract_quiz_id_from_data(data, pattern_type="flexible"):
    """
    Extract quiz ID from QR code data using regex patterns.
    Trims any extra digits that might be page numbers.
    
    Args:
        data: The decoded QR code data string
        pattern_type: Type of pattern to use (standard, alternative, or flexible)
        
    Returns:
        str: The extracted quiz ID or None if no match
    """
    if not data:
        return None
    
    # Standard format: 2-3 uppercase letters + optional digits + underscore + 3-4 digits
    standard_match = re.search(r'[A-Z]{2,3}\d*_\d{3,4}', data)
    if pattern_type in ("standard", "all") and standard_match:
        result = re.search(r'([A-Z]{2,3}\d*_\d{3,4})', data).group(1)
        return _trim_extra_digit(result)
        
    # Alternative format: 2-3 uppercase letters + optional lowercase + optional digits + underscore + 3-4 digits
    alt_match = re.search(r'([A-Z]{2,3}[a-z]*\d*_\d{3,4})', data)
    if pattern_type in ("alternative", "all") and alt_match:
        result = re.search(r'([A-Z]{2,3}[a-z]*\d*_\d{3,4})', data).group(1)
        return _trim_extra_digit(result)
        
    # Flexible format: 2-4 letters + optional digits + underscore + 2-5 digits
    # For SoA01_0009 format
    flex_match = re.search(r'([A-Za-z]{2,4}\d*_\d{2,5})', data)
    if pattern_type in ("flexible", "all") and flex_match:
        result = flex_match.group(1)
        
        # Handle both old and new QR code formats
        # Old format: SoA02_00091 (quiz ID + page number without separator)
        # New format: SoA02_0009:p1 (quiz ID + separator + page indicator + page number)
        
        # First check if this is the new format with a separator
        if ':p' in result:
            # New format - extract just the quiz ID part
            quiz_id = result.split(':p')[0]
            result = quiz_id
        # Otherwise handle the old format
        elif result.startswith('SoA') and '_' in result:
            # Split by underscore and reconstruct the ID
            prefix, suffix = result.split('_', 1)
            
            # Ensure prefix is SoA02 format (3 letters + 2 digits)
            if len(prefix) >= 5:
                prefix = prefix[:5]
                
            # For old format, the suffix might have the page number appended
            # Extract just the first 4 digits for the quiz number
            if len(suffix) >= 4:
                suffix = suffix[:4]
            
            corrected = f"{prefix}_{suffix}"
            result = corrected
        
        # Trim any extra digit that might be a page number
        return _trim_extra_digit(result)
        
    return None

def _trim_extra_digit(quiz_id):
    """
    Trim any extra digit at the end of a quiz ID that might be a page number.
    For example, "KaB01_00101" -> "KaB01_0010"
    
    Args:
        quiz_id: The quiz ID to trim
        
    Returns:
        str: The trimmed quiz ID
    """
    if not quiz_id:
        return quiz_id
        
    # Check if the quiz ID has an underscore
    if '_' not in quiz_id:
        return quiz_id
        
    # Split by underscore
    prefix, suffix = quiz_id.split('_', 1)
    
    # If the suffix is longer than 4 digits, trim it to 4
    if len(suffix) > 4 and suffix[:4].isdigit():
        return f"{prefix}_{suffix[:4]}"
    
    return quiz_id

# Global threshold value to be reused across pages
_qr_threshold = None

def decode_raw_qr_data(image):
    """Decode the raw string content of a QR code in the lower-right corner.

    This is the shared crop/threshold/decode logic used by
    extract_quiz_id_from_qr, factored out so callers that need the *full*
    raw QR payload (e.g. MCQ26 encodes '{quiz_id}|{page_number}') can get it
    without extract_quiz_id_from_qr's quiz-ID-only regex extraction
    discarding the rest of the content.

    Args:
        image: The input image containing QR code(s) - assumed to be grayscale

    Returns:
        str: The raw decoded QR string, or None if nothing could be decoded
    """
    global _qr_threshold

    if image is None or image.size == 0 or image.max() == image.min():
        logger.error("Image appears to be empty or invalid")
        return None

    # Extract the lower right corner region where QR code is expected
    height, width = image.shape[:2]
    x1 = int(width * 0.75)
    y1 = int(height * 0.75)
    x2 = width
    y2 = height
    qr_region = image[y1:y2, x1:x2]

    # Calculate threshold only for the first page or if not set yet
    if _qr_threshold is None:
        hist = cv2.calcHist([qr_region], [0], None, [256], [0, 256])
        hist_flat = hist.flatten()
        cum_hist = np.cumsum(hist_flat)
        cum_hist = cum_hist / cum_hist[-1]  # Normalize
        dark_threshold = next((i for i, v in enumerate(cum_hist) if v >= 0.01), 0)
        light_threshold = next((i for i, v in enumerate(cum_hist) if v >= 0.90), 255)
        _qr_threshold = int((dark_threshold + light_threshold) / 2)

    _, qr_region_binary = cv2.threshold(qr_region, _qr_threshold, 255, cv2.THRESH_BINARY)

    # Try pyzbar on binary region only
    try:
        with suppress_stderr():
            decoded_objects = pyzbar_decode(qr_region_binary)
        if decoded_objects:
            logger.info(f"QR(pyzbar): decoded {len(decoded_objects)} object(s) in lower-right region")
            for obj in decoded_objects:
                try:
                    data = obj.data.decode('utf-8', errors='ignore')
                    if data:
                        return data
                except Exception as e:
                    logger.debug(f"QR(pyzbar) decode object error: {e}")
        else:
            logger.info("QR(pyzbar): no decoded objects in lower-right region")
    except Exception as e:
        logger.debug(f"QR(pyzbar) exception: {e}")

    # Try OpenCV on binary region only
    try:
        qrd = cv2.QRCodeDetector()
        data, points, _ = qrd.detectAndDecode(qr_region_binary)
        if data:
            return data
        logger.info("QR(opencv): no decoded data in lower-right region")
    except Exception as e:
        logger.debug(f"QR(opencv) exception: {e}")

    logger.error("No QR codes detected with any method for this page region")
    return None


def extract_quiz_id_from_qr(image):
    """Extract quiz ID from QR code in the image, focusing on the lower right corner.
    
    Args:
        image: The input image containing QR code(s) - assumed to be grayscale
        
    Returns:
        str: The extracted quiz ID or None if not found
    """
    data = decode_raw_qr_data(image)
    if not data:
        return None
    quiz_id = _extract_quiz_id_from_data(data, "all")
    if quiz_id is None:
        print(f"QR DEBUG: raw='{data}' -> quiz_id='{quiz_id}'")
    logger.info(f"QR raw='{data}' -> quiz_id='{quiz_id}'")
    return quiz_id


# ---------------------------------------------------------------------------
# QR code generation helpers (formerly qr_code_utils.py)
# ---------------------------------------------------------------------------

def generate_qr_code(student_code, quiz_id, page_number, out_path=None, box_size=10, border=4):
    """Generate a QR code encoding the quiz ID.

    Args:
        student_code: Student code (not used in QR content; kept for API compatibility)
        quiz_id: Quiz ID in format {student_code}{module_num}_{index}
        page_number: Page number to encode (not used in content; kept for API compatibility)
        out_path: Optional path to save the QR code image
        box_size: Size of each box in the QR code
        border: Border size around the QR code

    Returns:
        PIL Image object of the generated QR code
    """
    import qrcode
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=box_size,
        border=border,
    )
    qr.add_data(quiz_id)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert('RGB')
    if out_path:
        img.save(out_path)
    return img

def read_qr_code(image_path_or_bytes):
    """Read a QR code from a file path, bytes, or numpy array. Returns decoded string or None."""
    import numpy as np
    import cv2
    from PIL import Image
    import io
    if isinstance(image_path_or_bytes, str):
        img = cv2.imread(image_path_or_bytes)
    elif isinstance(image_path_or_bytes, np.ndarray):
        img = np.ascontiguousarray(image_path_or_bytes) if not image_path_or_bytes.flags['C_CONTIGUOUS'] else image_path_or_bytes
    else:
        img = np.array(Image.open(io.BytesIO(image_path_or_bytes)))
    if img.dtype != np.uint8:
        img = img.astype(np.uint8)
    detector = cv2.QRCodeDetector()
    data, bbox, _ = detector.detectAndDecode(img)
    return data if data else None
