"""
reorder_Qbank.py
================
Interactively pick an integrated QBank file, then:
  1. Parse the header and all question blocks, validating each block's structure.
  2. Sort blocks by their Feedback section (e.g. "Section 2.4" < "Section 2.5").
  3. Renumber every question ID using the canonical prefix from the header
     (qTop + difficulty), so all IDs run <prefix>_1, <prefix>_2, …
  4. Update header fields qIDstrt, qIDend, numQ to match.
  5. Write the result back to the same file with exactly 2 blank lines between blocks.

Block structure:
    <QID>.
    Question: <stem — one line>
    <blank line>
    A. <choice>
    B. <choice>
    C. <choice>
    D. <choice>
    E. <choice>
    Answer: <repeat of one choice>
    Feedback: <Section or Box reference>
    Context: <one line of context>
    Overlap: <QID1> <QID2> …   ← optional, all on one line

The file picker loops until the user cancels.

Usage:
    python reorder_Qbank.py
"""

import json
import re
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path


# ── Section-sort key ──────────────────────────────────────────────────────────

_SECTION_RE = re.compile(r"(?:Section|Box)\s+([\d]+(?:\.[\d]+)*)", re.IGNORECASE)


def _section_sort_key(block: dict):
    """Return a tuple of ints for stable section ordering.

    "Section 2.10" → (2, 10).  Blocks with no parseable reference sort last.
    """
    fb = block.get("feedback", "")
    m = _SECTION_RE.search(fb)
    if m:
        return tuple(int(x) for x in m.group(1).split("."))
    return (999999,)


# ── Question-ID helpers ───────────────────────────────────────────────────────

_QID_LINE_RE = re.compile(r"^([A-Za-z]{2,4}\d+)_(\d+)\.\s*$")  # full ID line
_QID_RE      = re.compile(r"^([A-Za-z]{2,4}\d+)_(\d+)$")        # bare ID


def _is_block_start(line: str) -> bool:
    return bool(_QID_LINE_RE.match(line))


def _parse_qid_line(line: str):
    """Return (prefix, number) from a '<QID>.' line, or raise ValueError."""
    m = _QID_LINE_RE.match(line)
    if not m:
        raise ValueError(f"Not a QID line: {line!r}")
    return m.group(1), int(m.group(2))


def _make_qid(prefix: str, number: int) -> str:
    return f"{prefix}_{number}"


# ── Header parsing ────────────────────────────────────────────────────────────

def _parse_header_block(lines: list):
    """Return (header_dict, first_body_line_index).

    Handles both header styles:
      Style A:  # {          (only first/last line has '# ')
                  "key": …
                }
      Style B:  # {          (every line prefixed with '#')
                #   "key": …
                # }
    """
    if not lines or not lines[0].strip().startswith("# {"):
        return None, 0

    json_lines = [lines[0].strip()[2:]]   # '# {' → '{'
    end = 1
    for i, line in enumerate(lines[1:], 1):
        stripped = line.strip()
        # Strip leading '#' prefix variants
        if stripped.startswith("# "):
            json_lines.append(stripped[2:])
        elif stripped == "#":
            json_lines.append("")
        else:
            json_lines.append(line)
        end = i + 1
        if stripped in ("}", "# }"):
            break

    try:
        hdr = json.loads("\n".join(json_lines))
    except json.JSONDecodeError:
        hdr = None

    while end < len(lines) and lines[end].strip() == "":
        end += 1

    return hdr, end


# ── Block parsing with strict validation ─────────────────────────────────────

_CHOICE_RE  = re.compile(r"^[A-E]\. ")
_OVERLAP_RE = re.compile(r"^Overlap: .+")


