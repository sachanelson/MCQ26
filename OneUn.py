"""
OneUn - One Unknown Variable Problem Generator

Generates per-student ODT quiz/worksheet files by filling a LibreOffice
Writer template with randomised or pseudo-random numerical values.

Architecture
------------
* The ODT template defines the layout: question text, subparts, answer
  boxes, and graph locations. Questions themselves (and the arithmetic used
  to compute their answers) live entirely in the ODT templates, not in the
  .txt definition file.
* The .txt definition file defines only the shared variable and constant
  tables. OneUn generates exactly one value per variable per student; that
  value is reused everywhere the variable's token appears (question text,
  tables, and answer-key expressions).
* The student code list (entered in the UI) determines how many output
  files are produced — one ODT per student.
* A plain-text summary log is written alongside the output files recording
  input paths, UI parameters, and the seed used for every student so that
  non-repeating repeat quizzes can be produced later.
* Graphing (PlotGenerator) is currently unreachable in the live workflow:
  it still expects an Equation object, but equations are no longer parsed
  from the .txt file. The intent is to parse a plot equation from the
  {{graph_*}} placeholder's own specification in the ODT template; this is
  NOT YET IMPLEMENTED (see OneUnODTGenerator._get_equation).

Variable table (tab or comma separated, single shared section)
--------------------------------------------------------------
  var, varName, varNameShortList, varType, Vmin, Vmax, increment
  $Vm, membrane potential, "Vm,VM", float, -100, 50, 5
  Each variable gets one generated value per student, used consistently
  wherever its token appears. To get independently-varying values (e.g. two
  different temperatures for two questions), define separate variables such
  as $T1 and $T2.

Constants table (tab or comma separated, optional section)
----------------------------------------------------------
  sym, symName, symNameShortList, symType, value
  #F,  Faraday constant, "F",  float, 96485
  #R,  gas constant,     "R",  float, 8.314
  In the ODT template use #F, #R to substitute the constant value.

Consistency rules (fatal errors if violated)
--------------------------------------------
  * Every #-prefixed token in the ODT template must appear in [CONSTANTS].
  * An [EQUATIONS] section anywhere in the .txt file is a fatal error —
    equations are no longer defined there.

ODT template placeholder frames
--------------------------------
  Insert text frames (Insert > Frame) in LibreOffice Writer containing one
  of the following placeholder strings as the sole text content:

    {{answer}}        single answer box (for a single-question document)
    {{answer_1}}      answer box for question 1
    {{answer_1a}}     answer box for question 1, subpart a
    {{graph}}         graph region (single-question document)
    {{graph_1}}       graph region for question 1

  The numeric suffix is just an author-chosen label to keep multiple
  questions' placeholders distinct. Subpart letters are ignored by the
  generator but must be present in the template for correct layout.

Substitution in ODT text/tables
--------------------------------
  $Variable  replaced by the generated value for that variable (from [VARIABLES]).
             Matched by exact name only.
  #Constant  replaced by the fixed constant value (from [CONSTANTS]).
             Error if the constant is not in the table.
"""

import ast
import math
import os
import re
import random
import itertools
from typing import Dict, List, Optional, Tuple, Any, Union
from dataclasses import dataclass
from pathlib import Path
from copy import deepcopy


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class VariableDef:
    """Definition of a single variable from the variable table."""
    var: str                    # e.g. "$Vm"
    var_name: str               # e.g. "membrane potential"
    var_name_short_list: List[str]  # e.g. ["Vm", "VM"]
    var_type: str               # "float", "int", "str"
    v_min: Any                  # min value (or first if str)
    v_max: Any                  # max value (or last if str)
    increment: Any              # step size or list of values

    def get_allowed_values(self) -> List[Any]:
        """Generate the list of allowed values for this variable."""
        if self.var_type == 'str':
            # increment is a list of string values
            if isinstance(self.increment, list):
                return self.increment
            return [self.v_min, self.v_max]

        if isinstance(self.increment, list):
            return self.increment

        # Numeric range
        values = []
        if self.var_type == 'int':
            step = int(self.increment) if self.increment else 1
            v = int(self.v_min)
            while v <= int(self.v_max):
                values.append(v)
                v += step
        else:  # float
            step = float(self.increment) if self.increment else 1.0
            v = float(self.v_min)
            # Use tolerance to handle floating point
            while v <= float(self.v_max) + step * 0.001:
                values.append(round(v, 10))
                v += step
        return values


@dataclass
class ConstantDef:
    """Definition of a single constant from the constants table."""
    sym: str                        # e.g. "#F"
    sym_name: str                   # e.g. "Faraday constant"
    sym_name_short_list: List[str]  # e.g. ["F"]
    sym_type: str                   # "float", "int", or "str"
    value: Any                      # the fixed value


@dataclass
class Equation:
    """Parsed equation. Currently only constructed ad-hoc by PlotGenerator
    (see _eval_for_y) since equations are no longer parsed from the .txt
    file. Retained to support graphing once equations can be parsed from
    the {{graph_*}} placeholder specification (not yet implemented)."""
    index: int               # equation number (1-based), 0 if single un-numbered
    raw: str                 # original equation string
    expression: str          # Python-evaluable expression
    variables: List[str]     # $-prefixed variable names


@dataclass
class ProblemDefinition:
    """Complete problem definition parsed from the text file."""
    equations: List[Equation]
    variables: Dict[str, VariableDef]   # keyed by $var name
    constants: Dict[str, ConstantDef]   # keyed by #sym name


@dataclass
class Problem:
    """A single generated problem instance."""
    given_values: Dict[str, Any]   # values drawn for every variable, for this student
    equation_index: int            # which equation this instance corresponds to (0 = single/first)


# ---------------------------------------------------------------------------
# Equation Parser
#
# Only `evaluate()` is currently used, by PlotGenerator, to support graphing.
# Equations are no longer parsed from the .txt file (see ProblemDefinitionParser);
# the plan is to eventually parse an Equation from the {{graph_*}} placeholder's
# own specification in the ODT template (not yet implemented).
# ---------------------------------------------------------------------------

class EquationParser:
    """Evaluates parsed equation expressions. Retained to support graphing."""

    @classmethod
    def evaluate(cls, equation: Equation, var_values: Dict[str, Any],
                 constants: Optional[Dict[str, Any]] = None) -> float:
        """Evaluate an equation expression given variable values.

        Args:
            equation: Equation object
            var_values: Dict mapping $var names to their numeric values
            constants: Dict mapping #sym names to ConstantDef (or bare values)

        Returns:
            Result of evaluation
        """
        # Build local namespace with variable values (strip $ prefix for eval)
        local_ns = {'math': math}
        for var_name, val in var_values.items():
            clean_name = var_name.lstrip('$')
            local_ns[clean_name] = val

        # Inject constant values (bare names, no prefix)
        if constants:
            for sym, const_def in constants.items():
                bare = sym.lstrip('#')
                local_ns[bare] = const_def.value if hasattr(const_def, 'value') else const_def

        # Replace $var with var in expression for eval
        eval_expr = equation.expression
        for var_name in equation.variables:
            clean_name = var_name.lstrip('$')
            eval_expr = eval_expr.replace(var_name, clean_name)

        try:
            result = eval(eval_expr, {"__builtins__": {}}, local_ns)
            return result
        except Exception as e:
            raise ValueError(f"Error evaluating '{eval_expr}': {e}")


# ---------------------------------------------------------------------------
# Variable Table Parser
# ---------------------------------------------------------------------------

