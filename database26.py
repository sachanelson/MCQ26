"""
Database module for MCQ26 system.

Schema: CourseInfo + CourseSection + SemesterCalendar + Classroom + Module + Student +
        Quiz + StudentModuleProgress + QuizSession + QuizSessionDefault.
Database file: MCQ26/db26/mcq_system26.db
"""
import os
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import json
from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, Float, ForeignKey, Integer, MetaData, String, Text,
    UniqueConstraint, desc, event, func, create_engine, text
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, relationship, Session, sessionmaker, scoped_session

logger = logging.getLogger(__name__)

# Database location: MCQ26/db26/mcq_system26.db
DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'db26',
    'mcq_system26.db'
)

NUM_MODULES = 19

# Quiz session types
SESSION_TYPE_CLASS   = 'class'
SESSION_TYPE_SECTION = 'section'
SESSION_TYPE_EXTRA   = 'extra'

# Classrooms seeded from the old MCQ system (utils.py / room_config.py)
DEFAULT_CLASSROOMS = [
    {'name': 'Abelson 131',   'capacity': 115},
    {'name': 'Bassine 208',   'capacity':  25},
    {'name': 'Bassine 251',   'capacity':  30},
    {"name": "G'zang 121",   'capacity': 115},
    {"name": "G'zang 122",   'capacity': 105},
    {"name": "G'zang 124",   'capacity':  90},
    {'name': 'SSC 109',       'capacity':  16},
    {'name': 'SSC 103',       'capacity':  26},
    {'name': 'Volen 119',     'capacity':  40},
    {'name': 'Volen 106',     'capacity':  24},
    {'name': 'Goldsmith 317', 'capacity':  60},
]

# Default dummy students used until a real roster is imported.
DEFAULT_STUDENTS = [
    {'student_code': 'StA', 'name': 'Stu Adent', 'pronouns': '', 'email': '', 'academic_level': 'UG', 'program_of_study': ''},
    {'student_code': 'StB', 'name': 'Stu Bdent', 'pronouns': '', 'email': '', 'academic_level': 'UG', 'program_of_study': ''},
    {'student_code': 'StC', 'name': 'Stu Cdent', 'pronouns': '', 'email': '', 'academic_level': 'UG', 'program_of_study': ''},
    {'student_code': 'StD', 'name': 'Stu Ddent', 'pronouns': '', 'email': '', 'academic_level': 'UG', 'program_of_study': ''},
]

Base = declarative_base()


# ---------------------------------------------------------------------------
# ORM Models
# ---------------------------------------------------------------------------

class CourseInfo(Base):
    """Course-level configuration (one row expected)."""
    __tablename__ = 'course_info'

    id              = Column(Integer, primary_key=True)
    course          = Column(String,  nullable=False, default='NBIO140B')
    year            = Column(Integer, nullable=True,  default=2026)
    semester        = Column(String(2), nullable=True, default='F')     # e.g. F, S, Su
    course_title    = Column(String,  nullable=True,  default='Principles of Neuroscience')
    instructors     = Column(String,  nullable=True,  default='Sacha Nelson')  # comma-separated
    course_folder            = Column(String,  nullable=False, default='')       # base filesystem path
    moodle_url               = Column(String,  nullable=True,  default='')
    min_signup_time          = Column(Integer, nullable=True,  default=24)       # hours
    min_cancel_time          = Column(Integer, nullable=True,  default=24)       # hours
    first_segment_count      = Column(Integer, nullable=True,  default=4)        # number of required first-segment modules
    max_attempts_per_module  = Column(Integer, nullable=True,  default=4)        # default max attempts per module
    passing_threshold        = Column(Float,   nullable=True, default=65.0)         # minimum score to pass a quiz
    completion_threshold     = Column(Float,   nullable=True, default=90.0)         # minimum score to complete a module
    # Class meeting info (whole-course level)
    class_days       = Column(String,  nullable=True, default='T,Th')   # comma-separated: M,T,W,Th,F
    class_start_time = Column(String,  nullable=True, default='15:55')  # HH:MM 24-hr
    class_end_time   = Column(String,  nullable=True, default='17:15')  # HH:MM 24-hr
    class_classroom  = Column(String,  nullable=True, default='')       # e.g. Abelson-Bass 131
    num_sections     = Column(Integer, nullable=True, default=0)

    def __repr__(self):
        return f"<CourseInfo(course='{self.course}', folder='{self.course_folder}')>"


class Module(Base):
    """Represents one of the {NUM_MODULES} course modules."""
    __tablename__ = 'modules'

    number   = Column(Integer, primary_key=True)   # 1-based (1 … NUM_MODULES)
    name     = Column(String,  nullable=False, default='')
    readings = Column(String,  nullable=True,  default='')

    def __repr__(self):
        return f"<Module(number={self.number}, name='{self.name}')>"


class CourseSection(Base):
    """One row per course section."""
    __tablename__ = 'course_sections'

    id            = Column(Integer, primary_key=True)
    section_number = Column(Integer, nullable=False, unique=True)  # e.g. 1,2,4,5,6
    code          = Column(String,  nullable=True, default='')     # short section code
    day_of_week   = Column(String,  nullable=True)   # M/T/W/Th/F
    start_time    = Column(String,  nullable=True)   # HH:MM 24-hr
    end_time      = Column(String,  nullable=True)   # HH:MM 24-hr
    classroom     = Column(String,  nullable=True)
    ta_instructor = Column(String,  nullable=True)
    comment       = Column(String,  nullable=True)
    # JSON-encoded list of [month, day] pairs, e.g. [[9,5],[9,12], ...]
    meeting_dates_json = Column(Text, nullable=True, default='[]')

    def __repr__(self):
        return f"<CourseSection(number={self.section_number}, day='{self.day_of_week}')>"


class SemesterCalendar(Base):
    """Semester calendar used to compute actual meeting dates.

    One row per semester (keyed by course_info.year + semester).
    Brandeis days are stored as JSON list of {"date": "YYYY-MM-DD", "substitute": "T"} objects.
    No-class days are stored as JSON list of "YYYY-MM-DD" strings.
    """
    __tablename__ = 'semester_calendar'

    id              = Column(Integer, primary_key=True)
    year            = Column(Integer, nullable=False)
    semester        = Column(String(2), nullable=False)   # F/S/Su
    first_day       = Column(String,  nullable=True)      # YYYY-MM-DD
    last_day        = Column(String,  nullable=True)      # YYYY-MM-DD (last day of classes)
    end_of_semester = Column(String,  nullable=True)      # YYYY-MM-DD (end of finals / semester)
    no_class_days_json   = Column(Text, nullable=True, default='[]')   # JSON list of YYYY-MM-DD
    brandeis_days_json   = Column(Text, nullable=True, default='[]')   # JSON list of {date, substitute}

    __table_args__ = (
        UniqueConstraint('year', 'semester', name='uq_year_semester'),
    )

    def __repr__(self):
        return f"<SemesterCalendar(year={self.year}, semester='{self.semester}')>"


class Classroom(Base):
    """A room that can host quiz sessions, with a seating capacity."""
    __tablename__ = 'classrooms'

    id       = Column(Integer, primary_key=True)
    name     = Column(String, nullable=False, unique=True)  # e.g. 'Abelson 131'
    capacity = Column(Integer, nullable=False, default=0)

    def __repr__(self):
        return f"<Classroom(name='{self.name}', capacity={self.capacity})>"


class Student(Base):
    """Represents a student in the system."""
    __tablename__ = 'students'

    student_id       = Column(Integer, primary_key=True)
    student_code     = Column(String,  unique=True)  # 3-letter quiz code
    name             = Column(String,  nullable=False)
    pronouns         = Column(String,  nullable=True)
    email            = Column(String,  nullable=True)
    academic_level   = Column(String,  nullable=True)  # e.g. UG, GRAD, TA
    program_of_study = Column(String,  nullable=True)
    section_number   = Column(Integer, nullable=True)  # FK to course_sections.section_number
    enrolled         = Column(Boolean, nullable=False, default=True)
    created_at       = Column(DateTime, default=datetime.now)

    # Relationships (mirrors old schema; signup table added later)
    module_progress = relationship("StudentModuleProgress", back_populates="student", cascade="all, delete-orphan")
    quizzes         = relationship("Quiz", back_populates="student", cascade="all, delete-orphan")
    session_signups = relationship("SessionSignup", back_populates="student", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Student(name='{self.name}', code='{self.student_code}', section={self.section_number})>"


class Quiz(Base):
    """Represents a single quiz attempt in the system."""
    __tablename__ = 'quizzes'

    id                 = Column(Integer, primary_key=True)
    student_id         = Column(Integer, ForeignKey('students.student_id', ondelete='CASCADE'), nullable=False)
    module_number      = Column(Integer, nullable=False)  # 1-based, 1..NUM_MODULES
    quiz_id            = Column(String,  nullable=False)
    date_taken         = Column(String,  nullable=False)
    time_taken         = Column(String,  nullable=True)
    date_graded        = Column(String,  nullable=True)
    time_graded        = Column(String,  nullable=True)
    date_signed_up     = Column(String,  nullable=True)
    is_passing         = Column(Integer, default=0)
    is_highest         = Column(Integer, default=0)
    score              = Column(Integer, nullable=True)  # 0-100, null = ungraded
    score_corrected    = Column(Integer, default=0)
    date_score_corrected = Column(String, nullable=True)
    moodle_updated     = Column(Integer, default=0)
    student_notified   = Column(Integer, default=0)
    comment            = Column(String, nullable=True)
    total_questions    = Column(Integer, nullable=True)
    signup_cancelled   = Column(Integer, default=0)
    has_odt            = Column(Boolean, default=False)
    odt_template_path  = Column(String, nullable=True, default='')
    odt_variable_names_json = Column(Text, nullable=True, default='[]')
    odt_variable_values_json = Column(Text, nullable=True, default='[]')
    grading_session_id = Column(Integer, ForeignKey('grading_sessions.grading_session_id', ondelete='SET NULL'), nullable=True)

    student = relationship("Student", back_populates="quizzes")

    def __repr__(self):
        return (f"<Quiz(id={self.id}, student_id={self.student_id}, "
                f"module={self.module_number}, quiz_id='{self.quiz_id}', score={self.score})>")


class QuizQuestion(Base):
    __tablename__ = 'quiz_questions'

    id                   = Column(Integer, primary_key=True)
    quiz_id              = Column(String, nullable=False, index=True)
    student_id           = Column(Integer, ForeignKey('students.student_id', ondelete='CASCADE'), nullable=False)
    module_number        = Column(Integer, nullable=False)
    question_number      = Column(Integer, nullable=False)
    question_id          = Column(String, nullable=False, index=True)
    question_text        = Column(Text, nullable=False)
    answer_choices_json  = Column(Text, nullable=False)
    correct_answer_index = Column(Integer, nullable=True)
    feedback_text        = Column(Text, nullable=True)
    context_text         = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint('quiz_id', 'question_number', name='uq_quiz_question_position'),
    )


