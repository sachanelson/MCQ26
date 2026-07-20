"""
Quiz Integration Module

This module provides integration between the new quantitative quiz system
and the existing MCQ system, allowing for shared functionality like
quiz ID generation and course information.
"""

import sys
import os
from typing import Dict, Optional, Tuple

# Add the MCQ system path to import existing functions
MCQ_PATH = "/Users/sacha/textProcessing/bubbleSheet/MCQ"
if MCQ_PATH not in sys.path:
    sys.path.append(MCQ_PATH)

try:
    from quiz_generator import parse_qID
    MCQ_AVAILABLE = True
except ImportError:
    print("Warning: MCQ system not available for integration")
    MCQ_AVAILABLE = False


class QuizIDGenerator:
    """
    Generates quiz IDs compatible with the existing MCQ system.
    """
    
    def __init__(self):
        """Initialize the quiz ID generator."""
        self.student_codes = {}
        self.quiz_counters = {}
        
    def generate_quiz_id(self, student_name: str, module_num: int, 
                        quiz_type: str = "Q") -> str:
        """
        Generate a quiz ID in the format {student_code}{module_num}_{type}{number}
        
        Args:
            student_name: Student name
            module_num: Module number (e.g., 26 for BIOL 26)
            quiz_type: Quiz type (Q for quiz, A for answer key, etc.)
            
        Returns:
            Formatted quiz ID
        """
        # Get or create student code
        student_code = self.get_student_code(student_name)
        
        # Format module number (2 digits)
        module_str = f"{module_num:02d}"
        
        # Get next quiz number for this student/module
        quiz_key = f"{student_code}_{module_str}"
        if quiz_key not in self.quiz_counters:
            self.quiz_counters[quiz_key] = 1
        else:
            self.quiz_counters[quiz_key] += 1
            
        quiz_num = self.quiz_counters[quiz_key]
        
        # Format quiz ID
        quiz_id = f"{student_code}{module_str}_{quiz_type}{quiz_num:03d}"
        
        return quiz_id
        
    def get_student_code(self, student_name: str) -> str:
        """
        Generate or retrieve a student code from the student name.
        
        Args:
            student_name: Full student name
            
        Returns:
            Student code (typically 2-4 characters)
        """
        if student_name in self.student_codes:
            return self.student_codes[student_name]
            
        # Generate code from initials
        names = student_name.strip().split()
        if len(names) >= 2:
            # First initial + last name initial
            code = names[0][0].upper() + names[-1][0].upper()
        else:
            # Use first two letters of name
            code = student_name[:2].upper()
            
        # Ensure uniqueness
        original_code = code
        counter = 1
        while code in self.student_codes.values():
            code = f"{original_code}{counter}"
            counter += 1
            
        self.student_codes[student_name] = code
        return code
        
    def parse_quiz_id(self, quiz_id: str) -> Tuple[int, int]:
        """
        Parse a quiz ID to extract module and quiz numbers.
        
        Args:
            quiz_id: Quiz ID string
            
        Returns:
            Tuple of (module_num, quiz_index)
        """
        if MCQ_AVAILABLE:
            return parse_qID(quiz_id)
        else:
            # Fallback parsing
            try:
                parts = quiz_id.split('_')
                if len(parts) >= 2:
                    # Extract module from first part (last 2 digits)
                    first_part = parts[0]
                    module_num = int(first_part[-2:])
                    
                    # Extract quiz number from second part
                    second_part = parts[1]
                    quiz_num = ''.join(c for c in second_part if c.isdigit())
                    quiz_index = int(quiz_num)
                    
                    return module_num, quiz_index
            except:
                pass
                
            return 0, 0


class CourseInfoManager:
    """
    Manages course information for quiz generation.
    """
    
    def __init__(self):
        """Initialize the course info manager."""
        self.courses = {
            'BIOL26': {
                'name': 'BIOL 26 - Physiology',
                'instructors': ['Dr. Smith', 'Dr. Johnson'],
                'default_student_prefix': 'Student',
                'module_number': 26
            },
            'BIOL25': {
                'name': 'BIOL 25 - Anatomy',
                'instructors': ['Dr. Williams', 'Dr. Brown'],
                'default_student_prefix': 'Student',
                'module_number': 25
            }
        }
        
    def get_course_info(self, course_code: str) -> Dict:
        """
        Get course information by code.
        
        Args:
            course_code: Course code (e.g., 'BIOL26')
            
        Returns:
            Dictionary with course information
        """
        return self.courses.get(course_code, {})
        
    def get_all_courses(self) -> Dict:
        """Get all available courses."""
        return self.courses
        
    def add_course(self, course_code: str, course_info: Dict):
        """
        Add a new course to the system.
        
        Args:
            course_code: Course code
            course_info: Course information dictionary
        """
        self.courses[course_code] = course_info
        
    def format_instructors(self, instructors) -> str:
        """
        Format instructor list for display.
        
        Args:
            instructors: List of instructor names or string
            
        Returns:
            Formatted instructor string
        """
        if isinstance(instructors, list):
            return ', '.join(instructors)
        return str(instructors)


