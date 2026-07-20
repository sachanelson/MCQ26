"""MCQ26 email generation and sending (feedback-only).

This module is a simplified port of the legacy
bubbleSheet/MCQ/generate_signup_email.py and bubbleSheet/MCQ/getEmail.py
routines.  It intentionally contains only quiz-feedback content; signup /
scheduling content is omitted while the MCQ26 signup design is undecided.

Legacy routines that would supply the omitted content are noted in comments.
"""
import os
import json
import base64
import logging
from datetime import datetime
from email.message import EmailMessage
from typing import Dict, Optional, Tuple, Any

from googleapiclient.discovery import build

from database26 import create_db_engine, get_course_info, get_student_by_code
from token_refresh26 import get_gmail_service

logger = logging.getLogger(__name__)

# Autosend configuration -----------------------------------------------------
# Stored in a small JSON file so the setting persists across runs.
_AUTOSEND_CONFIG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'email_autosend.json'
)


def _load_autosend() -> bool:
    try:
        with open(_AUTOSEND_CONFIG_FILE, 'r') as f:
            return bool(json.load(f).get('autosend', False))
    except (FileNotFoundError, json.JSONDecodeError):
        return False


def _save_autosend(enabled: bool) -> None:
    with open(_AUTOSEND_CONFIG_FILE, 'w') as f:
        json.dump({'autosend': bool(enabled)}, f, indent=2)


def get_autosend_status() -> bool:
    """Return True if feedback emails should be sent automatically."""
    return _load_autosend()


def set_autosend_status(enabled: bool) -> None:
    """Persist the autosend setting."""
    _save_autosend(enabled)


# Student lookup -------------------------------------------------------------
def get_student_info(student_code: str, engine=None) -> Optional[Dict[str, Any]]:
    """Return student info needed for email from the MCQ26 database.

    Legacy equivalent: bubbleSheet/MCQ/generate_signup_email.py:get_student_info
    """
    if engine is None:
        engine = create_db_engine()
    student = get_student_by_code(engine, student_code)
    if student is None:
        return None
    return {
        'student_id': student.student_id,
        'name': student.name,
        'email': student.email,
        'student_code': student.student_code,
    }


# Feedback generation --------------------------------------------------------
def generate_quiz_feedback_text(
    quiz_score: int,
    module_number: int,
    date_taken: str,
    passing: bool,
    completed: bool,
    detailed_feedback: Optional[str] = None,
    is_regrade: bool = False,
) -> str:
    """Return the quiz-feedback paragraph for an email.

    Legacy equivalent (combined with template): bubbleSheet/MCQ/generate_signup_email.py:generate_quiz_feedback_text
    """
    lines = []
    if is_regrade:
        lines.append(f"Your Module {module_number} Quiz has been regraded.")
    lines.append(
        f"For the Module {module_number} Quiz taken on {date_taken} you received a score of {quiz_score}%."
    )
    if completed:
        lines.append("This is an excellent grade and you have now passed and completed this module.")
    elif passing:
        lines.append("This is a passing grade.")
    else:
        lines.append("This is not a passing grade, but you can retake it.")

    text = "\n".join(lines)

    if detailed_feedback:
        text += f"\n\nDETAILED FEEDBACK:\n\n{detailed_feedback}"
    return text


