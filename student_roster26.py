import math
import re
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from database26 import Student, get_section


_TRAILING_PAREN_RE = re.compile(r'\s*\([^)]*\)?\s*$')


def _text(value) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ''
    return str(value).strip()


def _strip_parenthetical(value) -> str:
    return _TRAILING_PAREN_RE.sub('', _text(value)).strip()


def parse_enrollment_name(column_a, column_c) -> str:
    """Extract a student's full name from an enrollment report row."""
    primary = _text(column_a)
    if ' - ' in primary:
        primary = primary.split(' - ', 1)[0].strip()
    primary = _strip_parenthetical(primary)
    if primary:
        return primary
    return _strip_parenthetical(column_c)


def generate_student_code(name: str, existing_codes: set[str]) -> str:
    name = _strip_parenthetical(name).strip()
    parts = name.split()
    if len(parts) < 2:
        raise ValueError(f'Cannot generate a student code for {name!r}')
    first_name = parts[0].lower()
    last_name = parts[-1].lower()
    candidates = []
    if len(first_name) > 1:
        candidates.append(f'{first_name[0].upper()}{first_name[1]}{last_name[0].upper()}')
    if len(last_name) > 1:
        candidates.append(f'{first_name[0].upper()}{last_name[0].upper()}{last_name[1]}')
    candidates.extend(
        f'{first_name[0].upper()}{letter}{last_name[0].upper()}'
        for letter in first_name[2:]
    )
    candidates.extend(
        f'{first_name[0].upper()}{last_name[0].upper()}{letter}'
        for letter in last_name[2:]
    )
    for code in candidates:
        if code.casefold() not in existing_codes:
            return code
    base = candidates[0] if candidates else f'{first_name[0].upper()}{last_name[0].upper()}'
    suffix = 2
    while f'{base}{suffix}'.casefold() in existing_codes:
        suffix += 1
    return f'{base}{suffix}'


def import_section_roster(file_path: str, engine, section_number: int) -> int:
    """Import an enrollment Excel report and assign every imported student to a section."""
    path = Path(file_path).expanduser()
    if path.suffix.lower() not in {'.xlsx', '.xls'}:
        raise ValueError('Roster imports must be Excel files (.xlsx or .xls).')
    if get_section(engine, section_number) is None:
        raise ValueError(f'Section {section_number} must be defined in Course Info before importing its roster.')

    report = pd.read_excel(path, header=None)
    imported = 0
    with Session(engine) as session:
        existing_codes = {
            student.student_code.casefold()
            for student in session.query(Student).all()
            if student.student_code
        }
        students_by_email = {
            student.email.casefold(): student
            for student in session.query(Student).all()
            if student.email
        }
        students_by_name = {
            student.name.casefold(): student
            for student in session.query(Student).all()
            if student.name
        }
        for _, row in report.iterrows():
            name = parse_enrollment_name(row.iloc[0] if len(row) > 0 else None,
                                         row.iloc[2] if len(row) > 2 else None)
            email = _text(row.iloc[4] if len(row) > 4 else None)
            if not name or not email or '@' not in email:
                continue
            student = students_by_email.get(email.casefold()) or students_by_name.get(name.casefold())
            if student is None:
                code = generate_student_code(name, existing_codes)
                student = Student(name=name, email=email, student_code=code, section_number=section_number)
                session.add(student)
                existing_codes.add(code.casefold())
                students_by_email[email.casefold()] = student
                students_by_name[name.casefold()] = student
            else:
                name_changed = student.name != name
                student.name = name
                student.email = email
                student.section_number = section_number
                code_invalid = not (student.student_code and student.student_code.isalnum())
                if name_changed or code_invalid:
                    old_code = (student.student_code or '').casefold()
                    existing_codes.discard(old_code)
                    student.student_code = generate_student_code(name, existing_codes)
                    existing_codes.add(student.student_code.casefold())
                    students_by_name[name.casefold()] = student
            imported += 1
        session.commit()
    return imported
