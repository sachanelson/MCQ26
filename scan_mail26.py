"""Find scan PDFs in Gmail and group them by original sender, date and subject.

Scans may arrive three ways and each is reduced to the original email's
sender/date/subject:
    direct   - sent straight to the scan account (headers used as-is)
    attached - forwarded with "Forward as attachment" (one .eml per original,
               several originals may share one Gmail message)
    inline   - forwarded normally; the "Forwarded message" block is parsed
"""
import base64
import email
import email.policy
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from dateutil import parser as date_parser

DEFAULT_PREFIX_LEN = 12
DOWNLOADED_LABEL = 'Downloaded'
DEFAULT_QUERY = 'has:attachment newer_than:30d'
MAX_MESSAGES = 200

_SUBJECT_PREFIX_RE = re.compile(r'^\s*(?:(?:re|fw|fwd)\s*:\s*)+', re.IGNORECASE)
_FORWARD_MARKER_RE = re.compile(
    r'^\s*(?:-+\s*(?:forwarded|original) message\s*-+|begin forwarded message:)',
    re.IGNORECASE | re.MULTILINE)
_RFC2822_DATE_RE = re.compile(r'^\s*(?:[A-Za-z]{3},\s*)?\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\s')
_FIELD_RE = re.compile(r'^\s*\*?(from|date|sent|subject)\s*:\s*\*?\s*(.*)$', re.IGNORECASE)


# MIME tree ------------------------------------------------------------------
@dataclass
class Node:
    """A MIME part from either the Gmail API payload or a parsed raw email."""
    mime: str
    filename: str = ''
    headers: Dict[str, str] = field(default_factory=dict)
    text: Optional[str] = None
    data: Optional[bytes] = None
    attachment_id: Optional[str] = None
    children: List['Node'] = field(default_factory=list)

    @property
    def is_pdf(self) -> bool:
        return self.mime == 'application/pdf' or self.filename.lower().endswith('.pdf')

    @property
    def is_message(self) -> bool:
        return self.mime == 'message/rfc822'


def _b64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + '=' * (-len(data) % 4))


def node_from_gmail(part: Dict) -> Node:
    headers = {h['name'].lower(): h['value'] for h in part.get('headers', [])}
    body = part.get('body', {})
    node = Node(
        mime=(part.get('mimeType') or '').lower(),
        filename=part.get('filename') or '',
        headers=headers,
        attachment_id=body.get('attachmentId'),
    )
    if body.get('data'):
        node.data = _b64(body['data'])
        if node.mime == 'text/plain' and not node.filename:
            node.text = node.data.decode('utf-8', errors='replace')
    node.children = [node_from_gmail(p) for p in part.get('parts', [])]
    return node


def node_from_email(msg: email.message.Message) -> Node:
    node = Node(
        mime=msg.get_content_type().lower(),
        filename=msg.get_filename() or '',
        headers={k.lower(): str(v) for k, v in msg.items()},
    )
    if msg.is_multipart():
        node.children = [node_from_email(p) for p in msg.get_payload()]
    elif node.mime == 'message/rfc822':
        payload = msg.get_payload()
        inner = payload[0] if isinstance(payload, list) else payload
        if isinstance(inner, (bytes, str)):
            inner = email.message_from_bytes(
                inner if isinstance(inner, bytes) else inner.encode(), policy=email.policy.default)
        node.children = [node_from_email(inner)]
    else:
        node.data = msg.get_payload(decode=True)
        if node.mime == 'text/plain' and not node.filename and node.data is not None:
            node.text = node.data.decode(msg.get_content_charset() or 'utf-8', errors='replace')
    return node


def parse_raw(raw: bytes) -> Node:
    return node_from_email(email.message_from_bytes(raw, policy=email.policy.default))


# Origin parsing ---------------------------------------------------------------
@dataclass
class Origin:
    sender: str          # lower-case address
    sender_display: str  # as written, e.g. 'Copier <scan@x.edu>'
    sent: Optional[datetime]
    subject: str
    method: str          # direct | attached | inline

    @property
    def local_date(self) -> Optional[date]:
        if self.sent is None:
            return None
        if self.sent.tzinfo is not None:
            return self.sent.astimezone().date()
        return self.sent.date()


def _address(value: str) -> str:
    pairs = getaddresses([value or ''])
    return pairs[0][1].strip().lower() if pairs and pairs[0][1] else (value or '').strip().lower()


def _parse_date(value: str) -> Optional[datetime]:
    if not value:
        return None
    if _RFC2822_DATE_RE.match(value):
        try:
            return parsedate_to_datetime(value)
        except Exception:
            pass
    try:
        cleaned = re.sub(r'\s+at\s+', ' ', value.strip())
        return date_parser.parse(cleaned, fuzzy=True)
    except Exception:
        return None


