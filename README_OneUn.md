Generates per-student ODT quiz/worksheet files from a LibreOffice Writer
template and a plain-text problem definition file.
 
---
 
## Workflow overview
 
```
Problem definition (.txt)  ──┐
                              ├──► OneUn ──► one ODT per student
ODT template (.odt)        ──┘             + summary log (.txt)
```
 
1. **Author the problem definition file** (`.txt`) — equations and a shared
   variable table (see format below).
2. **Author the ODT template** in LibreOffice Writer — formatted question text,
   tables, figures, and placeholder text frames marking where answer boxes and
   graphs appear (see template instructions below).
3. **Run the generator** via the *One Unknown* tab in `generator_gui26.py`:
   - Select the `.txt` and `.odt` files.
   - Choose generation mode (random / pseudo random) and an optional base seed.
   - Enter student codes.  **One ODT is produced per student code.**
   - If a graph is wanted, tick *Include graph*, choose the equation and X/Y variables.
4. A **summary log** (`<output_stem>_summary.txt`) is written alongside the
   output files, recording all input paths, parameters, and the per-student
   seed so that non-repeating repeat quizzes can be produced later.
 
---
 
## Problem definition file format (`.txt`)
 
### Sections
 
```
[EQUATIONS]
(1) $E = (R * !$T) / (z * F) * ln($Cout / $Cin)
(2) $Q = $C * $V
 
[VARIABLES]
var, varName, varNameShortList, varType, Vmin, Vmax, increment
$E,    equilibrium potential, "E,Eeq",   float, -100,   50,    0.1
$T,    temperature,           "T,Temp",  float,  293,  313,    5
$Cout, outside concentration, "Cout,Co", float,    1,  150,    5
$Cin,  inside concentration,  "Cin,Ci",  float,    1,  150,    5
$Q,    charge,                "Q",       float,    0,   10,    0.5
$C,    capacitance,           "C",       float, 1e-6, 100e-6, 1e-6
$V,    voltage,               "V",       float,    0,  100,    5
 
[CONSTANTS]
sym, symName, symNameShortList, symType, value
#R, gas constant,     "R", float, 8.314
#F, Faraday constant, "F", float, 96485
#z, valence,          "z", int,   1
```

**Headings:** `[EQUATIONS]`, `[VARIABLES]`, and `[CONSTANTS]` are **recommended but optional**.
The parser also accepts `EQUATIONS:` / `VARIABLES:` / `CONSTANTS:` (case-insensitive) and will
auto-detect equation lines (contain `=` and `$`), variable rows (start with `$` or `var`),
and constant rows (start with `#`) when headings are omitted.
 
### Equation syntax
 
| Syntax         | Meaning                                                        |
|---------------|----------------------------------------------------------------|
| `$Vm`         | variable named `Vm`                                            |
| `!$T`         | variable `T`, **pinned** — same value used in every equation for this student |
| `(1)` prefix  | equation number (required when more than one equation)         |
| `^` or `**`   | exponentiation                                                 |
| `root2($x)`   | square root of x                                               |
| `ln($x)`      | natural log                                                    |
| `log10($x)`   | log base 10                                                    |
| `log2($x)`    | log base 2 (any integer base)                                  |
 
### Variable table columns
 
| Column             | Description                                                                    |
|-------------------|--------------------------------------------------------------------------------|
| `var`             | Variable name, always prefixed `$` (never `!$` in this column)                |
| `varName`         | Full descriptive name                                                          |
| `varNameShortList`| Comma-separated short names in quotes, e.g. `"E,Eeq"`                         |
| `varType`         | `float`, `int`, or `str`                                                       |
| `Vmin`            | Minimum value (or first allowed string value)                                  |
| `Vmax`            | Maximum value (or last allowed string value)                                   |
| `increment`       | Step size for numeric ranges; semicolon-separated list for explicit values     |
 
**Notes:**
- The table is shared across all equations.  If `$T` appears in equation 1 and
  equation 2, the same table row supplies its allowed values for both.
- Use `!$T` *in the equation text* to indicate that the same randomly-chosen
  value of `$T` is used across all equations for a given student.  Without `!`,
  each equation independently samples its own value of `$T`.
 
### Constants table
 
Physical or fixed quantities that do not vary between students belong in `[CONSTANTS]`.
In equations they appear as **bare symbols** (no prefix): `R`, `F`, `z`.  In the ODT
template use `#` prefix to substitute the value: `#F`, `#R`.
 
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
 
- Every `$variable` referenced in any equation must appear in `[VARIABLES]`.
- Every `#token` in the ODT template must appear in `[CONSTANTS]`.
- Multiple equations must be numbered consecutively starting at 1.
 
### Multi-equation example
 
```
[EQUATIONS]
(1) $E = (R * !$T) / (z * F) * ln($Cout / $Cin)
(2) $Ecell = $E1 - $E2
```
 
This produces **two questions** per quiz (one per equation).  `!$T` means the
same temperature is drawn for both questions for any given student.
 
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
| `{{answer}}`    | Answer region (single-equation quiz)            |
| `{{answer_1}}`  | Answer region for question 1                    |
| `{{answer_1a}}` | Answer region for question 1, sub-part a        |
| `{{answer_2b}}` | Answer region for question 2, sub-part b        |
| `{{graph}}`     | Graph image region (single-equation quiz)       |
| `{{graph_1}}`   | Graph image for question 1 (equation 1's plot)  |
| `{{graph_2}}`   | Graph image for question 2 (equation 2's plot)  |
 
**Rules:**
- The numeric suffix (1, 2, …) must match the equation number in the `.txt`
  file.  `{{graph_1}}` is replaced with the plot generated from equation 1's
  problem instance.
- Sub-part letters (`a`, `b`, …) in answer placeholders are recorded but
  ignored by the generator — they exist for your layout reference only.
- `{{answer_*}}` frames are cleared to become blank student answer regions.
- `{{graph_*}}` frames are replaced by the generated PNG plot.
- All other content (text, tables, existing images, styles, calibration marks)
  is copied unchanged from the template.
 
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
 
If a variable name has a trailing integer that matches an equation number, it is
interpreted as that equation's instance of the base variable.  For example, if
the table defines `$T`, then `$T1` in the template uses equation 1's value of
`$T`, and `$T2` uses equation 2's value of `$T`.  If the table defines `$T1`
directly, that exact definition takes precedence.
 
> **Note:** Variables that are used only in the ODT text (and not in any
equation) must still be listed in `[VARIABLES]` so the generator knows their
allowed values.  They are treated as extra values and are chosen independently
per student (or pinned with `!$` if desired).
 
### Recommended template structure
 
```
[Page header — course, date, student name line]
 
Question 1
──────────
[Formatted equation and context text]
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
| **Pseudo Random**| All possible value combinations are shuffled once and consumed without reuse, so no two students receive the same variable combination for a given equation. |
 
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