class SectionMeeting(Base):
    __tablename__ = 'section_meetings'

    meeting_id        = Column(Integer, primary_key=True)
    section_number    = Column(Integer, ForeignKey('course_sections.section_number', ondelete='CASCADE'), nullable=False)
    meeting_date      = Column(String, nullable=False)
    start_time        = Column(String, nullable=False)
    end_time          = Column(String, nullable=True)
    meeting_sequence  = Column(Integer, nullable=False)
    title             = Column(String, nullable=True)
    worksheet_enabled = Column(Boolean, default=False, nullable=False)
    definition_path   = Column(String, nullable=True)
    template_path     = Column(String, nullable=True)
    created_at        = Column(DateTime, server_default=func.now())
    updated_at        = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('section_number', 'meeting_date', 'start_time', name='uq_section_meeting_time'),
        UniqueConstraint('meeting_sequence', name='uq_section_meeting_sequence'),
    )


class SectionMeetingGrade(Base):
    __tablename__ = 'section_meeting_grades'

    grade_id                = Column(Integer, primary_key=True)
    section_meeting_id      = Column(Integer, ForeignKey('section_meetings.meeting_id', ondelete='CASCADE'), nullable=False)
    student_id              = Column(Integer, ForeignKey('students.student_id', ondelete='CASCADE'), nullable=False)
    worksheet_id            = Column(String, nullable=True)
    score                   = Column(Integer, nullable=True)
    attendance_status       = Column(String, nullable=True)
    submission_status       = Column(String, nullable=True)
    grader                  = Column(String, nullable=True)
    graded_at               = Column(DateTime, nullable=True)
    note                    = Column(Text, nullable=True)
    submitted_work_path     = Column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint('section_meeting_id', 'student_id', name='uq_section_meeting_student'),
        CheckConstraint('score IS NULL OR score BETWEEN 0 AND 2', name='ck_section_meeting_grade_score'),
    )


class StudentModuleProgress(Base):
    """Tracks a student's progress in a specific module.

    Simplified status model: one row per student/module records whether the
    module is completed, the highest score, and the number of attempts used.
    Eligibility for the second segment is derived from completion of the first
    N modules (where N = course_info.first_segment_count).
    """
    __tablename__ = 'student_module_progress'

    id            = Column(Integer, primary_key=True)
    student_id    = Column(Integer, ForeignKey('students.student_id', ondelete='CASCADE'), nullable=False)
    module_number = Column(Integer, ForeignKey('modules.number', ondelete='CASCADE'), nullable=False)
    completed     = Column(Boolean, default=False)  # module completed?
    highest_score = Column(Float, default=0.0)
    attempts_count = Column(Integer, default=0)     # how many attempts used (max enforced elsewhere)
    last_attempt  = Column(DateTime, nullable=True)

    student = relationship("Student", back_populates="module_progress")
    module  = relationship("Module")

    __table_args__ = (
        UniqueConstraint('student_id', 'module_number', name='uq_student_module'),
        {'sqlite_autoincrement': True},
    )

    def __repr__(self):
        return (f"<StudentModuleProgress(student_id={self.student_id}, module_number={self.module_number}, "
                f"completed={self.completed}, attempts={self.attempts_count}, score={self.highest_score})>")


class QuizSession(Base):
    """Represents a scheduled quiz session (replaces the old quiz_blocks table).

    A session has a single start/end time, a room, a proctor, a capacity, and a
    type ('class', 'section', or 'extra'). 'extra' sessions are stand-alone;
    'class' and 'section' sessions are normally created from QuizSessionDefault
    templates.
    """
    __tablename__ = 'quiz_sessions'

    session_id  = Column(Integer, primary_key=True)
    session_type  = Column(String,  nullable=False, default=SESSION_TYPE_EXTRA)
    date          = Column(String,  nullable=False)  # YYYY-MM-DD
    start_time    = Column(String,  nullable=False)  # HH:MM
    end_time      = Column(String,  nullable=False)  # HH:MM
    room          = Column(String,  nullable=False)
    proctor       = Column(String,  nullable=False)
    capacity      = Column(Integer, nullable=False)
    active        = Column(Boolean, default=True)
    created_at    = Column(DateTime, server_default=func.now())
    updated_at    = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return (f"<QuizSession(id={self.session_id}, type='{self.session_type}', "
                f"date='{self.date}', time='{self.start_time}-{self.end_time}')>")


class SessionSignup(Base):
    __tablename__ = 'session_signups'

    signup_id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey('quiz_sessions.session_id', ondelete='CASCADE'), nullable=False)
    student_id = Column(Integer, ForeignKey('students.student_id', ondelete='CASCADE'), nullable=False)
    module_number = Column(Integer, nullable=False)
    quiz_id = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.now)

    student = relationship("Student", back_populates="session_signups")
    session = relationship("QuizSession")

    __table_args__ = (
        UniqueConstraint('session_id', 'student_id', 'quiz_id', name='uq_session_signup_quiz'),
    )


class GradingSession(Base):
    """Represents one grading session: grading a single scanned batch of quizzes.

    Sessions are organized by date plus a letter suffix ('a', 'b', 'c', ...)
    for multiple scans graded on the same date. The archived scan file lives
    under `<course_folder>/grading/<session_date>/<session_date><letter>/`.

    For now, the linkage between qsessions (when quizzes were administered)
    and grading sessions (when they were scanned/graded) is tracked manually
    by staff; this table only records the grading event itself.
    """
    __tablename__ = 'grading_sessions'

    grading_session_id     = Column(Integer, primary_key=True)
    session_date           = Column(String, nullable=False)   # YYYY-MM-DD
    letter                 = Column(String(1), nullable=False)
    scan_path              = Column(String, nullable=False)   # archived copy of the scan file
    original_scan_filename = Column(String, nullable=True)
    notes                  = Column(Text, nullable=True)
    created_at             = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint('session_date', 'letter', name='uq_grading_session_date_letter'),
    )

    def __repr__(self):
        return f"<GradingSession(id={self.grading_session_id}, date='{self.session_date}{self.letter}')>"


class OutgoingEmail(Base):
    __tablename__ = 'outgoing_emails'

    email_id = Column(Integer, primary_key=True)
    recipient = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    email_type = Column(String, nullable=False)
    status = Column(String, nullable=False, default='queued')
    created_at = Column(DateTime, default=datetime.now)
    sent_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)


class QuizSessionDefault(Base):
    """Default template for recurring class/section quiz sessions.

    Each type ('class' or 'section') has a default day of week, start/end
    time, room, proctor and capacity. Extra sessions do not have a template.
    """
    __tablename__ = 'quiz_session_defaults'

    default_id  = Column(Integer, primary_key=True)
    session_type = Column(String, nullable=False, unique=True)  # 'class' or 'section'
    day_of_week  = Column(Integer, nullable=True)  # 0=Monday ... 6=Sunday
    start_time   = Column(String, nullable=False)  # HH:MM
    end_time     = Column(String, nullable=False)  # HH:MM
    room         = Column(String, nullable=False)
    proctor      = Column(String, nullable=False)
    capacity     = Column(Integer, nullable=False)
    active       = Column(Boolean, default=True)

    def __repr__(self):
        return (f"<QuizSessionDefault(type='{self.session_type}', dow={self.day_of_week}, "
                f"time='{self.start_time}-{self.end_time}')>")


# ---------------------------------------------------------------------------
# Engine / session helpers
# ---------------------------------------------------------------------------