def origin_from_headers(headers: Dict[str, str], method: str) -> Origin:
    sender = headers.get('from', '')
    return Origin(
        sender=_address(sender),
        sender_display=sender.strip(),
        sent=_parse_date(headers.get('date', '')),
        subject=(headers.get('subject') or '').strip(),
        method=method,
    )


def origin_from_forward_text(text: str) -> Optional[Origin]:
    """Parse the first 'Forwarded message' block of a plain-text body."""
    if not text:
        return None
    marker = _FORWARD_MARKER_RE.search(text)
    if not marker:
        return None
    fields: Dict[str, str] = {}
    for line in text[marker.end():].splitlines()[:15]:
        m = _FIELD_RE.match(line)
        if m:
            key = m.group(1).lower()
            key = 'date' if key == 'sent' else key
            fields.setdefault(key, m.group(2).strip())
        elif fields and not line.strip() and 'subject' in fields:
            break
    if 'from' not in fields:
        return None
    return origin_from_headers(fields, 'inline')


# Scan items ---------------------------------------------------------------------
@dataclass
class PdfRef:
    filename: str
    node: Node


@dataclass
class ScanItem:
    """One original email (with PDFs) found inside a Gmail message."""
    gmail_id: str
    item_index: int
    received: Optional[datetime]
    origin: Origin
    pdfs: List[PdfRef]

    @property
    def key(self) -> Tuple[str, int]:
        return (self.gmail_id, self.item_index)


def _walk(node: Node, stop_at_messages: bool = True) -> Iterable[Node]:
    yield node
    for child in node.children:
        if stop_at_messages and child.is_message:
            yield child
            continue
        yield from _walk(child, stop_at_messages)


def _pdfs(node: Node) -> List[PdfRef]:
    refs = []
    for n in _walk(node, stop_at_messages=True):
        if n.is_pdf and not n.children:
            refs.append(PdfRef(n.filename or 'attachment.pdf', n))
    return refs


def _first_text(node: Node) -> str:
    for n in _walk(node, stop_at_messages=True):
        if n.text:
            return n.text
    return ''


def _message_headers(msg_node: Node) -> Dict[str, str]:
    """Headers of the email wrapped by a message/rfc822 node."""
    if msg_node.children and 'from' in msg_node.children[0].headers:
        return msg_node.children[0].headers
    return msg_node.headers


def extract_items(gmail_id: str, root: Node, received: Optional[datetime] = None) -> List[ScanItem]:
    items: List[ScanItem] = []
    attached = [n for n in _walk(root) if n.is_message and n is not root]
    for msg_node in attached:
        first = msg_node.children[0] if msg_node.children else None
        inner = first if first is not None and 'from' in first.headers else msg_node
        pdfs = _pdfs(inner)
        if pdfs:
            items.append(ScanItem(gmail_id, len(items), received,
                                  origin_from_headers(_message_headers(msg_node), 'attached'), pdfs))
    own_pdfs = _pdfs(root)
    if own_pdfs:
        origin = origin_from_forward_text(_first_text(root)) or origin_from_headers(root.headers, 'direct')
        items.append(ScanItem(gmail_id, len(items), received, origin, own_pdfs))
    return items


def needs_expansion(root: Node) -> List[Node]:
    """Attached emails the Gmail payload did not expand (must be fetched)."""
    return [n for n in _walk(root) if n.is_message and n is not root and not n.children]


# Grouping ----------------------------------------------------------------------
def normalize_subject(subject: str) -> str:
    return re.sub(r'\s+', ' ', _SUBJECT_PREFIX_RE.sub('', subject or '')).strip()


def group_key(origin: Origin, prefix_len: int = DEFAULT_PREFIX_LEN):
    return (origin.sender, origin.local_date,
            normalize_subject(origin.subject)[:prefix_len].casefold())


def _sent_order(item: 'ScanItem'):
    sent = item.origin.sent
    return (sent is None, sent.timestamp() if sent else 0, item.gmail_id, item.item_index)


def find_matches(selected: ScanItem, items: Iterable[ScanItem],
                 prefix_len: int = DEFAULT_PREFIX_LEN,
                 consecutive: bool = False) -> List[ScanItem]:
    """Items with the selected item's sender, date and subject prefix.

    With `consecutive`, only the unbroken run of matches around the selected
    item is kept: emails from the same sender on the same day are put in sent
    order, and any non-matching email between them ends the run.
    """
    key = group_key(selected.origin, prefix_len)
    unique = {i.key: i for i in items}
    unique[selected.key] = selected
    if not consecutive:
        return sorted((i for i in unique.values() if group_key(i.origin, prefix_len) == key),
                      key=_sent_order)
    same_day = sorted((i for i in unique.values()
                       if (i.origin.sender, i.origin.local_date) == key[:2]), key=_sent_order)
    is_match = [group_key(i.origin, prefix_len) == key for i in same_day]
    pos = next(n for n, i in enumerate(same_day) if i.key == selected.key)
    start, end = pos, pos
    while start > 0 and is_match[start - 1]:
        start -= 1
    while end + 1 < len(same_day) and is_match[end + 1]:
        end += 1
    return same_day[start:end + 1]


