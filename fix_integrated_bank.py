"""
Fix an integrated question-bank file by replacing Answer/Feedback/Context
entries with the latest-dated component files from a source QBanks directory.

Example:
    python fix_integrated_bank.py \
        --integrated /Users/sacha/textProcessing/NBIO140_2026/module7/COL/M7_COL2_INT.txt \
        --source-topic-dir /Users/sacha/textProcessing/NBIO 140B/module9/COL \
        --in-place
"""
import argparse
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import integrate_Qbanks as iq


def _parse_date(date_str: str) -> Optional[datetime]:
    """Parse date suffix like 'Aug0725' or 'Sep1725'."""
    try:
        return datetime.strptime(date_str, '%b%d%y')
    except ValueError:
        return None


def _find_latest_component_file(
    component_dir: Path,
    topic: str,
    bank: str,
    letter: str,
) -> Optional[Path]:
    """Find the latest-dated component file for a given topic/bank/letter."""
    candidates: List[Tuple[datetime, Path]] = []
    topic_lower = topic.lower()
    for f in component_dir.iterdir():
        if not f.is_file() or f.name.startswith('Overlap_'):
            continue
        m = iq._FILE_RE.match(f.name)
        if not m:
            continue
        if m.group('letter').upper() != letter.upper():
            continue
        if m.group('bank') != bank:
            continue
        prefix = m.group('prefix')
        if not prefix.lower().endswith(topic_lower):
            continue
        d = _parse_date(m.group('date'))
        if d:
            candidates.append((d, f))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _split_header_body(text: str) -> Tuple[Optional[Dict], str]:
    """Parse the JSON header (source or integrated format) and return body text."""
    lines = text.splitlines()
    body_start = 0
    in_header = False
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith('# {'):
            in_header = True
        elif in_header and (s == '}' or s.startswith('# }')):
            body_start = i + 1
            break
    header_lines = lines[:body_start]
    json_lines = []
    for line in header_lines:
        s = line.strip()
        if s.startswith('# '):
            json_lines.append(s[2:])
        elif s.startswith('#'):
            json_lines.append(s[1:])
        else:
            json_lines.append(s)
    header_dict = None
    if json_lines:
        try:
            header_dict = json.loads('\n'.join(json_lines))
        except json.JSONDecodeError as e:
            print(f'[WARNING] Could not parse header: {e}')
    body = '\n'.join(lines[body_start:]).strip()
    return header_dict, body


def _extract_topic_and_bank(integrated_path: Path) -> Tuple[str, str]:
    """Read the first question ID from the integrated file."""
    text = integrated_path.read_text(encoding='utf-8')
    _, body = _split_header_body(text)
    entries = iq._parse_entries(body)
    if not entries:
        raise ValueError(f'No questions found in {integrated_path}')
    first_id = next(iter(entries))
    m = re.match(r'^([A-Za-z]+)(\d+)_(\d+)$', first_id)
    if not m:
        raise ValueError(f'Unexpected question ID format: {first_id}')
    return m.group(1), m.group(2)


def _strip_answer_prefix(text: str) -> str:
    return re.sub(r'^Correct Answer:\s*', '', text, flags=re.IGNORECASE).strip()


def _strip_feedback_prefix(text: str) -> str:
    return re.sub(r'^Review:\s*', '', text, flags=re.IGNORECASE).strip()


def _load_component_entries(path: Path) -> Dict[str, str]:
    _, entries = iq._load_component(path)
    return entries


def _replace_field(block: str, label: str, new_value: str) -> str:
    """Replace the first line starting with label in block with label + new_value."""
    pattern = rf'^{re.escape(label)}.*$'
    if re.search(pattern, block, flags=re.MULTILINE):
        return re.sub(
            pattern,
            lambda m: f'{label} {new_value}',
            block,
            count=1,
            flags=re.MULTILINE,
        )
    # Label not present; insert before Feedback/Context/end.
    if label == 'Answer:':
        for marker in ['Feedback:', 'Context:']:
            if marker in block:
                return block.replace(marker, f'Answer: {new_value}\n{marker}', 1)
        return block.rstrip() + f'\nAnswer: {new_value}\n'
    if label == 'Feedback:':
        if 'Context:' in block:
            return block.replace('Context:', f'Feedback: {new_value}\nContext:', 1)
        return block.rstrip() + f'\nFeedback: {new_value}\n'
    if label == 'Context:':
        return block.rstrip() + f'\nContext: {new_value}\n'
    return block


