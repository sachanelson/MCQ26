"""
Quantitative Question Bank Module

This module provides a framework for creating quantitative, non-multiple choice questions
with a focus on calculations and equations. The first implementation includes Nernst
equation problems for calculating equilibrium potentials.
"""

import random
import math
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass


# Physical constants
GAS_CONSTANT = 8.314  # J/(mol·K) - Universal gas constant
FARADAY_CONSTANT = 96485  # C/mol - Faraday constant
TEMPERATURE_CELSIUS_TO_KELVIN = 273.15


@dataclass
class NernstParameters:
    """Parameters for Nernst equation calculations."""
    ion_concentration_in: float  # mM (millimolar)
    ion_concentration_out: float  # mM (millimolar)
    valence: int  # Ion charge (+1, +2, -1, etc.)
    temperature_celsius: float  # °C


class NernstEquationGenerator:
    """
    Generates Nernst equation problems with realistic parameters.
    """
    
    def __init__(self):
        """Initialize the Nernst equation generator."""
        # Common physiological ion concentrations (mM)
        self.typical_concentrations = {
            'K+': {'in': (120, 150), 'out': (3, 6)},      # Potassium
            'Na+': {'in': (5, 15), 'out': (135, 150)},    # Sodium
            'Cl-': {'in': (4, 10), 'out': (110, 125)},    # Chloride
            'Ca2+': {'in': (0.0001, 0.1), 'out': (1, 3)}  # Calcium
        }
        
        # Common temperature ranges
        self.temperature_ranges = {
            'room_temp': (20, 25),
            'body_temp': (36, 38),
            'cold': (4, 10),
            'warm': (30, 35)
        }
        
    def generate_parameters(self, ion: str = 'K+', temperature_type: str = 'body_temp',
                           concentration_variation: float = 0.2) -> NernstParameters:
        """
        Generate realistic Nernst equation parameters.
        
        Args:
            ion: Ion type ('K+', 'Na+', 'Cl-', 'Ca2+')
            temperature_type: Type of temperature ('body_temp', 'room_temp', etc.)
            concentration_variation: Allowed variation from typical values (0.0 to 1.0)
            
        Returns:
            NernstParameters object with generated values
        """
        # Get concentration ranges
        if ion not in self.typical_concentrations:
            ion = 'K+'  # Default to potassium
            
        conc_ranges = self.typical_concentrations[ion]
        in_range = conc_ranges['in']
        out_range = conc_ranges['out']
        
        # Generate concentrations with variation
        def vary_concentration(base_range, variation):
            center = (base_range[0] + base_range[1]) / 2
            half_range = (base_range[1] - base_range[0]) / 2
            new_half_range = half_range * (1 + variation)
            new_min = max(0.001, center - new_half_range)
            new_max = center + new_half_range
            return self._generate_clean_number(new_min, new_max)
        
        conc_in = vary_concentration(in_range, concentration_variation)
        conc_out = vary_concentration(out_range, concentration_variation)
        
        # Get temperature
        if temperature_type not in self.temperature_ranges:
            temperature_type = 'body_temp'
        temp_range = self.temperature_ranges[temperature_type]
        temperature = self._generate_clean_number(temp_range[0], temp_range[1])
        
        # Determine valence
        valence_map = {'K+': 1, 'Na+': 1, 'Cl-': -1, 'Ca2+': 2}
        valence = valence_map.get(ion, 1)
        
        return NernstParameters(
            ion_concentration_in=conc_in,
            ion_concentration_out=conc_out,
            valence=valence,
            temperature_celsius=temperature
        )
    
    def _generate_clean_number(self, min_val: float, max_val: float,
                              max_decimal_places: int = 2) -> float:
        """
        Generate a number that's easy to work with (avoid excessive decimals).

        Args:
            min_val: Minimum value
            max_val: Maximum value
            max_decimal_places: Maximum decimal places allowed

        Returns:
            Clean number within the range
        """
        # Try to generate numbers with 0, 1, or 2 decimal places
        for decimal_places in range(max_decimal_places + 1):
            multiplier = 10 ** decimal_places
            min_int = int(min_val * multiplier)
            max_int = int(max_val * multiplier)

            if min_int < max_int:
                # Avoid returning 0 when the minimum is positive (e.g., tiny Ca2+ concentrations)
                if min_int == 0 and min_val > 0:
                    min_int = 1
                value_int = random.randint(min_int, max_int)
                return value_int / multiplier

        # Fallback: generate any number in range and round
        return round(random.uniform(min_val, max_val), max_decimal_places)
    
    def calculate_equilibrium_potential(self, params: NernstParameters) -> float:
        """
        Calculate the equilibrium potential using the Nernst equation.
        
        Nernst equation: E = (R * T) / (z * F) * ln([ion]out / [ion]in)
        
        Args:
            params: NernstParameters object
            
        Returns:
            Equilibrium potential in volts
        """
        # Convert temperature to Kelvin
        temp_kelvin = params.temperature_celsius + TEMPERATURE_CELSIUS_TO_KELVIN
        
        # Calculate the Nernst equation
        # E = (R * T) / (z * F) * ln([ion]out / [ion]in)
        ratio = params.ion_concentration_out / params.ion_concentration_in
        if ratio <= 0:
            raise ValueError("Invalid concentration ratio")
            
        # Natural logarithm
        ln_ratio = math.log(ratio)
        
        # Calculate potential in volts
        potential_volts = (GAS_CONSTANT * temp_kelvin) / (params.valence * FARADAY_CONSTANT) * ln_ratio
        
        # Convert to millivolts
        potential_mv = potential_volts * 1000
        
        return potential_mv
    
    def format_nernst_equation(self, params: NernstParameters) -> str:
        """
        Format the Nernst equation with the given parameters.
        
        Args:
            params: NernstParameters object
            
        Returns:
            Formatted equation string
        """
        temp_kelvin = params.temperature_celsius + TEMPERATURE_CELSIUS_TO_KELVIN
        
        equation = (f"E = ({GAS_CONSTANT} × {temp_kelvin:.1f} K) / "
                   f"({params.valence} × {FARADAY_CONSTANT}) × "
                   f"ln({params.ion_concentration_out:.1f} / {params.ion_concentration_in:.1f})")
        
        return equation
    
    def generate_question(self, ion: str = 'K+', temperature_type: str = 'body_temp',
                         difficulty: str = 'medium') -> Dict:
        """
        Generate a complete Nernst equation question.
        
        Args:
            ion: Ion type
            temperature_type: Temperature condition
            difficulty: Difficulty level ('easy', 'medium', 'hard')
            
        Returns:
            Dictionary containing the question components
        """
        # Adjust variation based on difficulty
        variation_map = {'easy': 0.1, 'medium': 0.2, 'hard': 0.4}
        concentration_variation = variation_map.get(difficulty, 0.2)
        
        # Generate parameters for all four ions so the table can list them all.
        all_ions = ['K+', 'Na+', 'Cl-', 'Ca2+']
        ion_params = {ion_name: self.generate_parameters(ion_name, temperature_type,
                                                        concentration_variation)
                      for ion_name in all_ions}

        # The target ion determines which equilibrium potential the student calculates.
        target_params = ion_params[ion]
        equilibrium_potential = self.calculate_equilibrium_potential(target_params)

        # Format the equation for the target ion
        equation = self.format_nernst_equation(target_params)

        # Build a table of concentrations for all four ions
        def fmt_conc(value):
            # 4 sig figs preserves tiny Ca2+ internal values without adding trailing zeros
            return f"{value:.4g} mM"

        all_ions_data = [
            {
                'ion': ion_name,
                'ion_in': fmt_conc(params.ion_concentration_in),
                'ion_out': fmt_conc(params.ion_concentration_out)
            }
            for ion_name, params in ion_params.items()
        ]

        # Create question components
        question = {
            'type': 'nernst_equation',
            'difficulty': difficulty,
            'ion': ion,
            'parameters': target_params,
            'all_ions': all_ions_data,
            'equilibrium_potential_mv': equilibrium_potential,
            'equation': equation,
            'stem': (f"Calculate the equilibrium potential for {ion} ions across the "
                     f"cell membrane given the following values:"),
            'given_info': (f"Given: [{ion}]in = {fmt_conc(target_params.ion_concentration_in)}, "
                          f"[{ion}]out = {fmt_conc(target_params.ion_concentration_out)}, "
                          f"temperature = {target_params.temperature_celsius:.1f}°C, "
                          f"valence = {target_params.valence:+d}"),
            'subquestions': [
                {
                    'letter': 'a',
                    'text': 'Write the Nernst equation and identify all variables:',
                    'has_answer_box': True,
                    'answer_type': 'equation'  # Extra large for equation writing
                },
                {
                    'letter': 'b',
                    'text': 'Substitute the given values into the equation:',
                    'has_answer_box': True,
                    'answer_type': 'medium'  # Medium for substitution
                },
                {
                    'letter': 'c',
                    'text': 'Calculate the final equilibrium potential (show your work):',
                    'has_answer_box': True,
                    'answer_type': 'long'  # Large for detailed calculations
                },
                {
                    'letter': 'd',
                    'text': f'What is the equilibrium potential in mV? (Round to 1 decimal place)',
                    'has_answer_box': True,
                    'answer_type': 'short'  # Small for final numerical answer
                }
            ],
            'answer': f"{equilibrium_potential:.1f} mV"
        }
        
        return question