def create_db_engine(db_path: str = None) -> Engine:
    """Create a SQLAlchemy engine pointing at *db_path* (default: db26/mcq_system26.db).

    Creates the db26/ directory and all tables if they don't already exist,
    runs lightweight migrations, and seeds an empty CourseInfo row,
    NUM_MODULES Module rows, and a small set of default Student rows on first run.
    """
    if db_path is None:
        db_path = DEFAULT_DB_PATH

    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    engine = create_engine(f'sqlite:///{db_path}')

    @event.listens_for(engine, 'connect')
    def _set_fk_pragma(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()

    # Create tables
    Base.metadata.create_all(engine)

    # Migrate: add new columns / recreate tables whose schema changed
    _migrate_add_columns(engine)
    _migrate_student_module_progress(engine)
    _migrate_quiz_questions(engine)

    # Re-create tables that migrations may have dropped
    Base.metadata.create_all(engine)

    # Seed on first run
    _seed_initial_data(engine)

    return engine


def reset_quiz_data(engine: Engine) -> None:
    """Drop and recreate quiz-related tables, clearing all quiz attempts.

    This is intended for testing/resetting.  Student, question-bank and course
    data are left untouched.  Module progress is also reset.
    """
    with engine.connect() as conn:
        # Drop any leftover artifacts from earlier failed migrations first.
        conn.execute(text("DROP TABLE IF EXISTS quiz_questions_old"))
        conn.execute(text("DROP INDEX IF EXISTS ix_quiz_questions_question_id"))
        conn.execute(text("DROP INDEX IF EXISTS ix_quiz_questions_quiz_id"))
        conn.execute(text("DROP TABLE IF EXISTS quiz_questions"))
        conn.execute(text("DROP TABLE IF EXISTS quizzes"))
        conn.execute(text("DELETE FROM student_module_progress"))
        conn.commit()
    Base.metadata.create_all(engine)
    logger.info("Quiz data reset: quizzes, quiz_questions and student_module_progress cleared.")


def get_db_session(engine: Engine) -> scoped_session:
    """Return a thread-local scoped session factory."""
    return scoped_session(sessionmaker(bind=engine, autocommit=False, autoflush=False))


# ---------------------------------------------------------------------------
# Migration helpers
# ---------------------------------------------------------------------------

def _migrate_add_columns(engine: Engine) -> None:
    """Add new columns to existing tables if they don't exist (SQLite ALTER TABLE)."""
    with engine.connect() as conn:
        # --- course_info ---
        ci_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(course_info)"))}
        _add_col = lambda col, defn: conn.execute(text(f"ALTER TABLE course_info ADD COLUMN {col} {defn}"))
        if 'moodle_url' not in ci_cols:
            _add_col('moodle_url', "VARCHAR DEFAULT ''")
            logger.info("Migration: added 'moodle_url' to course_info.")
        if 'year' not in ci_cols:
            _add_col('year', 'INTEGER DEFAULT 2026')
            logger.info("Migration: added 'year' to course_info.")
        if 'semester' not in ci_cols:
            _add_col('semester', "VARCHAR(2) DEFAULT 'F'")
            logger.info("Migration: added 'semester' to course_info.")
        if 'first_segment_count' not in ci_cols:
            _add_col('first_segment_count', 'INTEGER DEFAULT 4')
            logger.info("Migration: added 'first_segment_count' to course_info.")
        if 'max_attempts_per_module' not in ci_cols:
            _add_col('max_attempts_per_module', 'INTEGER DEFAULT 4')
            logger.info("Migration: added 'max_attempts_per_module' to course_info.")
        if 'class_days' not in ci_cols:
            _add_col('class_days', "VARCHAR DEFAULT 'T,Th'")
            logger.info("Migration: added 'class_days' to course_info.")
        if 'class_start_time' not in ci_cols:
            _add_col('class_start_time', "VARCHAR DEFAULT '15:55'")
            logger.info("Migration: added 'class_start_time' to course_info.")
        if 'class_end_time' not in ci_cols:
            _add_col('class_end_time', "VARCHAR DEFAULT '17:15'")
            logger.info("Migration: added 'class_end_time' to course_info.")
        if 'class_classroom' not in ci_cols:
            _add_col('class_classroom', "VARCHAR DEFAULT ''")
            logger.info("Migration: added 'class_classroom' to course_info.")
        if 'num_sections' not in ci_cols:
            _add_col('num_sections', 'INTEGER DEFAULT 0')
            logger.info("Migration: added 'num_sections' to course_info.")
        if 'passing_threshold' not in ci_cols:
            _add_col('passing_threshold', 'FLOAT DEFAULT 65.0')
            logger.info("Migration: added 'passing_threshold' to course_info.")
        if 'completion_threshold' not in ci_cols:
            _add_col('completion_threshold', 'FLOAT DEFAULT 90.0')
            logger.info("Migration: added 'completion_threshold' to course_info.")
        # --- classrooms (new table; no ALTER needed, create_all handles it) ---
        # --- semester_calendar ---
        existing_tables = {name for name in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).scalars()}
        if 'semester_calendar' in existing_tables:
            sc_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(semester_calendar)"))}
            if 'end_of_semester' not in sc_cols:
                conn.execute(text('ALTER TABLE semester_calendar ADD COLUMN end_of_semester VARCHAR'))
                logger.info("Migration: added 'end_of_semester' to semester_calendar.")
        # --- students ---
        st_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(students)"))}
        if 'section_number' not in st_cols:
            conn.execute(text('ALTER TABLE students ADD COLUMN section_number INTEGER'))
            logger.info("Migration: added 'section_number' to students.")
        if 'enrolled' not in st_cols:
            conn.execute(text('ALTER TABLE students ADD COLUMN enrolled BOOLEAN NOT NULL DEFAULT 1'))
            logger.info("Migration: added 'enrolled' to students.")
        # --- course_sections ---
        cs_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(course_sections)"))}
        if 'code' not in cs_cols:
            conn.execute(text("ALTER TABLE course_sections ADD COLUMN code VARCHAR DEFAULT ''"))
            logger.info("Migration: added 'code' to course_sections.")
        # --- quizzes ---
        qz_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(quizzes)"))}
        if 'has_odt' not in qz_cols:
            conn.execute(text("ALTER TABLE quizzes ADD COLUMN has_odt BOOLEAN DEFAULT 0"))
            logger.info("Migration: added 'has_odt' to quizzes.")
        if 'odt_template_path' not in qz_cols:
            conn.execute(text("ALTER TABLE quizzes ADD COLUMN odt_template_path VARCHAR DEFAULT ''"))
            logger.info("Migration: added 'odt_template_path' to quizzes.")
        if 'odt_variable_names_json' not in qz_cols:
            conn.execute(text("ALTER TABLE quizzes ADD COLUMN odt_variable_names_json TEXT DEFAULT '[]'"))
            logger.info("Migration: added 'odt_variable_names_json' to quizzes.")
        if 'odt_variable_values_json' not in qz_cols:
            conn.execute(text("ALTER TABLE quizzes ADD COLUMN odt_variable_values_json TEXT DEFAULT '[]'"))
            logger.info("Migration: added 'odt_variable_values_json' to quizzes.")
        if 'grading_session_id' not in qz_cols:
            conn.execute(text("ALTER TABLE quizzes ADD COLUMN grading_session_id INTEGER"))
            logger.info("Migration: added 'grading_session_id' to quizzes.")
        conn.commit()
    # Backfill NULLs on the existing CourseInfo row
    with Session(engine) as session:
        row = session.query(CourseInfo).first()
        if row is not None:
            changed = False
            if row.year is None:                      row.year = 2026;                     changed = True
            if not row.semester:                      row.semester = 'F';                  changed = True
            if row.first_segment_count is None:       row.first_segment_count = 4;         changed = True
            if row.max_attempts_per_module is None:   row.max_attempts_per_module = 4;     changed = True
            if not row.course:                        row.course = 'NBIO140B';              changed = True
            if not row.course_title:                  row.course_title = 'Principles of Neuroscience'; changed = True
            if not row.instructors:                   row.instructors = 'Sacha Nelson';    changed = True
            if not row.class_days:                    row.class_days = 'T,Th';             changed = True
            if not row.class_start_time:              row.class_start_time = '15:55';      changed = True
            if not row.class_end_time:                row.class_end_time = '17:15';        changed = True
            if row.num_sections is None:              row.num_sections = 0;                changed = True
            if row.passing_threshold is None:         row.passing_threshold = 65.0;         changed = True
            if row.completion_threshold is None:      row.completion_threshold = 90.0;      changed = True
            if changed:
                session.commit()
                logger.info("Migration: backfilled default values on existing CourseInfo row.")


def _migrate_quiz_questions(engine: Engine) -> None:
    with engine.connect() as conn:
        existing_tables = {
            name for name in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).scalars()
        }
        if 'quiz_questions' not in existing_tables:
            return
        columns = {
            row[1] for row in conn.execute(text("PRAGMA table_info(quiz_questions)"))
        }
        required = {
            'quiz_id', 'student_id', 'module_number', 'question_number', 'question_id',
            'question_text', 'answer_choices_json', 'correct_answer_index',
            'feedback_text', 'context_text',
        }
        if not required.issubset(columns):
            count = conn.execute(text("SELECT COUNT(*) FROM quiz_questions")).scalar()
            if count:
                logger.warning(
                    "quiz_questions has incompatible schema and %d rows; manual migration required.", count
                )
                return
            conn.execute(text("DROP TABLE quiz_questions"))
            conn.commit()
            logger.info("Migration: dropped empty incompatible quiz_questions table.")
            return

        # Remove the old per-student/per-module unique constraint if it still exists.
        # The canonical constraint is now (quiz_id, question_number) only.
        indexes = {
            row[1] for row in conn.execute(text("PRAGMA index_list(quiz_questions)"))
        }
        if 'sqlite_autoindex_quiz_questions_2' not in indexes:
            return

        logger.info("Migration: recreating quiz_questions to remove old (student_id, module_number, question_id) unique constraint.")
        conn.execute(text("DROP TABLE IF EXISTS quiz_questions_old"))
        conn.execute(text("DROP TABLE IF EXISTS quiz_questions_new"))
        conn.execute(text("ALTER TABLE quiz_questions RENAME TO quiz_questions_old"))

        # Create a new table under a temporary name to avoid index-name collisions.
        temp_meta = MetaData()
        temp_table = QuizQuestion.__table__.to_metadata(temp_meta, name='quiz_questions_new')
        temp_table.create(conn)

        conn.execute(text(
            "INSERT INTO quiz_questions_new "
            "(id, quiz_id, student_id, module_number, question_number, question_id, "
            "question_text, answer_choices_json, correct_answer_index, feedback_text, context_text) "
            "SELECT id, quiz_id, student_id, module_number, question_number, question_id, "
            "question_text, answer_choices_json, correct_answer_index, feedback_text, context_text "
            "FROM quiz_questions_old"
        ))
        conn.execute(text("DROP TABLE quiz_questions_old"))
        conn.execute(text("ALTER TABLE quiz_questions_new RENAME TO quiz_questions"))
        conn.commit()
        logger.info("Migration: quiz_questions recreated successfully.")