class VariableTableParser:
    """Parses the variable definition table from a text file."""

    HEADER_FIELDS = ['var', 'varName', 'varNameShortList', 'varType',
                     'Vmin', 'Vmax', 'increment']

    @classmethod
    def parse(cls, lines: List[str]) -> Dict[str, VariableDef]:
        """Parse variable table lines into VariableDef objects.

        Expected columns: var, varName, varNameShortList, varType,
        Vmin, Vmax, increment.  An optional trailing canBmissing column
        (from older files) is ignored.

        Args:
            lines: List of lines (first may be header)

        Returns:
            Dict keyed by variable name (e.g. "$Vm")
        """
        variables = {}

        # Detect delimiter
        if lines and '\t' in lines[0]:
            delimiter = '\t'
        else:
            delimiter = ','

        # Skip header line if it matches known fields
        start = 0
        if lines:
            first_fields = [f.strip().lower() for f in lines[0].split(delimiter)]
            if 'var' in first_fields or 'varname' in first_fields:
                start = 1

        for line in lines[start:]:
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            fields = cls._split_respecting_quotes(line, delimiter)
            if len(fields) < 7:
                # Pad with empty strings
                fields.extend([''] * (7 - len(fields)))

            var_def = cls._parse_row(fields)
            if var_def:
                variables[var_def.var] = var_def

        return variables

    @classmethod
    def _split_respecting_quotes(cls, line: str, delimiter: str) -> List[str]:
        """Split a line by delimiter, respecting quoted fields."""
        fields = []
        current = ''
        in_quotes = False
        quote_char = None

        for ch in line:
            if ch in ('"', "'") and not in_quotes:
                in_quotes = True
                quote_char = ch
            elif ch == quote_char and in_quotes:
                in_quotes = False
                quote_char = None
            elif ch == delimiter and not in_quotes:
                fields.append(current.strip())
                current = ''
                continue
            else:
                current += ch

        fields.append(current.strip())
        return fields

    @classmethod
    def _parse_row(cls, fields: List[str]) -> Optional[VariableDef]:
        """Parse a single row of the variable table."""
        var = fields[0].strip()
        if not var.startswith('$'):
            var = '$' + var

        var_name = fields[1].strip()

        # Short names list
        short_list_str = fields[2].strip().strip('"').strip("'")
        var_name_short_list = [s.strip() for s in short_list_str.split(',') if s.strip()]

        var_type = fields[3].strip().lower()
        if var_type not in ('float', 'int', 'str'):
            var_type = 'float'

        # Parse Vmin, Vmax
        v_min_str = fields[4].strip()
        v_max_str = fields[5].strip()

        # Parse increment - could be a number or a list
        increment_str = fields[6].strip()

        # Convert types
        if var_type == 'str':
            v_min = v_min_str
            v_max = v_max_str
            # increment could be a list of allowed values
            if increment_str:
                increment = [s.strip() for s in increment_str.split(';') if s.strip()]
            else:
                increment = [v_min, v_max]
        elif var_type == 'int':
            v_min = int(float(v_min_str)) if v_min_str else 0
            v_max = int(float(v_max_str)) if v_max_str else 0
            if increment_str and ';' in increment_str:
                increment = [int(float(x)) for x in increment_str.split(';') if x.strip()]
            elif increment_str:
                increment = int(float(increment_str))
            else:
                increment = 1
        else:  # float
            v_min = float(v_min_str) if v_min_str else 0.0
            v_max = float(v_max_str) if v_max_str else 0.0
            if increment_str and ';' in increment_str:
                increment = [float(x) for x in increment_str.split(';') if x.strip()]
            elif increment_str:
                increment = float(increment_str)
            else:
                increment = 1.0

        return VariableDef(
            var=var,
            var_name=var_name,
            var_name_short_list=var_name_short_list,
            var_type=var_type,
            v_min=v_min,
            v_max=v_max,
            increment=increment
        )


# ---------------------------------------------------------------------------
# Constants Table Parser
# ---------------------------------------------------------------------------

class ConstantsTableParser:
    """Parses the constants definition table from a text file.

    Expected columns: sym, symName, symNameShortList, symType, value
    The 'sym' column uses # prefix (e.g. #F).  Rows without # are accepted
    and the prefix is added automatically.
    """

    HEADER_FIELDS = ['sym', 'symname', 'symnameshorlist', 'symtype', 'value']

    @classmethod
    def parse(cls, lines: List[str]) -> Dict[str, ConstantDef]:
        """Parse constant table lines into ConstantDef objects.

        Args:
            lines: List of lines (first may be header)

        Returns:
            Dict keyed by #sym name (e.g. "#F")
        """
        constants: Dict[str, ConstantDef] = {}

        if not lines:
            return constants

        # Detect delimiter
        delimiter = '\t' if '\t' in lines[0] else ','

        # Skip header line if it matches known fields
        start = 0
        first_fields = [f.strip().lower() for f in lines[0].split(delimiter)]
        if 'sym' in first_fields:
            start = 1

        for line in lines[start:]:
            line = line.strip()
            if not line or line.startswith('#') and ',' not in line and '\t' not in line:
                continue

            fields = VariableTableParser._split_respecting_quotes(line, delimiter)
            if len(fields) < 5:
                fields.extend([''] * (5 - len(fields)))

            const_def = cls._parse_row(fields)
            if const_def:
                constants[const_def.sym] = const_def

        return constants

    @classmethod
    def _parse_row(cls, fields: List[str]) -> Optional[ConstantDef]:
        """Parse a single row of the constants table."""
        sym = fields[0].strip()
        if not sym.startswith('#'):
            sym = '#' + sym

        sym_name = fields[1].strip()

        short_list_str = fields[2].strip().strip('"').strip("'")
        sym_name_short_list = [s.strip() for s in short_list_str.split(',') if s.strip()]

        sym_type = fields[3].strip().lower()
        if sym_type not in ('float', 'int', 'str'):
            sym_type = 'float'

        value_str = fields[4].strip()
        if sym_type == 'int':
            value: Any = int(float(value_str)) if value_str else 0
        elif sym_type == 'float':
            value = float(value_str) if value_str else 0.0
        else:
            value = value_str

        return ConstantDef(
            sym=sym,
            sym_name=sym_name,
            sym_name_short_list=sym_name_short_list,
            sym_type=sym_type,
            value=value
        )


# ---------------------------------------------------------------------------
# Problem Definition File Parser
# ---------------------------------------------------------------------------

class ProblemDefinitionParser:
    """Parses a complete problem definition file."""

    EQUATION_SECTION = 'EQUATIONS'
    VARIABLE_SECTION = 'VARIABLES'
    CONSTANT_SECTION = 'CONSTANTS'

    @classmethod
    def parse_file(cls, filepath: str) -> ProblemDefinition:
        """Parse a problem definition text file.

        File format (headings are recommended but optional — the parser will
        auto-detect equations and variable rows if the headings are omitted):

            [EQUATIONS]
            (1) $E = ($R * $T) / ($z * $F) * ln($Cout / $Cin)

            [VARIABLES]
            var, varName, varNameShortList, varType, Vmin, Vmax, increment
            $E, equilibrium potential, "E,Eeq", float, -100, 50, 0.1
            ...

        Acceptable heading forms: ``[EQUATIONS]``, ``[VARIABLES]``,
        ``EQUATIONS:`` or ``VARIABLES:`` (case-insensitive).

        Args:
            filepath: Path to the definition file

        Returns:
            ProblemDefinition object
        """
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        return cls.parse_text(content)

    @classmethod
    def parse_text(cls, content: str) -> ProblemDefinition:
        """Parse problem definition from text content."""
        lines = content.split('\n')

        # Find sections
        eq_lines = []
        var_lines = []
        const_lines = []
        current_section = None

        for line in lines:
            stripped = line.strip()

            # Check for section headers
            if stripped.upper().startswith('[EQUATIONS]') or stripped.upper() == 'EQUATIONS:':
                current_section = cls.EQUATION_SECTION
                continue
            elif stripped.upper().startswith('[VARIABLES]') or stripped.upper() == 'VARIABLES:':
                current_section = cls.VARIABLE_SECTION
                continue
            elif stripped.upper().startswith('[CONSTANTS]') or stripped.upper() == 'CONSTANTS:':
                current_section = cls.CONSTANT_SECTION
                continue

            # Skip empty lines and pure comment lines
            if not stripped or (stripped.startswith('#') and current_section != cls.CONSTANT_SECTION):
                continue

            if current_section == cls.EQUATION_SECTION:
                eq_lines.append(stripped)
            elif current_section == cls.VARIABLE_SECTION:
                var_lines.append(stripped)
            elif current_section == cls.CONSTANT_SECTION:
                const_lines.append(stripped)
            else:
                # Auto-detect: lines with $ and = are equations
                if '=' in stripped and '$' in stripped:
                    eq_lines.append(stripped)
                elif stripped.startswith('$') or stripped.lower().startswith('var'):
                    var_lines.append(stripped)
                elif stripped.startswith('#'):
                    const_lines.append(stripped)

        if eq_lines:
            raise ValueError('OneUn definitions no longer accept an [EQUATIONS] section; place answer expressions in the answer-key ODT template instead.')

        variables = VariableTableParser.parse(var_lines)
        constants = ConstantsTableParser.parse(const_lines)

        return ProblemDefinition(
            equations=[],
            variables=variables,
            constants=constants,
        )


