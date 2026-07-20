import re
from dataclasses import dataclass
from typing import Literal

DocumentKind = Literal['quiz', 'worksheet']

_QUIZ_ID_RE = re.compile(r'^(?P<student_code>[A-Za-z0-9]+)(?P<module_number>\d{2})_(?P<sequence>\d{4})$')
_WORKSHEET_ID_RE = re.compile(r'^(?P<student_code>[A-Za-z0-9]+)WS_(?P<sequence>\d{4})$')


@dataclass(frozen=True)
class DocumentId:
    kind: DocumentKind
    student_code: str
    sequence: int
    module_number: int | None = None

    @property
    def base_id(self) -> str:
        if self.kind == 'quiz':
            return format_quiz_id(self.student_code, self.module_number, self.sequence)
        return format_worksheet_id(self.student_code, self.sequence)


def _validate_student_code(student_code: str) -> str:
    normalized = student_code.strip()
    if not normalized or not normalized.isalnum():
        raise ValueError('student_code must contain only letters and digits')
    return normalized


def _validate_sequence(sequence: int) -> int:
    if not isinstance(sequence, int) or not 1 <= sequence <= 9999:
        raise ValueError('sequence must be an integer from 1 through 9999')
    return sequence


def format_quiz_id(student_code: str, module_number: int, attempt: int) -> str:
    code = _validate_student_code(student_code)
    if not isinstance(module_number, int) or not 0 <= module_number <= 99:
        raise ValueError('module_number must be an integer from 0 through 99')
    return f'{code}{module_number:02d}_{_validate_sequence(attempt):04d}'


def format_worksheet_id(student_code: str, meeting_sequence: int) -> str:
    return f'{_validate_student_code(student_code)}WS_{_validate_sequence(meeting_sequence):04d}'


def parse_document_id(document_id: str) -> DocumentId:
    value = document_id.strip()
    worksheet_match = _WORKSHEET_ID_RE.fullmatch(value)
    if worksheet_match:
        return DocumentId(
            kind='worksheet',
            student_code=worksheet_match.group('student_code'),
            sequence=int(worksheet_match.group('sequence')),
        )
    quiz_match = _QUIZ_ID_RE.fullmatch(value)
    if quiz_match:
        return DocumentId(
            kind='quiz',
            student_code=quiz_match.group('student_code'),
            module_number=int(quiz_match.group('module_number')),
            sequence=int(quiz_match.group('sequence')),
        )
    raise ValueError(f'Invalid document ID: {document_id!r}')


def artifact_id(base_id: str, artifact: str) -> str:
    document = parse_document_id(base_id)
    allowed = {'quiz': {'Q', 'A', 'F'}, 'worksheet': {'WS', 'WA'}}
    if artifact not in allowed[document.kind]:
        raise ValueError(f'Unsupported {document.kind} artifact: {artifact}')
    return f'{document.base_id}{artifact}'


def quiz_artifact_ids(base_id: str) -> dict[str, str]:
    document = parse_document_id(base_id)
    if document.kind != 'quiz':
        raise ValueError('A quiz base ID is required')
    return {artifact: artifact_id(base_id, artifact) for artifact in ('Q', 'A', 'F')}


def worksheet_artifact_ids(base_id: str) -> dict[str, str]:
    document = parse_document_id(base_id)
    if document.kind != 'worksheet':
        raise ValueError('A worksheet base ID is required')
    return {artifact: artifact_id(base_id, artifact) for artifact in ('WS', 'WA')}
