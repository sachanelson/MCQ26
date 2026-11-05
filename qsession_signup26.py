from datetime import datetime
from pathlib import Path
from typing import Dict, List

from sqlalchemy.orm import Session

from database26 import Student, get_quiz_session


def qsession_directory(course_folder: str, session: Dict) -> Path:
    date_value = datetime.strptime(session['date'], '%Y-%m-%d')
    start_time = session['start_time'].replace(':', '')
    session_name = (
        f"{date_value.strftime('%a').lower()}{date_value.strftime('%b').lower()}"
        f"{date_value.day:02d}{date_value.strftime('%y')}_{start_time}"
    )
    directory = Path(course_folder).expanduser() / 'qsessions' / session['date'] / session_name
    for name in ('answer_keys', 'feedback', 'scans'):
        (directory / name).mkdir(parents=True, exist_ok=True)
    return directory


def enrolled_students(engine, section_number: int | None = None) -> List[Student]:
    with Session(engine) as session:
        query = session.query(Student).filter(Student.enrolled.is_(True))
        if section_number is not None:
            query = query.filter(Student.section_number == section_number)
        return query.order_by(Student.name).all()

