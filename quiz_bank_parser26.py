"""
Parser for the MCQ26 integrated question bank format.

An integrated bank file starts with a JSON header (commented with `#` or plain)
and then contains blocks like:

    RST0_1.
    Question: What type of molecules ...?

    A. Lipid-soluble molecules
    B. Inorganic ions and water-soluble molecules
    C. Nonpolar molecules
    D. Hydrophobic molecules
    E. Gases like oxygen and carbon dioxide
    Answer: B. Inorganic ions and water-soluble molecules
    Feedback: Section 2.4
    Context: The lipid bilayer of the plasma membrane...

Each block is parsed into a dict with:
    id, stem, choices, correct_idx, feedback, context
"""
import json
import os
import re
from typing import List, Dict, Optional


def _load_header(raw_lines: List[str]) -> Dict:
    """Try to read the JSON header at the top of a bank file."""
    header_lines = []
    in_header = False
    for line in raw_lines:
        stripped = line.strip()
        if stripped.startswith('# {'):
            in_header = True
            header_lines.append(stripped.lstrip('#').strip())
        elif stripped.startswith('# }') or stripped.startswith('}'):
            if in_header:
                header_lines.append(stripped.lstrip('#').strip())
            break
        elif in_header:
            # strip leading '# ' from each header line
            if stripped.startswith('# '):
                header_lines.append(stripped[2:])
            elif stripped.startswith('#'):
                header_lines.append(stripped[1:])
            else:
                header_lines.append(stripped)
        elif stripped.startswith('{'):
            in_header = True
            header_lines.append(stripped)
        elif stripped.startswith('}'):
            break

    if not header_lines:
        return {}

    try:
        return json.loads('\n'.join(header_lines))
    except json.JSONDecodeError as e:
        print(f"[WARNING] Failed to parse JSON header: {e}")
        return {}


def _strip_label(line: str) -> str:
    """Remove an answer/feedback/context prefix from a line."""
    prefixes = [
        r'^Answer:\s*',
        r'^Feedback:\s*',
        r'^Context:\s*',
    ]
    for pat in prefixes:
        line = re.sub(pat, '', line, count=1, flags=re.IGNORECASE)
    return line


def load_integrated_bank(path: str) -> List[Dict]:
    """Parse an integrated question bank and return a list of question dicts.

    Each dict contains:
        - id:        question id (e.g. "RST0_1")
        - stem:      the question stem text
        - choices:   list of 5 answer choice strings
        - correct_idx: int index 0..4 of the correct choice
        - feedback:  feedback string (may be empty)
        - context:   context string (may be empty)
        - header:    the parsed JSON header dict
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Question bank not found: {path}")

    with open(path, 'r', encoding='utf-8') as f:
        raw_lines = f.readlines()

    header = _load_header(raw_lines)

    questions: List[Dict] = []
    current: Optional[Dict] = None

    # Skip the header in the main scan: start after the closing brace
    start_idx = 0
    for i, line in enumerate(raw_lines):
        if line.strip().startswith('}') or line.strip().startswith('# }'):
            start_idx = i + 1
            break

    for line in raw_lines[start_idx:]:
        stripped = line.strip()
        if not stripped:
            # Empty line ends a question block
            if current is not None and current.get('choices'):
                questions.append(current)
                current = None
            continue

        # Question id line: e.g. "RST0_1."
        m_id = re.match(r'^([A-Za-z0-9_]+)\.$', stripped)
        if m_id:
            if current is not None and current.get('choices'):
                questions.append(current)
            current = {
                'id': m_id.group(1),
                'stem': '',
                'choices': [],
                'correct_idx': None,
                'feedback': '',
                'context': '',
                'overlap': [],
                'header': header,
            }
            continue

        if current is None:
            continue

        # Question stem
        if stripped.lower().startswith('question:'):
            current['stem'] = re.sub(r'^Question:\s*', '', stripped, flags=re.IGNORECASE).lstrip(': ').strip()
            continue

        # Answer choices A-E
        m_choice = re.match(r'^([A-E])\.\s*(.*)$', stripped)
        if m_choice:
            letter, text = m_choice.groups()
            idx = ord(letter.upper()) - ord('A')
            # Ensure choices list is long enough
            while len(current['choices']) <= idx:
                current['choices'].append('')
            current['choices'][idx] = text
            continue

        # Answer line: "Answer: B. ..."
        m_answer = re.match(r'^Answer:\s*([A-E])\.\s*(.*)$', stripped, re.IGNORECASE)
        if m_answer:
            letter = m_answer.group(1).upper()
            current['correct_idx'] = ord(letter) - ord('A')
            # If answer text includes a choice not in choices, add it as a fallback
            text = m_answer.group(2).strip()
            idx = current['correct_idx']
            if idx is not None and len(current['choices']) <= idx:
                while len(current['choices']) <= idx:
                    current['choices'].append('')
                current['choices'][idx] = text
            continue

        # Feedback line
        if stripped.lower().startswith('feedback:'):
            current['feedback'] = re.sub(r'^Feedback:\s*', '', stripped, flags=re.IGNORECASE)
            continue

        # Context line (may be multi-line; for now take the first line)
        if stripped.lower().startswith('context:'):
            current['context'] = re.sub(r'^Context:\s*', '', stripped, flags=re.IGNORECASE)
            continue

        # Overlap line: comma-separated IDs of related questions
        if stripped.lower().startswith('overlap:'):
            overlap_text = re.sub(r'^Overlap:\s*', '', stripped, flags=re.IGNORECASE)
            current['overlap'] = [oid.strip() for oid in overlap_text.split(',') if oid.strip()]
            continue

        # If we are in context/feedback and the line does not start a new field,
        # append it to the current context (simple continuation)
        if current['context']:
            current['context'] += ' ' + stripped

    # Add the last question if the file does not end with a blank line
    if current is not None and current.get('choices'):
        questions.append(current)

    # Validate: require correct_idx and a full set of 5 choices
    valid = []
    for q in questions:
        if q['correct_idx'] is None:
            print(f"[WARNING] Question {q.get('id')} has no correct answer; skipping")
            continue
        if len(q['choices']) != 5:
            print(f"[WARNING] Question {q.get('id')} has {len(q['choices'])} choices instead of 5; skipping")
            continue
        if not all(q['choices']):
            print(f"[WARNING] Question {q.get('id')} has empty choices; skipping")
            continue
        valid.append(q)

    return valid


def load_question_banks(bank_paths: List[str]) -> Dict[str, List[Dict]]:
    """Load multiple integrated banks into a dict mapping path -> questions."""
    result = {}
    for path in bank_paths:
        if not os.path.exists(path):
            raise FileNotFoundError(f"Question bank not found: {path}")
        result[path] = load_integrated_bank(path)
    return result
