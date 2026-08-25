"""
integrate_Qbanks.py
====================
Convert existing question-bank component files (Questions / Answers / Feedback /
Context) into a single integrated file per bank set.

Source layout (LLM-generated banks, modules 1-25):
    <course_root>/module{N}/{TOPIC}/QBanks/
        Questions/  M{N}_{TOP}Q{k}_{date}.txt
        Answers/    M{N}_{TOP}A{k}_{date}.txt
        Feedback/   M{N}_{TOP}F{k}_{date}.txt
        Context/    M{N}_{TOP}C{k}_{date}.txt

Source layout (algorithmic banks, module 0):
    <course_root>/module0/{TOPIC}/Question_banks/
        Questions/      M0_{TOP}Q{k}_{date}.txt
        Answers/        M0_{TOP}A{k}_{date}.txt
        DistFeedback/   M0_{TOP}F{k}_{date}.txt
        (no Context)

Output (written alongside QBanks/ or Question_banks/):
    Integrated/  M{N}_{TOP}{k}_INT.txt

Integrated file format
----------------------
# { ... header JSON ... }

TOPIC0_1.
Question:
<full question text (multi-line)>
Answer:
<answer line, stripped of "Correct Answer: " prefix>
Feedback:
<feedback text, stripped of leading ID prefix>
Context:
<context text, stripped of leading ID prefix>

TOPIC0_2.
...

Usage
-----
# Test on a single module (prints to stdout, no files written):
    python integrate_Qbanks.py --test 6

# Process a single module (writes files):
    python integrate_Qbanks.py --module 6

# Process all modules:
    python integrate_Qbanks.py --all
"""

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

# ── Configuration ────────────────────────────────────────────────────────────

OLD_COURSE_ROOT = Path(os.path.expanduser("~/textProcessing/NBIO 140B"))
NEW_COURSE_ROOT = Path(os.path.expanduser("~/textProcessing/NBIO140_2026"))
COURSE_ROOT = OLD_COURSE_ROOT

# ── File-discovery helpers ────────────────────────────────────────────────────

def _find_qbanks_dir(topic_dir: Path):
    """Return the QBanks or Question_banks directory, or None."""
    for name in ("QBanks", "Question_banks"):
        d = topic_dir / name
        if d.is_dir():
            return d
    return None


def _component_dirs(qbanks_dir: Path):
    """Return (questions_dir, answers_dir, feedback_dir, context_dir) or None.

    Tries standard LLM layout first, then module-0 layout.
    """
    q_dir = qbanks_dir / "Questions"
    a_dir = qbanks_dir / "Answers"
    f_dir_llm = qbanks_dir / "Feedback"
    f_dir_m0  = qbanks_dir / "DistFeedback"
    c_dir = qbanks_dir / "Context"

    if not q_dir.is_dir():
        return None

    f_dir = f_dir_llm if f_dir_llm.is_dir() else (f_dir_m0 if f_dir_m0.is_dir() else None)
    return q_dir, (a_dir if a_dir.is_dir() else None), f_dir, (c_dir if c_dir.is_dir() else None)


# ── Filename stem grouping ────────────────────────────────────────────────────
# File names look like:  M6_CNSQ0_Aug0925.txt
# The "bank key" is everything before the single-letter type code (Q/A/F/C)
# and the date, i.e.  "M6_CNS" + bank_number  →  "M6_CNS0"
#
# Regex groups: prefix (e.g. M6_CNS), type_letter (Q/A/F/C), bank_num (0/2/…),
#               date string, extension.

_FILE_RE = re.compile(
    r"^(?P<prefix>[A-Z0-9]+_[A-Z]+)"   # e.g. M6_CNS
    r"(?P<letter>[QAFC])"              # type letter
    r"(?P<bank>\d+)"                   # bank number
    r"_(?P<date>[^.]+)"                # _Aug0925
    r"\.txt$",
    re.IGNORECASE,
)


def _bank_key(filename: str):
    """Return (prefix, bank_num) or None."""
    m = _FILE_RE.match(filename)
    if not m:
        return None
    return m.group("prefix"), m.group("bank")


def _discover_banks(q_dir: Path):
    """Return sorted list of (prefix, bank_num) tuples found in the Questions dir."""
    keys = set()
    for f in q_dir.iterdir():
        k = _bank_key(f.name)
        if k:
            keys.add(k)
    return sorted(keys, key=lambda x: (x[0], int(x[1])))