class QuantitativeQuestionBank:
    """
    Main interface for the quantitative question bank system.
    """
    
    def __init__(self):
        """Initialize the question bank."""
        self.nernst_generator = NernstEquationGenerator()
        self.question_types = {
            'nernst_equation': self.nernst_generator.generate_question
        }
        
    def generate_question_set(self, num_questions: int, 
                            question_types: List[str] = None,
                            difficulty: str = 'medium') -> List[Dict]:
        """
        Generate a set of quantitative questions.
        
        Args:
            num_questions: Number of questions to generate
            question_types: List of question types (defaults to nernst_equation)
            difficulty: Difficulty level for all questions
            
        Returns:
            List of question dictionaries
        """
        if question_types is None:
            question_types = ['nernst_equation']
            
        questions = []
        
        for i in range(num_questions):
            # Select question type
            q_type = random.choice(question_types)
            
            # Generate question
            if q_type in self.question_types:
                generator = self.question_types[q_type]
                
                # Vary parameters for diversity
                if q_type == 'nernst_equation':
                    ions = ['K+', 'Na+', 'Cl-', 'Ca2+']
                    temp_types = ['body_temp', 'room_temp', 'warm']
                    
                    question = generator(
                        ion=random.choice(ions),
                        temperature_type=random.choice(temp_types),
                        difficulty=difficulty
                    )
                else:
                    question = generator(difficulty=difficulty)
                
                questions.append(question)
                
        return questions
    
    def get_question_preview(self, question: Dict) -> str:
        """
        Get a formatted preview of a question.
        
        Args:
            question: Question dictionary
            
        Returns:
            Formatted question string
        """
        preview = f"Question: {question['stem']}\n"
        preview += f"Given: {question['given_info']}\n\n"
        
        for subq in question['subquestions']:
            preview += f"{subq['letter']}) {subq['text']}\n"
            
        preview += f"\nAnswer: {question['answer']}"
        
        return preview


def test_question_bank():
    """Test the quantitative question bank."""
    print("Testing Quantitative Question Bank")
    print("=" * 40)
    
    # Create question bank
    bank = QuantitativeQuestionBank()
    
    # Generate sample questions
    questions = bank.generate_question_set(
        num_questions=3,
        difficulty='medium'
    )
    
    # Display questions
    for i, question in enumerate(questions, 1):
        print(f"\nQuestion {i}:")
        print(bank.get_question_preview(question))
        print("-" * 40)


if __name__ == "__main__":
    test_question_bank()