def generate_quiz_feedback_email(
    student_code: str,
    quiz_score: int,
    module_number: int,
    date_taken: Optional[str] = None,
    detailed_feedback: Optional[str] = None,
    is_regrade: bool = False,
    engine=None,
) -> Tuple[str, str, Dict[str, Any]]:
    """Generate a feedback-only email.

    Returns (subject, body, context_dict).  The body contains only quiz
    feedback.  Legacy signup/scheduling content would be appended here; see
    comments below.

    Legacy routine that produced the combined email:
      bubbleSheet/MCQ/generate_signup_email.py:generate_email
    """
    if engine is None:
        engine = create_db_engine()
    student_info = get_student_info(student_code, engine=engine)
    if student_info is None:
        raise ValueError(f"Student not found: {student_code}")

    if date_taken is None:
        date_taken = datetime.now().strftime('%Y-%m-%d')

    course_info = get_course_info(engine)
    passing_threshold = float(course_info.get('passing_threshold', 65.0))
    completion_threshold = float(course_info.get('completion_threshold', 90.0))

    passing = quiz_score >= passing_threshold
    completed = quiz_score >= completion_threshold

    subject = f"Quiz Feedback for {student_info['name']}"

    body_lines = [
        f"Dear {student_info['name']},",
        "",
        "Here is your personalized quiz feedback:",
        "",
        generate_quiz_feedback_text(
            quiz_score=quiz_score,
            module_number=module_number,
            date_taken=date_taken,
            passing=passing,
            completed=completed,
            detailed_feedback=detailed_feedback,
            is_regrade=is_regrade,
        ),
        "",
        # SIGNUP / SCHEDULING CONTENT HOOK
        # If MCQ26 later reintroduces signups, append the legacy content here.
        # Available quiz sessions can be supplied by the legacy routine:
        #   bubbleSheet/MCQ/generate_signup_email.py:get_available_blocks
        # and formatted by:
        #   bubbleSheet/MCQ/generate_signup_email.py:format_available_blocks_with_checks
        "Best regards,",
        f"Professors {course_info.get('instructors', 'Sacha Nelson')}",
    ]

    body = "\n".join(body_lines)
    context = {
        'student_code': student_code,
        'student_info': student_info,
        'quiz_score': quiz_score,
        'module_number': module_number,
        'date_taken': date_taken,
        'passing': passing,
        'completed': completed,
        'is_regrade': is_regrade,
    }
    return subject, body, context


# Email sending --------------------------------------------------------------
def send_email(
    service,
    to_email: str,
    subject: str,
    message_text: str,
    email_type: str = 'quiz_feedback',
) -> bool:
    """Send an email via the Gmail API and return True on success.

    Legacy equivalent: bubbleSheet/MCQ/getEmail.py:send_email
    This MCQ26 version removes the development-mode redirect and returns a
    plain bool.
    """
    try:
        message = EmailMessage()
        message.set_content(message_text)
        message['To'] = to_email
        message['Subject'] = subject

        profile = service.users().getProfile(userId='me').execute()
        sender_email = profile['emailAddress']
        message['From'] = sender_email

        encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        create_message = {'raw': encoded_message}
        sent = service.users().messages().send(userId='me', body=create_message).execute()
        message_id = sent.get('id')

        if not message_id:
            logger.error("Gmail API did not return a message id")
            return False

        logger.info(f"Sent {email_type} email to {to_email}, message_id={message_id}")
        return True
    except Exception as e:
        logger.error(f"Error sending email: {e}")
        return False


def generate_and_send_quiz_feedback(
    student_code: str,
    quiz_score: int,
    module_number: int,
    date_taken: Optional[str] = None,
    detailed_feedback: Optional[str] = None,
    is_regrade: bool = False,
    force_send: bool = False,
    engine=None,
) -> Dict[str, Any]:
    """Generate feedback email and send it or return it for manual review.

    Returns a dict:
      {'sent': True}  - email was sent successfully
      {'sent': False, 'reason': 'autosend_disabled', 'subject': ..., 'body': ..., 'recipient': ...}
                       - caller can queue / display it manually
      {'sent': False, 'reason': ..., 'error': ...}
                       - something went wrong
    """
    if engine is None:
        engine = create_db_engine()
    subject, body, context = generate_quiz_feedback_email(
        student_code=student_code,
        quiz_score=quiz_score,
        module_number=module_number,
        date_taken=date_taken,
        detailed_feedback=detailed_feedback,
        is_regrade=is_regrade,
        engine=engine,
    )

    student_info = context['student_info']
    recipient = student_info['email']
    if not recipient:
        return {
            'sent': False,
            'reason': 'no_email',
            'error': f"Student {student_code} has no email address",
        }

    if not force_send and not get_autosend_status():
        return {
            'sent': False,
            'reason': 'autosend_disabled',
            'subject': subject,
            'body': body,
            'recipient': recipient,
            'context': context,
        }

    service = get_gmail_service()
    if service is None:
        return {
            'sent': False,
            'reason': 'no_service',
            'error': 'Unable to create Gmail service',
            'subject': subject,
            'body': body,
            'recipient': recipient,
        }

    ok = send_email(service, recipient, subject, body, email_type='quiz_feedback')
    if ok:
        return {'sent': True, 'recipient': recipient, 'context': context}
    return {
        'sent': False,
        'reason': 'send_failed',
        'error': 'send_email returned False',
        'subject': subject,
        'body': body,
        'recipient': recipient,
    }
