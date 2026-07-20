import csv
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Optional

from database26 import (
    get_all_sections,
    get_course_info,
    get_section_meeting_grades,
    get_section_meetings,
    get_students_for_section,
    save_section_meeting,
    save_section_meeting_grade,
)
from document_ids26 import format_worksheet_id, worksheet_artifact_ids


def sync_section_meetings(engine) -> list[Dict]:
    course_info = get_course_info(engine)
    year = int(course_info.get('year') or datetime.now().year)
    existing = {
        (row['section_number'], row['meeting_date'], row['start_time']): row
        for row in get_section_meetings(engine)
    }
    synchronized = []
    for section in get_all_sections(engine):
        for month, day in section.get('meeting_dates', []):
            meeting_date = f'{year}-{int(month):02d}-{int(day):02d}'
            key = (section['section_number'], meeting_date, section['start_time'])
            row = existing.get(key)
            meeting_id = save_section_meeting(engine, {
                'meeting_id': row['meeting_id'] if row else None,
                'section_number': section['section_number'],
                'meeting_date': meeting_date,
                'start_time': section['start_time'],
                'end_time': section.get('end_time', ''),
                'meeting_sequence': row['meeting_sequence'] if row else None,
            })
            synchronized.append(next(
                item for item in get_section_meetings(engine, section['section_number'])
                if item['meeting_id'] == meeting_id
            ))
    return synchronized


def workspace_root(course_folder: str) -> Path:
    if not course_folder:
        raise ValueError('Course folder must be configured before creating section workspaces')
    return Path(course_folder).expanduser().resolve() / 'sections'


def meeting_workspace(course_folder: str, meeting: Dict) -> Path:
    stamp = f"{meeting['meeting_date']}_{meeting['start_time'].replace(':', '')}"
    return workspace_root(course_folder) / f"section_{int(meeting['section_number']):02d}" / stamp


def package_workspace(course_folder: str, meeting: Dict, worksheet_id: str) -> Path:
    return meeting_workspace(course_folder, meeting) / 'packages' / worksheet_id


def _write_json(path: Path, data: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')


def _grade_by_student(engine, meeting_id: int) -> Dict[int, Dict]:
    return {row['student_id']: row for row in get_section_meeting_grades(engine, meeting_id)}


def prepare_meeting_workspace(engine, course_folder: str, meeting: Dict) -> Dict:
    root = meeting_workspace(course_folder, meeting)
    for directory in ('packages', 'instructor/worksheets', 'instructor/answer_keys', 'submissions', 'grading', 'logs'):
        (root / directory).mkdir(parents=True, exist_ok=True)

    students = get_students_for_section(engine, meeting['section_number'])
    grades = _grade_by_student(engine, meeting['meeting_id'])
    roster_rows = []
    packages = []
    for student in students:
        grade = grades.get(student.student_id)
        worksheet_id = grade['worksheet_id'] if grade and grade['worksheet_id'] else format_worksheet_id(
            student.student_code, meeting['meeting_sequence']
        )
        save_section_meeting_grade(engine, {
            'section_meeting_id': meeting['meeting_id'],
            'student_id': student.student_id,
            'worksheet_id': worksheet_id,
        })
        artifacts = worksheet_artifact_ids(worksheet_id)
        package_dir = package_workspace(course_folder, meeting, worksheet_id)
        package_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            'worksheet_id': worksheet_id,
            'section_meeting_id': meeting['meeting_id'],
            'meeting_date': meeting['meeting_date'],
            'section_number': meeting['section_number'],
            'student_id': student.student_id,
            'student_code': student.student_code,
            'student_name': student.name,
            'artifacts': {
                'worksheet_odt': f"{artifacts['WS']}.odt",
                'worksheet_pdf': f"{artifacts['WS']}.pdf",
                'answer_key_odt': f"{artifacts['WA']}.odt",
                'answer_key_pdf': f"{artifacts['WA']}.pdf",
            },
        }
        _write_json(package_dir / 'manifest.json', manifest)
        roster_rows.append({
            'student_id': student.student_id,
            'student_code': student.student_code,
            'student_name': student.name,
            'worksheet_id': worksheet_id,
        })
        packages.append(manifest)

    _write_csv(root / 'roster_snapshot.csv', roster_rows)
    _write_csv(root / 'instructor' / 'print_order.csv', roster_rows)
    _write_json(root / 'meeting_manifest.json', {
        'meeting': meeting,
        'prepared_at': datetime.now().isoformat(timespec='seconds'),
        'roster_count': len(roster_rows),
        'packages': [item['worksheet_id'] for item in packages],
    })
    export_meeting_grades(engine, course_folder, meeting)
    return {'workspace': str(root), 'packages': packages}


