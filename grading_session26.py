"""Grading session bookkeeping for MCQ26.

A grading session corresponds to grading one scanned batch (one PDF of
scanned quiz bubble sheets). Sessions are organized on disk by date, plus a
letter suffix ('a', 'b', 'c', ...) for multiple scans graded on the same
date:

    <course_folder>/grading/<session_date>/<session_date><letter>/

Each session directory holds an archived copy of the scan file plus any
per-quiz artifacts written during grading (see grading26.save_grading_artifacts
and the 'unresolved' subfolder for scans whose QR code couldn't be read).

For now, the linkage between qsessions (when quizzes were administered) and
grading sessions (when they were scanned/graded) is tracked manually by
staff; this module only manages the grading-session bookkeeping itself.
"""
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from database26 import create_grading_session, get_grading_sessions


def grading_root(course_folder: str) -> Path:
    """Return the top-level 'grading' folder under *course_folder*."""
    return Path(course_folder).expanduser() / 'grading'


def session_directory(course_folder: str, session_date: str, letter: str) -> Path:
    """Return the directory for a given grading session date/letter."""
    name = f'{session_date}{letter}'
    return grading_root(course_folder) / session_date / name


def next_letter(engine, session_date: str) -> str:
    """Return the next unused letter ('a', 'b', 'c', ...) for *session_date*."""
    used = {row['letter'] for row in get_grading_sessions(engine, session_date=session_date)}
    for i in range(26):
        letter = chr(ord('a') + i)
        if letter not in used:
            return letter
    raise ValueError(f'No letters left for grading sessions on {session_date}')


def start_grading_session(
    engine,
    course_folder: str,
    scan_path: str,
    session_date: Optional[str] = None,
) -> Dict:
    """Start a new grading session for *scan_path*.

    Assigns the next available date/letter, creates the session directory,
    archives a copy of the scan file into it, and records the session in the
    database. Returns a dict with the GradingSession fields plus a
    'directory' key (Path) for the session's on-disk folder.
    """
    if not course_folder:
        raise ValueError('Course folder is not set. Set it in Course Info first.')
    if session_date is None:
        session_date = datetime.now().strftime('%Y-%m-%d')

    letter = next_letter(engine, session_date)
    directory = session_directory(course_folder, session_date, letter)
    directory.mkdir(parents=True, exist_ok=True)

    scan_source = Path(scan_path)
    archived_scan = directory / scan_source.name
    shutil.copy2(scan_source, archived_scan)

    info = create_grading_session(
        engine,
        session_date=session_date,
        letter=letter,
        scan_path=str(archived_scan),
        original_scan_filename=scan_source.name,
    )
    info['directory'] = directory
    return info