# ---------------------------------------------------------------------------
# Problem Generator
# ---------------------------------------------------------------------------

class ProblemGenerator:
    """Generates one Problem instance per student, containing a single value
    for every variable in the definition. That value is reused everywhere the
    variable's token appears in the ODT template (question text, tables, and
    answer-key expressions).
    """

    def __init__(self, definition: ProblemDefinition):
        self.definition = definition
        # State for pseudo_random mode: a shuffled pool of unique value
        # combinations per equation, consumed across students.
        self._pseudo_random_pools: Optional[Dict[int, List[Dict[str, Any]]]] = None
        self._pseudo_random_indices: Dict[int, int] = {}
        self._pseudo_random_initialized: bool = False

    def _build_pseudo_random_pools(self, max_pool_size: int = 1_000_000) -> None:
        """Pre-compute and shuffle all value combinations for each equation.

        Raises:
            ValueError: if an equation has more than max_pool_size combinations.
        """
        self._pseudo_random_pools = {}
        self._pseudo_random_indices = {}
        var_names = sorted(self.definition.variables)
        allowed_lists = [self.definition.variables[var].get_allowed_values() for var in var_names]
        total = 1
        for values in allowed_lists:
            total *= len(values)
        if total > max_pool_size:
            raise ValueError(
                f'Too many OneUn value combinations ({total}) for pseudo_random mode; max is {max_pool_size}'
            )
        combos = [dict(zip(var_names, values)) for values in itertools.product(*allowed_lists)]
        random.shuffle(combos)
        self._pseudo_random_pools[1] = combos
        self._pseudo_random_indices[1] = 0

    def generate_for_student(self, seed: Optional[int] = None) -> List[Problem]:
        """Generate one Problem for a single student, with a value for every
        variable in the definition.

        Values are drawn from a shuffled pool of all allowed value combinations
        so that, within the same ProblemGenerator, no combination is reused until
        the pool is exhausted.  A separate ProblemGenerator is created for each
        student, so values are independent (effectively random) across students.

        Args:
            seed: Random seed used to shuffle the pool on first call.

        Returns:
            A single-element list containing the generated Problem.
        """
        if not self._pseudo_random_initialized:
            if seed is not None:
                random.seed(seed)
            self._build_pseudo_random_pools()
            self._pseudo_random_initialized = True

        pool = self._pseudo_random_pools[1]
        index = self._pseudo_random_indices[1]
        if index >= len(pool):
            # Exhausted the pool: wrap around and allow repetition.
            index = index % len(pool)
        given_values = dict(pool[index])
        self._pseudo_random_indices[1] = index + 1
        return [Problem(given_values=given_values, equation_index=1)]


# ---------------------------------------------------------------------------
# Plot Generator
# ---------------------------------------------------------------------------

class PlotGenerator:
    """Generates PNG plots for a single equation.

    Supports linear and logarithmic/exponential functions.
    X and Y variables are chosen by the caller; all other variables are
    held at the values supplied in `fixed_values`.
    """

    def generate(self, equation: Equation,
                 x_var: str, y_var: str,
                 fixed_values: Dict[str, Any],
                 definition: 'ProblemDefinition',
                 output_path: str,
                 use_gridlines: bool = True,
                 log_x: bool = False,
                 log_y: bool = False,
                 n_points: int = 200) -> str:
        """Generate a PNG plot and save it.

        Args:
            equation: Equation to plot
            x_var: $-prefixed variable name for the X axis
            y_var: $-prefixed variable name for the Y axis
            fixed_values: Values for all variables not on X or Y axes
            definition: ProblemDefinition (for axis labels)
            output_path: Path to save the PNG
            use_gridlines: Whether to draw grid lines
            log_x: Use log scale on X axis
            log_y: Use log scale on Y axis
            n_points: Number of points to sample

        Returns:
            Path to saved PNG
        """
        import numpy as np
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        x_def = definition.variables.get(x_var)
        y_def = definition.variables.get(y_var)

        # Build X range from variable definition
        if x_def and x_def.var_type != 'str':
            x_min = float(x_def.v_min)
            x_max = float(x_def.v_max)
        else:
            x_min, x_max = 1.0, 10.0

        if log_x:
            x_min = max(x_min, 1e-10)
            xs = np.logspace(np.log10(x_min), np.log10(x_max), n_points)
        else:
            xs = np.linspace(x_min, x_max, n_points)

        # Evaluate Y for each X
        ys = []
        for x_val in xs:
            all_vals = dict(fixed_values)
            all_vals[x_var] = float(x_val)
            try:
                y_val = self._eval_for_y(equation, y_var, all_vals)
                ys.append(float(y_val))
            except Exception:
                ys.append(float('nan'))

        ys = np.array(ys)

        # Build labels
        x_label = x_def.var_name if x_def else x_var.lstrip('$')
        if x_def and x_def.var_name_short_list:
            x_label = f"{x_def.var_name_short_list[0]}"
        y_label = y_def.var_name if y_def else y_var.lstrip('$')
        if y_def and y_def.var_name_short_list:
            y_label = f"{y_def.var_name_short_list[0]}"

        fig, ax = plt.subplots(figsize=(6, 4), dpi=150)
        ax.plot(xs, ys, linewidth=2, color='#1f77b4')

        if log_x:
            ax.set_xscale('log')
        if log_y:
            ax.set_yscale('log')

        ax.set_xlabel(x_label, fontsize=12)
        ax.set_ylabel(y_label, fontsize=12)
        ax.grid(use_gridlines, linestyle='--', alpha=0.5)

        # Build title from fixed values (abbreviated)
        fixed_parts = []
        for k, v in sorted(fixed_values.items()):
            vdef = definition.variables.get(k)
            short = vdef.var_name_short_list[0] if (vdef and vdef.var_name_short_list) else k.lstrip('$')
            if isinstance(v, float):
                fixed_parts.append(f"{short}={v:g}")
            else:
                fixed_parts.append(f"{short}={v}")
        if fixed_parts:
            ax.set_title(', '.join(fixed_parts), fontsize=8)

        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close(fig)
        return output_path

    def _eval_for_y(self, equation: Equation, y_var: str,
                    all_vals: Dict[str, Any]) -> float:
        """Evaluate the equation for y_var given all other values.

        Handles two forms:
          - y_var = <expr>    → evaluate RHS
          - <expr> = y_var    → evaluate LHS
        For embedded cases, assumes the equation can be rearranged to
        y_var = RHS and evaluates RHS with x substituted.
        """
        expr = equation.expression
        if '=' not in expr:
            raise ValueError("Equation has no '='")

        lhs, rhs = expr.split('=', 1)
        lhs, rhs = lhs.strip(), rhs.strip()
        y_clean = y_var.lstrip('$')

        # Case 1: y_var = RHS
        if lhs == y_var or lhs == y_clean:
            eval_eq = Equation(index=equation.index, raw=equation.raw,
                               expression=rhs, variables=equation.variables)
            return EquationParser.evaluate(eval_eq, all_vals)

        # Case 2: LHS = y_var
        if rhs == y_var or rhs == y_clean:
            eval_eq = Equation(index=equation.index, raw=equation.raw,
                               expression=lhs, variables=equation.variables)
            return EquationParser.evaluate(eval_eq, all_vals)

        # Case 3: y_var is embedded — evaluate whichever side doesn't contain it
        if y_var not in lhs and y_clean not in lhs:
            eval_eq = Equation(index=equation.index, raw=equation.raw,
                               expression=lhs, variables=equation.variables)
            return EquationParser.evaluate(eval_eq, all_vals)
        if y_var not in rhs and y_clean not in rhs:
            eval_eq = Equation(index=equation.index, raw=equation.raw,
                               expression=rhs, variables=equation.variables)
            return EquationParser.evaluate(eval_eq, all_vals)

        raise ValueError(f"Cannot isolate {y_var} in equation: {expr}")


