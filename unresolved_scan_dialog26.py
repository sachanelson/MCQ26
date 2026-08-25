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
    QSpinBox, QDialogButtonBox, QScrollArea,
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
