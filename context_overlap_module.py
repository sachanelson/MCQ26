#!/usr/bin/env python3
"""
context_overlap_module.py

Standalone utility: compute context-overlap across ALL integrated qbank files
for a single module, restricted to question pairs that share the same
section/box reference (the Feedback field).

Usage:
    python context_overlap_module.py
    # or
    python context_overlap_module.py --module 2

The script:
1. Asks for a module number.
2. Finds all *_INT.txt files directly inside each topic subfolder of the module
   folder (e.g. module2/CHA/M2_CHA0_INT.txt).
3. Parses each integrated bank file.
4. Groups questions by their feedback string (section/box label).
5. Within each section group, compares context fields pairwise for >50% word overlap.
6. Merges transitive pairs into consolidated groups.
7. Writes a report to <course_folder>/module<N>/context_overlap_M<N>.txt.
"""

import argparse
import os
import re
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

COURSE_FOLDER = os.path.expanduser("~/textProcessing/NBIO140_2026")


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def find_integrated_banks(module_folder: str) -> List[Tuple[str, str]]:
    """Return list of (topic_code, abs_path) for every *_INT.txt file found
    directly inside an immediate topic subfolder of *module_folder*.

    Layout: <module_folder>/<TOPIC>/*_INT.txt
    Files containing ' copy' in the name are excluded.
    """
    results = []
    try:
        topic_dirs = [
            d for d in os.listdir(module_folder)
            if os.path.isdir(os.path.join(module_folder, d))
            and not d.startswith(".")
        ]
    except OSError as e:
        print(f"[ERROR] Cannot list module folder: {e}")
        return results

    for topic_code in sorted(topic_dirs):
        topic_path = os.path.join(module_folder, topic_code)
        try:
            for fname in sorted(os.listdir(topic_path)):
                if not fname.endswith("_INT.txt"):
                    continue
                if fname.startswith("."):
                    continue
                if " copy" in fname.lower():
                    continue
                results.append((topic_code, os.path.join(topic_path, fname)))
        except OSError:
            continue

    return results


# ---------------------------------------------------------------------------
# Integrated bank parser (inline format: field labels on same line as value)
# ---------------------------------------------------------------------------