# ---------------------------------------------------------------------------
# Template Processor
# ---------------------------------------------------------------------------

# Regex for placeholder names: {{answer}}, {{answer_1}}, {{answer_1a; 2}}, {{graph}}, {{graph_2}}
# Optional semicolon and trailing content (e.g. point value) is captured but ignored.
_PLACEHOLDER_RE = re.compile(r'\{\{(answer|graph)(?:_(\d+[A-Za-z]*))?(?:\s*;\s*([^}]+))?\}\}', re.IGNORECASE)


class TemplateProcessor:
    """Copies an ODT template and substitutes both:

    * {{answer}} / {{graph}} placeholder frames, and
    * $Variable tokens in the body text and tables with values drawn for the
      current student.

    Placeholder frames: insert a text box (Insert → Frame) and put a string
    such as ``{{graph_1}}`` or ``{{answer_1a}}`` as the sole content.

    Variables in text/tables: simply type the variable name from the .txt file
    (e.g. ``$Kin`` or ``$T1``).  The processor replaces it with the generated
    value, matched by exact name only.  To get an independently-drawn value for
    a second question, define a separate variable (e.g. ``$T1`` and ``$T2``)
    rather than reusing one name.
    """

    # Regex for $VarName tokens in text
    _VAR_RE = re.compile(r'\$[A-Za-z_][A-Za-z0-9_]*')
    # Regex for #ConstName tokens in text
    _CONST_RE = re.compile(r'#[A-Za-z_][A-Za-z0-9_]*')

    def process(self, template_path: str, output_path: str,
                problems: Optional[List[Problem]] = None,
                definition: Optional[ProblemDefinition] = None,
                graph_images: Optional[Dict[str, str]] = None,
                metadata: Optional[Dict] = None,
                student_code: str = '',
                student_name: str = '',
                section_code: str = '',
                answer_key: bool = False) -> str:
        """Process a template ODT, substituting variables and placeholders.

        Args:
            template_path: Path to the ODT template file
            output_path: Destination ODT path
            problems: List of generated Problem instances for the current student
            definition: Parsed problem definition (for variable metadata)
            graph_images: Mapping from placeholder key (e.g. 'graph',
                'graph_1') to PNG file path. Keys are lowercase without braces.
            metadata: Dict with keys: doc_type, course, instructors, quiz_date.
                      When present a header (title + info line + signature) is
                      prepended to the document body.
            student_code: Student identifier inserted into the header.

        Returns:
            Path to the written output file
        """
        import shutil
        import zipfile
        import tempfile
        from lxml import etree

        graph_images = graph_images or {}
        problems = problems or []
        definition = definition or ProblemDefinition(
            equations=[], variables={}, constants={}
        )

        # Work in a temp directory
        tmp_dir = tempfile.mkdtemp()
        try:
            # Unzip the ODT
            with zipfile.ZipFile(template_path, 'r') as z:
                z.extractall(tmp_dir)

            # Parse content.xml
            content_xml = os.path.join(tmp_dir, 'content.xml')
            tree = etree.parse(content_xml)
            root = tree.getroot()

            # Collect namespace map
            ns = {
                'draw': 'urn:oasis:names:tc:opendocument:xmlns:drawing:1.0',
                'svg':  'urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0',
                'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0',
                'xlink': 'http://www.w3.org/1999/xlink',
            }

            # Parse styles.xml so we can register header styles
            styles_xml = os.path.join(tmp_dir, 'styles.xml')
            styles_tree = etree.parse(styles_xml) if os.path.exists(styles_xml) else None

            # Inject programmatic header/footer if metadata supplied
            if metadata:
                self._ensure_header_styles(root, styles_tree)
                self._inject_header(root, ns, metadata, student_code, student_name, section_code)
                self._ensure_header(styles_tree, section_code, student_name, metadata)
                self._ensure_footer(styles_tree, metadata=metadata)
                self._reduce_page_margins_for_signature(styles_tree)
                if styles_tree is not None:
                    styles_tree.write(styles_xml, xml_declaration=True,
                                      encoding='UTF-8', pretty_print=False)

            # Blank pages for ODT quizzes are now inserted at PDF-conversion time
            # (see generator_gui26._convert_odt_to_pdf), so no ODT-level blank
            # page injection is performed here.

            # Some tokens are split across inline text spans in the ODT XML.
            # Join them so the later per-node substitutions see whole tokens.
            self._normalize_token_spans(root, definition, ns)

            # Replace #Constant tokens in body text and tables
            self._substitute_constants(root, definition)

            # Replace $Variable tokens in body text and tables
            if problems:
                self._substitute_variables(root, problems, definition, ns)

            # Process all draw:frame elements
            for frame in root.iter(f"{{{ns['draw']}}}frame"):
                text_content = self._get_frame_text(frame, ns)
                m = _PLACEHOLDER_RE.match(text_content.strip())
                if not m:
                    continue

                kind = m.group(1).lower()   # 'answer' or 'graph'
                num  = m.group(2) or ''     # e.g. '' or '1'
                key  = f"{kind}_{num}" if num else kind

                if kind == 'answer':
                    if answer_key:
                        self._make_answer_key_frame(frame, ns, text_content)
                    else:
                        self._make_answer_frame(frame, ns)
                elif kind == 'graph':
                    png_path = graph_images.get(key) or graph_images.get('graph')
                    if png_path:
                        self._embed_image(frame, png_path, key, tmp_dir, ns)

            # Write content.xml back (do not pretty-print to avoid extra
            # whitespace between inline elements such as subscripts).
            tree.write(content_xml, xml_declaration=True,
                       encoding='UTF-8', pretty_print=False)

            # Re-zip to output_path
            if not output_path.endswith('.odt'):
                output_path += '.odt'
            with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zout:
                for root_dir, dirs, files in os.walk(tmp_dir):
                    for file in files:
                        file_path = os.path.join(root_dir, file)
                        arcname = os.path.relpath(file_path, tmp_dir)
                        zout.write(file_path, arcname)

        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        return output_path

    @staticmethod
    def _format_value(value: Any, var_def: Optional[VariableDef]) -> str:
        """Convert a variable value to a display string."""
        if isinstance(value, float):
            # Use enough precision and trim trailing zeros
            s = f"{value:.6g}"
            return s
        return str(value)

    # ----------------------------------------------------------------
    # Header injection (mirrors ODTQuizGenerator._add_header layout)
    # ----------------------------------------------------------------

    # ODF namespaces used for header XML construction
    _NS_STYLE = 'urn:oasis:names:tc:opendocument:xmlns:style:1.0'
    _NS_TEXT  = 'urn:oasis:names:tc:opendocument:xmlns:text:1.0'
    _NS_FO    = 'urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0'

    # Style definitions: (name, family, fo-props, text-props)
    _HEADER_STYLE_DEFS = [
        ('OneUnTitle', 'paragraph',
         {'text-align': 'center', 'margin-top': '0.1in',
          'margin-bottom': '0.05in', 'line-height': '100%'},
         {'font-family': 'Helvetica', 'font-size': '12pt', 'font-weight': 'bold'}),
        ('OneUnQuizIDSpan', 'text',
         {},
         {'font-family': 'Helvetica', 'font-size': '8pt', 'font-weight': 'normal'}),
        ('OneUnHeader', 'paragraph',
         {'text-align': 'right', 'margin-top': '0.05in',
          'margin-bottom': '0.1in', 'line-height': '100%'},
         {'font-family': 'Helvetica', 'font-size': '11pt'}),
        ('OneUnSignature', 'paragraph',
         {'margin-top': '0in', 'margin-bottom': '0.4in'},
         {'font-family': 'Helvetica', 'font-size': '11pt'}),
        ('OneUnExtraPage', 'paragraph',
         {'break-before': 'page', 'margin-top': '0in',
          'margin-bottom': '0in', 'line-height': '100%'},
         {}),
    ]

    def _ensure_header_styles(self, content_root, styles_tree) -> None:
        """Register OneUn header styles into automatic-styles of content.xml.

        Styles are added only if not already present (idempotent).
        """
        from lxml import etree
        ns_s = self._NS_STYLE
        ns_fo = self._NS_FO
        ns_text = self._NS_TEXT

        # Find automatic-styles element in content.xml
        auto_styles = content_root.find(
            f'{{{ns_s}}}automatic-styles'
        )
        if auto_styles is None:
            # Fall back: look for office:automatic-styles
            ns_office = 'urn:oasis:names:tc:opendocument:xmlns:office:1.0'
            auto_styles = content_root.find(f'{{{ns_office}}}automatic-styles')
        if auto_styles is None:
            return  # Cannot inject without finding the container

        existing = {el.get(f'{{{ns_s}}}name')
                    for el in auto_styles.iter(f'{{{ns_s}}}style')}

        for sname, sfamily, fo_props, text_props in self._HEADER_STYLE_DEFS:
            if sname in existing:
                continue
            style_el = etree.SubElement(auto_styles, f'{{{ns_s}}}style')
            style_el.set(f'{{{ns_s}}}name', sname)
            style_el.set(f'{{{ns_s}}}family', sfamily)

            if fo_props:
                if sfamily == 'paragraph':
                    pp = etree.SubElement(style_el,
                                          f'{{{ns_s}}}paragraph-properties')
                    for k, v in fo_props.items():
                        pp.set(f'{{{ns_fo}}}{k}', v)

            if text_props:
                tp = etree.SubElement(style_el, f'{{{ns_s}}}text-properties')
                for k, v in text_props.items():
                    if k == 'font-family':
                        tp.set(f'{{{ns_s}}}font-name', v)
                    else:
                        tp.set(f'{{{ns_fo}}}{k}', v)

    def _inject_header(self, root, ns: Dict, metadata: Dict,
                       student_code: str, student_name: str = '',
                       section_code: str = '') -> None:
        """Prepend title / info / signature paragraphs to the document body.

        Matches the layout of ODTQuizGenerator._add_header exactly:
          Line 1 (bold 12pt centred):  <doc_type>   <student_code>
          Line 2 (11pt):               Course: …  Instructor: …  Student: …  Date: …
          Line 3 (11pt):               Signature: ___…
        """
        from lxml import etree
        ns_text = self._NS_TEXT
        ns_style = self._NS_STYLE

        doc_type      = metadata.get('doc_type', 'Quiz')
        course        = metadata.get('course', '')
        instructors   = metadata.get('instructors', '')
        quiz_date     = metadata.get('quiz_date', '')
        start_question = metadata.get('start_question')
        start_page     = metadata.get('start_page')

        if not isinstance(instructors, str):
            instructors = ', '.join(instructors or [])
        instructors = instructors.strip("[]'\" ")

        display_name = student_name or student_code

        # Keep multi-word names/course on one line; allow breaks between fields.
        def _nbsp(s: str) -> str:
            return str(s).replace(' ', '\xa0')

        # Locate office:body / office:text
        ns_office = 'urn:oasis:names:tc:opendocument:xmlns:office:1.0'
        body = root.find(f'{{{ns_office}}}body')
        if body is None:
            return
        text_el = body.find(f'{{{ns_office}}}text')
        if text_el is None:
            return

        def _p(style_name: str) -> 'etree._Element':
            el = etree.Element(f'{{{ns_text}}}p')
            el.set(f'{{{ns_text}}}style-name', style_name)
            return el

        def _span(style_name: str, text: str) -> 'etree._Element':
            el = etree.Element(f'{{{ns_text}}}span')
            el.set(f'{{{ns_text}}}style-name', style_name)
            el.text = text
            return el

        # --- Signature line ---
        sig_p = _p('OneUnSignature')
        sig_p.text = ('Signature: _______________________________'
                      '          Date: _______________________________')
        text_el.insert(0, sig_p)

    def _ensure_footer(self, styles_tree, metadata=None) -> None:
        """Add a centered page-number footer to the first master page."""
        if styles_tree is None:
            return
        from lxml import etree
        ns_s = self._NS_STYLE
        ns_fo = self._NS_FO
        ns_text = self._NS_TEXT
        ns_office = 'urn:oasis:names:tc:opendocument:xmlns:office:1.0'

        root = styles_tree.getroot()
        auto_styles = root.find(f'{{{ns_office}}}automatic-styles')
        if auto_styles is None:
            return

        # Ensure a centered footer paragraph style exists.
        existing = {el.get(f'{{{ns_s}}}name')
                    for el in auto_styles.iter(f'{{{ns_s}}}style')}
        if 'OneUnFooter' not in existing:
            style_el = etree.SubElement(auto_styles, f'{{{ns_s}}}style')
            style_el.set(f'{{{ns_s}}}name', 'OneUnFooter')
            style_el.set(f'{{{ns_s}}}family', 'paragraph')
            pp = etree.SubElement(style_el, f'{{{ns_s}}}paragraph-properties')
            pp.set(f'{{{ns_fo}}}text-align', 'center')

        master_styles = root.find(f'{{{ns_office}}}master-styles')
        if master_styles is None:
            return
        master = master_styles.find(f'{{{ns_s}}}master-page')
        if master is None:
            return

        # Replace any existing footer on this master page.
        footer = master.find(f'{{{ns_s}}}footer')
        if footer is not None:
            master.remove(footer)
        footer = etree.SubElement(master, f'{{{ns_s}}}footer')
        p = etree.SubElement(footer, f'{{{ns_text}}}p')
        p.set(f'{{{ns_text}}}style-name', 'OneUnFooter')

    def _ensure_header(self, styles_tree, section_code: str,
                       student_name: str, metadata: Dict) -> None:
        """Add a right-justified running header to the first master page."""
        if styles_tree is None:
            return
        from lxml import etree
        ns_s = self._NS_STYLE
        ns_fo = self._NS_FO
        ns_text = self._NS_TEXT
        ns_office = 'urn:oasis:names:tc:opendocument:xmlns:office:1.0'

        root = styles_tree.getroot()
        auto_styles = root.find(f'{{{ns_office}}}automatic-styles')
        if auto_styles is None:
            return

        # Ensure a right-justified running header paragraph style exists.
        existing = {el.get(f'{{{ns_s}}}name')
                    for el in auto_styles.iter(f'{{{ns_s}}}style')}
        if 'OneUnRunningHeader' not in existing:
            style_el = etree.SubElement(auto_styles, f'{{{ns_s}}}style')
            style_el.set(f'{{{ns_s}}}name', 'OneUnRunningHeader')
            style_el.set(f'{{{ns_s}}}family', 'paragraph')
            pp = etree.SubElement(style_el, f'{{{ns_s}}}paragraph-properties')
            pp.set(f'{{{ns_fo}}}text-align', 'right')
            pp.set(f'{{{ns_fo}}}margin-top', '0in')
            pp.set(f'{{{ns_fo}}}margin-bottom', '0in')
            tp = etree.SubElement(style_el, f'{{{ns_s}}}text-properties')
            tp.set(f'{{{ns_fo}}}font-family', 'Helvetica')
            tp.set(f'{{{ns_fo}}}font-size', '11pt')

        master_styles = root.find(f'{{{ns_office}}}master-styles')
        if master_styles is None:
            return
        master = master_styles.find(f'{{{ns_s}}}master-page')
        if master is None:
            return

        # Replace any existing header on this master page.
        header = master.find(f'{{{ns_s}}}header')
        if header is not None:
            master.remove(header)
        header = etree.SubElement(master, f'{{{ns_s}}}header')
        p = etree.SubElement(header, f'{{{ns_text}}}p')
        p.set(f'{{{ns_text}}}style-name', 'OneUnRunningHeader')

        quiz_date = metadata.get('quiz_date', '')
        if section_code:
            header_text = (
                f"Student:\xa0{student_name}, "
                f"Section:\xa0{section_code}, "
                f"created:\xa0{quiz_date}"
            )
        else:
            header_text = (
                f"Student:\xa0{student_name}, "
                f"created:\xa0{quiz_date}"
            )
        p.text = header_text.replace(' ', '\xa0')

    def _reduce_page_margins_for_signature(self, styles_tree) -> None:
        """Shrink top/bottom page margins so the prepended signature line fits
        on page 1 without pushing template content onto an extra blank page.
        """
        if styles_tree is None:
            return
        from lxml import etree
        ns_s = self._NS_STYLE
        ns_fo = self._NS_FO

        root = styles_tree.getroot()
        for pl in root.iter(f'{{{ns_s}}}page-layout-properties'):
            for attr in ('margin-top', 'margin-bottom'):
                full = f'{{{ns_fo}}}{attr}'
                val = pl.get(full)
                if val is None:
                    continue
                # Only shrink values known to be at least 0.6in; ignore others.
                if val.endswith('in'):
                    try:
                        if float(val[:-2]) > 0.6:
                            pl.set(full, '0.6in')
                    except ValueError:
                        pass

    def _normalize_token_spans(self, root, definition: ProblemDefinition, ns: Dict):
        """Merge inline text spans that split known variable/constant tokens.

        Writer can store a single token such as ``$Naout`` as ``$Na`` in the
        parent paragraph and ``out`` in a styled ``<text:span>``.  This pass
        joins such runs so the later per-node substitutions see the complete
        token.  The inline span that carried the suffix is emptied.
        """
        known = set(definition.variables.keys()) | set(definition.constants.keys())
        if not known:
            return

        token_re = re.compile('|'.join(map(re.escape, sorted(known, key=len, reverse=True))))
        text_ns = ns.get('text', 'urn:oasis:names:tc:opendocument:xmlns:text:1.0')
        block_p = f'{{{text_ns}}}p'
        block_h = f'{{{text_ns}}}h'

        def walk(elem, include_tail=True):
            if elem.text is not None:
                yield elem, 'text', elem.text
            for child in elem:
                yield from walk(child, True)
            if include_tail and elem.tail is not None:
                yield elem, 'tail', elem.tail

        for block in list(root.iter(block_p, block_h)):
            while True:
                segments = list(walk(block, include_tail=False))
                full = ''.join(text for _, _, text in segments)
                segment_starts = []
                pos = 0
                for _, _, text in segments:
                    segment_starts.append(pos)
                    pos += len(text)

                split_match = None
                for m in token_re.finditer(full):
                    s, e = m.start(), m.end()
                    i = j = None
                    a = b = 0
                    for idx, start in enumerate(segment_starts):
                        seg_end = start + len(segments[idx][2])
                        if i is None and start <= s < seg_end:
                            i = idx
                            a = s - start
                        if j is None and start <= e <= seg_end:
                            j = idx
                            b = e - start
                            break
                    if i is not None and j is not None and i != j:
                        split_match = (i, j, a, b, m.group(0))
                        break

                if split_match is None:
                    break

                i, j, a, b, token = split_match
                seg_i_elem, seg_i_attr, seg_i_text = segments[i]
                seg_j_elem, seg_j_attr, seg_j_text = segments[j]
                prefix = seg_i_text[:a]
                suffix = seg_j_text[b:]
                setattr(seg_i_elem, seg_i_attr, prefix + token)
                for k in range(i + 1, j):
                    elem_k, attr_k, _ = segments[k]
                    setattr(elem_k, attr_k, '')
                setattr(seg_j_elem, seg_j_attr, suffix)

    def _substitute_constants(self, root, definition: ProblemDefinition):
        """Replace #ConstName tokens in text nodes with their fixed values.

        Raises ValueError if a #token is encountered that is not in the
        [CONSTANTS] table.
        """
        def _replace(text: str) -> str:
            def repl(m):
                token = m.group(0)      # e.g. "#F"
                if token not in definition.constants:
                    raise ValueError(
                        f"Template uses constant token '{token}' which is not "
                        f"defined in [CONSTANTS]."
                    )
                cd = definition.constants[token]
                val = cd.value
                if isinstance(val, float):
                    return f"{val:.6g}"
                return str(val)
            return self._CONST_RE.sub(repl, text)

        for elem in root.iter():
            if elem.text and '#' in elem.text:
                elem.text = _replace(elem.text)
            if elem.tail and '#' in elem.tail:
                elem.tail = _replace(elem.tail)

    def _substitute_variables(self, root, problems: List[Problem],
                              definition: ProblemDefinition, ns: Dict):
        """Replace $VarName tokens in text nodes with generated values."""
        # Build a value lookup table keyed by exact variable name.
        exact_values: Dict[str, Any] = {}
        for p in problems:
            for var_name, val in p.given_values.items():
                if var_name not in exact_values:
                    exact_values[var_name] = val

        def lookup(token: str) -> Tuple[Optional[Any], Optional[VariableDef]]:
            """Return (value, var_def) for a $Variable token, or (None, None)."""
            if token in exact_values:
                return exact_values[token], definition.variables.get(token)
            return None, None

        missing_tokens: set = set()
        for elem in root.iter():
            for attr in ('text', 'tail'):
                text = getattr(elem, attr, None)
                if not text:
                    continue

                def repl(match: re.Match) -> str:
                    token = match.group(0)
                    value, var_def = lookup(token)
                    if value is None:
                        missing_tokens.add(token)
                        return token
                    return self._format_value(value, var_def)

                new_text = self._VAR_RE.sub(repl, text)
                setattr(elem, attr, new_text)

        if missing_tokens:
            print(f"OneUn template warning: no generated value for variables: "
                  f"{sorted(missing_tokens)}")


    @staticmethod
    def _get_frame_text(frame, ns: Dict) -> str:
        """Extract all text content from a draw:frame."""
        paragraphs = []
        for paragraph in frame.iter(f"{{{ns['text']}}}p"):
            paragraphs.append(''.join(paragraph.itertext()))
        return '\n'.join(paragraphs)

    @staticmethod
    def _evaluate_answer_expression(expression: str) -> str:
        normalized = re.sub(r'\broot2\(', 'math.sqrt(', expression)
        normalized = re.sub(r'\bln\(', 'math.log(', normalized)
        normalized = re.sub(r'\blog10\(', 'math.log10(', normalized)
        normalized = re.sub(r'\blog2\(', 'math.log2(', normalized)
        try:
            node = ast.parse(normalized, mode='eval')
            unit = ''
        except SyntaxError as error:
            # The expression may be followed by a unit, e.g. "(1+2) V". Try to
            # split off the trailing token(s) and evaluate the numeric prefix.
            offset = getattr(error, 'offset', 0) or 0
            if offset <= 0:
                raise ValueError(f'Invalid computed answer expression {expression!r}: {error.msg}')
            prefix = normalized[:offset - 1].rstrip()
            unit = normalized[offset - 1:].strip()
            if not prefix or not unit:
                raise ValueError(f'Invalid computed answer expression {expression!r}: {error.msg}')
            try:
                node = ast.parse(prefix, mode='eval')
            except SyntaxError as inner:
                raise ValueError(f'Invalid computed answer expression {expression!r}: {inner.msg}')

        allowed = (
            ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Call,
            ast.Attribute, ast.Name, ast.Load, ast.Add, ast.Sub, ast.Mult,
            ast.Div, ast.FloorDiv, ast.Mod, ast.Pow, ast.UAdd, ast.USub,
        )
        allowed_functions = {'sqrt', 'log', 'log10', 'log2'}
        for item in ast.walk(node):
            if not isinstance(item, allowed) or isinstance(item, ast.Constant) and not isinstance(item.value, (int, float)):
                raise ValueError(f'Computed answer expressions may contain only numeric arithmetic: {expression!r}')
            if isinstance(item, ast.Name) and item.id != 'math':
                raise ValueError(f'Computed answer expressions may contain only numeric arithmetic: {expression!r}')
            if isinstance(item, ast.Attribute) and (
                not isinstance(item.value, ast.Name)
                or item.value.id != 'math'
                or item.attr not in allowed_functions
            ):
                raise ValueError(f'Computed answer expressions may contain only numeric arithmetic: {expression!r}')
            if isinstance(item, ast.Call) and (
                item.keywords
                or len(item.args) != 1
                or not isinstance(item.func, (ast.Name, ast.Attribute))
            ):
                raise ValueError(f'Computed answer functions require one numeric argument: {expression!r}')
        result = eval(compile(node, '<oneun-answer>', 'eval'), {'__builtins__': {}, 'math': math}, {})
        # Round final numeric answer to 3 decimal places, trim trailing zeros.
        try:
            numeric = float(result)
            value = f"{numeric:.3f}".rstrip('0').rstrip('.')
        except (TypeError, ValueError):
            value = str(result)
        return f"{value} {unit}".strip() if unit else value

    @classmethod
    def _render_answer_line(cls, answer: str) -> str:
        if answer.startswith('='):
            return cls._evaluate_answer_expression(answer[1:].strip())
        return answer

    @classmethod
    def _make_answer_key_frame(cls, frame, ns: Dict, text_content: str):
        lines = [line.strip() for line in text_content.splitlines() if line.strip()]
        answer_lines = lines[1:]
        if not answer_lines:
            raise ValueError('An answer-key frame must contain its {{answer...}} name followed by one or two answer lines.')
        if len(answer_lines) > 2:
            raise ValueError('An answer-key frame may contain at most two answer lines after its {{answer...}} name.')
        rendered = [cls._render_answer_line(line) for line in answer_lines]
        cls._set_frame_text(frame, ns, rendered)

    @staticmethod
    def _set_frame_text(frame, ns: Dict, values: List[str]):
        text_ns = ns['text']
        paragraphs = list(frame.iter(f"{{{text_ns}}}p"))
        if not paragraphs:
            return
        first = paragraphs[0]
        for paragraph in paragraphs:
            for sub in list(paragraph):
                paragraph.remove(sub)
            paragraph.text = None
        for i, value in enumerate(values):
            if i < len(paragraphs):
                paragraphs[i].text = value
            else:
                new_p = deepcopy(first)
                for sub in list(new_p):
                    new_p.remove(sub)
                new_p.text = value
                paragraphs[-1].addnext(new_p)
                paragraphs.append(new_p)

    @staticmethod
    def _make_answer_frame(frame, ns: Dict):
        """Clear a frame's text content to make an empty answer box."""
        draw_ns = ns['draw']
        text_ns = ns['text']
        for child in list(frame):
            tag = child.tag
            if tag in (f"{{{draw_ns}}}text-box",
                       f"{{{text_ns}}}p"):
                # Remove all text children, leave frame structure
                for p in list(child.iter(f"{{{text_ns}}}p")):
                    for sub in list(p):
                        p.remove(sub)
                    p.text = None

    @staticmethod
    def _embed_image(frame, png_path: str, key: str,
                     tmp_dir: str, ns: Dict):
        """Replace a frame's text-box content with an embedded image."""
        import shutil as _shutil
        draw_ns = ns['draw']
        xlink_ns = ns['xlink']
        svg_ns   = ns['svg']

        # Copy PNG into ODT Pictures/ folder
        pictures_dir = os.path.join(tmp_dir, 'Pictures')
        os.makedirs(pictures_dir, exist_ok=True)
        img_filename = f"{key}.png"
        dest = os.path.join(pictures_dir, img_filename)
        _shutil.copy2(png_path, dest)
        href = f"Pictures/{img_filename}"

        # Remove existing text-box children
        for child in list(frame):
            frame.remove(child)

        # Insert draw:image
        img_elem = etree.SubElement(frame, f"{{{draw_ns}}}image")
        img_elem.set(f"{{{xlink_ns}}}href", href)
        img_elem.set(f"{{{xlink_ns}}}type", "simple")
        img_elem.set(f"{{{xlink_ns}}}show", "embed")
        img_elem.set(f"{{{xlink_ns}}}actuate", "onLoad")