def _sort_key(qid: str) -> Tuple[str, int]:
    m = re.search(r'(\d+)$', qid)
    return (qid, int(m.group(1)) if m else 0)


def fix_integrated_bank(
    integrated_path: Path,
    source_topic_dir: Path,
    output_path: Path,
    topic: Optional[str] = None,
    bank: Optional[str] = None,
) -> None:
    if topic is None or bank is None:
        inferred_topic, inferred_bank = _extract_topic_and_bank(integrated_path)
        topic = topic or inferred_topic
        bank = bank or inferred_bank
    qbanks_dir = iq._find_qbanks_dir(source_topic_dir)
    if qbanks_dir is None:
        raise ValueError(f'No QBanks/Question_banks dir under {source_topic_dir}')
    dirs = iq._component_dirs(qbanks_dir)
    if dirs is None:
        raise ValueError(f'No Questions dir in {qbanks_dir}')
    q_dir, a_dir, f_dir, c_dir = dirs

    text = integrated_path.read_text(encoding='utf-8')
    header_dict, body = _split_header_body(text)
    original_entries = iq._parse_entries(body)

    a_entries: Dict[str, str] = {}
    f_entries: Dict[str, str] = {}
    c_entries: Dict[str, str] = {}

    if a_dir:
        a_file = _find_latest_component_file(a_dir, topic, bank, 'A')
        if a_file:
            print(f'Using answers: {a_file}')
            a_entries = _load_component_entries(a_file)
    if f_dir:
        f_file = _find_latest_component_file(f_dir, topic, bank, 'F')
        if f_file:
            print(f'Using feedback: {f_file}')
            f_entries = _load_component_entries(f_file)
    if c_dir:
        c_file = _find_latest_component_file(c_dir, topic, bank, 'C')
        if c_file:
            print(f'Using context: {c_file}')
            c_entries = _load_component_entries(c_file)

    # Reconstruct header
    if header_dict:
        header_lines = ['# ' + json.dumps(header_dict, indent=2).replace('\n', '\n# ')]
    else:
        header_lines = []

    lines: List[str] = []
    if header_lines:
        lines.extend(header_lines)
        lines.append('')

    for qid in sorted(original_entries, key=_sort_key):
        block = original_entries[qid]
        if qid in a_entries:
            block = _replace_field(block, 'Answer:', _strip_answer_prefix(a_entries[qid]))
        if qid in f_entries:
            block = _replace_field(block, 'Feedback:', _strip_feedback_prefix(f_entries[qid]))
        if qid in c_entries:
            block = _replace_field(block, 'Context:', c_entries[qid].strip())
        lines.append(f'{qid}.')
        lines.append(block)
        lines.append('')

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text('\n'.join(lines), encoding='utf-8')


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Fix an integrated bank by pulling latest Answer/Feedback/Context components.'
    )
    parser.add_argument('--integrated', required=True, type=Path,
                        help='Path to the integrated *_INT.txt file to fix.')
    parser.add_argument('--source-topic-dir', required=True, type=Path,
                        help='Topic directory containing QBanks/Question_banks (e.g. module9/COL).')
    parser.add_argument('--source-topic', type=str,
                        help='Override the topic code to look for in the source files '
                             '(use if the topic code changed between old and new course).')
    parser.add_argument('--source-bank', type=str,
                        help='Override the bank/difficulty number to look for in the source files.')
    parser.add_argument('--output', type=Path,
                        help='Output path. Defaults to integrated path with .fixed suffix.')
    parser.add_argument('--in-place', action='store_true',
                        help='Overwrite the integrated file, keeping a .bak backup.')
    args = parser.parse_args()

    integrated_path = args.integrated
    source_topic_dir = args.source_topic_dir

    if args.in_place:
        output_path = integrated_path
        backup_path = integrated_path.with_suffix(integrated_path.suffix + '.bak')
        shutil.copy2(integrated_path, backup_path)
        print(f'Backed up original to {backup_path}')
    else:
        output_path = args.output or integrated_path.with_suffix(integrated_path.suffix + '.fixed')

    fix_integrated_bank(
        integrated_path,
        source_topic_dir,
        output_path,
        topic=args.source_topic,
        bank=args.source_bank,
    )
    print(f'Wrote fixed integrated bank to {output_path}')


if __name__ == '__main__':
    main()
