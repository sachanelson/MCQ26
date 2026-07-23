from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List

from sqlalchemy.orm import Session

from database26 import Quiz, Student, create_session_signups, get_active_session_signups, get_quiz_session
from document_ids26 import artifact_id


def qsession_directory(course_folder: str, session: Dict) -> Path:
    date_value = datetime.strptime(session['date'], '%Y-%m-%d')
    start_time = session['start_time'].replace(':', '')
    session_name = (
        f"{date_value.strftime('%a').lower()}{date_value.strftime('%b').lower()}"
        f"{date_value.day:02d}{date_value.strftime('%y')}_{start_time}"
    )
    directory = Path(course_folder).expanduser() / 'qsessions' / session['date'] / session_name
    for name in ('quizzes', 'answer_keys', 'feedback', 'scans'):
        (directory / name).mkdir(parents=True, exist_ok=True)
    return directory


def enrolled_students(engine, section_number: int | None = None) -> List[Student]:
    with Session(engine) as session:
        query = session.query(Student).filter(Student.enrolled.is_(True))
        if section_number is not None:
            query = query.filter(Student.section_number == section_number)
        return query.order_by(Student.name).all()


def available_quizzes_by_student(engine, student_ids: Iterable[int], module_number: int, course_folder: str) -> Dict[int, Quiz]:
    quiz_folder = Path(course_folder).expanduser() / f'module{module_number}' / 'quizzes'
    assigned: Dict[int, Quiz] = {}
    with Session(engine) as session:
        for student_id in student_ids:
            quizzes = (
                session.query(Quiz)
                .filter(
                    Quiz.student_id == student_id,
                    Quiz.module_number == module_number,
                    Quiz.score.is_(None),
                    Quiz.signup_cancelled == 0,
                )
                .order_by(Quiz.id)
                .all()
            )
            for quiz in quizzes:
                if (quiz_folder / f"{artifact_id(quiz.quiz_id, 'Q')}.pdf").is_file():
                    assigned[student_id] = quiz
                    break
    return assigned


def assign_students_to_qsession(
    engine,
    session_id: int,
    student_ids: Iterable[int],
    module_number: int,
    course_folder: str,
) -> Dict[str, object]:
    session = get_quiz_session(engine, session_id)
    if session is None:
        raise ValueError('Select an existing qsession before assigning students.')
    if not course_folder:
        raise ValueError('Set the 2026 course folder in Course Info before assigning students.')
    student_ids = list(dict.fromkeys(student_ids))
    if not student_ids:
        raise ValueError('Select at least one student to assign.')
    available = available_quizzes_by_student(engine, student_ids, module_number, course_folder)
    missing = [student_id for student_id in student_ids if student_id not in available]
    directory = qsession_directory(course_folder, session)
    quiz_ids = {student_id: quiz.quiz_id for student_id, quiz in available.items()}
    created = create_session_signups(engine, session_id, list(available), module_number, quiz_ids)
    for quiz in available.values():
        _link_quiz_artifacts(course_folder, module_number, quiz.quiz_id, directory)
    return {
        'created': created,
        'directory': directory,
        'missing_student_ids': missing,
        'signups': get_active_session_signups(engine, session_id),
    }


def _link_quiz_artifacts(course_folder: str, module_number: int, quiz_id: str, directory: Path) -> None:
    source_dir = Path(course_folder).expanduser() / f'module{module_number}' / 'quizzes'
    artifacts = (
        ('Q', directory / 'quizzes'),
        ('A', directory / 'answer_keys'),
    )
    for kind, target_dir in artifacts:
        artifact = artifact_id(quiz_id, kind)
        for source in source_dir.glob(f'{artifact}.*'):
            target = target_dir / source.name
            if target.is_symlink() and target.resolve() == source.resolve():
                continue
            if target.exists() or target.is_symlink():
                target.unlink()
            target.symlink_to(source.resolve())