def _migrate_student_module_progress(engine: Engine) -> None:
    """Recreate student_module_progress if it still has the old schema.

    The old table had a 'status' column and no 'completed'/'attempts_count'.
    Since the new DB has no progress data yet, we can safely drop and recreate
    if a mismatch is detected. If the table has data, we log a warning and leave
    it alone so a manual migration can be planned.
    """
    with engine.connect() as conn:
        existing_tables = {name for name in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).scalars()}
        if 'student_module_progress' not in existing_tables:
            return

        existing_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(student_module_progress)"))}
        if 'completed' in existing_cols and 'attempts_count' in existing_cols:
            return  # already up to date

        # Schema mismatch: check for data before dropping
        row_count = conn.execute(text("SELECT COUNT(*) FROM student_module_progress")).scalar()
        if row_count > 0:
            logger.warning(
                "student_module_progress has old schema AND %d rows; "
                "not auto-dropping. Manual migration required.", row_count
            )
            return

        conn.execute(text("DROP TABLE student_module_progress"))
        conn.commit()
        logger.info("Migration: dropped old student_module_progress table (empty) to recreate with new schema.")


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

def _seed_initial_data(engine: Engine) -> None:
    """Insert default rows if the database is brand new."""
    with Session(engine) as session:
        # One CourseInfo row
        if session.query(CourseInfo).count() == 0:
            session.add(CourseInfo(
                course='NBIO140B',
                year=2026,
                semester='F',
                course_title='Principles of Neuroscience',
                instructors='Sacha Nelson',
                course_folder='',
                moodle_url='',
                min_signup_time=24,
                min_cancel_time=24,
                first_segment_count=4,
                max_attempts_per_module=4,
            ))
            logger.info("Seeded empty CourseInfo row.")

        # NUM_MODULES Module rows
        if session.query(Module).count() == 0:
            for n in range(1, NUM_MODULES + 1):
                session.add(Module(number=n, name=f'Module {n}', readings=''))
            logger.info(f"Seeded {NUM_MODULES} Module rows.")

        # Classrooms
        if session.query(Classroom).count() == 0:
            for c in DEFAULT_CLASSROOMS:
                session.add(Classroom(**c))
            logger.info(f"Seeded {len(DEFAULT_CLASSROOMS)} default Classroom rows.")

        # Default dummy students
        if session.query(Student).count() == 0:
            for s in DEFAULT_STUDENTS:
                session.add(Student(**s))
            logger.info(f"Seeded {len(DEFAULT_STUDENTS)} default Student rows.")

        session.commit()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_course_info(engine: Engine) -> Dict:
    """Return course info as a plain dict (safe default if table is empty)."""
    with Session(engine) as session:
        row = session.query(CourseInfo).first()
        if row is None:
            return {}
        return {
            'course':          row.course          or '',
            'year':            row.year            if row.year is not None else 2026,
            'semester':        row.semester        or 'F',
            'course_title':    row.course_title    or '',
            'instructors':     row.instructors     or '',
            'course_folder':   row.course_folder   or '',
            'moodle_url':      row.moodle_url      or '',
            'min_signup_time': row.min_signup_time or 24,
            'min_cancel_time': row.min_cancel_time or 24,
            'first_segment_count': row.first_segment_count or 4,
            'max_attempts_per_module': row.max_attempts_per_module or 4,
            'passing_threshold': row.passing_threshold or 65.0,
            'completion_threshold': row.completion_threshold or 90.0,
            'class_days':       row.class_days      or 'T,Th',
            'class_start_time': row.class_start_time or '15:55',
            'class_end_time':   row.class_end_time   or '17:15',
            'class_classroom':  row.class_classroom  or '',
            'num_sections':     row.num_sections     if row.num_sections is not None else 0,
        }


def get_course_moodle_url(engine: Engine) -> str:
    return get_course_info(engine).get('moodle_url', '')


def save_course_info(engine: Engine, data: Dict) -> None:
    """Upsert course info from *data* dict into the single CourseInfo row."""
    with Session(engine) as session:
        row = session.query(CourseInfo).first()
        if row is None:
            row = CourseInfo()
            session.add(row)
        row.course          = data.get('course',          row.course          or '')
        row.year            = data.get('year',            row.year            if row.year is not None else 2026)
        row.semester        = data.get('semester',        row.semester        or 'F')
        row.course_title    = data.get('course_title',    row.course_title    or '')
        row.instructors     = data.get('instructors',     row.instructors     or '')
        row.course_folder   = data.get('course_folder',   row.course_folder   or '')
        row.moodle_url      = data.get('moodle_url',      row.moodle_url      or '')
        row.min_signup_time         = data.get('min_signup_time',         row.min_signup_time         or 24)
        row.min_cancel_time         = data.get('min_cancel_time',         row.min_cancel_time         or 24)
        row.first_segment_count     = data.get('first_segment_count',     row.first_segment_count     or 4)
        row.max_attempts_per_module = data.get('max_attempts_per_module', row.max_attempts_per_module or 4)
        row.passing_threshold    = data.get('passing_threshold',    row.passing_threshold    or 65.0)
        row.completion_threshold = data.get('completion_threshold', row.completion_threshold or 90.0)
        row.class_days       = data.get('class_days',       row.class_days      or 'T,Th')
        row.class_start_time = data.get('class_start_time', row.class_start_time or '15:55')
        row.class_end_time   = data.get('class_end_time',   row.class_end_time   or '17:15')
        row.class_classroom  = data.get('class_classroom',  row.class_classroom  or '')
        row.num_sections     = data.get('num_sections',     row.num_sections     if row.num_sections is not None else 0)
        session.commit()


def get_modules(engine: Engine) -> List[Dict]:
    """Return all modules as a list of dicts sorted by number."""
    with Session(engine) as session:
        rows = session.query(Module).order_by(Module.number).all()
        return [{'number': r.number, 'name': r.name, 'readings': r.readings or ''} for r in rows]


def save_module(engine: Engine, number: int, name: str, readings: str = '') -> None:
    """Update a single module row identified by *number*."""
    with Session(engine) as session:
        row = session.query(Module).filter_by(number=number).first()
        if row is None:
            row = Module(number=number)
            session.add(row)
        row.name     = name
        row.readings = readings
        session.commit()


def format_student_display(name: str, student_code: str) -> str:
    """Format student name and code for display (mirrors MCQ.utils)."""
    return f"{name} ({student_code})"


def get_all_students(engine: Engine, enrolled_only: bool = True) -> List[Student]:
    """Return Student rows ordered by name."""
    with Session(engine) as session:
        query = session.query(Student)
        if enrolled_only:
            query = query.filter(Student.enrolled.is_(True))
        return query.order_by(Student.name).all()


def get_all_students_as_dicts(engine: Engine, enrolled_only: bool = True) -> List[Dict]:
    """Return students as plain dictionaries (safe for serialization)."""
    with Session(engine) as session:
        query = session.query(Student)
        if enrolled_only:
            query = query.filter(Student.enrolled.is_(True))
        rows = query.order_by(Student.name).all()
        return [_student_to_dict(r) for r in rows]


def save_enrolled_students(engine: Engine, students: List[Dict], removed_student_ids: List[int]) -> None:
    """Save roster edits, create new students, and mark removed students as no longer enrolled."""
    from student_roster26 import generate_student_code
    with Session(engine) as session:
        all_students = session.query(Student).all()
        rows_by_id = {
            row.student_id: row
            for row in all_students
            if row.enrolled
        }
        assigned_codes = {s.student_code.casefold() for s in all_students if s.student_code}
        seen_codes = set()
        for data in students:
            student_id = data.get('student_id')
            is_new = student_id is None
            if is_new:
                row = Student(name='', enrolled=True)
                session.add(row)
            else:
                row = rows_by_id.get(student_id)
                if row is None:
                    raise ValueError(f"Enrolled student ID {student_id!r} was not found.")
            name = str(data.get('name') or '').strip()
            code = str(data.get('student_code') or '').strip()
            section_number = data.get('section_number')
            if not name:
                raise ValueError('Each enrolled student must have a name.')
            if not code:
                code = generate_student_code(name, assigned_codes)
            if not code:
                raise ValueError(f"Student '{name}' must have a code.")
            folded_code = code.casefold()
            if folded_code in seen_codes:
                raise ValueError(f"Student code '{code}' is duplicated.")
            seen_codes.add(folded_code)
            if is_new:
                conflict = session.query(Student).filter(
                    Student.student_code.ilike(code)
                ).first()
            else:
                conflict = session.query(Student).filter(
                    Student.student_code.ilike(code), Student.student_id != student_id
                ).first()
            if conflict is not None:
                raise ValueError(f"Student code '{code}' is already assigned to another student.")
            if section_number is not None:
                section = session.query(CourseSection).filter_by(section_number=section_number).first()
                if section is None:
                    raise ValueError(f"Section {section_number} does not exist.")
            row.name = name
            row.student_code = code
            row.section_number = section_number
            row.enrolled = True
            assigned_codes.add(folded_code)
        for student_id in removed_student_ids:
            row = rows_by_id.get(student_id)
            if row is not None:
                row.enrolled = False
        session.commit()


