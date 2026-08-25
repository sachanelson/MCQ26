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
    imported_names: set = set()
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
        conflicts = []
        for idx, row in report.iterrows():
            name = parse_enrollment_name(row.iloc[0] if len(row) > 0 else None,
                                         row.iloc[2] if len(row) > 2 else None)
            email = _text(row.iloc[4] if len(row) > 4 else None)
            if not name or not email or '@' not in email:
                continue
            imported_names.add(name.casefold())
            name_lower = name.casefold()
            email_lower = email.casefold()
            by_email = students_by_email.get(email_lower)
            by_name = students_by_name.get(name_lower)
            if by_email is not None and by_email is by_name:
                # Exact match by both name and email to the same student.
                student = by_email
                name_changed = student.name != name
                student.name = name
                student.email = email
                student.section_number = section_number
                student.enrolled = True
                if name_changed or not (student.student_code and student.student_code.isalnum()):
                    old_code = (student.student_code or '').casefold()
                    existing_codes.discard(old_code)
                    student.student_code = generate_student_code(name, existing_codes)
                    existing_codes.add(student.student_code.casefold())
                    students_by_name[name_lower] = student
                imported += 1
            elif by_email is not None or by_name is not None:
                n_info = f"name={by_name.name}/{by_name.student_code or '?'}/sec{by_name.section_number}" if by_name else "no name match"
                e_info = f"email={by_email.name}/{by_email.student_code or '?'}/sec{by_email.section_number}" if by_email else "no email match"
                conflicts.append(f"Row {idx}: '{name}' / '{email}' matched by {n_info} and {e_info} but not to the same student")
            else:
                code = generate_student_code(name, existing_codes)
                student = Student(name=name, email=email, student_code=code, section_number=section_number)
                session.add(student)
                existing_codes.add(code.casefold())
                students_by_email[email_lower] = student
                students_by_name[name_lower] = student
                imported += 1

        if conflicts:
            print(f"Roster import conflicts for section {section_number}:")
            for msg in conflicts:
                print("  " + msg)

        # Report current-section students who are not in the imported roster.
        current_students = session.query(Student).filter_by(section_number=section_number).all()
        for student in current_students:
            if student.name and student.name.casefold() not in imported_names:
                other_rows = session.query(Student).filter(
                    Student.student_id != student.student_id,
                    Student.name.ilike(student.name),
                ).all()
                if other_rows:
                    other_sections = sorted({s.section_number for s in other_rows if s.section_number is not None})
                    print(f"Student {student.name} ({student.student_code}) in section {section_number} "
                          f"not in imported roster; also listed in section(s): {', '.join(map(str, other_sections))}")
                else:
                    print(f"Student {student.name} ({student.student_code}) in section {section_number} "
                          f"not in imported roster; not listed in any other section")

        session.commit()
    return imported