def generate_quantitative_worksheets(
    engine,
    course_folder: str,
    meeting: Dict,
    definition,
    template_path: str,
    metadata: Dict,
    answer_key_template_path: Optional[str] = None,
    plot_config: Optional[Dict] = None,
    mode: str = 'random',
    base_seed: Optional[int] = None,
) -> list[str]:
    from OneUn import OneUnODTGenerator

    prepared = prepare_meeting_workspace(engine, course_folder, meeting)
    generated = []
    generator = OneUnODTGenerator()
    for package in prepared['packages']:
        worksheet_id = package['worksheet_id']
        student_code = package['student_code']
        package_dir = package_workspace(course_folder, meeting, worksheet_id)
        artifacts = worksheet_artifact_ids(worksheet_id)
        artifact = artifacts['WS']
        files = generator.generate_quiz(
            definition=definition,
            template_path=template_path,
            output_path=str(package_dir / 'worksheet.odt'),
            student_codes=[student_code],
            quiz_metadata=metadata,
            plot_config=plot_config,
            mode=mode,
            output_ids={student_code: artifact},
            answer_key_template_path=answer_key_template_path,
            answer_key_output_ids={student_code: artifacts['WA']},
            base_seed=base_seed,
        )
        generated.extend(files)
        source = Path(files[0])
        link = meeting_workspace(course_folder, meeting) / 'instructor' / 'worksheets' / source.name
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(source)
        if answer_key_template_path:
            answer_key = package_dir / f"{artifacts['WA']}.odt"
            key_link = meeting_workspace(course_folder, meeting) / 'instructor' / 'answer_keys' / answer_key.name
            if key_link.exists() or key_link.is_symlink():
                key_link.unlink()
            key_link.symlink_to(answer_key)
    return generated


def attach_submission(engine, course_folder: str, meeting: Dict, student_id: int, source_path: str) -> str:
    grades = _grade_by_student(engine, meeting['meeting_id'])
    grade = grades.get(student_id)
    if grade is None or not grade['worksheet_id']:
        raise ValueError('Prepare the meeting workspace before attaching a submission')
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f'Submission file not found: {source}')
    destination_dir = meeting_workspace(course_folder, meeting) / 'submissions' / grade['worksheet_id']
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / source.name
    if source != destination:
        destination.write_bytes(source.read_bytes())
    relative_path = str(destination.relative_to(meeting_workspace(course_folder, meeting)))
    save_section_meeting_grade(engine, {
        'section_meeting_id': meeting['meeting_id'],
        'student_id': student_id,
        'submission_status': 'received',
        'submitted_work_path': relative_path,
    })
    return relative_path


def export_meeting_grades(engine, course_folder: str, meeting: Dict) -> str:
    grades = _grade_by_student(engine, meeting['meeting_id'])
    students = get_students_for_section(engine, meeting['section_number'])
    rows = []
    for student in students:
        grade = grades.get(student.student_id, {})
        rows.append({
            'student_code': student.student_code,
            'student_name': student.name,
            'worksheet_id': grade.get('worksheet_id', ''),
            'score': '' if grade.get('score') is None else grade['score'],
            'attendance_status': grade.get('attendance_status', ''),
            'submission_status': grade.get('submission_status', ''),
            'grader': grade.get('grader', ''),
            'note': grade.get('note', ''),
            'submitted_work_path': grade.get('submitted_work_path', ''),
        })
    destination = meeting_workspace(course_folder, meeting) / 'grading' / 'grade_export.csv'
    _write_csv(destination, rows)
    return str(destination)


def _write_csv(path: Path, rows: Iterable[Dict]) -> None:
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0]) if rows else ['student_id', 'student_code', 'student_name', 'worksheet_id']
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