def _parse_blocks(body_lines: list, errors: list):
    """Parse body_lines into validated block dicts.

    Errors (structural problems) are appended to `errors` as strings.
    Returns a list of block dicts on success (errors may still be non-empty).

    Each block dict:
        qid            – original full ID string
        orig_line      – 1-based line number of the QID line in the full file
        prefix         – topic+difficulty prefix, e.g. "RST0"
        number         – original trailing integer
        stem           – str (single line)
        choices        – list of 5 str lines
        answer         – str
        feedback       – str  (used for sorting)
        context        – str
        overlap        – str or None
    """
    blocks = []
    i = 0
    n = len(body_lines)

    while i < n:
        # Skip blank lines between blocks
        while i < n and not body_lines[i].strip():
            i += 1
        if i >= n:
            break

        line = body_lines[i]
        if not _is_block_start(line):
            errors.append(f"Line {i+1}: expected a question ID line, got: {line!r}")
            i += 1
            continue

        orig_line = i + 1
        prefix, number = _parse_qid_line(line)
        qid = f"{prefix}_{number}"
        block_errors = []
        i += 1

        def _expect_inline_label(label):
            """Expect 'Label: <content>' on one line; return content or None."""
            nonlocal i
            prefix_str = f"{label}: "
            if i >= n or not body_lines[i].startswith(prefix_str):
                got = repr(body_lines[i].rstrip()) if i < n else "'EOF'"
                block_errors.append(
                    f"  expected '{label}: <content>' at body line {i+1}, got {got}"
                )
                return None
            content = body_lines[i][len(prefix_str):].rstrip()
            i += 1
            return content

        # ── "Question: <stem>" ────────────────────────────────────────────────
        stem_val = _expect_inline_label("Question")
        stem = stem_val if stem_val is not None else ""
        if stem_val is None:
            block_errors.append(f"  missing question stem")

        # blank line after stem
        if i < n and body_lines[i].strip() == "":
            i += 1
        else:
            block_errors.append(f"  missing blank line after stem")

        # 5 choice lines A–E
        choices = []
        for expected_letter in "ABCDE":
            if i < n and body_lines[i].startswith(f"{expected_letter}. "):
                choices.append(body_lines[i].rstrip())
                i += 1
            else:
                got = body_lines[i].rstrip() if i < n else "EOF"
                block_errors.append(f"  expected choice '{expected_letter}.', got {got!r}")
                # Try to recover by skipping to next known label or block
                break

        # ── "Answer: <content>" ───────────────────────────────────────────────
        answer_val = _expect_inline_label("Answer")
        answer = answer_val if answer_val is not None else ""

        # ── "Feedback: <content>" ─────────────────────────────────────────────
        feedback_val = _expect_inline_label("Feedback")
        feedback = feedback_val if feedback_val is not None else ""

        # ── "Context: <content>" ──────────────────────────────────────────────
        context_val = _expect_inline_label("Context")
        context = context_val if context_val is not None else ""

        # ── Optional "Overlap: …" ─────────────────────────────────────────────
        overlap = None
        if i < n and body_lines[i].startswith("Overlap: "):
            overlap = body_lines[i].rstrip()
            i += 1

        if block_errors:
            errors.append(f"Block {qid} (starting at body line {orig_line}):")
            errors.extend(block_errors)
        else:
            blocks.append({
                "qid":      qid,
                "orig_line": orig_line,
                "prefix":   prefix,
                "number":   number,
                "stem":     stem,
                "choices":  choices,
                "answer":   answer,
                "feedback": feedback,
                "context":  context,
                "overlap":  overlap,
            })

    return blocks


# ── Block serialisation ───────────────────────────────────────────────────────

def _serialise_block(block: dict, new_qid: str) -> str:
    """Render a block with the given new QID."""
    parts = [f"{new_qid}."]
    parts.append(f"Question: {block['stem']}")
    parts.append("")                      # blank line after stem
    parts.extend(block["choices"])
    parts.append(f"Answer: {block['answer']}")
    parts.append(f"Feedback: {block['feedback']}")
    parts.append(f"Context: {block['context']}")
    if block["overlap"] is not None:
        parts.append(block["overlap"])    # already includes "Overlap: " prefix
    return "\n".join(parts)


# ── Header serialisation ──────────────────────────────────────────────────────

def _serialise_header(hdr: dict) -> str:
    """Render the header in the exact file format:

        # {
        #    "key": value,
        #    ...
        # }

    Every interior line is prefixed with '#    ' (hash + 4 spaces).
    The opening '# {' and closing '# }' lines have no extra indent.
    """
    inner = json.dumps(hdr, indent=3)
    lines = inner.splitlines()
    # json.dumps(indent=3) indents interior lines with 3 spaces.
    # Prepending '# ' gives '#    "key": value' (hash + space + 3-space indent)
    # = 4 chars before the key, matching the original file style.
    result = ["# " + lines[0]]           # '# {'
    for line in lines[1:-1]:
        result.append("# " + line) if line.strip() else result.append("#")
    result.append("# " + lines[-1])      # '# }'
    return "\n".join(result)