def _parse_date(date_str: str):
    """Parse a filename date like 'Aug0725' into a datetime, or None."""
    from datetime import datetime
    try:
        return datetime.strptime(date_str, '%b%d%y')
    except ValueError:
        return None


def _file_sort_key(path: Path):
    """Return a sort key that prefers filename dates, then file mtime."""
    m = _FILE_RE.match(path.name)
    if m:
        d = _parse_date(m.group('date'))
        if d:
            return (d, path.stat().st_mtime)
    return (None, path.stat().st_mtime)


def _find_file(directory: Path, prefix: str, letter: str, bank: str):
    """Find the most recent file matching prefix+letter+bank in directory."""
    if directory is None or not directory.is_dir():
        return None
    pattern = re.compile(
        rf"^{re.escape(prefix)}{letter}{re.escape(bank)}_.*\.txt$",
        re.IGNORECASE,
    )
    matches = [f for f in directory.iterdir() if pattern.match(f.name)]
    # Exclude Overlap_ files
    matches = [f for f in matches if not f.name.startswith("Overlap_")]
    if not matches:
        return None
    # Sort by filename date (newest first), with mtime as fallback
    matches.sort(key=_file_sort_key, reverse=True)
    return matches[0]


# ── Parsing helpers ───────────────────────────────────────────────────────────

def _parse_header(text: str):
    """Extract the JSON header block and return (header_dict, body_text).

    Handles both source-style headers (only the first line '# {') and
    integrated-style headers (every line prefixed with '# ').
    """
    lines = text.splitlines()
    if not lines or not lines[0].strip().startswith("# {"):
        return None, text.strip()

    # Collect JSON lines, stripping a leading '# ' prefix if present.
    json_lines = [lines[0].strip()[2:]]  # '# {' → '{'
    body_start = 1
    for i, line in enumerate(lines[1:], 1):
        s = line.strip()
        if s.startswith('# '):
            json_lines.append(s[2:])
        else:
            json_lines.append(s)
        body_start = i + 1
        if s == "}" or s.startswith("# }"):
            break

    header_dict = None
    try:
        header_dict = json.loads("\n".join(json_lines))
    except json.JSONDecodeError:
        pass

    body = "\n".join(lines[body_start:]).strip()
    return header_dict, body


# ID prefix pattern at the start of a line:  "ACT0_1. " or "CNS0_1. "
_ID_PREFIX_RE = re.compile(r"^[A-Za-z]+\d+_\d+\.\s*")


def _parse_entries(body: str):
    """Split body into dict {question_id: text_block}.

    The text block is the content *after* the ID prefix, possibly multi-line.
    """
    entries = {}
    current_id = None
    current_lines = []

    for line in body.splitlines():
        m = _ID_PREFIX_RE.match(line)
        if m:
            if current_id is not None:
                entries[current_id] = "\n".join(current_lines).strip()
            current_id = line[:m.end()].rstrip(". ").strip()
            current_lines = [line[m.end():]]
        else:
            if current_id is not None:
                current_lines.append(line)

    if current_id is not None:
        entries[current_id] = "\n".join(current_lines).strip()

    return entries


def _load_component(path):
    """Return (header_dict, entries_dict) from a component file, or (None, {})."""
    if path is None or not path.is_file():
        return None, {}
    text = path.read_text(encoding="utf-8")
    header, body = _parse_header(text)
    entries = _parse_entries(body)
    return header, entries


# ── Integration ───────────────────────────────────────────────────────────────