def load_integrated_bank(path: str) -> List[Dict]:
    """Parse an integrated bank file (*_INT.txt) in the inline format:

        CHA0_1.
        Question: What is ...?

        A. Choice one
        B. Choice two
        ...
        Answer: B. Choice two
        Feedback: Section 2.14
        Context: The quick brown fox...

    Returns a list of dicts with keys:
        id, stem, choices, correct_idx, feedback, context
    """
    with open(path, "r", encoding="utf-8") as f:
        raw_lines = f.readlines()

    # Skip JSON header (lines starting with # { ... # } or { ... })
    start_idx = 0
    in_header = False
    for i, line in enumerate(raw_lines):
        s = line.strip()
        if s.startswith("# {") or (s.startswith("{") and not in_header):
            in_header = True
        if in_header and (s.startswith("# }") or s == "}"):
            start_idx = i + 1
            break

    questions: List[Dict] = []
    current: Optional[Dict] = None

    for line in raw_lines[start_idx:]:
        stripped = line.strip()

        if not stripped:
            if current is not None and current.get("choices"):
                questions.append(current)
                current = None
            continue

        # Question ID line: e.g. "CHA0_1."
        m_id = re.match(r'^([A-Za-z0-9_]+)\.$', stripped)
        if m_id:
            if current is not None and current.get("choices"):
                questions.append(current)
            current = {
                "id": m_id.group(1),
                "stem": "",
                "choices": [],
                "correct_idx": None,
                "feedback": "",
                "context": "",
            }
            continue

        if current is None:
            continue

        # Question stem: "Question: text"
        m_q = re.match(r'^Question:\s*(.*)', stripped, re.IGNORECASE)
        if m_q:
            current["stem"] = m_q.group(1).strip()
            continue

        # Answer choices: "A. text" through "E. text"
        m_choice = re.match(r'^([A-E])\.\s+(.*)', stripped)
        if m_choice:
            letter, text = m_choice.group(1), m_choice.group(2).strip()
            idx = ord(letter.upper()) - ord("A")
            while len(current["choices"]) <= idx:
                current["choices"].append("")
            current["choices"][idx] = text
            continue

        # Answer line: "Answer: B. text"
        m_ans = re.match(r'^Answer:\s*([A-E])\.\s*(.*)', stripped, re.IGNORECASE)
        if m_ans:
            current["correct_idx"] = ord(m_ans.group(1).upper()) - ord("A")
            continue

        # Feedback line: "Feedback: Section 2.14"
        m_fb = re.match(r'^Feedback:\s*(.*)', stripped, re.IGNORECASE)
        if m_fb:
            current["feedback"] = m_fb.group(1).strip()
            continue

        # Context line: "Context: text ..." (may be followed by continuation lines)
        m_ctx = re.match(r'^Context:\s*(.*)', stripped, re.IGNORECASE)
        if m_ctx:
            current["context"] = m_ctx.group(1).strip()
            continue

        # Context continuation (no recognised keyword prefix)
        if current["context"]:
            current["context"] += " " + stripped

    # Flush last question
    if current is not None and current.get("choices"):
        questions.append(current)

    # Validate
    valid = []
    for q in questions:
        if q["correct_idx"] is None:
            print(f"  [WARNING] {q['id']}: no correct answer — skipping")
            continue
        if len(q["choices"]) != 5 or not all(q["choices"]):
            print(f"  [WARNING] {q['id']}: incomplete choices ({len(q['choices'])}) — skipping")
            continue
        valid.append(q)

    return valid


# ---------------------------------------------------------------------------
# Overlap detection (same logic as llm_converter26.compute_context_similarity)
# ---------------------------------------------------------------------------

def contexts_overlap(ctx1: str, ctx2: str) -> Optional[str]:
    """Return a direction string if the two contexts overlap (>50% word match),
    else return None.

    ctx1 and ctx2 are expected to be non-empty strings.
    """
    words1 = ctx1.split()
    words2 = ctx2.split()
    if not words1 or not words2:
        return None
    w1 = " ".join(words1)
    w2 = " ".join(words2)
    if w1 in w2 and len(words1) / len(words2) > 0.5:
        return "contained_in_right"
    if w2 in w1 and len(words2) / len(words1) > 0.5:
        return "contained_in_left"
    return None


def merge_groups(pairs: List[Tuple[str, str, str]]) -> List[Dict]:
    """Given a list of (id1, id2, direction) overlap pairs, merge transitive
    pairs into consolidated groups.

    Returns a list of dicts: {'question_ids': set, 'relations': list of tuples}
    """
    groups: List[Optional[Dict]] = []
    q_to_group: Dict[str, int] = {}

    for q1_id, q2_id, direction in pairs:
        g1 = q_to_group.get(q1_id)
        g2 = q_to_group.get(q2_id)

        if g1 is None and g2 is None:
            idx = len(groups)
            groups.append({"question_ids": {q1_id, q2_id}, "relations": [(q1_id, q2_id, direction)]})
            q_to_group[q1_id] = idx
            q_to_group[q2_id] = idx

        elif g1 is not None and g2 is None:
            groups[g1]["question_ids"].add(q2_id)
            groups[g1]["relations"].append((q1_id, q2_id, direction))
            q_to_group[q2_id] = g1

        elif g1 is None and g2 is not None:
            groups[g2]["question_ids"].add(q1_id)
            groups[g2]["relations"].append((q1_id, q2_id, direction))
            q_to_group[q1_id] = g2

        elif g1 != g2:
            # Merge g2 into g1
            groups[g1]["question_ids"].update(groups[g2]["question_ids"])
            groups[g1]["relations"].extend(groups[g2]["relations"])
            groups[g1]["relations"].append((q1_id, q2_id, direction))
            for qid in groups[g2]["question_ids"]:
                q_to_group[qid] = g1
            groups[g2] = None  # mark merged

        else:
            groups[g1]["relations"].append((q1_id, q2_id, direction))

    return [g for g in groups if g is not None]