# ── Validation ────────────────────────────────────────────────────────────────

def _looks_like_qbank(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if not text.lstrip().startswith("# {"):
        return False
    if not re.search(r'^[A-Za-z]{2,4}\d+_\d+\.\s*$', text, re.MULTILINE):
        return False
    return True


# ── Main processing ───────────────────────────────────────────────────────────

def process_file(path: Path):
    text = path.read_text(encoding="utf-8")
    all_lines = text.splitlines()

    hdr, body_start = _parse_header_block(all_lines)
    body_lines = all_lines[body_start:]

    errors = []
    blocks = _parse_blocks(body_lines, errors)

    if errors:
        msg = (
            f"{len(errors)} structural problem(s) found in {path.name}.\n"
            "Please fix the file manually before reordering:\n\n"
            + "\n".join(errors[:30])
            + ("\n…(truncated)" if len(errors) > 30 else "")
        )
        messagebox.showerror("Structural errors — file not modified", msg)
        return

    if not blocks:
        messagebox.showerror("Error", f"No question blocks found in:\n{path}")
        return

    # ── Canonical prefix from header ──────────────────────────────────────────
    q_top      = hdr.get("qTop", "") if hdr else ""
    difficulty = str(hdr.get("difficulty", 0)) if hdr else "0"
    canon_prefix = f"{q_top}{difficulty}" if q_top else blocks[0]["prefix"]

    # ── Sort by feedback section ──────────────────────────────────────────────
    original_order = [b["qid"] for b in blocks]
    blocks.sort(key=_section_sort_key)
    reordered = [b["qid"] for b in blocks] != original_order

    # ── Renumber and serialise ────────────────────────────────────────────────
    serialised_blocks = []
    for new_num, block in enumerate(blocks, 1):
        new_qid = _make_qid(canon_prefix, new_num)
        serialised_blocks.append(_serialise_block(block, new_qid))

    # ── Update header ─────────────────────────────────────────────────────────
    if hdr is not None:
        hdr["numQ"]    = len(blocks)
        hdr["qIDstrt"] = _make_qid(canon_prefix, 1)
        hdr["qIDend"]  = _make_qid(canon_prefix, len(blocks))

    # ── Assemble with 2 blank lines between blocks ────────────────────────────
    header_str = _serialise_header(hdr) if hdr is not None else ""
    blocks_str = "\n\n\n".join(serialised_blocks)   # 2 blank lines = 3 newlines
    output = header_str + "\n\n" + blocks_str + "\n"

    path.write_text(output, encoding="utf-8")

    msg_lines = [
        f"File: {path.name}",
        f"Questions processed: {len(blocks)}",
        "Blocks reordered by Feedback section." if reordered else "Block order was already correct.",
        f"Questions renumbered {canon_prefix}_1 … {canon_prefix}_{len(blocks)}.",
        "File saved.",
    ]
    messagebox.showinfo("Done", "\n".join(msg_lines))


# ── File-picker loop ──────────────────────────────────────────────────────────

def main():
    root = tk.Tk()
    root.withdraw()

    while True:
        path_str = filedialog.askopenfilename(
            title="Select an integrated QBank file",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialdir=str(Path.home() / "textProcessing"),
        )

        if not path_str:
            break   # user cancelled

        path = Path(path_str)

        if not _looks_like_qbank(path):
            messagebox.showerror(
                "Not a QBank file",
                f"The selected file does not appear to be an integrated QBank:\n{path}\n\n"
                "Expected: file starting with a JSON header block (# {{ … }})\n"
                "and containing question ID lines like RST0_1.",
            )
            continue

        try:
            process_file(path)
        except Exception as exc:
            import traceback
            messagebox.showerror("Unexpected error", str(exc))
            traceback.print_exc()
            continue

    root.destroy()


if __name__ == "__main__":
    main()
