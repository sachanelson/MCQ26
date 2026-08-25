Generates per-student ODT quiz/worksheet files from a LibreOffice Writer
template and a plain-text problem definition file.
 
---
 
## Workflow overview
 
```
Problem definition (.txt)  ──┐
                              ├──► OneUn ──► one ODT per student
ODT template (.odt)        ──┘             + summary log (.txt)
```
 
1. **Author the problem definition file** (`.txt`) — a shared variable and
   constant table (see format below). Equations are **not** written here;
   the questions themselves live entirely in the ODT template's text, and
   the corresponding computed answers live in the answer-key ODT template's
   `{{answer_*}}` frames (see [Answer-key templates](#answer-key-templates)).
2. **Author the ODT template** in LibreOffice Writer — formatted question text,
   tables, figures, and placeholder text frames marking where answer boxes and
   graphs appear (see template instructions below). Reference `$Variable`
   tokens from the definition file directly in the question text wherever a
   generated value should appear.
3. **Run the generator** via the *One Unknown* tab in `generator_gui26.py`:
   - Select the `.txt` and `.odt` files.
   - Choose generation mode (random / pseudo random) and an optional base seed.
   - Enter student codes.  **One ODT is produced per student code.**
   - If a graph is wanted, tick *Include graph*, choose the X/Y variables.
4. A **summary log** (`<output_stem>_summary.txt`) is written alongside the
   output files, recording all input paths, parameters, and the per-student
   seed so that non-repeating repeat quizzes can be produced later.
 
---
 
## Problem definition file format (`.txt`)
 
### Sections
 
```
[VARIABLES]
var, varName, varNameShortList, varType, Vmin, Vmax, increment
$T1,   temperature (Q1),        "T1,Temp1", float,  293,  313,    5
$T2,   temperature (Q2),        "T2,Temp2", float,  293,  313,    5
$Cout, outside concentration,   "Cout,Co",  float,    1,  150,    5
$Cin,  inside concentration,    "Cin,Ci",   float,    1,  150,    5
$C,    capacitance,             "C",        float, 1e-6, 100e-6, 1e-6
$V,    voltage,                 "V",        float,    0,  100,    5
 
[CONSTANTS]
sym, symName, symNameShortList, symType, value
#R, gas constant,     "R", float, 8.314
#F, Faraday constant, "F", float, 96485
#z, valence,          "z", int,   1
```

**Headings:** `[VARIABLES]` and `[CONSTANTS]` are **recommended but optional**.
The parser also accepts `VARIABLES:` / `CONSTANTS:` (case-insensitive) and will
auto-detect variable rows (start with `$` or `var`) and constant rows (start
with `#`) when headings are omitted.

> **No `[EQUATIONS]` section.** Earlier versions of OneUn defined equations in
> this file; that is no longer supported — the parser raises an error if it
> finds one. Question text and structure now live entirely in the ODT
> template, and any computed answers are expressed as `=<expression>` lines
> inside the answer-key template's `{{answer_*}}` frames (see
> [Answer-key templates](#answer-key-templates)).
 
### Variable table columns
 
| Column             | Description                                                                    |
|-------------------|--------------------------------------------------------------------------------|
| `var`             | Variable name, prefixed `$`                                                    |
| `varName`         | Full descriptive name                                                          |
| `varNameShortList`| Comma-separated short names in quotes, e.g. `"E,Eeq"`                         |
| `varType`         | `float`, `int`, or `str`                                                       |
| `Vmin`            | Minimum value (or first allowed string value)                                  |
| `Vmax`            | Maximum value (or last allowed string value)                                   |
| `increment`       | Step size for numeric ranges; semicolon-separated list for explicit values     |
 
**Notes:**
- Each variable is generated **once per student** and that single value is
  used consistently everywhere the variable's `$name` token appears in the
  ODT template (question text, tables, and answer-key expressions). In this
  sense every variable is effectively "pinned" for the whole document — there
  is no separate pinned/unpinned distinction or `!$` syntax anymore.
- If two questions need **independently drawn** values of a conceptually
  similar quantity (e.g. two different temperatures), define **two separate
  variables** with distinct names — e.g. `$T1` and `$T2` — as shown above,
  rather than reusing one variable name across questions.
 
### Constants table
 
Physical or fixed quantities that do not vary between students belong in `[CONSTANTS]`.
In the ODT template (question text, tables, and answer-key expressions) use the
`#` prefix to substitute the value: `#F`, `#R`.
 
```
[CONSTANTS]
sym, symName, symNameShortList, symType, value
#R, gas constant, "R", float, 8.314
#F, Faraday constant, "F", float, 96485
#z, valence, "z", int, 1
```
 
| Column            | Description                                              |
|------------------|----------------------------------------------------------|
| `sym`            | Symbol, always prefixed `#` (e.g. `#F`)                  |
| `symName`        | Full descriptive name                                    |
| `symNameShortList` | Comma-separated short names in quotes                  |
| `symType`        | `float`, `int`, or `str`                                 |
| `value`          | The single fixed value                                   |
 
### Consistency rules (fatal errors, printed to terminal)
 
- Every `#token` referenced in the ODT template (question or answer-key) must
  appear in `[CONSTANTS]`.
- An `[EQUATIONS]` section anywhere in the file is a fatal error.
 
---
 
## ODT template instructions
 
Create your template in **LibreOffice Writer**.  The generator copies the
template once per student, then substitutes the placeholder text frames.
 
### Placeholder text frames
 
Insert a text frame (**Insert → Frame → Frame…**) wherever you want an answer
box or a graph.  Type the placeholder string as the **sole text content** of
the frame.  The frame name (set via F4 → Frame dialog) can be anything; only
the text content is matched.
 
| Placeholder text | Meaning                                         |
|-----------------|-------------------------------------------------|
| `{{answer}}`    | Answer region (single-question document)        |
| `{{answer_1}}`  | Answer region for question 1                    |
| `{{answer_1a}}` | Answer region for question 1, sub-part a        |
| `{{answer_2b}}` | Answer region for question 2, sub-part b        |
| `{{graph}}`     | Graph image region (single-question document)   |
| `{{graph_1}}`   | Graph image for question 1                      |
| `{{graph_2}}`   | Graph image for question 2                      |
| `{{answer_1a; 2}}` | Same as `{{answer_1a}}`; `; 2` records 2 points |
 
**Rules:**
- The numeric suffix (1, 2, …) is just a label you choose to keep multiple
  questions' placeholders distinct — it is not looked up against anything in
  the `.txt` file (which no longer defines equations or question numbers).
- Sub-part letters (`a`, `b`, …) in answer placeholders are recorded but
  ignored by the generator — they exist for your layout reference only.
- An optional point value may follow a semicolon after the placeholder name,
  e.g. `{{answer_1a; 2}}` or `{{answer_1; 3 points}}`. It is captured but not
  used by the current generator and will not affect answer rendering.
- `{{answer_*}}` frames are cleared to become blank student answer regions.
- `{{graph_*}}` frames are replaced by the generated PNG plot.
- All other content (text, tables, existing images, styles, calibration marks)
  is copied unchanged from the template.

> **Known limitation:** `{{graph_*}}` embedding currently relies on an
> internal equation object that the `.txt` parser no longer produces, so graph
> generation is presently a no-op regardless of the *Include graph* setting.
 
### Answer-key templates

You may optionally author a **separate ODT template** dedicated to the answer
key (select it as the *Answer-key template* in the GUI, or it is
auto-detected as the `.odt` file whose name contains "answer" or "key" — see
`_oneun_resolve_input_folder`). When provided, the generator produces a second
ODT per student from this template, with `{{answer_*}}` frames filled in with
the correct answer instead of being left blank.

Note that **`$Variable` and `#Constant` substitution runs on the entire
document — including inside answer-key frames — before the frames themselves
are processed.** This means any `$Var` / `#Const` tokens you type inside an
answer-key frame are already replaced with that student's generated values by
the time the frame content is interpreted below.

An `{{answer_*}}` frame in an answer-key template must contain its
`{{answer...}}` placeholder line (with an optional `; <points>` suffix such as
`{{answer_1a; 2}}`) followed by **one or two non-blank answer lines**. The
placeholder line itself does not count toward that limit. A "line" means a
separate **paragraph** (i.e. you pressed Return/Enter to start it) — not a
visually wrapped line. If your equation is long and word-wraps within the
frame because the frame is narrow, that is purely a rendering effect:
LibreOffice does not insert any paragraph break or line-break markup for
wrapped text, so it still counts as a single line and will not trip this
check. Only an actual Enter (new paragraph) or Shift+Enter (soft line break)
inside the frame would count against the limit — avoid both within an
`<answer line>` itself.

```
{{answer_1a}}
<answer line>
```

or, with two answer lines (e.g. a literal restatement followed by a computed
reduction):

```
{{answer_1a}}
<answer line 1>
<answer line 2>
```

Each `<answer line>` is interpreted independently according to two
conventions:

| Form                          | Behaviour                                                                 |
|-------------------------------|----------------------------------------------------------------------------|
| Leading `=`: `=<expression>`  | Everything after `=` is evaluated as a **numeric arithmetic expression** and the *calculated* result is inserted. |
| Anything else                 | Used verbatim (after `$`/`#` substitution). No calculation is performed.  |

A literal answer must not begin with `=`, since that would be interpreted as
an expression to evaluate.

Examples (after `$`/`#` substitution has already replaced `$E1`, `$E2` with
numbers):

```
{{answer_1a}}
=$E1 - $E2 mV
```
→ evaluates the subtraction and inserts the computed result followed by the
literal unit text `mV`.

```
{{answer_2b}}
Depolarization
```
→ inserted literally as `Depolarization` — no substitution or calculation.

```
{{answer_1}}
$T1
```
→ no leading `=`, so the already-substituted value of `$T1` is inserted
directly, unchanged.

**Expression rules for `=` answers:**
- Allowed operators: `+ - * / // % ^`/`**` (exponent), unary `+`/`-`.
- Allowed functions: `root2(x)` (square root), `ln(x)`, `log10(x)`, `log2(x)`.
- Only numeric arithmetic is permitted — any other identifier or function call
  raises an error.
- A trailing non-numeric token (e.g. a unit) is allowed after the numeric
  expression, e.g. `=$Q / $C V`.
- The final numeric result is rounded to 3 decimal places with trailing zeros
  trimmed (e.g. `12.340` → `12.34`).

**Frame content rules:**
- Exactly one placeholder-name line (with an optional `; <points>` suffix) followed
  by exactly one answer line — more or fewer lines raises an error at generation time.
- Sub-part letters and numeric suffixes in the placeholder name (e.g.
  `{{answer_1a}}`) follow the same numbering rules as in the student template.

### Variable substitution in body text and tables
 
You can include the same `$Variable` names from the `.txt` file directly in the
ODT body text or in table cells.  The generator replaces every occurrence with
the value generated for that student:
 
```
Assuming a temperature of $T1, calculate the equilibrium potential …

Ion      | [ ]in  | [ ]out
---------|--------|-------
K+       | $Kin   | $Kout
```
 
After generation this becomes, for example:
 
```
Assuming a temperature of 298, calculate the equilibrium potential …

Ion      | [ ]in  | [ ]out
---------|--------|-------
K+       | 5      | 145
```
 
Substitution is by **exact variable name only** — there is no equation-index
fallback. If you want two independently drawn values (e.g. two different
temperatures for two different questions), define two distinct rows in
`[VARIABLES]`, such as `$T1` and `$T2`, and use each token where its value
should appear.
 
> **Note:** Every `$Variable` used anywhere in the ODT template (question
> text, tables, or answer-key expressions) must be listed in `[VARIABLES]` so
> the generator knows its allowed values. Each is generated once per student
> and that same value is reused everywhere the token appears.
 
### Recommended template structure
 
```
[Page header — course, date, student name line]
 
Question 1
──────────
[Formatted question text]
[Table of given values — type variable names from the .txt file in cells; the generator replaces
 such as $Kin, $Kout, $T1 with the values drawn for this student]
 
  (a) Show your work:
      [ {{answer_1a}} ]   ← Insert > Frame, type {{answer_1a}} as content
 
  (b) Final answer for E:
      [ {{answer_1b}} ]
 
  Graph of E vs Cout:
      [ {{graph_1}} ]     ← sized to match the PNG output
 
Question 2
──────────
…
```
 
### Graph size and position
 
Size the `{{graph_*}}` frame to the exact dimensions you want for the plot.
The generator embeds the PNG into that frame; LibreOffice will scale it to fit.
Tick *Size/location from template* in the UI to preserve the frame geometry
exactly.
 
---
 
## Generation modes
 
| Mode          | Behaviour                                                                     |
|--------------|-------------------------------------------------------------------------------|
| **Random**       | Values chosen randomly from the allowed-values list.  Each student gets a unique draw derived from their per-student seed. |
| **Pseudo Random**| All possible value combinations (across all variables) are shuffled once and consumed without reuse, so no two students receive the same combination. |
 
---
 
## Seeds and reproducibility
 
- Enable **Use base seed** and set a value (e.g. 42).
- Per-student seeds are derived as `base_seed + student_index` (0-based), so
  every student in the list gets a different but reproducible set of values.
- The summary log records the seed for every student.
- To regenerate a student's quiz identically, use the same seed.
- To generate a new non-repeating version, increment the seed (e.g. add 1000).
- Without a base seed the generation is not reproducible.
 
---
 
## Output files
 
For base output path `~/quizzes/nernst_quiz.odt` with students `S001`, `S002`:
 
```
~/quizzes/nernst_quiz_S001.odt
~/quizzes/nernst_quiz_S002.odt
~/quizzes/nernst_quiz_summary.txt
```
 
The summary log (`_summary.txt`) records:
- Paths to input `.txt` and `.odt` files
- Generation mode, course, instructor, quiz date
- Per-student seeds
- Paths to all generated output files
 
---
 
## Requirements
 
| Package      | Purpose                          |
|-------------|----------------------------------|
| `PyQt6`     | GUI                              |
| `lxml`      | ODT XML manipulation             |
| `matplotlib`| Plot generation                  |
| `numpy`     | Numerical evaluation for plots   |
| LibreOffice | Template authoring; optional headless PDF/PNG conversion |
 
Install Python dependencies:
 
```bash
pip install PyQt6 lxml matplotlib numpy
```
READMEEOF