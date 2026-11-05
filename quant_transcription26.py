"""Handwriting transcription of extracted Quant pages using a vision LLM.

CURRENTLY DISABLED in the grading pipeline (the hook in
grading26.record_results is commented out); kept for possible later use.  The
command-line entry point below still works on demand once a key is configured.

Each Quant page is rendered to PNG and sent to the configured provider
(Anthropic, OpenAI or Google Gemini).  The result is one Markdown file per
grading session, `<grading_dir>/extracted_quant/quant_transcription.md`, with
one section per quiz and one sub-section per page.

API keys and the provider/model choice come from `~/.mcq26/llm_config.json`
(kept outside the repository), falling back to the ANTHROPIC_API_KEY /
OPENAI_API_KEY / GEMINI_API_KEY environment variables:

    {
      "transcription_provider": "anthropic",
      "transcription_models": {"anthropic": "...", "openai": "...", "gemini": "..."},
      "api_keys": {"anthropic": "...", "openai": "...", "gemini": "..."}
    }

Command line (re-transcribe an existing grading session):

    python quant_transcription26.py <grading_dir> [--provider P] [--model M] [--out FILE]
"""
import argparse
import base64
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / '.mcq26' / 'llm_config.json'
PROVIDERS = ('anthropic', 'openai', 'gemini')
KEY_ENV_VARS = {
    'anthropic': 'ANTHROPIC_API_KEY',
    'openai': 'OPENAI_API_KEY',
    'gemini': 'GEMINI_API_KEY',
}
DEFAULT_PROVIDER = 'anthropic'
DEFAULT_MODELS = {
    'anthropic': 'claude-sonnet-4-5',
    'openai': 'gpt-5',
    'gemini': 'gemini-2.5-pro',
}
OUTPUT_NAME = 'quant_transcription.md'
RENDER_DPI = 150
MAX_WORKERS = 4
REQUEST_TIMEOUT = 300

