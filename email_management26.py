from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QDialog,
    QTextEdit, QLineEdit, QFormLayout, QDialogButtonBox,
)
from PyQt6.QtCore import Qt

from database26 import (
    delete_outgoing_email, get_outgoing_emails, update_outgoing_email,
)
from email26 import send_queued_email


class EmailManagementPanel(QWidget):
    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._init_ui()
        self._refresh_emails()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        refresh_button = QPushButton('Refresh')
        refresh_button.clicked.connect(self._refresh_emails)
        controls.addWidget(refresh_button)
        send_button = QPushButton('Send Selected Queued Email(s)')
        send_button.clicked.connect(self._send_selected)
        controls.addWidget(send_button)
        edit_button = QPushButton('Edit Selected Queued Email')
        edit_button.clicked.connect(self._edit_selected)
        controls.addWidget(edit_button)
        delete_button = QPushButton('Delete Selected Email(s)')
        delete_button.clicked.connect(self._delete_selected)
        controls.addWidget(delete_button)
        controls.addStretch()
        layout.addLayout(controls)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([
            'Recipient', 'Subject', 'Type', 'Created',
        ])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemDoubleClicked.connect(self._show_selected_email)
        layout.addWidget(self.table)
        layout.addWidget(QLabel('Double-click an email to view it (queued emails can be edited).'))

    def _refresh_emails(self):
        self._emails = get_outgoing_emails(self.engine, status='queued')
        self.table.setRowCount(len(self._emails))
        for row, email in enumerate(self._emails):
            values = [
                email['recipient'], email['subject'], email['email_type'],
                email['created_at'],
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, email['email_id'])
                self.table.setItem(row, column, item)

    def _selected_rows(self):
        """Return the unique selected row indices, sorted ascending."""
        return sorted({index.row() for index in self.table.selectedIndexes()})

    def _selected_emails(self):
        rows = self._selected_rows()
        return [self._emails[row] for row in rows if 0 <= row < len(self._emails)]

    def _show_selected_email(self, item=None):
        if item is not None:
            row = item.row()
        else:
            rows = self._selected_rows()
            row = rows[0] if rows else self.table.currentRow()
        if row < 0 or row >= len(self._emails):
            return
        email = self._emails[row]
        editable = email['status'] == 'queued'
        dialog = QDialog(self)
        dialog.setWindowTitle(email['subject'])
        dialog.resize(720, 480)
        layout = QVBoxLayout(dialog)
        if editable:
            recipient_edit = QLineEdit(email['recipient'])
            subject_edit = QLineEdit(email['subject'])
            form = QFormLayout()
            form.addRow('To:', recipient_edit)
            form.addRow('Subject:', subject_edit)
            layout.addLayout(form)
        else:
            layout.addWidget(QLabel(f"To: {email['recipient']}"))
            layout.addWidget(QLabel(f"Subject: {email['subject']}"))
        layout.addWidget(QLabel(f"Status: {email['status']}"))
        body = QTextEdit()
        body.setReadOnly(not editable)
        body.setPlainText(email['body'])
        layout.addWidget(body)
        if editable:
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Save
                | QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(
                lambda: self._save_email(dialog, email['email_id'],
                                         recipient_edit.text(),
                                         subject_edit.text(),
                                         body.toPlainText()))
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
        else:
            close_button = QPushButton('Close')
            close_button.clicked.connect(dialog.accept)
            layout.addWidget(close_button)
        dialog.exec()

    def _edit_selected(self):
        emails = self._selected_emails()
        if len(emails) != 1:
            QMessageBox.warning(self, 'Edit Email', 'Select exactly one email to edit.')
            return
        if emails[0]['status'] != 'queued':
            QMessageBox.warning(self, 'Edit Email', 'Only queued emails can be edited.')
            return
        self._show_selected_email()

    def _save_email(self, dialog, email_id, recipient, subject, body):
        recipient = recipient.strip()
        subject = subject.strip()
        if not recipient or not subject:
            QMessageBox.warning(self, 'Edit Email', 'Recipient and subject are required.')
            return
        if update_outgoing_email(self.engine, email_id, recipient=recipient,
                                 subject=subject, body=body):
            dialog.accept()
            self._refresh_emails()
        else:
            QMessageBox.warning(self, 'Edit Email',
                                'Could not save: email is no longer queued.')

    def _delete_selected(self):
        emails = self._selected_emails()
        if not emails:
            QMessageBox.warning(self, 'No Email Selected', 'Select one or more emails to delete.')
            return
        if len(emails) == 1:
            detail = f"the email to {emails[0]['recipient']} with subject:\n{emails[0]['subject']}"
        else:
            detail = f"{len(emails)} selected emails"
        answer = QMessageBox.question(
            self,
            'Delete Email(s)?',
            f"Delete {detail}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        missing = []
        for email in emails:
            if not delete_outgoing_email(self.engine, email['email_id']):
                missing.append(str(email['email_id']))
        if missing:
            QMessageBox.warning(self, 'Email(s) Not Deleted', f"Some emails no longer exist: {', '.join(missing)}")
        self._refresh_emails()

    def _send_selected(self):
        emails = [e for e in self._selected_emails() if e['status'] == 'queued']
        if not emails:
            QMessageBox.warning(self, 'No Queued Email Selected', 'Select one or more queued emails to send.')
            return
        failed = []
        for email in emails:
            if not send_queued_email(
                self.engine,
                email['email_id'],
                email['recipient'],
                email['subject'],
                email['body'],
                email['email_type'],
            ):
                failed.append(email['recipient'])
        if failed:
            QMessageBox.warning(
                self, 'Email(s) Not Sent',
                f"The following could not be sent:\n{', '.join(failed)}\nSee the status tooltip for details."
            )
        else:
            QMessageBox.information(self, 'Email(s) Sent', f"Sent {len(emails)} email(s).")
        self._refresh_emails()
