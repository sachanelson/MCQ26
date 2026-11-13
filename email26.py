"""MCQ26 email generation and sending (quiz feedback only).

This module is a simplified port of the legacy
bubbleSheet/MCQ/generate_signup_email.py and bubbleSheet/MCQ/getEmail.py
routines. MCQ26 has no quiz-session-signup emails (no signup confirmations
or signup-problem notifications) - the only email content is quiz feedback.

Feedback emails are always queued (database26.OutgoingEmail) rather than
sent immediately; a staff member reviews and sends them from the Email
Management panel (email_management26.py).
"""
import base64
import logging
from datetime import datetime
from email.message import EmailMessage
from typing import Dict, Optional, Tuple, Any

from googleapiclient.discovery import build

from database26 import (
    create_db_engine, get_course_info, get_student_by_code,
    queue_outgoing_email, update_outgoing_email_status,
)
from token_refresh26 import get_gmail_service
import gmail_accounts26

logger = logging.getLogger(__name__)
TEST_FEEDBACK_RECIPIENT = 'nelson@brandeis.edu'


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
    if passing:
        if quiz_score > 89:
            lines.append("This is an excellent score.")
        lines.append(f"You have now passed module {module_number}.")
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

    Returns (subject, body, context_dict). MCQ26 has no signup emails, so
    the body contains only quiz feedback.

    Legacy routine this was ported from (which also included signup/
    scheduling content, now removed): bubbleSheet/MCQ/generate_signup_email.py:generate_email
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

    passing = quiz_score >= passing_threshold

    subject = f"Quiz {module_number} Feedback for {student_info['name']}"

    body_lines = [
        f"Dear {student_info['name']},",
        "",
    ]
    if is_regrade:
        body_lines += [
            "An error was detected in the initial grading of this quiz and so it has been manually rescored.",
            "",
        ]
    body_lines += [
        "Here is your personalized quiz feedback:",
        "",
        generate_quiz_feedback_text(
            quiz_score=quiz_score,
            module_number=module_number,
            date_taken=date_taken,
            passing=passing,
            detailed_feedback=detailed_feedback,
            is_regrade=is_regrade,
        ),
        "",
        "Best regards,",
        f"Professor {course_info.get('instructors')}",
        "",
        "********************************************",
        "Please do not reply; this account is monitored infrequently. Send email intended for Sacha Nelson to 'nelson@brandeis.edu'",
    ]

    body = "\n".join(body_lines)
    context = {
        'student_code': student_code,
        'student_info': student_info,
        'quiz_score': quiz_score,
        'module_number': module_number,
        'date_taken': date_taken,
        'passing': passing,
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


def queue_test_quiz_feedback(
    student_code: str,
    quiz_score: int,
    module_number: int,
    date_taken: Optional[str] = None,
    detailed_feedback: Optional[str] = None,
    is_regrade: bool = False,
    engine=None,
) -> Dict[str, Any]:
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
    student_email = context['student_info'].get('email') or '(no student email address)'
    test_line = f'TEST MODE, email would have been sent to {student_email}.'
    body = f'{test_line}\n\n{body}'
    subject = f'[TEST MODE] {subject}'
    email_id = queue_outgoing_email(
        engine, TEST_FEEDBACK_RECIPIENT, subject, body, 'test_quiz_feedback',
    )
    return {
        'queued': True,
        'email_id': email_id,
        'subject': subject,
        'body': body,
        'recipient': TEST_FEEDBACK_RECIPIENT,
        'intended_recipient': student_email,
        'context': context,
    }


def generate_and_queue_quiz_feedback(
    student_code: str,
    quiz_score: int,
    module_number: int,
    date_taken: Optional[str] = None,
    detailed_feedback: Optional[str] = None,
    is_regrade: bool = False,
    engine=None,
) -> Dict[str, Any]:
    """Generate a feedback email and queue it for a staff member to review and send.

    Feedback emails are never sent automatically - use the Email Management
    panel (email_management26.py) to review and send queued emails via
    send_queued_email.

    Returns a dict:
      {'queued': True, 'email_id': ..., 'subject': ..., 'body': ..., 'recipient': ..., 'context': ...}
      {'queued': False, 'reason': ..., 'error': ...}  - something went wrong (e.g. no email on file)
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
            'queued': False,
            'reason': 'no_email',
            'error': f"Student {student_code} has no email address",
        }

    email_id = queue_outgoing_email(engine, recipient, subject, body, 'quiz_feedback')
    return {
        'queued': True,
        'email_id': email_id,
        'subject': subject,
        'body': body,
        'recipient': recipient,
        'context': context,
    }


def get_feedback_service():
    """Gmail service for the feedback account (Accounts tab), else legacy token.json."""
    account = gmail_accounts26.get_account('feedback')
    if account:
        return gmail_accounts26.get_service(account)
    return get_gmail_service()


def send_queued_email(engine, email_id: int, recipient: str, subject: str, body: str, email_type: str) -> bool:
    service = get_feedback_service()
    if service is None:
        account = gmail_accounts26.get_account('feedback') or 'token.json'
        update_outgoing_email_status(engine, email_id, 'failed',
                                     f'Unable to create Gmail service ({account} not signed in)')
        return False
    if send_email(service, recipient, subject, body, email_type=email_type):
        update_outgoing_email_status(engine, email_id, 'sent')
        return True
    update_outgoing_email_status(engine, email_id, 'failed', 'send_email returned False')
    return False
