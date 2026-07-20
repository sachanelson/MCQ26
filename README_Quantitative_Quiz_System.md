# Quantitative Quiz System

A comprehensive ODT-based quiz generation system for quantitative problems, designed to complement the existing MCQ PDF-based system. This system creates quizzes that require students to fill in equations and calculations rather than selecting multiple-choice answers.

## Overview

The Quantitative Quiz System generates OpenDocument Text (.odt) files with:
- Header/footer components that mimic the existing MCQ PDF system
- Questions with numbered stems and lettered sub-sections
- Answer boxes for equations and calculations
- Support for quantitative problems like Nernst equation calculations

## Features

### Document Generation
- **ODT Format**: Creates OpenDocument Text files compatible with LibreOffice, OpenOffice, etc.
- **Header Components**: Quiz type, quiz ID, course info, instructors, student, date, signature line
- **Calibration Marks**: Three square markers for alignment (similar to PDF system)
- **No QR Codes**: Simplified design without QR code functionality

### Question Types
- **Nernst Equation Problems**: Calculate equilibrium potentials for ions
- **Quantitative Calculations**: Multi-step problems with answer boxes
- **Expandable Framework**: Easy to add new question types

### User Interface
- **Qt-based GUI**: Modern graphical interface for quiz configuration
- **Question Preview**: Real-time preview of generated questions
- **Batch Generation**: Create multiple questions at once
- **Progress Tracking**: Visual progress indicators during document generation

## System Architecture

### Core Modules

1. **`odt_quiz_generator.py`** - Base ODT document generation
   - Creates ODT documents with proper formatting
   - Handles header/footer components
   - Manages styles and layout

2. **`quantitative_question_bank.py`** - Question generation engine
   - Nernst equation problem generator
   - Parameter selection with constraints
   - Realistic physiological values

3. **`quantitative_quiz_gui.py`** - Graphical user interface
   - Qt-based interface for quiz configuration
   - Question preview and management
   - Background document generation

4. **`quiz_integration.py`** - Integration with MCQ system
   - Shared quiz ID generation
   - Course information management
   - Statistics and metadata

5. **`run_quantitative_quiz.py`** - Main entry point
   - Command-line interface
   - Dependency checking
   - Sample quiz generation

## Installation

### Prerequisites
- Python 3.7 or higher
- pip package manager

### Setup

1. **Create virtual environment** (recommended):
   ```bash
   python -m venv MCQ26
   source MCQ26/bin/activate  # On Windows: MCQ26\Scripts\activate
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements_quantitative.txt
   ```

3. **Verify installation**:
   ```bash
   python run_quantitative_quiz.py --check
   ```

## Usage

### Command Line Interface

#### Launch GUI
```bash
python run_quantitative_quiz.py --gui
```

#### Create Sample Quiz
```bash
python run_quantitative_quiz.py --sample
```

#### Check Dependencies
```bash
python run_quantitative_quiz.py --check
```

### GUI Usage

1. **Configure Quiz Information**:
   - Quiz Type (Quiz, Answer Key, Extra Page)
   - Course name and instructors
   - Student name and quiz ID
   - Date

2. **Set Question Parameters**:
   - Number of questions
   - Question type (Nernst Equation, Mixed)
   - Difficulty level (Easy, Medium, Hard)

3. **Generate Questions**:
   - Click "Generate Questions" to create question set
   - Preview questions in the preview pane
   - Review all questions in the "All Questions" tab

4. **Create Quiz Document**:
   - Click "Create Quiz Document" to generate ODT file
   - Monitor progress with the progress bar
   - Document opens automatically if option is selected

### Programmatic Usage

```python
from odt_quiz_generator import ODTQuizGenerator
from quantitative_question_bank import QuantitativeQuestionBank
from quiz_integration import SharedQuizComponents

# Initialize components
shared = SharedQuizComponents()
question_bank = QuantitativeQuestionBank()
generator = ODTQuizGenerator()

# Create quiz metadata
metadata = shared.create_quiz_metadata(
    student_name="John Doe",
    course_code="BIOL26",
    quiz_type="Quiz"
)

# Generate questions
questions = question_bank.generate_question_set(
    num_questions=5,
    question_types=['nernst_equation'],
    difficulty='medium'
)

# Create document
generator.create_document(**metadata)

# Add questions
for i, question in enumerate(questions, 1):
    generator.add_question(
        number=i,
        stem=question['stem'],
        subquestions=question['subquestions']
    )

# Save document
filename = generator.save_document("my_quiz")
```

