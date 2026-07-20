"""
ODT-based Quantitative Quiz Generator

This module creates OpenDocument Text (.odt) quizzes for quantitative problems
that require students to fill in equations and calculations, complementing the
existing MCQ PDF-based system.
"""

from datetime import datetime
from typing import List, Dict, Optional, Tuple

# ODT generation library
from odf.opendocument import OpenDocumentText
from odf.text import P, H, Span, Tab, LineBreak
from odf.style import Style, TextProperties, ParagraphProperties, TableProperties
from odf.table import Table, TableRow, TableCell, TableColumn
from odf.draw import Frame, Image, Rect


class ODTQuizGenerator:
    """
    Generates ODT-based quantitative quizzes with header/footer components
    that mimic the existing MCQ PDF system.
    """
    
    def __init__(self):
        """Initialize the ODT quiz generator."""
        self.doc = None
        self.quiz_data = {}
        
    def create_document(self, quiz_type: str = "Quiz", course: str = "", 
                       instructors: str = "", student: str = "", 
                       quiz_date: str = "", quiz_id: str = ""):
        """
        Create a new ODT quiz document with header components.
        
        Args:
            quiz_type: Type of document (Quiz, Answer Key, etc.)
            course: Course name
            instructors: Instructor names
            student: Student name
            quiz_date: Date of quiz
            quiz_id: Quiz identifier
        """
        # Create new ODT document
        self.doc = OpenDocumentText()
        
        # Store quiz data
        self.quiz_data = {
            'quiz_type': quiz_type,
            'course': course,
            'instructors': instructors,
            'student': student,
            'quiz_date': quiz_date or datetime.now().strftime("%Y-%m-%d"),
            'quiz_id': quiz_id
        }
        
        # Skip metadata due to odfpy version compatibility issues
        # Create styles
        self._create_styles()
        
        # Add header
        self._add_header()
        
    def _create_styles(self):
        """Create document styles matching the PDF system formatting."""
        # Title style (Helvetica Bold 12pt, centered) - fix positioning
        title_style = Style(name="Title", family="paragraph")
        title_style.addElement(ParagraphProperties(
            margintop="0.1in", marginbottom="0.05in", 
            textalign="center", lineheight="100%"
        ))
        title_style.addElement(TextProperties(
            fontfamily="Helvetica", fontsize="12pt", fontweight="bold"
        ))
        self.doc.styles.addElement(title_style)
        
        # Quiz ID style (Helvetica 8pt, positioned near title)
        quiz_id_style = Style(name="QuizID", family="paragraph")
        quiz_id_style.addElement(ParagraphProperties(
            margintop="-0.1in", marginbottom="0.2in",
            textalign="right"
        ))
        quiz_id_style.addElement(TextProperties(
            fontfamily="Helvetica", fontsize="8pt"
        ))
        self.doc.styles.addElement(quiz_id_style)

        # Quiz ID inline span style (8pt regular) - placed right of the title
        quiz_id_span_style = Style(name="QuizIDSpan", family="text")
        quiz_id_span_style.addElement(TextProperties(
            fontfamily="Helvetica", fontsize="8pt", fontweight="normal"
        ))
        self.doc.styles.addElement(quiz_id_span_style)
        
        # Header info style (Helvetica 11pt) - matching PDF spacing
        header_style = Style(name="Header", family="paragraph")
        header_style.addElement(ParagraphProperties(
            margintop="0.05in", marginbottom="0.3in", lineheight="100%"
        ))
        header_style.addElement(TextProperties(
            fontfamily="Helvetica", fontsize="11pt"
        ))
        self.doc.styles.addElement(header_style)
        
        # Signature style
        signature_style = Style(name="Signature", family="paragraph")
        signature_style.addElement(ParagraphProperties(
            margintop="0.1in", marginbottom="0.4in"
        ))
        signature_style.addElement(TextProperties(
            fontfamily="Helvetica", fontsize="11pt"
        ))
        self.doc.styles.addElement(signature_style)
        
        # Calibration marks style - positioned like PDF
        calibration_style = Style(name="Calibration", family="paragraph")
        calibration_style.addElement(ParagraphProperties(
            margintop="0.5in", marginbottom="0.2in"
        ))
        calibration_style.addElement(TextProperties(
            fontfamily="Helvetica", fontsize="6pt"
        ))
        self.doc.styles.addElement(calibration_style)
        
        # Question number style (Helvetica Bold 11pt)
        question_num_style = Style(name="QuestionNumber", family="paragraph")
        question_num_style.addElement(ParagraphProperties(
            margintop="0.2in", marginbottom="0.05in"
        ))
        question_num_style.addElement(TextProperties(
            fontfamily="Helvetica", fontsize="11pt", fontweight="bold"
        ))
        self.doc.styles.addElement(question_num_style)
        
        # Question text style (Helvetica 11pt, indented)
        question_text_style = Style(name="QuestionText", family="paragraph")
        question_text_style.addElement(ParagraphProperties(
            margintop="0.05in", marginbottom="0.15in", textindent="0.25in"
        ))
        question_text_style.addElement(TextProperties(
            fontfamily="Helvetica", fontsize="11pt"
        ))
        self.doc.styles.addElement(question_text_style)

        # Inline question stem style (normal weight, appended to the number)
        question_stem_inline_style = Style(name="QuestionStemInline", family="text")
        question_stem_inline_style.addElement(TextProperties(
            fontfamily="Helvetica", fontsize="11pt", fontweight="normal"
        ))
        self.doc.styles.addElement(question_stem_inline_style)
        
        # Subquestion style (Helvetica 11pt, indented)
        subquestion_style = Style(name="Subquestion", family="paragraph")
        subquestion_style.addElement(ParagraphProperties(
            margintop="0.05in", marginbottom="0.1in", textindent="0.5in"
        ))
        subquestion_style.addElement(TextProperties(
            fontfamily="Helvetica", fontsize="11pt"
        ))
        self.doc.styles.addElement(subquestion_style)
        
        # Answer box table style - single cell with no internal borders
        answer_box_table_style = Style(name="AnswerBoxTable", family="table")
        answer_box_table_style.addElement(TableProperties(
            align="left", maybreakbetweenrows="false", width="100%"))
        self.doc.automaticstyles.addElement(answer_box_table_style)

        # Given-data (concentration) table style
        given_table_style = Style(name="GivenTable", family="table")
        given_table_style.addElement(TableProperties(
            align="left", maybreakbetweenrows="false", width="80%"))
        self.doc.automaticstyles.addElement(given_table_style)

        from odf.style import TableCellProperties
        given_header_cell_style = Style(name="GivenHeaderCell", family="table-cell")
        given_header_cell_style.addElement(TableCellProperties(
            border="0.5pt solid #000000",
            backgroundcolor="#E8E8E8"))
        given_header_cell_style.addElement(TextProperties(
            fontfamily="Helvetica", fontsize="11pt", fontweight="bold"))
        self.doc.automaticstyles.addElement(given_header_cell_style)

        given_data_cell_style = Style(name="GivenDataCell", family="table-cell")
        given_data_cell_style.addElement(TableCellProperties(
            border="0.5pt solid #000000"))
        given_data_cell_style.addElement(TextProperties(
            fontfamily="Helvetica", fontsize="11pt"))
        self.doc.automaticstyles.addElement(given_data_cell_style)

        # Answer box cell style - thick outer border, no internal lines
        # (the table is a single cell, so this is the only border)
        from odf.style import TableCellProperties
        answer_box_cell_style = Style(name="AnswerBoxCell", family="table-cell")
        answer_box_cell_style.addElement(TableCellProperties(
            border="1.5pt solid #000000"))
        self.doc.automaticstyles.addElement(answer_box_cell_style)

        # Answer box row heights (kept for each box type)
        from odf.style import TableRowProperties
        for box_type, height in [('AnswerBoxShort', '0.35in'),
                                  ('AnswerBoxMedium', '0.65in'),
                                  ('AnswerBoxLong', '1.25in'),
                                  ('AnswerBoxEquation', '0.95in')]:
            row_style = Style(name=box_type, family="table-row")
            row_style.addElement(TableRowProperties(minrowheight=height))
            self.doc.automaticstyles.addElement(row_style)
        
    def _add_header(self):
        """Add header components matching the PDF system."""
        # Add centered title (bold, 12pt Helvetica like PDF), with the quiz ID
        # placed inline immediately to the right of the title (8pt, regular),
        # matching the PDF layout.
        title_para = P(stylename="Title")
        title_para.addText(self.quiz_data['quiz_type'])
        if self.quiz_data['quiz_id']:
            title_para.addElement(Span(stylename="QuizIDSpan",
                                       text="   " + self.quiz_data['quiz_id']))
        self.doc.text.addElement(title_para)
        
        # Clean up instructors field (remove brackets and quotes)
        instructors_clean = self.quiz_data['instructors']
        if isinstance(instructors_clean, str):
            instructors_clean = instructors_clean.strip('[]\'\" ')
        
        # Add header information line (matching PDF spacing)
        # Use 'Instructor' (singular) and add extra spaces after instructor/student names.
        header_str = (f"Course: {self.quiz_data['course']}    "
                      f"Instructor: {instructors_clean}        "
                      f"Student: {self.quiz_data['student']}        "
                      f"Date: {self.quiz_data['quiz_date']}")
        header_para = P(stylename="Header", text=header_str)
        self.doc.text.addElement(header_para)
        
        # Add signature line (skip for extra pages)
        if self.quiz_data['quiz_type'] != 'Extra Page':
            signature_para = P(stylename="Signature", 
                             text="Signature: _______________________________")
            self.doc.text.addElement(signature_para)
            
        # Add calibration marks using frames for absolute positioning
        self._add_calibration_marks_frames()
        
    def _add_calibration_marks_frames(self):
        """Add calibration marks via the 'Standard' master page header.

        Four solid 5mm black squares are placed as page-anchored ``draw:rect``
        shapes inside the master-page header. Because they are anchored to the
        page and live in the header, they repeat at the same absolute position
        on *every* page of the document (supporting multi-page quizzes).

        Geometry matches the PDF generator (A4, 210x297mm): square centers are
        20mm from the left/right edges and 18mm from the top/bottom edges.
        Unlike the PDF (which omitted the bottom-right because a QR code lived
        there), all four corners are marked here.
        """
        from odf.style import (PageLayout, PageLayoutProperties, MasterPage,
                               Header, HeaderStyle, HeaderFooterProperties,
                               GraphicProperties)
        from odf.draw import Rect

        # Page geometry constants (mm), matching the PDF generator.
        PAGE_W, PAGE_H = 210.0, 297.0
        SIZE = 5.0          # square edge length
        INSET_X = 20.0      # center distance from left/right edges
        INSET_Y = 18.0      # center distance from top/bottom edges

        # Graphic style: solid black fill, no outline. The horizontal/vertical
        # *-rel="page" attributes are essential: without them LibreOffice
        # measures svg:x/svg:y relative to the text area (offsetting the marks
        # by the page margins) instead of from the page's top-left corner.
        rect_style = Style(name="CalMark", family="graphic")
        rect_style.addElement(GraphicProperties(
            fill="solid", fillcolor="#000000", stroke="none",
            horizontalpos="from-left", horizontalrel="page",
            verticalpos="from-top", verticalrel="page"))
        self.doc.automaticstyles.addElement(rect_style)

        # Page layout: A4 with PDF-matching margins. Top/bottom margins leave
        # room for the corner marks; left/right match the PDF's 10mm.
        page_layout = PageLayout(name="Standard")
        page_layout.addElement(PageLayoutProperties(
            pagewidth=f"{PAGE_W}mm", pageheight=f"{PAGE_H}mm",
            margintop="10mm", marginbottom="18mm",
            marginleft="10mm", marginright="10mm",
            printorientation="portrait"))
        # Minimal header region; the marks are absolutely positioned so the
        # header itself must not push body content down.
        header_style = HeaderStyle()
        header_style.addElement(HeaderFooterProperties(
            minheight="0.1mm", marginbottom="0mm"))
        page_layout.addElement(header_style)
        self.doc.automaticstyles.addElement(page_layout)

        # Master page named 'Standard' so it is applied to the body by default.
        master_page = MasterPage(name="Standard", pagelayoutname="Standard")

        # Top-left corner of each 5mm square (center minus half the edge).
        positions = [
            (INSET_X - SIZE / 2, INSET_Y - SIZE / 2),                    # top-left
            (PAGE_W - INSET_X - SIZE / 2, INSET_Y - SIZE / 2),           # top-right
            (INSET_X - SIZE / 2, PAGE_H - INSET_Y - SIZE / 2),           # bottom-left
            (PAGE_W - INSET_X - SIZE / 2, PAGE_H - INSET_Y - SIZE / 2),  # bottom-right
        ]

        header = Header()
        marks_para = P()
        for x, y in positions:
            marks_para.addElement(Rect(
                stylename=rect_style,
                width=f"{SIZE}mm", height=f"{SIZE}mm",
                x=f"{x}mm", y=f"{y}mm",
                anchortype="page"))
        header.addElement(marks_para)
        master_page.addElement(header)

        self.doc.masterstyles.addElement(master_page)
        
    def add_question(self, number: int, stem: str, subquestions: List[Dict] = None,
                     given_data: Dict = None):
        """
        Add a quantitative question to the quiz.

        Args:
            number: Question number
            stem: Question stem text
            subquestions: List of subquestion dictionaries with keys:
                - 'letter': subquestion letter (a, b, c, etc.)
                - 'text': subquestion text
                - 'has_answer_box': whether to include an answer box
                - 'answer_type': type of answer (short, medium, long, equation)
            given_data: Optional dictionary with 'ion', 'ion_in', 'ion_out' keys
                to render a concentration table between the stem and subquestion a).
        """
        if subquestions is None:
            subquestions = []

        # Add question number and stem on the same line (e.g., "1. Calculate...")
        question_para = P(stylename="QuestionNumber")
        question_para.addText(f"{number}. ")
        question_para.addElement(Span(stylename="QuestionStemInline", text=stem))
        self.doc.text.addElement(question_para)

        # Add given-data table (concentrations) if provided
        if given_data:
            self._add_given_data_table(given_data)

        # Add subquestions
        for subq in subquestions:
            letter = subq.get('letter', '')
            text = subq.get('text', '')
            has_box = subq.get('has_answer_box', False)
            answer_type = subq.get('answer_type', 'medium')  # Default to medium
            
            # Add subquestion text (indented, 11pt Helvetica)
            subq_text = f"{letter}) {text}"
            subq_para = P(stylename="Subquestion", text=subq_text)
            self.doc.text.addElement(subq_para)
            
            # Add actual rectangular answer box if requested
            if has_box:
                self._add_answer_box(answer_type)
                
    def _add_answer_box(self, answer_type: str = "medium"):
        """
        Add an actual rectangular answer box for handwritten responses with variable sizing.

        The answer space is a single-cell table with a thick outer border and no
        internal lines.

        Args:
            answer_type: Type of answer determining box size:
                - 'short': Small box for single numbers/short answers
                - 'medium': Medium box for equations/calculations
                - 'long': Large box for detailed explanations
                - 'equation': Extra large box for complex equations
        """
        # Map answer types to the row styles created in _create_styles
        row_style_map = {
            'short': 'AnswerBoxShort',
            'medium': 'AnswerBoxMedium',
            'long': 'AnswerBoxLong',
            'equation': 'AnswerBoxEquation'
        }
        row_style = row_style_map.get(answer_type, 'AnswerBoxMedium')

        # Single-cell table with a thick outer border (no internal lines possible
        # because there is only one cell).
        table = Table(name="AnswerBox", stylename="AnswerBoxTable")

        col = TableColumn()
        table.addElement(col)

        row = TableRow(stylename=row_style)
        cell = TableCell(stylename="AnswerBoxCell")
        cell.addElement(P(text=""))  # Empty paragraph for handwritten answers
        row.addElement(cell)
        table.addElement(row)

        self.doc.text.addElement(table)

    def _add_given_data_table(self, given_data: Dict):
        """
        Render a concentration table with all four ions.

        Expected keys in given_data:
            - all_ions: list of dicts, each with keys 'ion', 'ion_in', 'ion_out'.
        """
        all_ions = given_data.get('all_ions', [])

        table = Table(name="GivenData", stylename="GivenTable")

        # Three equal columns
        for _ in range(3):
            table.addElement(TableColumn())

        # Header row: generic column labels
        header_row = TableRow()
        for label in ["Ion", "[] inside", "[] outside"]:
            cell = TableCell(stylename="GivenHeaderCell")
            cell.addElement(P(text=label))
            header_row.addElement(cell)
        table.addElement(header_row)

        # Data rows: one per ion (K+, Na+, Cl-, Ca2+)
        for ion_info in all_ions:
            data_row = TableRow()
            for key in ['ion', 'ion_in', 'ion_out']:
                cell = TableCell(stylename="GivenDataCell")
                cell.addElement(P(text=str(ion_info.get(key, ''))))
                data_row.addElement(cell)
            table.addElement(data_row)

        self.doc.text.addElement(table)

    def save_document(self, filename: str):
        """
        Save the ODT document to file.
        
        Args:
            filename: Output filename (should end with .odt)
        """
        if not filename.endswith('.odt'):
            filename += '.odt'
            
        self.doc.save(filename)
        return filename