def get_student_by_id(engine: Engine, student_id: int) -> Optional[Student]:
    """Return a Student row by primary key."""
    with Session(engine) as session:
        return session.query(Student).filter_by(student_id=student_id).first()


def get_student_by_code(engine: Engine, student_code: str) -> Optional[Student]:
    """Return a Student row by student_code (case-insensitive)."""
    with Session(engine) as session:
        return (
            session.query(Student)
            .filter(Student.student_code.ilike(student_code))
            .first()
        )


def get_student_by_code_as_dict(engine: Engine, student_code: str) -> Optional[Dict]:
    """Return a student dict by student_code, or None if not found."""
    student = get_student_by_code(engine, student_code)
    if student is None:
        return None
    return _student_to_dict(student)


def _student_to_dict(student: Student) -> Dict:
    """Convert a Student ORM instance to a plain dictionary."""
    return {
        'student_id':       student.student_id,
        'student_code':     student.student_code or '',
        'name':             student.name or '',
        'pronouns':         student.pronouns or '',
        'email':            student.email or '',
        'academic_level':   student.academic_level or '',
        'program_of_study': student.program_of_study or '',
        'section_number':   student.section_number,
        'enrolled':         student.enrolled,
    }


# ---------------------------------------------------------------------------
# Section helpers
# ---------------------------------------------------------------------------

def get_all_sections(engine: Engine) -> List[Dict]:
    """Return all CourseSection rows ordered by section_number."""
    with Session(engine) as session:
        rows = session.query(CourseSection).order_by(CourseSection.section_number).all()
        return [_section_to_dict(r) for r in rows]


def get_section(engine: Engine, section_number: int) -> Optional[Dict]:
    """Return a single section dict, or None."""
    with Session(engine) as session:
        row = session.query(CourseSection).filter_by(section_number=section_number).first()
        return _section_to_dict(row) if row else None


def save_section(engine: Engine, data: Dict) -> None:
    """Upsert a CourseSection row from *data* dict.

    Required key: ``section_number``.  All other keys are optional.
    ``meeting_dates`` should be a list of [month, day] pairs.
    """
    with Session(engine) as session:
        num = data['section_number']
        row = session.query(CourseSection).filter_by(section_number=num).first()
        if row is None:
            row = CourseSection(section_number=num)
            session.add(row)
        row.code          = data.get('code',          row.code)
        row.day_of_week   = data.get('day_of_week',   row.day_of_week)
        row.start_time    = data.get('start_time',    row.start_time)
        row.end_time      = data.get('end_time',      row.end_time)
        row.classroom     = data.get('classroom',     row.classroom)
        row.ta_instructor = data.get('ta_instructor', row.ta_instructor)
        row.comment       = data.get('comment',       row.comment)
        dates = data.get('meeting_dates')
        if dates is not None:
            row.meeting_dates_json = json.dumps(dates)
        session.commit()


def delete_section(engine: Engine, section_number: int) -> None:
    """Delete a CourseSection row by section_number."""
    with Session(engine) as session:
        row = session.query(CourseSection).filter_by(section_number=section_number).first()
        if row:
            session.delete(row)
            session.commit()


def get_students_in_section(engine: Engine, section_number: int) -> List[Student]:
    """Return all students enrolled in *section_number*, ordered by name."""
    with Session(engine) as session:
        return (
            session.query(Student)
            .filter_by(section_number=section_number, enrolled=True)
            .order_by(Student.name)
            .all()
        )


def set_student_section(engine: Engine, student_code: str, section_number: Optional[int]) -> None:
    """Assign (or clear) the section for a student identified by *student_code*."""
    with Session(engine) as session:
        row = session.query(Student).filter(Student.student_code.ilike(student_code)).first()
        if row is None:
            raise ValueError(f"Student code '{student_code}' not found.")
        row.section_number = section_number
        session.commit()


def _section_to_dict(row: 'CourseSection') -> Dict:
    """Convert a CourseSection ORM instance to a plain dict."""
    return {
        'section_number': row.section_number,
        'code':           row.code          or '',
        'day_of_week':    row.day_of_week   or '',
        'start_time':     row.start_time    or '',
        'end_time':       row.end_time      or '',
        'classroom':      row.classroom     or '',
        'ta_instructor':  row.ta_instructor or '',
        'comment':        row.comment       or '',
        'meeting_dates':  json.loads(row.meeting_dates_json or '[]'),
    }


# ---------------------------------------------------------------------------
# Semester calendar helpers
# ---------------------------------------------------------------------------

def get_semester_calendar(engine: Engine, year: int, semester: str) -> Optional[Dict]:
    """Return the SemesterCalendar for *year*/*semester* as a dict, or None."""
    with Session(engine) as session:
        row = session.query(SemesterCalendar).filter_by(year=year, semester=semester).first()
        return _calendar_to_dict(row) if row else None


def save_semester_calendar(engine: Engine, data: Dict) -> None:
    """Upsert a SemesterCalendar row.

    Expected keys: year, semester, first_day, last_day,
    no_class_days (list of YYYY-MM-DD strings),
    brandeis_days (list of {"date": "YYYY-MM-DD", "substitute": "T"} dicts).
    """
    with Session(engine) as session:
        row = session.query(SemesterCalendar).filter_by(
            year=data['year'], semester=data['semester']
        ).first()
        if row is None:
            row = SemesterCalendar(year=data['year'], semester=data['semester'])
            session.add(row)
        row.first_day       = data.get('first_day',       row.first_day)
        row.last_day        = data.get('last_day',        row.last_day)
        row.end_of_semester = data.get('end_of_semester', row.end_of_semester)
        ncd = data.get('no_class_days')
        if ncd is not None:
            row.no_class_days_json = json.dumps(ncd)
        bd = data.get('brandeis_days')
        if bd is not None:
            row.brandeis_days_json = json.dumps(bd)
        session.commit()


def compute_meeting_dates(
    first_day: str,
    last_day: str,
    days_of_week: List[str],
    no_class_days: List[str],
    brandeis_days: List[Dict],
) -> List[str]:
    """Return sorted list of YYYY-MM-DD meeting dates.

    Args:
        first_day: YYYY-MM-DD string for first day of classes.
        last_day:  YYYY-MM-DD string for last day of classes.
        days_of_week: List of day abbreviations that the class meets, e.g. ['T', 'Th'].
        no_class_days: List of YYYY-MM-DD strings when there is no class.
        brandeis_days: List of dicts {"date": "YYYY-MM-DD", "substitute": "T"} where
                       the given date runs as the substitute day's schedule.

    Returns:
        Sorted list of YYYY-MM-DD date strings on which the class meets.
    """
    from datetime import date, timedelta

    _DAY_ABBR = {'M': 0, 'T': 1, 'W': 2, 'Th': 3, 'F': 4}

    target_dows = set()
    for d in days_of_week:
        if d in _DAY_ABBR:
            target_dows.add(_DAY_ABBR[d])

    no_class_set = set(no_class_days)

    # Map each Brandeis day's calendar-date to the substitute weekday it runs as
    brandeis_map: Dict[str, int] = {}
    for bd in brandeis_days:
        sub = bd.get('substitute', '')
        if sub in _DAY_ABBR:
            brandeis_map[bd['date']] = _DAY_ABBR[sub]

    start = date.fromisoformat(first_day)
    end   = date.fromisoformat(last_day)

    results = []
    current = start
    while current <= end:
        ds = current.isoformat()
        if ds not in no_class_set:
            # Effective weekday: use Brandeis-day substitute if applicable
            effective_dow = brandeis_map.get(ds, current.weekday())
            if effective_dow in target_dows:
                results.append(ds)
        current += timedelta(days=1)

    return sorted(results)


def _calendar_to_dict(row: 'SemesterCalendar') -> Dict:
    """Convert a SemesterCalendar ORM instance to a plain dict."""
    return {
        'year':           row.year,
        'semester':       row.semester,
        'first_day':       row.first_day       or '',
        'last_day':        row.last_day        or '',
        'end_of_semester': row.end_of_semester or '',
        'no_class_days':   json.loads(row.no_class_days_json or '[]'),
        'brandeis_days':   json.loads(row.brandeis_days_json or '[]'),
    }


# ---------------------------------------------------------------------------
# Classroom helpers
# ---------------------------------------------------------------------------

def get_students_for_section(engine: Engine, section_number: int) -> List[Student]:
    """Return all Student rows whose section_number matches *section_number*."""
    with Session(engine) as session:
        return (
            session.query(Student)
            .filter_by(section_number=section_number, enrolled=True)
            .order_by(Student.name)
            .all()
        )


def get_all_classrooms(engine: Engine) -> List[Dict]:
    """Return all Classroom rows ordered by name."""
    with Session(engine) as session:
        rows = session.query(Classroom).order_by(Classroom.name).all()
        return [{'id': r.id, 'name': r.name, 'capacity': r.capacity} for r in rows]


def save_classroom(engine: Engine, name: str, capacity: int, classroom_id: int = None) -> None:
    """Upsert a Classroom row. If *classroom_id* is given, update that row; otherwise insert."""
    with Session(engine) as session:
        if classroom_id is not None:
            row = session.query(Classroom).filter_by(id=classroom_id).first()
        else:
            row = session.query(Classroom).filter_by(name=name).first()
        if row is None:
            row = Classroom(name=name, capacity=capacity)
            session.add(row)
        else:
            row.name     = name
            row.capacity = capacity
        session.commit()