# ---------------------------------------------------------------------------
# Question block formatting
# ---------------------------------------------------------------------------

def format_question_block(q: Dict, bank_path: str, topic_code: str) -> str:
    """Render a question as a human-readable block for the output file."""
    lines = []
    bank_label = f"[{topic_code}] {os.path.basename(bank_path)}"
    lines.append(f"  Question {q['id']}  —  {bank_label}")
    lines.append(f"  Q: {q['stem']}")
    letters = "ABCDE"
    for i, choice in enumerate(q["choices"]):
        prefix = f"  *{letters[i]}." if i == q.get("correct_idx") else f"   {letters[i]}."
        lines.append(f"{prefix} {choice}")
    lines.append(f"  Answer: {letters[q['correct_idx']]}. {q['choices'][q['correct_idx']]}" if q.get("correct_idx") is not None else "  Answer: unknown")
    lines.append(f"  Feedback: {q.get('feedback', '')}")
    lines.append(f"  Context: {q.get('context', '')}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def run_analysis(module_num: int) -> None:
    module_folder = os.path.join(COURSE_FOLDER, f"module{module_num}")
    if not os.path.isdir(module_folder):
        print(f"[ERROR] Module folder not found: {module_folder}")
        sys.exit(1)

    print(f"\nScanning module{module_num} for integrated qbank files...")
    banks = find_integrated_banks(module_folder)

    if not banks:
        print("[ERROR] No integrated qbank files found.")
        sys.exit(1)

    print(f"Found {len(banks)} integrated bank file(s):")
    for topic, path in banks:
        print(f"  [{topic}] {os.path.relpath(path, module_folder)}")

    # Load all questions, tagging each with its source
    all_questions: List[Dict] = []
    load_errors = []
    for topic_code, path in banks:
        try:
            qs = load_integrated_bank(path)
            for q in qs:
                q["_bank_path"] = path
                q["_topic_code"] = topic_code
            all_questions.extend(qs)
            print(f"  Loaded {len(qs)} questions from [{topic_code}] {os.path.basename(path)}")
        except Exception as e:
            load_errors.append((path, str(e)))
            print(f"  [WARNING] Could not load {path}: {e}")

    if not all_questions:
        print("[ERROR] No questions loaded.")
        sys.exit(1)

    print(f"\nTotal questions loaded: {len(all_questions)}")

    # Group questions by feedback label (section/box)
    section_groups: Dict[str, List[Dict]] = {}
    no_feedback: List[Dict] = []
    for q in all_questions:
        fb = q.get("feedback", "").strip()
        if fb:
            section_groups.setdefault(fb, []).append(q)
        else:
            no_feedback.append(q)

    print(f"Distinct section/box labels: {len(section_groups)}")
    if no_feedback:
        print(f"Questions with no feedback label (skipped for overlap): {len(no_feedback)}")

    # Find overlapping pairs within each section group
    all_overlap_pairs: List[Tuple[str, str, str]] = []  # (id1, id2, direction_str)
    total_comparisons = 0

    for section_label, qs in section_groups.items():
        if len(qs) < 2:
            continue
        for i, q1 in enumerate(qs):
            for q2 in qs[i + 1:]:
                ctx1 = q1.get("context", "")
                ctx2 = q2.get("context", "")
                if not isinstance(ctx1, str):
                    ctx1 = str(ctx1)
                if not isinstance(ctx2, str):
                    ctx2 = str(ctx2)
                if not ctx1.strip() or not ctx2.strip():
                    continue
                total_comparisons += 1
                direction = contexts_overlap(ctx1, ctx2)
                if direction == "contained_in_right":
                    desc = f"{q1['id']} ({q1['_topic_code']}) is contained in {q2['id']} ({q2['_topic_code']}) [>50% word overlap, section: {section_label}]"
                    all_overlap_pairs.append((q1["id"], q2["id"], desc))
                elif direction == "contained_in_left":
                    desc = f"{q2['id']} ({q2['_topic_code']}) is contained in {q1['id']} ({q1['_topic_code']}) [>50% word overlap, section: {section_label}]"
                    all_overlap_pairs.append((q1["id"], q2["id"], desc))

    overlap_groups = merge_groups(all_overlap_pairs)

    # Build a lookup: id -> question dict
    q_by_id: Dict[str, Dict] = {q["id"]: q for q in all_questions}

    # Write output
    output_path = os.path.join(module_folder, f"context_overlap_M{module_num}.txt")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"Context Overlap Analysis — Module {module_num}\n")
        f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Course folder: {COURSE_FOLDER}\n")
        f.write(f"Module folder: {module_folder}\n")
        f.write(f"\nBanks analysed ({len(banks)}):\n")
        for topic_code, path in banks:
            f.write(f"  [{topic_code}] {os.path.relpath(path, module_folder)}\n")
        if load_errors:
            f.write(f"\nLoad errors ({len(load_errors)}):\n")
            for path, err in load_errors:
                f.write(f"  {path}: {err}\n")
        f.write(f"\nTotal questions loaded:         {len(all_questions)}\n")
        f.write(f"Distinct section/box labels:    {len(section_groups)}\n")
        f.write(f"Questions without feedback:      {len(no_feedback)}\n")
        f.write(f"Context pairs compared:          {total_comparisons}\n")
        f.write(f"Overlapping pairs found:         {len(all_overlap_pairs)}\n")
        f.write(f"Consolidated overlap groups:     {len(overlap_groups)}\n")
        f.write("\n" + "=" * 80 + "\n\n")

        if not overlap_groups:
            f.write("No overlapping contexts found.\n")
        else:
            for group_num, group in enumerate(overlap_groups, 1):
                qids = sorted(group["question_ids"], key=str)
                f.write(f"GROUP #{group_num}  ({len(qids)} questions)\n")
                f.write(f"Questions: {', '.join(qids)}\n\n")

                f.write("Overlap relations:\n")
                for q1_id, q2_id, desc in group["relations"]:
                    f.write(f"  - {desc}\n")
                f.write("\n")

                f.write("Full question blocks:\n")
                f.write("-" * 60 + "\n")
                for qid in qids:
                    q = q_by_id.get(qid)
                    if q:
                        f.write(format_question_block(q, q["_bank_path"], q["_topic_code"]))
                        f.write("\n" + "-" * 60 + "\n")
                    else:
                        f.write(f"  {qid}: not found in loaded questions\n")
                        f.write("-" * 60 + "\n")
                f.write("\n" + "=" * 80 + "\n\n")

        # Summary table of section groups for reference
        f.write("\nSection/box group sizes (questions per label across all banks):\n")
        for label in sorted(section_groups.keys()):
            qs = section_groups[label]
            topics = ", ".join(sorted({q["_topic_code"] for q in qs}))
            f.write(f"  {label:<30}  {len(qs):>3} questions  [{topics}]\n")

    print(f"\nDone. {len(overlap_groups)} overlap group(s) found.")
    print(f"Report written to: {output_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute context overlap across integrated qbank files for one module."
    )
    parser.add_argument(
        "--module", "-m",
        type=int,
        default=None,
        help="Module number (e.g. 9). If omitted, the script will prompt."
    )
    args = parser.parse_args()

    if args.module is not None:
        module_num = args.module
    else:
        try:
            module_num = int(input("Enter module number: ").strip())
        except (ValueError, KeyboardInterrupt):
            print("\nInvalid input. Exiting.")
            sys.exit(1)

    run_analysis(module_num)


if __name__ == "__main__":
    main()
