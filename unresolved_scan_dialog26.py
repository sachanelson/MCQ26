"""Dialog for manually resolving a scanned quiz whose QR code could not be read.

Shows the full scanned first page so a grader can read the printed quiz ID /
student name by eye, then collects the three pieces of information needed to
reconstruct the quiz ID: the module number, the student code, and the 4-digit
quiz/attempt number (the digits after the underscore in the quiz ID).
"""
from typing import Optional, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLabel, QLineEdit,
    QSpinBox, QDialogButtonBox, QScrollArea, QComboBox,
)


class UnresolvedScanDialog(QDialog):
    """Prompts the grader to identify a quiz whose QR code failed to decode."""

    def __init__(self, image_path: Optional[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle('Resolve Unreadable Scan')
        self.resize(760, 800)
        self._init_ui(image_path)

    def _init_ui(self, image_path: Optional[str]) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            'The quiz ID / student code could not be read from this page.\n'
            'Read the printed information below and enter it here.'
        ))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        image_label = QLabel()
        image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap(image_path) if image_path else None
        if pixmap and not pixmap.isNull():
            image_label.setPixmap(pixmap.scaledToWidth(720, Qt.TransformationMode.SmoothTransformation))
        else:
            image_label.setText('(No scanned page image available.)')
        scroll.setWidget(image_label)
        scroll.setMinimumHeight(500)
        layout.addWidget(scroll)

        form = QFormLayout()
        self.module_spin = QSpinBox()
        self.module_spin.setRange(0, 99)
        form.addRow('Module #:', self.module_spin)

        self.student_code_edit = QLineEdit()
        self.student_code_edit.setPlaceholderText('e.g. StA')
        form.addRow('Student Code:', self.student_code_edit)

        self.quiz_number_spin = QSpinBox()
        self.quiz_number_spin.setRange(1, 9999)
        form.addRow('Quiz Number (4 digits after "_"):', self.quiz_number_spin)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Resolve')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Skip')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> Tuple[int, str, int]:
        """Return (module_number, student_code, quiz_number) as entered."""
        return (
            self.module_spin.value(),
            self.student_code_edit.text().strip(),
            self.quiz_number_spin.value(),
        )


class BlankPageDialog(QDialog):
    """Ask the grader to confirm that an unresolved page is blank.

    Shown for no-QR pages whose ink coverage is low enough to be a stray
    blank page but not low enough to auto-drop - it could be a faintly
    written Quant/ODT page.  Accepted means 'blank - skip this page';
    rejected means the caller should fall back to identifying the page with
    UnresolvedScanDialog.
    """

    def __init__(self, image_path: Optional[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle('Blank Page?')
        self.resize(760, 800)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            'This page has no QR code and appears blank.\n'
            'If it is a stray blank page, skip it; if it has faint writing '
            '(e.g. Quant work), identify it instead.'
        ))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        image_label = QLabel()
        image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap(image_path) if image_path else None
        if pixmap and not pixmap.isNull():
            image_label.setPixmap(pixmap.scaledToWidth(720, Qt.TransformationMode.SmoothTransformation))
        else:
            image_label.setText('(No scanned page image available.)')
        scroll.setWidget(image_label)
        scroll.setMinimumHeight(500)
        layout.addWidget(scroll)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.No
        )
        buttons.button(QDialogButtonBox.StandardButton.Yes).setText('Blank - Skip Page')
        buttons.button(QDialogButtonBox.StandardButton.No).setText('Not Blank - Identify')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class TransferPromptDialog(QDialog):
    """Ask whether to transfer a quiz printed for a spare or non-enrolled owner.

    Shows the quiz's first scanned page so the grader can read the name the
    student actually wrote.  Accepted means 'transfer this quiz'.
    """

    def __init__(self, image_path: Optional[str], message: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Transfer Quiz?')
        self.resize(760, 800)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(message))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        image_label = QLabel()
        image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap(image_path) if image_path else None
        if pixmap and not pixmap.isNull():
            image_label.setPixmap(pixmap.scaledToWidth(720, Qt.TransformationMode.SmoothTransformation))
        else:
            image_label.setText('(No scanned page image available.)')
        scroll.setWidget(image_label)
        scroll.setMinimumHeight(500)
        layout.addWidget(scroll)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.No
        )
        buttons.button(QDialogButtonBox.StandardButton.Yes).setText('Transfer…')
        buttons.button(QDialogButtonBox.StandardButton.No).setText('Keep As Is')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class MultipleMarkDialog(QDialog):
    def __init__(
        self, image_path: Optional[str], quiz_id: str, question_number: int,
        question_id: str, detected: list[str], parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle('Resolve Multiple Marks')
        self.resize(760, 800)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f'{quiz_id}, question {question_number} ({question_id})\n'
            f'Marked circles: {", ".join(detected)}\nSelect the intended answer.'
        ))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        image_label = QLabel()
        image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap(image_path) if image_path else None
        if pixmap and not pixmap.isNull():
            image_label.setPixmap(pixmap.scaledToWidth(720, Qt.TransformationMode.SmoothTransformation))
        else:
            image_label.setText('(No scanned page image available.)')
        scroll.setWidget(image_label)
        scroll.setMinimumHeight(500)
        layout.addWidget(scroll)

        form = QFormLayout()
        self.answer_combo = QComboBox()
        self.answer_combo.addItems(detected + ['Count correct', 'Leave unanswered'])
        form.addRow('Answer:', self.answer_combo)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Resolve')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Skip Quiz')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def value(self) -> str:
        return self.answer_combo.currentText()


class MissingAnswerDialog(QDialog):
    """Prompts the grader to supply an answer for an MCQ question where no
    bubble was detected by the reader (e.g. the student used a check mark)."""

    def __init__(
        self, image_path: Optional[str], quiz_id: str, question_number: int,
        question_id: str, parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle('Resolve Missing Answer')
        self.resize(760, 800)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f'{quiz_id}, question {question_number} ({question_id})\n'
            'No answer was detected on the scanned page. Select the intended answer.'
        ))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        image_label = QLabel()
        image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap(image_path) if image_path else None
        if pixmap and not pixmap.isNull():
            image_label.setPixmap(pixmap.scaledToWidth(720, Qt.TransformationMode.SmoothTransformation))
        else:
            image_label.setText('(No scanned page image available.)')
        scroll.setWidget(image_label)
        scroll.setMinimumHeight(500)
        layout.addWidget(scroll)

        form = QFormLayout()
        self.answer_combo = QComboBox()
        self.answer_combo.addItems(['A', 'B', 'C', 'D', 'E', 'Count correct', 'Leave unanswered'])
        self.answer_combo.setCurrentText('Leave unanswered')
        form.addRow('Answer:', self.answer_combo)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Resolve')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Skip Quiz')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def value(self) -> str:
        return self.answer_combo.currentText()