def delete_classroom(engine: Engine, classroom_id: int) -> None:
    """Delete a Classroom row by primary key."""
    with Session(engine) as session:
        row = session.query(Classroom).filter_by(id=classroom_id).first()
        if row:
            session.delete(row)
            session.commit()


def get_max_students_for_room(engine: Engine, room_name: str) -> int:
    """Return the capacity for *room_name*, or 0 if not found."""
    with Session(engine) as session:
        row = session.query(Classroom).filter_by(name=room_name).first()
        return row.capacity if row else 0


# ---------------------------------------------------------------------------
# Progress / quiz helpers
# ---------------------------------------------------------------------------

def get_student_progress(engine: Engine, student_id: int) -> List[StudentModuleProgress]:
    """Return all StudentModuleProgress rows for a student, ordered by module."""
    with Session(engine) as session:
        return (
            session.query(StudentModuleProgress)
            .filter_by(student_id=student_id)
            .order_by(StudentModuleProgress.module_number)
            .all()
        )


def get_all_student_progress(engine: Engine) -> List[StudentModuleProgress]:
    """Return all StudentModuleProgress rows ordered by student, module."""
    with Session(engine) as session:
        return (
            session.query(StudentModuleProgress)
            .order_by(StudentModuleProgress.student_id, StudentModuleProgress.module_number)
            .all()
        )


def _ensure_student_module_progress(engine: Engine, student_id: int, module_number: int) -> StudentModuleProgress:
    """Get or create a progress row for (student_id, module_number)."""
    with Session(engine) as session:
        row = session.query(StudentModuleProgress).filter_by(
            student_id=student_id, module_number=module_number
        ).first()
        if row is None:
            row = StudentModuleProgress(student_id=student_id, module_number=module_number)
            session.add(row)
            session.commit()
        return row


def update_progress_completed(engine: Engine, student_id: int, module_number: int, completed: bool) -> None:
    """Set the completed flag for a student/module progress row."""
    with Session(engine) as session:
        row = session.query(StudentModuleProgress).filter_by(
            student_id=student_id, module_number=module_number
        ).first()
        if row is None:
            row = StudentModuleProgress(student_id=student_id, module_number=module_number)
            session.add(row)
        row.completed = completed
        session.commit()


def increment_attempts_count(engine: Engine, student_id: int, module_number: int) -> None:
    """Increment the attempts_count for a student/module progress row."""
    with Session(engine) as session:
        row = session.query(StudentModuleProgress).filter_by(
            student_id=student_id, module_number=module_number
        ).first()
        if row is None:
            row = StudentModuleProgress(student_id=student_id, module_number=module_number)
            session.add(row)
        row.attempts_count = (row.attempts_count or 0) + 1
        session.commit()


def get_all_quizzes(engine: Engine) -> List[Quiz]:
    """Return all Quiz rows ordered by id."""
    with Session(engine) as session:
        return session.query(Quiz).order_by(Quiz.id).all()


def get_quizzes_for_student(engine: Engine, student_id: int) -> List[Quiz]:
    """Return all Quiz rows for a student, most recent first."""
    with Session(engine) as session:
        return (
            session.query(Quiz)
            .filter_by(student_id=student_id)
            .order_by(Quiz.id.desc())
            .all()
        )


def add_quiz_attempt(
    engine: Engine,
    student_id: int,
    module_number: int,
    quiz_id: str,
    date_taken: str,
    score: Optional[int] = None,
    total_questions: Optional[int] = None,
    time_taken: str = '',
) -> Quiz:
    """Insert a new Quiz row and return it."""
    with Session(engine) as session:
        quiz = Quiz(
            student_id=student_id,
            module_number=module_number,
            quiz_id=quiz_id,
            date_taken=date_taken,
            time_taken=time_taken,
            score=score,
            total_questions=total_questions,
        )
        session.add(quiz)
        session.commit()
        return quiz


def get_course_thresholds(engine: Engine) -> Tuple[float, float]:
    """Return (passing_threshold, completion_threshold) from course info."""
    info = get_course_info(engine)
    passing = float(info.get('passing_threshold', 65.0))
    completion = float(info.get('completion_threshold', 90.0))
    return passing, completion


def recompute_module_progress(engine: Engine, student_id: int, module_number: int) -> None:
    """Recompute highest_score, attempts_count, completed for a student/module.

    Called after any quiz score change (new attempt or regrade).
    """
    passing, completion = get_course_thresholds(engine)
    with Session(engine) as session:
        attempts = (
            session.query(Quiz)
            .filter_by(student_id=student_id, module_number=module_number)
            .filter(Quiz.score.isnot(None))
            .all()
        )
        highest = 0.0
        last_attempt_dt = None
        for attempt in attempts:
            score = attempt.score or 0.0
            if score > highest:
                highest = float(score)
            graded_dt = None
            if attempt.date_graded:
                try:
                    graded_dt = datetime.strptime(attempt.date_graded, '%Y-%m-%d')
                except (ValueError, TypeError):
                    graded_dt = None
            if graded_dt and (last_attempt_dt is None or graded_dt > last_attempt_dt):
                last_attempt_dt = graded_dt

        row = session.query(StudentModuleProgress).filter_by(
            student_id=student_id, module_number=module_number
        ).first()
        if row is None:
            row = StudentModuleProgress(student_id=student_id, module_number=module_number)
            session.add(row)
        row.attempts_count = len(attempts)
        row.highest_score = highest
        row.completed = highest >= completion
        if last_attempt_dt:
            row.last_attempt = last_attempt_dt
        session.commit()


def record_quiz_attempt(
    engine: Engine,
    student_id: int,
    module_number: int,
    quiz_id: str,
    date_taken: str,
    score: Optional[int] = None,
    total_questions: Optional[int] = None,
    time_taken: str = '',
    date_graded: Optional[str] = None,
    date_signed_up: Optional[str] = None,
    grading_session_id: Optional[int] = None,
) -> Quiz:
    """Record a quiz attempt and update module progress in one step.

    If a Quiz row already exists for this exact (student_id, module_number,
    quiz_id) - e.g. the ungraded placeholder row written when the quiz was
    generated, or a previous grading pass over the same physical quiz - it is
    updated in place rather than duplicated. This is what makes re-grading
    (re-scanning the same quiz_id, whether to fix a scanning error or an
    earlier manual-resolution mistake) update the existing attempt instead of
    creating a second one. If the row already had a different score, the
    correction is flagged via `score_corrected`/`date_score_corrected` for
    audit purposes.

    The is_highest flag is dropped; highest_score is tracked on
    StudentModuleProgress instead. *grading_session_id* records which
    GradingSession (scanned batch) most recently produced/updated this
    attempt, if any.
    """
    if date_graded is None:
        date_graded = datetime.now().strftime('%Y-%m-%d')
    if time_taken is None:
        time_taken = datetime.now().strftime('%H:%M:%S')

    with Session(engine) as session:
        passing, _ = get_course_thresholds(engine)
        is_passing = 1 if (score is not None and float(score) >= passing) else 0

        quiz = session.query(Quiz).filter_by(
            student_id=student_id, module_number=module_number, quiz_id=quiz_id,
        ).first()
        if quiz is None:
            quiz = Quiz(student_id=student_id, module_number=module_number, quiz_id=quiz_id)
            session.add(quiz)
        elif quiz.score is not None and score is not None and quiz.score != score:
            quiz.score_corrected = 1
            quiz.date_score_corrected = datetime.now().strftime('%Y-%m-%d')

        quiz.date_taken = date_taken
        quiz.time_taken = time_taken
        quiz.date_graded = date_graded
        quiz.date_signed_up = date_signed_up if date_signed_up is not None else quiz.date_signed_up
        quiz.grading_session_id = grading_session_id
        quiz.score = score
        quiz.total_questions = total_questions
        quiz.is_passing = is_passing
        session.commit()
        session.refresh(quiz)

    recompute_module_progress(engine, student_id, module_number)
    return quiz


def get_quiz_score_by_quiz_id(engine: Engine, quiz_id: str) -> Optional[int]:
    """Return the currently recorded score for *quiz_id*, or None if it
    hasn't been graded yet (or doesn't exist)."""
    with Session(engine) as session:
        row = session.query(Quiz).filter_by(quiz_id=quiz_id).first()
        return row.score if row else None


def update_quiz_score(engine: Engine, quiz_id: int, new_score: Optional[int]) -> Optional[Quiz]:
    """Update a quiz score (used by manual regrade) and recompute progress.

    Returns the updated Quiz row or None if not found. If the quiz already
    had a different score, the change is flagged via
    `score_corrected`/`date_score_corrected` for audit purposes.
    """
    with Session(engine) as session:
        quiz = session.query(Quiz).filter_by(id=quiz_id).first()
        if quiz is None:
            return None
        passing, _ = get_course_thresholds(engine)
        if quiz.score is not None and new_score is not None and quiz.score != new_score:
            quiz.score_corrected = 1
            quiz.date_score_corrected = datetime.now().strftime('%Y-%m-%d')
        quiz.score = new_score
        quiz.is_passing = 1 if (new_score is not None and float(new_score) >= passing) else 0
        quiz.date_graded = datetime.now().strftime('%Y-%m-%d')
        session.commit()
        student_id = quiz.student_id
        module_number = quiz.module_number

    recompute_module_progress(engine, student_id, module_number)
    return quiz