def integrate_bank(q_dir, a_dir, f_dir, c_dir, prefix, bank_num, *, test=False):
    """Build the integrated text for one bank set.

    Returns the integrated string, or None on error.
    """
    q_file = _find_file(q_dir, prefix, "Q", bank_num)
    a_file = _find_file(a_dir, prefix, "A", bank_num) if a_dir else None
    f_file = _find_file(f_dir, prefix, "F", bank_num) if f_dir else None
    c_file = _find_file(c_dir, prefix, "C", bank_num) if c_dir else None

    if q_file is None:
        print(f"  [SKIP] No Questions file for {prefix}{bank_num}", file=sys.stderr)
        return None

    hdr_q, q_entries = _load_component(q_file)
    _,      a_entries = _load_component(a_file)
    _,      f_entries = _load_component(f_file)
    _,      c_entries = _load_component(c_file)

    # Warn if component entry counts do not match
    counts = {
        'Questions': (q_file, len(q_entries)),
        'Answers': (a_file, len(a_entries)),
        'Feedback': (f_file, len(f_entries)),
        'Context': (c_file, len(c_entries)),
    }
    present = [(name, path, n) for name, (path, n) in counts.items() if path is not None]
    if present:
        nums = [n for _, _, n in present]
        if max(nums) != min(nums):
            print(
                f"  [WARNING] Component entry counts do not match for {prefix}{bank_num}:",
                file=sys.stderr,
            )
            for name, path, n in present:
                print(f"    {name:10s} {path.name}: {n} entries", file=sys.stderr)

    if not q_entries:
        # Check whether the file has content but uses a different ID scheme
        # (e.g. module-0 algorithmic banks like "FVC011265c0000 …")
        raw_body = q_file.read_text(encoding="utf-8").split("\n")
        non_blank = [l for l in raw_body if l.strip() and not l.startswith("#")]
        if non_blank:
            print(f"  [SKIP] Unrecognised ID format (algorithmic bank?): {q_file.name}",
                  file=sys.stderr)
        else:
            print(f"  [SKIP] Empty Questions file: {q_file}", file=sys.stderr)
        return None

    # Sort question IDs numerically by the trailing number
    def _sort_key(qid):
        m = re.search(r"_(\d+)$", qid)
        return int(m.group(1)) if m else 0

    sorted_ids = sorted(q_entries.keys(), key=_sort_key)

    lines = []

    # Header (from Questions file)
    if hdr_q:
        hdr_copy = dict(hdr_q)
        hdr_copy["integrated"] = True
        hdr_copy.pop("element", None)
        lines.append("# " + json.dumps(hdr_copy, indent=2).replace("\n", "\n# "))
        lines.append("")

    for qid in sorted_ids:
        lines.append(f"{qid}.")

        # Question: <stem> on one line, then a blank line, then choices
        q_lines = q_entries[qid].splitlines()
        stem_idx = 0
        while stem_idx < len(q_lines) and not q_lines[stem_idx].strip():
            stem_idx += 1
        stem = q_lines[stem_idx].strip() if stem_idx < len(q_lines) else ""
        choice_idx = stem_idx + 1
        while choice_idx < len(q_lines) and not q_lines[choice_idx].strip():
            choice_idx += 1
        choices = q_lines[choice_idx:]
        while choices and not choices[-1].strip():
            choices.pop()
        lines.append(f"Question: {stem}")
        lines.append("")  # blank line after the question stem
        lines.extend(choices)

        ans = a_entries.get(qid, "").strip()
        if ans:
            # Strip "Correct Answer: " prefix if present
            ans = re.sub(r"^Correct Answer:\s*", "", ans, flags=re.IGNORECASE)
            ans = ' '.join(ans.split())
            lines.append(f"Answer: {ans}")

        fb = f_entries.get(qid, "").strip()
        if fb:
            # Strip "Review: " prefix if present
            fb = re.sub(r"^Review:\s*", "", fb, flags=re.IGNORECASE)
            fb = ' '.join(fb.split())
            lines.append(f"Feedback: {fb}")

        ctx = c_entries.get(qid, "").strip()
        if ctx:
            ctx = ' '.join(ctx.split())
            lines.append(f"Context: {ctx}")

        lines.append("")  # blank line between questions

    return "\n".join(lines)


# ── Module processing ─────────────────────────────────────────────────────────

