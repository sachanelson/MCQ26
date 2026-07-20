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
import sys
from pathlib import Path

# ── Configuration ────────────────────────────────────────────────────────────

COURSE_ROOT = Path(os.path.expanduser("~/textProcessing/NBIO140_modules_2026"))

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


def _find_file(directory: Path, prefix: str, letter: str, bank: str):
    """Find the single file matching prefix+letter+bank in directory."""
    if directory is None or not directory.is_dir():
        return None
    pattern = re.compile(
        rf"^{re.escape(prefix)}{letter}{re.escape(bank)}_.*\.txt$",
        re.IGNORECASE,
    )
    matches = [f for f in directory.iterdir() if pattern.match(f.name)]
    # Exclude Overlap_ files
    matches = [f for f in matches if not f.name.startswith("Overlap_")]
    return matches[0] if len(matches) == 1 else (matches[0] if matches else None)


# ── Parsing helpers ───────────────────────────────────────────────────────────

def _parse_header(text: str):
    """Extract the JSON header block and return (header_dict, body_text).

    The header format is:
        # {
          "key": "value",
          ...
        }
        <blank line>
        <questions body>

    Only the very first line carries the '# ' prefix; the remaining JSON lines
    are plain.  The block ends at the first line that is exactly '}'.
    """
    lines = text.splitlines()
    if not lines or not lines[0].strip().startswith("# {"):
        return None, text.strip()

    # Collect JSON lines: first line strips the leading '# '
    json_lines = [lines[0].strip()[2:]]  # '# {' → '{'
    body_start = 1
    for i, line in enumerate(lines[1:], 1):
        json_lines.append(line)
        body_start = i + 1
        if line.strip() == "}":
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
        lines.append("Question:")
        lines.append(q_entries[qid])

        ans = a_entries.get(qid, "").strip()
        if ans:
            # Strip "Correct Answer: " prefix if present
            ans = re.sub(r"^Correct Answer:\s*", "", ans, flags=re.IGNORECASE)
            lines.append("Answer:")
            lines.append(ans)

        fb = f_entries.get(qid, "").strip()
        if fb:
            # Strip "Review: " prefix if present
            fb = re.sub(r"^Review:\s*", "", fb, flags=re.IGNORECASE)
            lines.append("Feedback:")
            lines.append(fb)

        ctx = c_entries.get(qid, "").strip()
        if ctx:
            lines.append("Context:")
            lines.append(ctx)

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

def main():
    parser = argparse.ArgumentParser(
        description="Integrate QBank component files into combined files."
    )
    group = parser.add_mutually_exclusive_group(required=True)
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
        help="Process all modules 0-25 and write integrated files.",
    )
    args = parser.parse_args()

    if args.test is not None:
        process_module(args.test, test=True)
    elif args.module is not None:
        process_module(args.module, test=False)
    elif args.all:
        for n in range(26):
            process_module(n, test=False)


if __name__ == "__main__":
    main()