def delete_quiz_attempt(engine: Engine, quiz_id: int) -> None:
    """Delete a Quiz row by its primary key and recompute module progress."""
    with Session(engine) as session:
        row = session.query(Quiz).filter_by(id=quiz_id).first()
        if row:
            student_id = row.student_id
            module_number = row.module_number
            session.delete(row)
            session.commit()
            recompute_module_progress(engine, student_id, module_number)


def update_quiz_question_correct_index(
    engine: Engine,
    quiz_question_id: int,
    correct_answer_index: Optional[int],
) -> Optional[QuizQuestion]:
    """Update the correct answer index for a single quiz question (regrade).

    Returns the updated row or None if not found.
    """
    with Session(engine) as session:
        row = session.query(QuizQuestion).filter_by(id=quiz_question_id).first()
        if row is None:
            return None
        row.correct_answer_index = correct_answer_index
        session.commit()
        session.refresh(row)
        return row


def quiz_attempt_exists(engine: Engine, student_id: int, module_number: int, quiz_id: str) -> bool:
    with Session(engine) as session:
        return session.query(Quiz.id).filter_by(
            student_id=student_id,
            module_number=module_number,
            quiz_id=quiz_id,
        ).first() is not None


def get_student_module_question_ids(engine: Engine, student_id: int, module_number: int) -> set[str]:
    with Session(engine) as session:
        return {
            question_id for (question_id,) in session.query(QuizQuestion.question_id)
            .filter_by(student_id=student_id, module_number=module_number)
            .all()
        }


def update_quiz_odt_values(
    engine: Engine,
    quiz_id: str,
    odt_variable_values: Optional[Dict[str, Any]] = None,
) -> Optional[Quiz]:
    """Update the ODT variable values for a quiz after ODTs are generated."""
    with Session(engine) as session:
        quiz = session.query(Quiz).filter_by(quiz_id=quiz_id).first()
        if quiz is None:
            return None
        quiz.odt_variable_values_json = json.dumps(odt_variable_values or {})
        session.commit()
        session.refresh(quiz)
    return quiz

def update_quiz_odt_info(
    engine: Engine,
    quiz_id: str,
    odt_template_path: Optional[str] = None,
    odt_variable_names: Optional[List[str]] = None,
    odt_variable_values: Optional[Dict[str, Any]] = None,
) -> Optional[Quiz]:
    """Update ODT template, variable names and values for a quiz."""
    with Session(engine) as session:
        quiz = session.query(Quiz).filter_by(quiz_id=quiz_id).first()
        if quiz is None:
            return None
        if odt_template_path is not None:
            quiz.odt_template_path = odt_template_path
        if odt_variable_names is not None:
            quiz.odt_variable_names_json = json.dumps(odt_variable_names)
        if odt_variable_values is not None:
            quiz.odt_variable_values_json = json.dumps(odt_variable_values)
        session.commit()
        session.refresh(quiz)
    return quiz


def get_quiz_questions(engine: Engine, quiz_id: str) -> List[Dict]:
    with Session(engine) as session:
        rows = (session.query(QuizQuestion)
                .filter_by(quiz_id=quiz_id)
                .order_by(QuizQuestion.question_number)
                .all())
        return [{
            'question_number': row.question_number,
            'question_id': row.question_id,
            'question_text': row.question_text,
            'answers': json.loads(row.answer_choices_json),
            'correct_answer_index': row.correct_answer_index,
            'feedback_text': row.feedback_text or '',
            'context_text': row.context_text or '',
        } for row in rows]


def add_generated_quiz_attempt(
    engine: Engine,
    student_id: int,
    module_number: int,
    quiz_id: str,
    date_taken: str,
    questions: List[Dict],
    time_taken: str = '',
    has_odt: bool = False,
    odt_template_path: Optional[str] = None,
    odt_variable_names: Optional[List[str]] = None,
    odt_variable_values: Optional[Dict[str, Any]] = None,
) -> Quiz:
    with Session(engine) as session:
        existing = (session.query(Quiz)
                    .filter_by(student_id=student_id, module_number=module_number, quiz_id=quiz_id)
                    .first())
        if existing is not None:
            question_count = session.query(QuizQuestion).filter_by(quiz_id=quiz_id).count()
            if question_count:
                return existing
            raise ValueError(f'Quiz {quiz_id} exists without question history')

        quiz = Quiz(
            student_id=student_id,
            module_number=module_number,
            quiz_id=quiz_id,
            date_taken=date_taken,
            time_taken=time_taken,
            total_questions=len(questions),
            has_odt=has_odt,
            odt_template_path=odt_template_path or '',
            odt_variable_names_json=json.dumps(odt_variable_names or []),
            odt_variable_values_json=json.dumps(odt_variable_values or {}),
        )
        session.add(quiz)
        session.flush()
        for number, question in enumerate(questions, 1):
            session.add(QuizQuestion(
                quiz_id=quiz_id,
                student_id=student_id,
                module_number=module_number,
                question_number=number,
                question_id=question['id'],
                question_text=question['stem'],
                answer_choices_json=json.dumps(question['answers']),
                correct_answer_index=question.get('correct_idx'),
                feedback_text=question.get('feedback', ''),
                context_text=question.get('context', ''),
            ))
        session.commit()
        return quiz


def get_section_meetings(engine: Engine, section_number: Optional[int] = None) -> List[Dict]:
    with Session(engine) as session:
        query = session.query(SectionMeeting)
        if section_number is not None:
            query = query.filter_by(section_number=section_number)
        rows = query.order_by(SectionMeeting.meeting_date, SectionMeeting.start_time).all()
        return [_section_meeting_to_dict(row) for row in rows]


def save_section_meeting(engine: Engine, data: Dict) -> int:
    with Session(engine) as session:
        meeting_id = data.get('meeting_id')
        row = session.query(SectionMeeting).filter_by(meeting_id=meeting_id).first() if meeting_id else None
        if row is None:
            sequence = data.get('meeting_sequence')
            if sequence is None:
                sequence = (session.query(func.max(SectionMeeting.meeting_sequence)).scalar() or 0) + 1
            row = SectionMeeting(meeting_sequence=sequence)
            session.add(row)
        row.section_number = data['section_number']
        row.meeting_date = data['meeting_date']
        row.start_time = data['start_time']
        row.end_time = data.get('end_time', row.end_time)
        row.title = data.get('title', row.title)
        row.worksheet_enabled = bool(data.get('worksheet_enabled', row.worksheet_enabled))
        row.definition_path = data.get('definition_path', row.definition_path)
        row.template_path = data.get('template_path', row.template_path)
        session.commit()
        session.refresh(row)
        return row.meeting_id


def save_section_meeting_grade(engine: Engine, data: Dict) -> int:
    score_supplied = 'score' in data
    score = data.get('score')
    if score_supplied and score is not None and score not in (0, 1, 2):
        raise ValueError('Section meeting score must be 0, 1, 2, or None')
    with Session(engine) as session:
        row = (session.query(SectionMeetingGrade)
               .filter_by(section_meeting_id=data['section_meeting_id'], student_id=data['student_id'])
               .first())
        if row is None:
            row = SectionMeetingGrade(
                section_meeting_id=data['section_meeting_id'],
                student_id=data['student_id'],
            )
            session.add(row)
        row.worksheet_id = data.get('worksheet_id', row.worksheet_id)
        if score_supplied:
            row.score = score
            row.graded_at = datetime.now() if score is not None else None
        row.attendance_status = data.get('attendance_status', row.attendance_status)
        row.submission_status = data.get('submission_status', row.submission_status)
        row.grader = data.get('grader', row.grader)
        row.note = data.get('note', row.note)
        row.submitted_work_path = data.get('submitted_work_path', row.submitted_work_path)
        session.commit()
        session.refresh(row)
        return row.grade_id


def get_section_meeting_grades(engine: Engine, section_meeting_id: int) -> List[Dict]:
    with Session(engine) as session:
        rows = (session.query(SectionMeetingGrade)
                .filter_by(section_meeting_id=section_meeting_id)
                .order_by(SectionMeetingGrade.student_id)
                .all())
        return [{
            'grade_id': row.grade_id,
            'section_meeting_id': row.section_meeting_id,
            'student_id': row.student_id,
            'worksheet_id': row.worksheet_id or '',
            'score': row.score,
            'attendance_status': row.attendance_status or '',
            'submission_status': row.submission_status or '',
            'grader': row.grader or '',
            'graded_at': row.graded_at.isoformat() if row.graded_at else None,
            'note': row.note or '',
            'submitted_work_path': row.submitted_work_path or '',
        } for row in rows]


def _section_meeting_to_dict(row: SectionMeeting) -> Dict:
    return {
        'meeting_id': row.meeting_id,
        'section_number': row.section_number,
        'meeting_date': row.meeting_date,
        'start_time': row.start_time,
        'end_time': row.end_time or '',
        'meeting_sequence': row.meeting_sequence,
        'title': row.title or '',
        'worksheet_enabled': bool(row.worksheet_enabled),
        'definition_path': row.definition_path or '',
        'template_path': row.template_path or '',
    }


# ---------------------------------------------------------------------------
# QuizSession helpers
# ---------------------------------------------------------------------------

def get_all_quiz_sessions(engine: Engine) -> List[Dict]:
    """Return all QuizSession rows ordered by date, start_time."""
    with Session(engine) as session:
        rows = (session.query(QuizSession)
                .order_by(QuizSession.date, QuizSession.start_time)
                .all())
        return [_session_to_dict(r) for r in rows]