def process_module(module_num: int, *, test: bool = False):
    module_dir = COURSE_ROOT / f"module{module_num}"
    if not module_dir.is_dir():
        print(f"Module directory not found: {module_dir}")
        return

    print(f"\n{'='*60}")
    print(f"Module {module_num}: {module_dir}")
    print(f"{'='*60}")

    topic_dirs = sorted([d for d in module_dir.iterdir() if d.is_dir()])

    for topic_dir in topic_dirs:
        qbanks_dir = _find_qbanks_dir(topic_dir)
        if qbanks_dir is None:
            continue

        result = _component_dirs(qbanks_dir)
        if result is None:
            continue
        q_dir, a_dir, f_dir, c_dir = result

        banks = _discover_banks(q_dir)
        if not banks:
            print(f"  [{topic_dir.name}] No banks found.")
            continue

        for prefix, bank_num in banks:
            out_name = f"{prefix}{bank_num}_INT.txt"
            print(f"\n  [{topic_dir.name}] {prefix}Q{bank_num} → {out_name}")

            integrated = integrate_bank(q_dir, a_dir, f_dir, c_dir, prefix, bank_num, test=test)
            if integrated is None:
                continue

            if test:
                # Print first ~60 lines to stdout for review
                preview_lines = integrated.splitlines()[:60]
                print("  --- PREVIEW (first 60 lines) ---")
                for ln in preview_lines:
                    print("  " + ln)
                if len(integrated.splitlines()) > 60:
                    print(f"  ... ({len(integrated.splitlines())} lines total)")
                print("  --- END PREVIEW ---")
            else:
                out_dir = qbanks_dir / "Integrated"
                out_dir.mkdir(exist_ok=True)
                out_path = out_dir / out_name
                out_path.write_text(integrated, encoding="utf-8")
                print(f"    Written: {out_path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def _merge_header_for_output(src_header, new_header):
    """Start from the source header, but keep the new course module number."""
    hdr = dict(src_header) if src_header else {}
    if new_header:
        if 'module' in new_header:
            hdr['module'] = new_header['module']
        # Preserve other new-course metadata only if missing in source
        for key in ('course number', 'course title', 'instructors', 'author'):
            if key in new_header and key not in hdr:
                hdr[key] = new_header[key]
    hdr['integrated'] = True
    hdr.pop('element', None)
    hdr['timestamp'] = datetime.now().isoformat()
    return hdr


def offer_convert_all(app):
    """Ask whether to convert all old modules; exit on No."""
    reply = QMessageBox.question(
        None,
        "Convert all old modules?",
        "You cancelled the single-file selection.\nConvert all old modules now?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if reply == QMessageBox.StandardButton.No:
        print("Cancelled.")
        sys.exit(0)
    print("\nConverting all old modules...")
    for n in range(26):
        process_module(n, test=False)
    print("Done.")
    sys.exit(0)


def gui_reintegrate():
    """Default GUI workflow: re-integrate one existing integrated file."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    new_path_str, _ = QFileDialog.getOpenFileName(
        None,
        "Select integrated qbank in new course",
        str(NEW_COURSE_ROOT),
        "Integrated Banks (*_INT.txt);;Text Files (*.txt)",
    )
    if not new_path_str:
        offer_convert_all(app)
        return
    new_path = Path(new_path_str)

    q_file_str, _ = QFileDialog.getOpenFileName(
        None,
        "Select old course Question file",
        str(OLD_COURSE_ROOT),
        "Question Files (M*Q*_*.txt);;Text Files (*.txt)",
    )
    if not q_file_str:
        offer_convert_all(app)
        return
    q_file = Path(q_file_str)

    m = _FILE_RE.match(q_file.name)
    if m is None:
        print(f"Selected file does not match the expected naming pattern: {q_file.name}", file=sys.stderr)
        return
    source_prefix = m.group('prefix')
    source_bank = m.group('bank')

    qbanks_dir = q_file.parent.parent
    dirs = _component_dirs(qbanks_dir)
    if dirs is None:
        print(f"No component directories found alongside {q_file.parent}", file=sys.stderr)
        return
    q_dir, a_dir, f_dir, c_dir = dirs

    integrated_text = integrate_bank(
        q_dir, a_dir, f_dir, c_dir, source_prefix, source_bank, test=False
    )
    if integrated_text is None:
        print("Integration failed.", file=sys.stderr)
        return

    src_header, body = _parse_header(integrated_text)
    new_header, _ = _parse_header(new_path.read_text(encoding='utf-8'))
    out_header = _merge_header_for_output(src_header, new_header)

    out_lines = ['# ' + json.dumps(out_header, indent=2).replace('\n', '\n# ')]
    out_lines.append('')
    out_lines.append(body)
    out_text = '\n'.join(out_lines) + '\n'

    new_path.write_text(out_text, encoding='utf-8')
    print(f"Wrote re-integrated file to {new_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Integrate QBank component files into combined files."
    )
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument(
        "--test", metavar="MODULE", type=int,
        help="Run in test mode on MODULE (preview to stdout, no files written).",
    )
    group.add_argument(
        "--module", metavar="MODULE", type=int,
        help="Process a single module and write integrated files.",
    )
    group.add_argument(
        "--all", action="store_true",
        help="Process all old-course modules 0-25 and write integrated files.",
    )
    args = parser.parse_args()

    if args.test is not None:
        process_module(args.test, test=True)
    elif args.module is not None:
        process_module(args.module, test=False)
    elif args.all:
        for n in range(26):
            process_module(n, test=False)
    else:
        gui_reintegrate()


if __name__ == "__main__":
    main()