PROMPT = """This image is one scanned page of a student's neuroscience quiz (the quantitative, written-answer section).
Transcribe ONLY the handwriting, verbatim. Keep the student's spelling, grammar, arithmetic and mistakes exactly as written; never correct, complete or improve an answer, even if it is obviously wrong.
- Start each answer with the printed question/part label it belongs to (e.g. "1a:", "2c:", "b:"). Do not copy the printed question text.
- Plain text notation: subscripts as E_K or g_Na, superscripts/charges as Mg2+ or e^(-x/λ), fractions as a/b.
- Put [?] directly after any word, number or symbol you are not sure of, e.g. "37.6[?] mV".
- Crossed-out text: [x: text], or [x] if unreadable. Text inserted above a line: [^ text].
- Drawings or graphs: describe briefly in [brackets] (axis labels, shape) without inventing values.
- Handwritten signature, name or date: "Signature: ...", "Name: ...", "Date: ...".
- A part with no handwriting: "<label>: [blank]". If the page has no handwriting at all, reply exactly: [no handwriting]
Output only the transcription, one answer per line."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def load_config() -> Dict:
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning(f'Could not read {CONFIG_PATH}: {e}')
        return {}


def resolve_backend(provider: Optional[str] = None, model: Optional[str] = None
                    ) -> Tuple[str, str, Optional[str]]:
    """Return (provider, model, api_key); api_key is None when not configured."""
    config = load_config()
    provider = (provider or config.get('transcription_provider') or DEFAULT_PROVIDER).lower()
    if provider not in PROVIDERS:
        raise ValueError(f'Unknown transcription provider {provider!r}; use one of {PROVIDERS}')
    model = model or (config.get('transcription_models') or {}).get(provider) or DEFAULT_MODELS[provider]
    api_key = (config.get('api_keys') or {}).get(provider) or os.environ.get(KEY_ENV_VARS[provider])
    return provider, model, api_key


# ---------------------------------------------------------------------------
# Provider calls
# ---------------------------------------------------------------------------
def _call_anthropic(png_b64: str, model: str, api_key: str) -> str:
    response = requests.post(
        'https://api.anthropic.com/v1/messages',
        headers={'x-api-key': api_key, 'anthropic-version': '2023-06-01',
                 'content-type': 'application/json'},
        json={
            'model': model,
            'max_tokens': 4096,
            'temperature': 0,
            'messages': [{'role': 'user', 'content': [
                {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png',
                                             'data': png_b64}},
                {'type': 'text', 'text': PROMPT},
            ]}],
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return ''.join(block.get('text', '') for block in response.json().get('content', [])
                   if block.get('type') == 'text')


def _call_openai(png_b64: str, model: str, api_key: str) -> str:
    response = requests.post(
        'https://api.openai.com/v1/chat/completions',
        headers={'Authorization': f'Bearer {api_key}', 'content-type': 'application/json'},
        json={
            'model': model,
            'max_completion_tokens': 16000,
            'messages': [{'role': 'user', 'content': [
                {'type': 'text', 'text': PROMPT},
                {'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{png_b64}',
                                                    'detail': 'high'}},
            ]}],
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()['choices'][0]['message'].get('content') or ''


def _call_gemini(png_b64: str, model: str, api_key: str) -> str:
    response = requests.post(
        f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
        headers={'x-goog-api-key': api_key, 'content-type': 'application/json'},
        json={
            'contents': [{'parts': [
                {'inline_data': {'mime_type': 'image/png', 'data': png_b64}},
                {'text': PROMPT},
            ]}],
            'generationConfig': {'temperature': 0, 'maxOutputTokens': 16000},
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    candidates = response.json().get('candidates') or []
    if not candidates:
        return ''
    parts = (candidates[0].get('content') or {}).get('parts') or []
    return ''.join(p.get('text', '') for p in parts if not p.get('thought'))


_CALLERS = {'anthropic': _call_anthropic, 'openai': _call_openai, 'gemini': _call_gemini}


def transcribe_image(png_bytes: bytes, provider: str, model: str, api_key: str,
                     retries: int = 3) -> str:
    """Transcribe the handwriting on one page image."""
    png_b64 = base64.b64encode(png_bytes).decode('ascii')
    for attempt in range(retries):
        try:
            return _CALLERS[provider](png_b64, model, api_key).strip()
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status in (429, 500, 502, 503, 504, 529) and attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            body = e.response.text[:300] if e.response is not None else ''
            raise RuntimeError(f'{provider} HTTP {status}: {body}') from e
        except requests.RequestException:
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            raise
    return ''


# ---------------------------------------------------------------------------
# Which pages belong to which quiz
# ---------------------------------------------------------------------------
@dataclass
class QuizQuantPages:
    quiz_id: str
    scan_path: str
    page_indices: List[int]
    student_name: str = ''
    student_code: str = ''
    section_number: Optional[int] = None
    module_number: Optional[int] = None
    printed_name: str = ''
    pages: List[Tuple[int, Optional[bytes]]] = field(default_factory=list)


def _sort_key(q: QuizQuantPages):
    parts = (q.student_name or '').lower().split()
    return (parts[-1] if parts else '', parts[0] if parts else '', q.quiz_id)


def quizzes_from_results(results, scan_path: str) -> List[QuizQuantPages]:
    """Quant pages for the resolved GradingResults of a grading run."""
    quizzes = []
    for r in results:
        if r.held_up or not r.quant_scan_pages:
            continue
        quizzes.append(QuizQuantPages(
            quiz_id=r.quiz_id, scan_path=str(scan_path), page_indices=list(r.quant_scan_pages),
            student_name=r.student_name or '', student_code=r.student_code or '',
            section_number=r.section_number, module_number=r.module_number,
            printed_name=r.original_student_name or '',
        ))
    return sorted(quizzes, key=_sort_key)


def quizzes_from_session(engine, grading_dir: str) -> List[QuizQuantPages]:
    """Quant pages for an already-graded session, from its saved artifacts."""
    from database26 import get_student_by_code
    from grading26 import _parsed_quizzes_dir

    quizzes = []
    for path in sorted(_parsed_quizzes_dir(grading_dir).glob('*_results.json')):
        with open(path) as f:
            artifact = json.load(f)
        if artifact.get('held_up') or not artifact.get('quant_scan_pages'):
            continue
        code = artifact.get('student_code') or ''
        student = get_student_by_code(engine, code) if code else None
        scan_path = artifact.get('scan_file') or str(Path(grading_dir) / 'combined_scan.pdf')
        quizzes.append(QuizQuantPages(
            quiz_id=artifact['quiz_id'], scan_path=scan_path,
            page_indices=list(artifact['quant_scan_pages']),
            student_name=student.name if student else '', student_code=code,
            section_number=artifact.get('section_number')
            if artifact.get('section_number') is not None
            else (student.section_number if student else None),
            module_number=artifact.get('module_number'),
            printed_name=_printed_name(engine, artifact['quiz_id']),
        ))
    return sorted(quizzes, key=_sort_key)


def _printed_name(engine, quiz_id: str) -> str:
    """Name of the student the quiz was printed for (from the quiz_id prefix)."""
    from database26 import get_student_by_code
    match = re.match(r'([A-Za-z]+)\d', quiz_id or '')
    student = get_student_by_code(engine, match.group(1)) if match else None
    return student.name if student else ''


def _render_pages(quizzes: List[QuizQuantPages], skip_blank: bool = True) -> None:
    """Fill quiz.pages with (scan_index, png_bytes or None for a blank page)."""
    import fitz
    import numpy as np
    from PIL import Image
    from grading26 import _is_blank_page

    docs = {}
    try:
        for quiz in quizzes:
            doc = docs.get(quiz.scan_path)
            if doc is None:
                doc = docs[quiz.scan_path] = fitz.open(quiz.scan_path)
            quiz.pages = []
            for idx in quiz.page_indices:
                if idx < 0 or idx >= len(doc):
                    continue
                pix = doc[idx].get_pixmap(dpi=RENDER_DPI)
                if skip_blank:
                    image = np.array(Image.frombytes('RGB', [pix.width, pix.height], pix.samples))
                    if _is_blank_page(image):
                        quiz.pages.append((idx, None))
                        continue
                quiz.pages.append((idx, pix.tobytes('png')))
    finally:
        for doc in docs.values():
            doc.close()


# ---------------------------------------------------------------------------
# Main entry points
# ---------------------------------------------------------------------------
def transcribe_quizzes(quizzes: List[QuizQuantPages], out_path, title: str = '',
                       provider: Optional[str] = None, model: Optional[str] = None,
                       max_workers: int = MAX_WORKERS) -> Optional[Path]:
    """Transcribe every Quant page and write the per-quiz Markdown file."""
    if not quizzes:
        return None
    provider, model, api_key = resolve_backend(provider, model)
    if not api_key:
        logger.warning(f'Quant transcription skipped: no {provider} API key in '
                       f'{CONFIG_PATH} or ${KEY_ENV_VARS[provider]}')
        return None

    _render_pages(quizzes)
    jobs = [(qi, pi, png) for qi, quiz in enumerate(quizzes)
            for pi, (_, png) in enumerate(quiz.pages) if png is not None]

    def run(job):
        _, _, png = job
        try:
            return transcribe_image(png, provider, model, api_key) or '[empty response]'
        except Exception as e:
            logger.warning(f'Transcription failed: {e}')
            return f'[transcription failed: {e}]'

    started = time.time()
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        texts = list(pool.map(run, jobs))
    by_page = {(qi, pi): text for (qi, pi, _), text in zip(jobs, texts)}

    lines = [
        f'# Quant transcription — {title}' if title else '# Quant transcription',
        '',
        f'Generated {datetime.now():%Y-%m-%d %H:%M} by {provider} `{model}` '
        f'in {time.time() - started:.0f}s. Machine transcription of handwriting: '
        'verify before relying on it for grading.',
        '',
        'Legend: `[?]` uncertain reading, `[x: …]` crossed out, `[^ …]` inserted above the line.',
        '',
    ]
    for qi, quiz in enumerate(quizzes):
        header = f'## {quiz.quiz_id} — {quiz.student_name or "(unknown student)"}'
        details = [f'{quiz.student_code}' if quiz.student_code else '',
                   f'Section {quiz.section_number}' if quiz.section_number is not None else '',
                   f'Module {quiz.module_number}' if quiz.module_number is not None else '']
        if quiz.printed_name and quiz.printed_name != quiz.student_name:
            details.append(f'printed for {quiz.printed_name}')
        details = ', '.join(d for d in details if d)
        lines += [header + (f' ({details})' if details else ''), '']
        for pi, (scan_idx, png) in enumerate(quiz.pages):
            lines.append(f'### Quant page {pi + 1} (scan page {scan_idx + 1})')
            lines.append('')
            lines.append('[blank page]' if png is None else by_page.get((qi, pi), ''))
            lines.append('')

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text('\n'.join(lines).rstrip() + '\n')
    logger.info(f'Quant transcription written to {out_path}')
    return out_path


def transcribe_grading_results(results, scan_path: str, grading_dir: str, **kwargs
                               ) -> Optional[Path]:
    """Pipeline hook: transcribe the Quant pages of a grading run."""
    quizzes = quizzes_from_results(results, scan_path)
    out = Path(grading_dir) / 'extracted_quant' / OUTPUT_NAME
    return transcribe_quizzes(quizzes, out, title=Path(grading_dir).name, **kwargs)


def transcribe_grading_session(engine, grading_dir: str, out_path=None, **kwargs
                               ) -> Optional[Path]:
    """Re-transcribe an already-graded session from its saved artifacts."""
    quizzes = quizzes_from_session(engine, grading_dir)
    out = out_path or Path(grading_dir) / 'extracted_quant' / OUTPUT_NAME
    return transcribe_quizzes(quizzes, out, title=Path(grading_dir).name, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('grading_dir')
    parser.add_argument('--provider', choices=PROVIDERS)
    parser.add_argument('--model')
    parser.add_argument('--out')
    parser.add_argument('--db', help='database path (default: MCQ26 default)')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')

    from database26 import create_db_engine
    engine = create_db_engine(args.db) if args.db else create_db_engine()
    path = transcribe_grading_session(engine, args.grading_dir, out_path=args.out,
                                      provider=args.provider, model=args.model)
    print(path or 'No transcription written (no Quant pages or no API key).')


if __name__ == '__main__':
    main()