## Question Types

### Nernst Equation Problems

The system generates Nernst equation problems with realistic parameters:

**Question Structure**:
- Main stem: Calculate equilibrium potential for specific ion
- Subquestion a: Write the Nernst equation
- Subquestion b: Substitute given values
- Subquestion c: Show calculation work
- Subquestion d: Final answer in mV

**Parameters**:
- Ion concentrations (intracellular/extracellular)
- Temperature (room temp, body temp, etc.)
- Ion valence (+1, +2, -1, etc.)
- Physiological ranges for realistic values

**Example Question**:
```
Calculate the equilibrium potential for K+ ions across a cell membrane.

Given: [K+]in = 140.0 mM, [K+]out = 5.0 mM, temperature = 37.0°C, valence = +1

a) Write the Nernst equation and identify all variables:
   [Answer Box]

b) Substitute the given values into the equation:
   [Answer Box]

c) Calculate the final equilibrium potential (show your work):
   [Answer Box]

d) What is the equilibrium potential in mV? (Round to 1 decimal place)
   [Answer Box]
```

## Integration with MCQ System

### Shared Components

1. **Quiz ID Generation**: Compatible with existing MCQ quiz ID format
2. **Course Information**: Shared course database
3. **Header/Footer Layout**: Mimics PDF system styling
4. **Calibration Marks**: Same positioning as PDF system

### Quiz ID Format

The system generates quiz IDs in the format: `{student_code}{module_num}_{type}{number}`

Example: `JD26_Q001`
- `JD` - Student code (John Doe)
- `26` - Module number (BIOL 26)
- `Q` - Quiz type (Q=Quiz, A=Answer Key)
- `001` - Sequential quiz number

## File Structure

```
MCQ26/
├── odt_quiz_generator.py          # ODT document generation
├── quantitative_question_bank.py  # Question generation engine
├── quantitative_quiz_gui.py       # Qt-based GUI
├── quiz_integration.py            # MCQ system integration
├── run_quantitative_quiz.py       # Main entry point
├── requirements_quantitative.txt  # Python dependencies
├── README_Quantitative_Quiz_System.md  # This file
└── MCQ26/                         # Virtual environment
```

## Dependencies

### Required
- **odfpy** (>=1.4.1) - ODT file generation
- **PyQt6** (>=6.0.0) - GUI framework

### Optional
- **matplotlib** (>=3.5.0) - Enhanced equation rendering
- **pillow** (>=8.0.0) - Image processing

## Graphics Format

**PNG** is the recommended graphics format for ODT integration:
- Universal support across ODT viewers
- Good quality for equations and diagrams
- Easy to generate and embed
- Reliable cross-platform compatibility

## Troubleshooting

### Common Issues

1. **"odfpy not available"**:
   ```bash
   pip install odfpy
   ```

2. **"PyQt6 not available"**:
   ```bash
   pip install PyQt6
   ```

3. **GUI won't start**:
   - Check PyQt6 installation
   - Ensure virtual environment is activated
   - Try running without GUI: `python run_quantitative_quiz.py --sample`

4. **ODT file won't open**:
   - Use LibreOffice or OpenOffice
   - Ensure file has .odt extension
   - Check file permissions

### Debug Mode

Enable debug output by setting environment variable:
```bash
export QUANTITATIVE_QUIZ_DEBUG=1
python run_quantitative_quiz.py --gui
```

## Development

### Adding New Question Types

1. **Create generator class** in `quantitative_question_bank.py`
2. **Add to question bank** with `generate_question()` method
3. **Update GUI** to include new question type
4. **Test with sample questions**

### Extending ODT Generation

1. **Add new styles** in `_create_styles()` method
2. **Create new layout methods** for special formatting
3. **Add graphics support** using PNG format
4. **Test with LibreOffice compatibility**

## Future Enhancements

- **Additional Question Types**: pH calculations, osmolarity, etc.
- **Enhanced Graphics**: Equation rendering, diagrams
- **Database Integration**: Store questions and metadata
- **Batch Processing**: Generate multiple quizzes at once
- **Template System**: Customizable quiz templates
- **Export Options**: PDF, HTML, other formats

## License

This system complements the existing MCQ system and follows the same licensing terms.

## Support

For issues and questions:
1. Check this README for common solutions
2. Verify all dependencies are installed
3. Test with sample quiz generation
4. Review debug output for error details