class SharedQuizComponents:
    """
    Provides shared components between MCQ and quantitative quiz systems.
    """
    
    def __init__(self):
        """Initialize shared components."""
        self.id_generator = QuizIDGenerator()
        self.course_manager = CourseInfoManager()
        
    def create_quiz_metadata(self, student_name: str, course_code: str,
                           quiz_type: str = "Quiz") -> Dict:
        """
        Create complete quiz metadata.
        
        Args:
            student_name: Student name
            course_code: Course code
            quiz_type: Type of quiz
            
        Returns:
            Dictionary with quiz metadata
        """
        course_info = self.course_manager.get_course_info(course_code)
        
        if not course_info:
            raise ValueError(f"Unknown course code: {course_code}")
            
        # Generate quiz ID
        module_num = course_info.get('module_number', 26)
        quiz_id = self.id_generator.generate_quiz_id(
            student_name, module_num, quiz_type[0].upper()
        )
        
        # Format instructors
        instructors = self.course_manager.format_instructors(
            course_info.get('instructors', [])
        )
        
        return {
            'quiz_id': quiz_id,
            'course': course_info.get('name', course_code),
            'instructors': instructors,
            'student': student_name,
            'quiz_type': quiz_type,
            'module_number': module_num
        }
        
    def get_quiz_statistics(self, quiz_ids: list) -> Dict:
        """
        Get statistics for a set of quizzes.
        
        Args:
            quiz_ids: List of quiz IDs
            
        Returns:
            Statistics dictionary
        """
        stats = {
            'total_quizzes': len(quiz_ids),
            'modules': {},
            'students': set()
        }
        
        for quiz_id in quiz_ids:
            module_num, quiz_index = self.id_generator.parse_quiz_id(quiz_id)
            
            if module_num not in stats['modules']:
                stats['modules'][module_num] = []
            stats['modules'][module_num].append(quiz_index)
            
            # Extract student code (first part before module number)
            parts = quiz_id.split('_')
            if parts:
                first_part = parts[0]
                if len(first_part) >= 2:
                    student_code = first_part[:-2]
                    stats['students'].add(student_code)
                    
        stats['unique_students'] = len(stats['students'])
        
        return stats


def test_integration():
    """Test the integration components."""
    print("Testing Quiz Integration Components")
    print("=" * 40)
    
    # Create shared components
    shared = SharedQuizComponents()
    
    # Test quiz ID generation
    print("1. Testing Quiz ID Generation:")
    quiz_id = shared.id_generator.generate_quiz_id("John Doe", 26, "Q")
    print(f"   Generated Quiz ID: {quiz_id}")
    
    parsed = shared.id_generator.parse_quiz_id(quiz_id)
    print(f"   Parsed: Module {parsed[0]}, Quiz {parsed[1]}")
    
    # Test course info
    print("\n2. Testing Course Information:")
    course_info = shared.course_manager.get_course_info('BIOL26')
    print(f"   Course: {course_info.get('name')}")
    print(f"   Instructors: {course_info.get('instructors')}")
    
    # Test quiz metadata creation
    print("\n3. Testing Quiz Metadata Creation:")
    metadata = shared.create_quiz_metadata("Jane Smith", "BIOL26", "Quiz")
    for key, value in metadata.items():
        print(f"   {key}: {value}")
        
    # Test statistics
    print("\n4. Testing Quiz Statistics:")
    quiz_ids = [
        shared.id_generator.generate_quiz_id("John Doe", 26, "Q"),
        shared.id_generator.generate_quiz_id("Jane Smith", 26, "Q"),
        shared.id_generator.generate_quiz_id("Bob Johnson", 25, "A")
    ]
    stats = shared.get_quiz_statistics(quiz_ids)
    print(f"   Total quizzes: {stats['total_quizzes']}")
    print(f"   Unique students: {stats['unique_students']}")
    print(f"   Modules: {list(stats['modules'].keys())}")


if __name__ == "__main__":
    test_integration()
