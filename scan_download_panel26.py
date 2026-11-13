"""Download scan PDFs from Gmail into a local folder.

Pick one email; every email with the same original sender, the same date and
the same first N subject characters is collected, and the chosen PDFs are
saved to a folder (never overwriting; identical files are skipped).
"""
import logging
import os
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QProgressDialog, QPushButton, QSpinBox, QSplitter,
    QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget,
)

import gmail_accounts26 as accounts
import scan_mail26 as sm
from email_accounts_panel26 import ensure_service

logger = logging.getLogger(__name__)

VIA_LABELS = {'direct': 'direct', 'attached': 'fwd as attachment', 'inline': 'forwarded'}


def _fmt(dt, with_time=True):
    if dt is None:
        return ''
    if dt.tzinfo is not None:
        dt = dt.astimezone()
    return dt.strftime('%Y-%m-%d %H:%M' if with_time else '%Y-%m-%d')


class ScanDownloadPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._service = None
        self._account = ''
        self._cache = {}       # gmail_id -> [ScanItem]
        self._rows = []        # ScanItems shown in the email table
        self._matches = []     # ScanItems shown in the match tree
        self._init_ui()

    # UI ---------------------------------------------------------------------
    def _init_ui(self):
        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self.account_label = QLabel()
        search_row.addWidget(self.account_label)
        search_row.addWidget(QLabel('Gmail search:'))
        self.query_edit = QLineEdit(sm.DEFAULT_QUERY)
        self.query_edit.setToolTip('Gmail search syntax, e.g. "has:attachment newer_than:7d" '
                                   'or "has:attachment after:2026/11/01".')
        self.query_edit.returnPressed.connect(self._load_emails)
        search_row.addWidget(self.query_edit, 1)
        self.include_done = QCheckBox(f'Include "{sm.DOWNLOADED_LABEL}"')
        self.include_done.setToolTip('Also list emails already labelled as downloaded.')
        search_row.addWidget(self.include_done)
        load_button = QPushButton('Load Emails')
        load_button.clicked.connect(self._load_emails)
        search_row.addWidget(load_button)
        layout.addLayout(search_row)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(['Received', 'Original From', 'Sent', 'Subject', 'PDFs', 'Via'])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.itemDoubleClicked.connect(lambda _item: self._find_matches())
        splitter.addWidget(self.table)

        match_widget = QWidget()
        match_layout = QVBoxLayout(match_widget)
        match_layout.setContentsMargins(0, 0, 0, 0)
        match_row = QHBoxLayout()
        match_row.addWidget(QLabel('Match on sender, date and first'))
        settings = accounts.load_settings()
        self.prefix_spin = QSpinBox()
        self.prefix_spin.setRange(1, 200)
        self.prefix_spin.setValue(int(settings.get('scan_prefix_len') or sm.DEFAULT_PREFIX_LEN))
        self.prefix_spin.setToolTip('Number of leading subject characters (after Fwd:/Re:) that must match.')
        self.prefix_spin.valueChanged.connect(
            lambda v: accounts.save_settings({'scan_prefix_len': v}))
        match_row.addWidget(self.prefix_spin)
        match_row.addWidget(QLabel('subject characters'))
        self.consecutive_check = QCheckBox('Require consecutive emails')
        self.consecutive_check.setChecked(bool(settings.get('scan_consecutive')))
        self.consecutive_check.setToolTip(
            "Only collect the unbroken run of matching emails around the selected one: the "
            "sender's emails that day are put in sent order and any non-matching email "
            "between them ends the run.")
        self.consecutive_check.toggled.connect(
            lambda v: accounts.save_settings({'scan_consecutive': v}))
        match_row.addWidget(self.consecutive_check)
        find_button = QPushButton('Find Matching Emails')
        find_button.clicked.connect(self._find_matches)
        match_row.addWidget(find_button)
        match_row.addStretch()
        match_layout.addLayout(match_row)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['Email / PDF', 'Sent', 'From', 'Via'])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        match_layout.addWidget(self.tree)
        splitter.addWidget(match_widget)
        layout.addWidget(splitter, 1)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel('Save to:'))
        self.folder_edit = QLineEdit(accounts.load_settings().get('scan_download_dir', ''))
        folder_row.addWidget(self.folder_edit, 1)
        choose = QPushButton('Choose Folder…')
        choose.clicked.connect(self._choose_folder)
        folder_row.addWidget(choose)
        self.label_check = QCheckBox(f'Label emails "{sm.DOWNLOADED_LABEL}" in Gmail')
        self.label_check.setChecked(True)
        folder_row.addWidget(self.label_check)
        download = QPushButton('Download Checked PDFs')
        download.clicked.connect(self._download)
        folder_row.addWidget(download)
        layout.addLayout(folder_row)

        self.status_label = QLabel(
            'Load emails, select one (double-click or "Find Matching Emails"), check the PDFs '
            'to keep, choose a folder and click Download.')
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self._show_account()

    def _show_account(self):
        account = accounts.get_account('scan')
        self.account_label.setText(f'Account: {account or "(not set)"}')

    # Gmail ------------------------------------------------------------------
    def _get_service(self):
        account = accounts.get_account('scan')
        if self._service is None or account != self._account:
            self._account, self._service = ensure_service(self, 'scan')
            self._cache.clear()
        self._show_account()
        return self._service

    def _query(self, base):
        if not self.include_done.isChecked():
            base = f'{base} -label:"{sm.DOWNLOADED_LABEL}"'
        return base

    def _load_ids(self, service, ids, title):
        """Load ScanItems for Gmail ids (cached). Returns False if cancelled."""
        todo = [i for i in ids if i not in self._cache]
        if not todo:
            return True
        progress = QProgressDialog(title, 'Cancel', 0, len(todo), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(300)
        for n, gmail_id in enumerate(todo):
            if progress.wasCanceled():
                return False
            progress.setValue(n)
            QApplication.processEvents()
            try:
                self._cache[gmail_id] = sm.load_items(service, gmail_id)
            except Exception as e:
                logger.error(f'Could not load message {gmail_id}: {e}')
                self._cache[gmail_id] = []
        progress.setValue(len(todo))
        return True

    def _load_emails(self):
        service = self._get_service()
        if service is None:
            return
        try:
            ids = sm.list_message_ids(service, self._query(self.query_edit.text().strip()))
        except Exception as e:
            QMessageBox.warning(self, 'Gmail search failed', str(e))
            return
        self._load_ids(service, ids, 'Reading emails…')
        self._rows = [item for gid in ids for item in self._cache.get(gid, [])]
        self.table.setRowCount(len(self._rows))
        for row, item in enumerate(self._rows):
            o = item.origin
            values = [_fmt(item.received), o.sender_display, _fmt(o.sent), o.subject,
                      ', '.join(p.filename for p in item.pdfs), VIA_LABELS.get(o.method, o.method)]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(value)
                self.table.setItem(row, col, cell)
        self.tree.clear()
        self._matches = []
        limit_note = f' (limit {sm.MAX_MESSAGES} messages)' if len(ids) >= sm.MAX_MESSAGES else ''
        self.status_label.setText(f'{len(self._rows)} email(s) with PDFs in {len(ids)} message(s){limit_note}.')

    def _find_matches(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._rows):
            QMessageBox.warning(self, 'No Email Selected', 'Select an email first.')
            return
        service = self._get_service()
        if service is None:
            return
        selected = self._rows[row]
        try:
            ids = sm.list_message_ids(service, self._query(sm.search_after_query(selected.origin)))
        except Exception as e:
            QMessageBox.warning(self, 'Gmail search failed', str(e))
            return
        self._load_ids(service, ids, 'Searching for matching emails…')
        candidates = [item for gid in set(ids) | {selected.gmail_id} for item in self._cache.get(gid, [])]
        self._matches = sm.find_matches(selected, candidates, self.prefix_spin.value(),
                                        consecutive=self.consecutive_check.isChecked())
        self._fill_tree()

    def _fill_tree(self):
        self.tree.clear()
        n_pdfs = 0
        for item in self._matches:
            o = item.origin
            parent = QTreeWidgetItem([o.subject, _fmt(o.sent), o.sender_display,
                                      VIA_LABELS.get(o.method, o.method)])
            parent.setFlags(parent.flags() | Qt.ItemFlag.ItemIsUserCheckable
                            | Qt.ItemFlag.ItemIsAutoTristate)
            parent.setCheckState(0, Qt.CheckState.Checked)
            for idx, pdf in enumerate(item.pdfs):
                child = QTreeWidgetItem([pdf.filename])
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(0, Qt.CheckState.Checked)
                child.setData(0, Qt.ItemDataRole.UserRole, (item.key, idx))
                parent.addChild(child)
                n_pdfs += 1
            self.tree.addTopLevelItem(parent)
            parent.setExpanded(True)
        if self._matches:
            o = self._matches[0].origin
            self.status_label.setText(
                f'{len(self._matches)} matching email(s), {n_pdfs} PDF(s) from {o.sender} '
                f'on {_fmt(o.sent, with_time=False)}.')

    # Saving -----------------------------------------------------------------
    def _choose_folder(self):
        start = self.folder_edit.text().strip()
        if not start or not os.path.isdir(start):
            start = os.path.expanduser('~')
        path = QFileDialog.getExistingDirectory(self, 'Select Folder for Scan PDFs', start)
        if path:
            self.folder_edit.setText(path)
            accounts.save_settings({'scan_download_dir': path})

    def _checked_pdfs(self):
        """Return {item_key: [pdf_index, ...]} for checked PDFs."""
        checked = {}
        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            for j in range(parent.childCount()):
                child = parent.child(j)
                if child.checkState(0) == Qt.CheckState.Checked:
                    key, idx = child.data(0, Qt.ItemDataRole.UserRole)
                    checked.setdefault(tuple(key), []).append(idx)
        return checked

    def _download(self):
        checked = self._checked_pdfs()
        if not checked:
            QMessageBox.warning(self, 'Nothing to Download', 'Find matching emails and check PDFs first.')
            return
        folder = self.folder_edit.text().strip()
        if not folder:
            self._choose_folder()
            folder = self.folder_edit.text().strip()
            if not folder:
                return
        service = self._get_service()
        if service is None:
            return
        accounts.save_settings({'scan_download_dir': folder})
        folder_path = Path(folder)
        items = {item.key: item for item in self._matches}
        total = sum(len(v) for v in checked.values())
        progress = QProgressDialog('Downloading PDFs…', 'Cancel', 0, total, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(300)
        raw_cache = {}
        counts = {'saved': 0, 'renamed': 0, 'duplicate': 0}
        errors, renamed = [], []
        done = 0
        for key, indexes in checked.items():
            item = items[key]
            for idx in indexes:
                if progress.wasCanceled():
                    break
                progress.setValue(done)
                QApplication.processEvents()
                name = item.pdfs[idx].filename
                try:
                    data = sm.pdf_bytes(service, item, idx, raw_cache)
                    path, status = sm.save_pdf(folder_path, name, data)
                    counts[status] += 1
                    if status == 'renamed':
                        renamed.append(f'{name} → {path.name}')
                except Exception as e:
                    logger.error(f'Could not save {name}: {e}')
                    errors.append(f'{name}: {e}')
                done += 1
        progress.setValue(total)

        labelled = 0
        if self.label_check.isChecked() and not errors and done == total:
            fully_done = {k for k, v in checked.items() if len(v) == len(items[k].pdfs)}
            gmail_ids = {gid for gid, _ in fully_done
                         if all(i.key in fully_done for i in self._cache.get(gid, []))}
            try:
                sm.add_label(service, gmail_ids, sm.get_or_create_label(service))
                labelled = len(gmail_ids)
            except Exception as e:
                errors.append(f'Labelling failed: {e}')

        lines = [f"Saved {counts['saved'] + counts['renamed']} PDF(s) to {folder}."]
        if counts['duplicate']:
            lines.append(f"Skipped {counts['duplicate']} identical file(s) already in the folder.")
        if renamed:
            lines.append('Renamed to avoid overwriting:\n  ' + '\n  '.join(renamed))
        if labelled:
            lines.append(f'Labelled {labelled} Gmail message(s) "{sm.DOWNLOADED_LABEL}".')
        if done < total:
            lines.append('Cancelled before all PDFs were downloaded.')
        if errors:
            lines.append('Errors:\n  ' + '\n  '.join(errors))
            QMessageBox.warning(self, 'Download Finished with Errors', '\n\n'.join(lines))
        else:
            QMessageBox.information(self, 'Download Finished', '\n\n'.join(lines))
        self.status_label.setText(lines[0])
