from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QDialog,
    QTextEdit,
)
from PyQt6.QtCore import Qt

from database26 import get_outgoing_emails
from email26 import get_autosend_status, send_queued_email, set_autosend_status


class EmailManagementPanel(QWidget):
    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._init_ui()
        self._refresh_emails()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.autosend_checkbox = QCheckBox('Autosend feedback emails')
        self.autosend_checkbox.setChecked(get_autosend_status())
        self.autosend_checkbox.toggled.connect(set_autosend_status)
        controls.addWidget(self.autosend_checkbox)
        refresh_button = QPushButton('Refresh')
        refresh_button.clicked.connect(self._refresh_emails)
        controls.addWidget(refresh_button)
        send_button = QPushButton('Send Selected Queued Email')
        send_button.clicked.connect(self._send_selected)
        controls.addWidget(send_button)
        controls.addStretch()
        layout.addLayout(controls)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            'Status', 'Recipient', 'Subject', 'Type', 'Created', 'Sent',
        ])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemDoubleClicked.connect(self._show_selected_email)
        layout.addWidget(self.table)
        layout.addWidget(QLabel('Double-click an email to view its contents.'))

    def _refresh_emails(self):
        self._emails = get_outgoing_emails(self.engine)
        self.table.setRowCount(len(self._emails))
        for row, email in enumerate(self._emails):
            values = [
                email['status'], email['recipient'], email['subject'], email['email_type'],
                email['created_at'], email['sent_at'],
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, email['email_id'])
                if column == 0 and email['error']:
                    item.setToolTip(email['error'])
                self.table.setItem(row, column, item)

    def _selected_email(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._emails):
            return None
        return self._emails[row]

    def _show_selected_email(self, _item=None):
        email = self._selected_email()
        if email is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(email['subject'])
        dialog.resize(720, 480)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"To: {email['recipient']}"))
        layout.addWidget(QLabel(f"Status: {email['status']}"))
        body = QTextEdit()
        body.setReadOnly(True)
        body.setPlainText(email['body'])
        layout.addWidget(body)
        close_button = QPushButton('Close')
        close_button.clicked.connect(dialog.accept)
        layout.addWidget(close_button)
        dialog.exec()

    def _send_selected(self):
        email = self._selected_email()
        if email is None:
            QMessageBox.warning(self, 'No Email Selected', 'Select a queued email first.')
            return
        if email['status'] != 'queued':
            QMessageBox.warning(self, 'Email Not Queued', 'Only queued emails can be sent manually.')
            return
        if send_queued_email(
            self.engine,
            email['email_id'],
            email['recipient'],
            email['subject'],
            email['body'],
            email['email_type'],
        ):
            QMessageBox.information(self, 'Email Sent', f"Sent email to {email['recipient']}.")
        else:
            QMessageBox.warning(self, 'Email Not Sent', 'The email could not be sent. See the status tooltip for details.')
        self._refresh_emails()