# ---------------------------------------------------------------------------
# ODT Output Generator
# ---------------------------------------------------------------------------

class OneUnODTGenerator:
    """Generates ODT quiz files by filling a template with problem values.

    The template is an ODT file created in LibreOffice Writer containing:
    - Question text and layout (fully formatted)
    - Named text frames with {{answer}}, {{answer_1}} … placeholders for
      student input regions
    - Named text frames with {{graph}}, {{graph_1}} … placeholders where
      generated PNG plots will be inserted
    """

    def __init__(self):
        self.template_processor = TemplateProcessor()
        self.plot_generator = PlotGenerator()

    def generate_quiz(self, definition: Optional[ProblemDefinition],
                      template_path: str,
                      output_path: str = 'oneun_quiz.odt',
                      student_codes: Optional[List[str]] = None,
                      quiz_metadata: Optional[Dict] = None,
                      plot_config: Optional[Dict] = None,
                      output_ids: Optional[Dict[str, str]] = None,
                      answer_key_template_path: Optional[str] = None,
                      answer_key_output_ids: Optional[Dict[str, str]] = None,
                      student_names: Optional[Dict[str, str]] = None,
                      student_section_codes: Optional[Dict[str, str]] = None,
                      base_seed: Optional[int] = None,
                      start_question: Optional[int] = None,
                      start_page: Optional[int] = None,
                      attempts: int = 1,
                      return_values: bool = False) -> Union[List[str], Tuple[List[str], List[str], Dict[str, List[Dict]]]]:
        """Generate ODT quiz files per student and attempt, and write a summary log.

        One Problem instance is generated per equation/attempt in the definition.
        The number of output files equals student_codes * attempts.

        Args:
            definition: Parsed ProblemDefinition (from .txt file)
            template_path: Path to the ODT template (required)
            output_path: Base output file path; student code and attempt are
                         appended for each generated file.
            student_codes: List of student code strings.  One ODT is
                           produced per entry.  Pass [''] for a single
                           generic output.
            quiz_metadata: Dict with keys: course, instructors, quiz_date
            plot_config: Dict with keys:
                - include_graph (bool)
                - equation_index (int, 1-based, selects which equation's
                  problem instance is plotted)
                - x_var (str, $-prefixed)
                - y_var (str, $-prefixed)
                - use_gridlines (bool)
                - log_x (bool)
                - log_y (bool)
            base_seed: If given, per-student seeds are derived as
                       base_seed + student_index.  Each attempt uses an
                       incremented seed so no two attempts for the same
                       student use the same random state.
            attempts: Number of quiz variants to generate per student.
            return_values: If True, also return a mapping of student code to
                           a list of the drawn variable value dicts (one per attempt).

        Returns:
            List of paths to the generated ODT files, or a tuple of
            (main_files, answer_key_files, student_odt_values) if
            return_values is True.  When return_values is False, main and
            answer-key files are concatenated in the returned list.
        """
        import tempfile
        import shutil as _shutil
        from datetime import datetime

        if not template_path or not os.path.exists(template_path):
            raise ValueError(f"Template file not found: {template_path}")
        if answer_key_template_path and not os.path.exists(answer_key_template_path):
            raise ValueError(f"Answer-key template file not found: {answer_key_template_path}")

        metadata = dict(quiz_metadata) if quiz_metadata else {}
        if start_question is not None:
            metadata['start_question'] = start_question
        if start_page is not None:
            metadata['start_page'] = start_page
        plot_cfg = plot_config or {}
        include_graph = plot_cfg.get('include_graph', False)
        student_codes = student_codes or ['']
        output_ids = output_ids or {}
        answer_key_output_ids = answer_key_output_ids or {}

        if definition is None:
            definition = ProblemDefinition(
                equations=[], variables={}, constants={})

        out_stem = os.path.splitext(output_path)[0]
        base_dir = os.path.dirname(os.path.abspath(output_path))
        os.makedirs(base_dir, exist_ok=True)

        attempts = max(1, int(attempts))
        generated_files: List[str] = []
        answer_key_files: List[str] = []
        student_seeds: Dict[str, Any] = {}
        odt_values: Dict[str, List[Dict]] = {}

        for idx, student_code in enumerate(student_codes):
            # Derive a per-student seed
            if base_seed is not None:
                student_seed = base_seed + idx
            else:
                student_seed = None
            student_seeds[student_code or f'generic_{idx}'] = student_seed

            # Each student gets a fresh ProblemGenerator so attempts for that
            # student do not reuse values until the value pool is exhausted.
            generator = ProblemGenerator(definition)

            section_code = (student_section_codes or {}).get(student_code, '')

            for attempt in range(1, attempts + 1):
                attempt_seed = (student_seed + (attempt - 1)
                                if student_seed is not None else None)

                # Generate the Problem (one value per variable) for this attempt
                problems = generator.generate_for_student(seed=attempt_seed)
                odt_values.setdefault(student_code or f'generic_{idx}', []).append(
                    dict(problems[0].given_values)
                )

                # Build graph images dict: graph_N -> png_path for each equation
                tmp_dir = tempfile.mkdtemp()
                graph_images: Dict[str, str] = {}
                try:
                    if include_graph:
                        eq_index = plot_cfg.get('equation_index', 1)
                        x_var = plot_cfg.get('x_var', '')
                        y_var = plot_cfg.get('y_var', '')
                        equation = self._get_equation(definition, eq_index)

                        if equation and x_var and y_var:
                            if (x_var in equation.variables and
                                    y_var in equation.variables):
                                # Find the matching problem instance
                                prob = next(
                                    (p for p in problems
                                     if p.equation_index == eq_index),
                                    problems[0]
                                )
                                fixed = {k: v for k, v in prob.given_values.items()
                                         if k != x_var and k != y_var}
                                png_path = os.path.join(tmp_dir, f"graph_{eq_index}.png")
                                try:
                                    self.plot_generator.generate(
                                        equation=equation,
                                        x_var=x_var, y_var=y_var,
                                        fixed_values=fixed,
                                        definition=definition,
                                        output_path=png_path,
                                        use_gridlines=plot_cfg.get('use_gridlines', True),
                                        log_x=plot_cfg.get('log_x', False),
                                        log_y=plot_cfg.get('log_y', False),
                                    )
                                    graph_images[f'graph_{eq_index}'] = png_path
                                    graph_images['graph'] = png_path
                                except Exception as e:
                                    print(f"Warning: plot failed for student "
                                          f"'{student_code}' attempt {attempt}: {e}")

                    attempt_suffix = f"_A{attempt}" if attempts > 1 else ""

                    fname_stem = os.path.basename(out_stem)
                    output_id = output_ids.get(student_code)
                    if output_id:
                        out_file = os.path.join(base_dir, f"{output_id}{attempt_suffix}.odt")
                    elif student_code:
                        out_file = os.path.join(
                            base_dir, f"{fname_stem}_{student_code}{attempt_suffix}.odt"
                        )
                    else:
                        out_file = os.path.join(base_dir, f"{fname_stem}{attempt_suffix}.odt")

                    student_name = (student_names or {}).get(student_code, student_code)

                    self.template_processor.process(
                        template_path=template_path,
                        output_path=out_file,
                        problems=problems,
                        definition=definition,
                        graph_images=graph_images,
                        metadata=metadata if metadata else None,
                        student_code=output_id or student_code or '',
                        student_name=student_name or '',
                        section_code=section_code,
                    )
                    generated_files.append(out_file)

                    if answer_key_template_path:
                        answer_key_id = answer_key_output_ids.get(student_code)
                        if answer_key_id:
                            answer_key_file = os.path.join(base_dir, f"{answer_key_id}{attempt_suffix}.odt")
                        elif student_code:
                            answer_key_file = os.path.join(base_dir, f"{fname_stem}_{student_code}_answer_key{attempt_suffix}.odt")
                        else:
                            answer_key_file = os.path.join(base_dir, f"{fname_stem}_answer_key{attempt_suffix}.odt")
                        answer_key_metadata = dict(metadata)
                        answer_key_metadata['doc_type'] = f"{metadata.get('doc_type', 'Quiz')} Answer Key"
                        self.template_processor.process(
                            template_path=answer_key_template_path,
                            output_path=answer_key_file,
                            problems=problems,
                            definition=definition,
                            graph_images=graph_images,
                            metadata=answer_key_metadata,
                            student_code=output_id or student_code or '',
                            student_name=student_name or '',
                            section_code=section_code,
                            answer_key=True,
                        )
                    answer_key_files.append(answer_key_file)

                finally:
                    _shutil.rmtree(tmp_dir, ignore_errors=True)

        # Write summary log
        log_path = f"{out_stem}_summary.txt"
        self._write_summary_log(
            log_path=log_path,
            definition_path=None,
            template_path=template_path,
            output_files=generated_files + answer_key_files,
            student_seeds=student_seeds,
            metadata=metadata,
            plot_config=plot_cfg,
            generated_at=datetime.now().isoformat(timespec='seconds')
        )
        print(f"Summary log written: {log_path}")

        if return_values:
            return generated_files, answer_key_files, odt_values
        return generated_files + answer_key_files

    @staticmethod
    def _write_summary_log(log_path: str,
                           definition_path: Optional[str],
                           template_path: str,
                           output_files: List[str],
                           student_seeds: Dict[str, Any],
                           metadata: Dict,
                           plot_config: Dict,
                           generated_at: str):
        """Write a plain-text summary log for the generation run."""
        lines = [
            "=" * 72,
            "OneUn Generation Summary",
            f"Generated at : {generated_at}",
            "=" * 72,
            "",
            "Input files",
            "-----------",
            f"  Definition : {definition_path or '(not recorded)'}",
            f"  Template   : {template_path}",
            "",
            "Parameters",
            "----------",
            f"  Course     : {metadata.get('course', '')}",
            f"  Instructors: {metadata.get('instructors', '')}",
            f"  Quiz date  : {metadata.get('quiz_date', '')}",
        ]
        if plot_config.get('include_graph'):
            lines += [
                f"  Graph      : equation {plot_config.get('equation_index', 1)}, "
                f"X={plot_config.get('x_var', '')}, Y={plot_config.get('y_var', '')}",
            ]
        lines += [
            "",
            "Output files and seeds",
            "----------------------",
            "  (Re-use the same seed for a student to reproduce their quiz.)",
            "  (Use a different seed to generate a new non-repeating version.)",
            "",
        ]
        for student, seed in student_seeds.items():
            seed_str = str(seed) if seed is not None else "none (not reproducible)"
            lines.append(f"  {student:<20}  seed={seed_str}")
        lines += [
            "",
            "Generated files",
            "---------------",
        ]
        for f in output_files:
            lines.append(f"  {f}")
        lines.append("")

        with open(log_path, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(lines) + '\n')

    @staticmethod
    def _get_equation(definition: 'ProblemDefinition', eq_index: int) -> Optional['Equation']:
        """Return the equation matching eq_index (1-based), or first if not found.

        TODO: `definition.equations` is always empty now that equations are no
        longer parsed from the .txt file, so this always returns None and
        graphing is currently a no-op. The plot equation should instead be
        parsed from the {{graph_*}} placeholder's own specification in the ODT
        template. Not yet implemented.
        """
        if not definition.equations:
            return None
        for eq in definition.equations:
            if eq.index == eq_index:
                return eq
        return definition.equations[0]


# ---------------------------------------------------------------------------
# Convenience API
# ---------------------------------------------------------------------------

def load_problem_definition(filepath: str) -> ProblemDefinition:
    """Load a problem definition from a text file."""
    return ProblemDefinitionParser.parse_file(filepath)



