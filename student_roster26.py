import math
import re
from pathlib import Path
from typing import Any, Dict, List

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


def _parse_roster_rows(file_path: str) -> List[Dict[str, Any]]:
    """Return a list of {source_row, name, email} dicts from an enrollment Excel file."""
    path = Path(file_path).expanduser()
    report = pd.read_excel(path, header=None)
    rows = []
    for idx, row in report.iterrows():
        name = parse_enrollment_name(
            row.iloc[0] if len(row) > 0 else None,
            row.iloc[2] if len(row) > 2 else None,
        )
        email = _text(row.iloc[4] if len(row) > 4 else None)
        if not name or not email or '@' not in email:
            continue
        rows.append({
            'source_row': int(idx),
            'name': name,
            'email': email,
        })
    return rows


def _analyze_roster_rows(
    rows: List[Dict[str, Any]],
    session,
    section_number: int,
) -> Dict[str, Any]:
    """Analyze parsed roster rows against the current database session.

    Returns a dict with:
        rows: the input rows annotated with 'action' and 'reason' keys
        conflicts: human-readable conflict messages
        duplicates: rows that share a name/email with another import row and
                    would otherwise be added as a new student
        updates: rows that will update an existing student
        additions: rows that will create a new student
    """
    conflicts: List[str] = []
    duplicate_rows: List[Dict[str, Any]] = []
    update_rows: List[Dict[str, Any]] = []
    add_rows: List[Dict[str, Any]] = []

    seen_names: Dict[str, Dict[str, Any]] = {}
    seen_emails: Dict[str, Dict[str, Any]] = {}

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

    for row in rows:
        name_lower = row['name'].casefold()
        email_lower = row['email'].casefold()
        by_email = students_by_email.get(email_lower)
        by_name = students_by_name.get(name_lower)
        row['duplicate_reasons'] = []

        if by_email is not None and by_email is by_name:
            row['action'] = 'update'
            row['student'] = by_email
            row['reason'] = (
                f"Will update existing student {by_email.name} "
                f"({by_email.student_code or '?'}) and assign to section {section_number}."
            )
            update_rows.append(row)
        elif by_email is not None and by_name is None:
            e_info = (
                f"email={by_email.name}/{by_email.student_code or '?'}/"
                f"sec{by_email.section_number}"
            )
            row['action'] = 'conflict'
            row['reason'] = (
                f"Row {row['source_row']}: '{row['name']}' / '{row['email']}' "
                f"matched by name=no name match and {e_info} but not to the same student"
            )
            conflicts.append(row['reason'])
        elif by_email is None and by_name is not None:
            n_info = (
                f"name={by_name.name}/{by_name.student_code or '?'}/"
                f"sec{by_name.section_number}"
            )
            row['action'] = 'conflict'
            row['reason'] = (
                f"Row {row['source_row']}: '{row['name']}' / '{row['email']}' "
                f"matched by {n_info} and email=no email match but not to the same student"
            )
            conflicts.append(row['reason'])
        elif by_email is not None and by_name is not None and by_email is not by_name:
            n_info = (
                f"name={by_name.name}/{by_name.student_code or '?'}/"
                f"sec{by_name.section_number}"
            )
            e_info = (
                f"email={by_email.name}/{by_email.student_code or '?'}/"
                f"sec{by_email.section_number}"
            )
            row['action'] = 'conflict'
            row['reason'] = (
                f"Row {row['source_row']}: '{row['name']}' / '{row['email']}' "
                f"matched by {n_info} and {e_info} but not to the same student"
            )
            conflicts.append(row['reason'])
        else:
            row['action'] = 'add'
            row['student'] = None
            row['reason'] = None
            add_rows.append(row)

        # Detect duplicate names/emails within the import file.
        if name_lower in seen_names and seen_names[name_lower] is not row:
            row['duplicate_reasons'].append(
                f"name '{row['name']}' also appears on row {seen_names[name_lower]['source_row']}"
            )
        if email_lower in seen_emails and seen_emails[email_lower] is not row:
            row['duplicate_reasons'].append(
                f"email '{row['email']}' also appears on row {seen_emails[email_lower]['source_row']}"
            )

        if row['action'] == 'add' and row['duplicate_reasons']:
            duplicate_rows.append(row)

        seen_names.setdefault(name_lower, row)
        seen_emails.setdefault(email_lower, row)

    return {
        'rows': rows,
        'conflicts': conflicts,
        'duplicates': duplicate_rows,
        'updates': update_rows,
        'additions': add_rows,
    }


def preview_section_roster(
    file_path_or_rows,
    engine,
    section_number: int,
) -> Dict[str, Any]:
    """Preview a roster import without writing to the database.

    Accepts either a path to an Excel file or an already-parsed list of row
    dicts with 'source_row', 'name', and 'email' keys.
    """
    if isinstance(file_path_or_rows, str):
        path = Path(file_path_or_rows).expanduser()
        if path.suffix.lower() not in {'.xlsx', '.xls'}:
            raise ValueError('Roster imports must be Excel files (.xlsx or .xls).')
        parsed_rows = _parse_roster_rows(file_path_or_rows)
    else:
        parsed_rows = list(file_path_or_rows)

    if get_section(engine, section_number) is None:
        raise ValueError(f'Section {section_number} must be defined in Course Info before importing its roster.')

    with Session(engine) as session:
        return _analyze_roster_rows(parsed_rows, session, section_number)


def import_section_roster(
    rows: List[Dict[str, Any]],
    engine,
    section_number: int,
) -> Dict[str, Any]:
    """Apply a roster import from already-parsed and reviewed row dicts.

    The caller is expected to have first used preview_section_roster, optionally
    edited or removed duplicate rows with user confirmation, and then passed
    the resulting row list here. The list is re-analyzed against the current
    database state before any writes occur.

    Returns a dict with 'imported' count and 'conflicts' list.
    """
    imported = 0
    imported_names: set[str] = set()

    with Session(engine) as session:
        analysis = _analyze_roster_rows(rows, session, section_number)
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

        for row in analysis['rows']:
            if row['action'] == 'conflict':
                continue

            name = row['name']
            email = row['email']
            name_lower = name.casefold()
            email_lower = email.casefold()
            imported_names.add(name_lower)

            if row['action'] == 'update':
                student = students_by_email.get(email_lower) or students_by_name.get(name_lower)
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
            elif row['action'] == 'add':
                code = generate_student_code(name, existing_codes)
                student = Student(name=name, email=email, student_code=code, section_number=section_number)
                session.add(student)
                existing_codes.add(code.casefold())
                students_by_email[email_lower] = student
                students_by_name[name_lower] = student
                imported += 1

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

    return {
        'imported': imported,
        'conflicts': analysis['conflicts'],
    }
