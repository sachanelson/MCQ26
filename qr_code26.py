"""
Simple QR code generation for MCQ26 quizzes.

Replaces the old MCQ.qr_detector dependency for the quiz creation path.
Encodes the quiz ID and page number in the QR image.
"""
import qrcode
from PIL import Image


def generate_qr_code(student_code: str, quiz_id: str, page_number: int = 1) -> Image.Image:
    """Generate a QR code image containing the quiz ID and page number.

    Args:
        student_code: Student code (included for compatibility with old API)
        quiz_id: Quiz identifier
        page_number: Page number (default 1)

    Returns:
        PIL Image.Image with the generated QR code.
    """
    content = f"{quiz_id}|{page_number}"
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(content)
    qr.make(fit=True)
    return qr.make_image(fill_color='black', back_color='white')