def get_quiz_sessions_for_month(engine: Engine, year: int, month: int) -> List[Dict]:
    """Return QuizSession rows for a given year/month."""
    prefix = f"{year}-{month:02d}"
    with Session(engine) as session:
        rows = (session.query(QuizSession)
                .filter(QuizSession.date.like(f"{prefix}%"))
                .order_by(QuizSession.date, QuizSession.start_time)
                .all())
        return [_session_to_dict(r) for r in rows]


def get_quiz_session(engine: Engine, session_id: int) -> Optional[Dict]:
    """Return a single QuizSession dict or None."""
    with Session(engine) as session:
        row = session.query(QuizSession).filter_by(session_id=session_id).first()
        return _session_to_dict(row) if row else None


def save_quiz_session(engine: Engine, data: Dict) -> int:
    """Upsert a QuizSession. Returns the session_id."""
    with Session(engine) as session:
        sid = data.get('session_id')
        row = session.query(QuizSession).filter_by(session_id=sid).first() if sid else None
        if row is None:
            row = QuizSession()
            session.add(row)
        row.session_type = data.get('session_type', SESSION_TYPE_EXTRA)
        row.date         = data.get('date', '')
        row.start_time   = data.get('start_time', '')
        row.end_time     = data.get('end_time', '')
        row.room         = data.get('room', '')
        row.proctor      = data.get('proctor', '')
        row.capacity     = int(data.get('capacity', 0))
        row.active       = bool(data.get('active', True))
        session.commit()
        session.refresh(row)
        return row.session_id


def delete_quiz_session(engine: Engine, session_id: int) -> None:
    """Delete a QuizSession row by primary key."""
    with Session(engine) as session:
        row = session.query(QuizSession).filter_by(session_id=session_id).first()
        if row:
            session.delete(row)
            session.commit()


def get_active_session_signups(engine: Engine, session_id: int) -> List[Dict]:
    with Session(engine) as session:
        rows = (
            session.query(SessionSignup, Student)
            .join(Student, Student.student_id == SessionSignup.student_id)
            .filter(SessionSignup.session_id == session_id)
            .order_by(Student.name)
            .all()
        )
        return [{
            'signup_id': signup.signup_id,
            'student_id': student.student_id,
            'student_code': student.student_code,
            'student_name': student.name,
            'module_number': signup.module_number,
            'quiz_id': signup.quiz_id,
        } for signup, student in rows]


def get_upcoming_quiz_sessions(engine: Engine, from_date: str | None = None) -> List[Dict]:
    """Return active QuizSession rows on or after *from_date* (defaults to today)."""
    if from_date is None:
        from_date = datetime.now().strftime('%Y-%m-%d')
    with Session(engine) as session:
        rows = (
            session.query(QuizSession)
            .filter(QuizSession.active.is_(True))
            .filter(QuizSession.date >= from_date)
            .order_by(QuizSession.date, QuizSession.start_time)
            .all()
        )
        return [_session_to_dict(r) for r in rows]


def get_session_signup_count(engine: Engine, session_id: int) -> int:
    """Return the number of active signups for a quiz session."""
    with Session(engine) as session:
        return session.query(SessionSignup).filter_by(session_id=session_id).count()


def find_student_by_email_or_name(engine: Engine, email: str, name: str) -> Optional[Dict]:
    """Find a student by e-mail or, failing that, by normalized full name.

    Returns a dict with student_id, student_code, and name, or None.
    """
    email_norm = (email or '').strip().lower()
    name_norm = ' '.join((name or '').lower().split())
    with Session(engine) as session:
        if email_norm:
            row = session.query(Student).filter(
                func.lower(Student.email) == email_norm,
                Student.enrolled.is_(True),
            ).first()
            if row:
                return {
                    'student_id': row.student_id,
                    'student_code': row.student_code,
                    'name': row.name,
                    'email': row.email or '',
                }
        if name_norm:
            row = session.query(Student).filter(
                func.lower(Student.name) == name_norm,
                Student.enrolled.is_(True),
            ).first()
            if row:
                return {
                    'student_id': row.student_id,
                    'student_code': row.student_code,
                    'name': row.name,
                    'email': row.email or '',
                }
        return None


def create_session_signups(
    engine: Engine,
    session_id: int,
    student_ids: List[int],
    module_number: int,
    quiz_ids: Dict[int, str],
) -> int:
    with Session(engine) as session:
        created = 0
        for student_id in student_ids:
            quiz_id = quiz_ids.get(student_id)
            if not quiz_id:
                continue
            exists = session.query(SessionSignup).filter_by(
                session_id=session_id, student_id=student_id, quiz_id=quiz_id,
            ).first()
            if exists is None:
                session.add(SessionSignup(
                    session_id=session_id,
                    student_id=student_id,
                    module_number=module_number,
                    quiz_id=quiz_id,
                ))
                created += 1
        session.commit()
        return created


def queue_outgoing_email(
    engine: Engine,
    recipient: str,
    subject: str,
    body: str,
    email_type: str,
) -> int:
    with Session(engine) as session:
        row = OutgoingEmail(
            recipient=recipient,
            subject=subject,
            body=body,
            email_type=email_type,
        )
        session.add(row)
        session.commit()
        return row.email_id


def get_outgoing_emails(engine: Engine, status: Optional[str] = None) -> List[Dict]:
    with Session(engine) as session:
        query = session.query(OutgoingEmail).order_by(OutgoingEmail.created_at.desc())
        if status is not None:
            query = query.filter_by(status=status)
        return [{
            'email_id': row.email_id,
            'recipient': row.recipient,
            'subject': row.subject,
            'body': row.body,
            'email_type': row.email_type,
            'status': row.status,
            'created_at': row.created_at.isoformat() if row.created_at else '',
            'sent_at': row.sent_at.isoformat() if row.sent_at else '',
            'error': row.error or '',
        } for row in query.all()]


def update_outgoing_email_status(
    engine: Engine,
    email_id: int,
    status: str,
    error: Optional[str] = None,
) -> None:
    with Session(engine) as session:
        row = session.query(OutgoingEmail).filter_by(email_id=email_id).first()
        if row is None:
            return
        row.status = status
        row.error = error
        row.sent_at = datetime.now() if status == 'sent' else None
        session.commit()


def _session_to_dict(row: 'QuizSession') -> Dict:
    return {
        'session_id':   row.session_id,
        'session_type': row.session_type,
        'date':         row.date,
        'start_time':   row.start_time,
        'end_time':     row.end_time,
        'room':         row.room,
        'proctor':      row.proctor,
        'capacity':     row.capacity,
        'active':       row.active,
    }


# ---------------------------------------------------------------------------
# QuizSessionDefault helpers
# ---------------------------------------------------------------------------

def get_session_default(engine: Engine, session_type: str) -> Optional[Dict]:
    """Return the QuizSessionDefault dict for 'class' or 'section', or None."""
    with Session(engine) as session:
        row = session.query(QuizSessionDefault).filter_by(session_type=session_type).first()
        return _session_default_to_dict(row) if row else None


def save_session_default(engine: Engine, data: Dict) -> None:
    """Upsert a QuizSessionDefault for a given session_type."""
    with Session(engine) as session:
        row = session.query(QuizSessionDefault).filter_by(
            session_type=data['session_type']
        ).first()
        if row is None:
            row = QuizSessionDefault(session_type=data['session_type'])
            session.add(row)
        row.day_of_week = data.get('day_of_week')
        row.start_time  = data.get('start_time', '')
        row.end_time    = data.get('end_time', '')
        row.room        = data.get('room', '')
        row.proctor     = data.get('proctor', '')
        row.capacity    = int(data.get('capacity', 0))
        row.active      = bool(data.get('active', True))
        session.commit()


def _session_default_to_dict(row: 'QuizSessionDefault') -> Dict:
    return {
        'session_type': row.session_type,
        'day_of_week':  row.day_of_week,
        'start_time':   row.start_time,
        'end_time':     row.end_time,
        'room':         row.room,
        'proctor':      row.proctor,
        'capacity':     row.capacity,
        'active':       row.active,
    }


# ---------------------------------------------------------------------------
# GradingSession helpers
# ---------------------------------------------------------------------------

def create_grading_session(
    engine: Engine,
    session_date: str,
    letter: str,
    scan_path: str,
    original_scan_filename: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict:
    """Insert a new GradingSession row and return it as a dict."""
    with Session(engine) as session:
        row = GradingSession(
            session_date=session_date,
            letter=letter,
            scan_path=scan_path,
            original_scan_filename=original_scan_filename or '',
            notes=notes,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return _grading_session_to_dict(row)


def get_grading_sessions(engine: Engine, session_date: Optional[str] = None) -> List[Dict]:
    """Return GradingSession rows, most recent first, optionally filtered by date."""
    with Session(engine) as session:
        query = session.query(GradingSession)
        if session_date is not None:
            query = query.filter_by(session_date=session_date)
        rows = query.order_by(GradingSession.session_date.desc(), GradingSession.letter.desc()).all()
        return [_grading_session_to_dict(row) for row in rows]


def get_grading_session(engine: Engine, grading_session_id: int) -> Optional[Dict]:
    """Return a single GradingSession dict by id, or None."""
    with Session(engine) as session:
        row = session.query(GradingSession).filter_by(grading_session_id=grading_session_id).first()
        return _grading_session_to_dict(row) if row else None


def _grading_session_to_dict(row: 'GradingSession') -> Dict:
    return {
        'grading_session_id': row.grading_session_id,
        'session_date': row.session_date,
        'letter': row.letter,
        'scan_path': row.scan_path,
        'original_scan_filename': row.original_scan_filename or '',
        'notes': row.notes or '',
        'created_at': row.created_at.isoformat() if row.created_at else None,
    }