def search_after_query(origin: Origin) -> str:
    """Gmail query covering every message that could carry `origin` (forwards come later)."""
    day = origin.local_date or date.today()
    return f"has:attachment after:{(day - timedelta(days=1)).strftime('%Y/%m/%d')}"


# Saving ------------------------------------------------------------------------
_UNSAFE_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_filename(name: str) -> str:
    name = _UNSAFE_RE.sub('_', name).strip().strip('.') or 'attachment.pdf'
    if not name.lower().endswith('.pdf'):
        name += '.pdf'
    return name


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_pdf(folder: Path, filename: str, data: bytes) -> Tuple[Optional[Path], str]:
    """Write `data` without overwriting. Returns (path, status).

    status is 'saved', 'renamed', or 'duplicate' (identical file already there;
    path is the existing file).
    """
    folder.mkdir(parents=True, exist_ok=True)
    name = safe_filename(filename)
    stem, suffix = Path(name).stem, Path(name).suffix
    digest = _sha256(data)
    candidate = folder / name
    n = 1
    while candidate.exists():
        if _sha256(candidate.read_bytes()) == digest:
            return candidate, 'duplicate'
        n += 1
        candidate = folder / f'{stem} ({n}){suffix}'
    candidate.write_bytes(data)
    return candidate, 'saved' if n == 1 else 'renamed'


# Gmail access --------------------------------------------------------------------
def gmail_received(msg: Dict) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(int(msg['internalDate']) / 1000).astimezone()
    except Exception:
        return None


def list_message_ids(service, query: str, limit: int = MAX_MESSAGES) -> List[str]:
    ids: List[str] = []
    request = service.users().messages().list(userId='me', q=query, maxResults=min(limit, 500))
    while request is not None and len(ids) < limit:
        response = request.execute()
        ids.extend(m['id'] for m in response.get('messages', []))
        request = service.users().messages().list_next(request, response)
    return ids[:limit]


def load_items(service, gmail_id: str) -> List[ScanItem]:
    msg = service.users().messages().get(userId='me', id=gmail_id, format='full').execute()
    root = node_from_gmail(msg['payload'])
    for msg_node in needs_expansion(root):
        if msg_node.attachment_id:
            msg_node.data = fetch_attachment(service, gmail_id, msg_node.attachment_id)
        if msg_node.data:
            msg_node.children = [parse_raw(msg_node.data)]
    return extract_items(gmail_id, root, gmail_received(msg))


def fetch_attachment(service, gmail_id: str, attachment_id: str) -> bytes:
    att = service.users().messages().attachments().get(
        userId='me', messageId=gmail_id, id=attachment_id).execute()
    return _b64(att['data'])


def fetch_raw_items(service, gmail_id: str) -> List[ScanItem]:
    msg = service.users().messages().get(userId='me', id=gmail_id, format='raw').execute()
    return extract_items(gmail_id, parse_raw(_b64(msg['raw'])), gmail_received(msg))


def pdf_bytes(service, item: ScanItem, pdf_index: int, raw_cache: Dict[str, List[ScanItem]]) -> bytes:
    ref = item.pdfs[pdf_index]
    if ref.node.data:
        return ref.node.data
    if ref.node.attachment_id:
        return fetch_attachment(service, item.gmail_id, ref.node.attachment_id)
    if item.gmail_id not in raw_cache:
        raw_cache[item.gmail_id] = fetch_raw_items(service, item.gmail_id)
    raw_item = raw_cache[item.gmail_id][item.item_index]
    return raw_item.pdfs[pdf_index].node.data or b''


def get_or_create_label(service, name: str = DOWNLOADED_LABEL) -> str:
    labels = service.users().labels().list(userId='me').execute().get('labels', [])
    for label in labels:
        if label['name'].lower() == name.lower():
            return label['id']
    created = service.users().labels().create(userId='me', body={
        'name': name, 'labelListVisibility': 'labelShow', 'messageListVisibility': 'show',
    }).execute()
    return created['id']


def add_label(service, gmail_ids: Iterable[str], label_id: str) -> None:
    ids = list(gmail_ids)
    if ids:
        service.users().messages().batchModify(
            userId='me', body={'ids': ids, 'addLabelIds': [label_id]}).execute()
