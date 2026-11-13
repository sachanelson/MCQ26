"""Gmail account settings (feedback / scan download) and the sign-in dialog."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTextBrowser, QVBoxLayout, QWidget,
)

import gmail_accounts26 as accounts

ROLES = {
    'feedback': 'Feedback emails (sending)',
    'scan': 'Scan downloads (reading)',
}


class SignInDialog(QDialog):
    """Explain how to set up Gmail access for an account and offer to sign in."""

    def __init__(self, account: str, parent=None):
        super().__init__(parent)
        self.account = account
        self.setWindowTitle(f'Set up Gmail access: {account}')
        self.resize(720, 560)
        layout = QVBoxLayout(self)
        self.text = QTextBrowser()
        self.text.setOpenExternalLinks(True)
        layout.addWidget(self.text)
        buttons = QDialogButtonBox()
        self.sign_in_button = buttons.addButton('Sign in', QDialogButtonBox.ButtonRole.AcceptRole)
        recheck = buttons.addButton('Re-check credentials.json', QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.sign_in_button.clicked.connect(self._sign_in)
        recheck.clicked.connect(self._refresh)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh()

    def _refresh(self):
        self.text.setHtml(accounts.setup_instructions(self.account))
        self.sign_in_button.setEnabled(accounts.credentials_path() is not None)

    def _sign_in(self):
        QMessageBox.information(
            self, 'Sign in',
            f'A browser window will open. Sign in as {self.account} and allow access, '
            'then return here.')
        try:
            accounts.sign_in(self.account)
        except Exception as e:
            QMessageBox.warning(self, 'Sign-in failed', str(e))
            return
        QMessageBox.information(self, 'Signed in', f'{self.account} is ready to use.')
        self.accept()


def ensure_service(parent, role: str):
    """Return (account, Gmail service) for `role`, prompting setup if needed.

    Returns (account, None) if the user cancels. If no account is configured
    for the role, asks the user to set one on the Accounts tab.
    """
    account = accounts.get_account(role)
    if not account:
        QMessageBox.warning(
            parent, 'No Gmail account set',
            f'Set the Gmail account for {ROLES[role].lower()} on the '
            'Email Management → Accounts tab first.')
        return account, None
    service = accounts.get_service(account)
    if service is None and SignInDialog(account, parent).exec() == QDialog.DialogCode.Accepted:
        service = accounts.get_service(account)
    return account, service


class EmailAccountsPanel(QWidget):
    """Choose which Gmail account sends feedback and which receives scans."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.edits = {}
        self.status_labels = {}
        layout = QVBoxLayout(self)
        box = QGroupBox('Gmail accounts')
        form = QFormLayout(box)
        settings = accounts.load_settings()
        for role, label in ROLES.items():
            row = QHBoxLayout()
            edit = QLineEdit(settings.get(f'{role}_account', ''))
            edit.setPlaceholderText('name@gmail.com')
            edit.editingFinished.connect(self._save)
            status = QLabel()
            status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            sign_in = QPushButton('Sign in / Set up…')
            sign_in.clicked.connect(lambda _=False, r=role: self._sign_in(r))
            row.addWidget(edit, 1)
            row.addWidget(status)
            row.addWidget(sign_in)
            form.addRow(label + ':', row)
            self.edits[role] = edit
            self.status_labels[role] = status
        layout.addWidget(box)
        note = QLabel(
            'Settings are stored per user in ~/.mcq26/email_settings.json and each '
            "account's sign-in token in ~/.mcq26/gmail_tokens/. If the feedback account "
            'is blank, the legacy MCQ26/token.json is used.')
        note.setWordWrap(True)
        layout.addWidget(note)
        check = QPushButton('Check Sign-in Status')
        check.clicked.connect(self._update_status)
        layout.addWidget(check, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()
        self._update_status(check_remote=False)

    def _save(self):
        accounts.save_settings({
            f'{role}_account': edit.text().strip().lower() for role, edit in self.edits.items()
        })
        self._update_status(check_remote=False)

    def _sign_in(self, role):
        self._save()
        account = accounts.get_account(role)
        if not account:
            QMessageBox.warning(self, 'No account', 'Enter a Gmail address first.')
            return
        SignInDialog(account, self).exec()
        self._update_status()

    def _update_status(self, check_remote=True):
        for role, label in self.status_labels.items():
            account = self.edits[role].text().strip().lower()
            if not account:
                label.setText('not set' if role == 'scan' else 'not set (legacy token.json)')
            elif check_remote:
                ok = accounts.load_credentials(account) is not None
                label.setText('signed in' if ok else 'NOT signed in')
            else:
                label.setText('token saved' if accounts.token_path(account).exists()
                              else 'no token yet')
