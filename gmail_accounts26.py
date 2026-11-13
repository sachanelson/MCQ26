"""Gmail account selection and per-account OAuth tokens for MCQ26.

Settings live in ~/.mcq26/email_settings.json (one file per user/machine):
    feedback_account  - Gmail account that sends queued feedback emails
    scan_account      - Gmail account that receives scan PDFs to download
    scan_download_dir - last folder chosen for scan downloads
    scan_prefix_len   - subject characters that must match for scan downloads
    scan_consecutive  - only collect an unbroken run of matching emails

Each account has its own token at ~/.mcq26/gmail_tokens/<account>.json.
The OAuth client file (credentials.json) is shared by all accounts and is
looked up in ~/.mcq26/ first, then in the MCQ26 folder.
"""
import json
import logging
import os
from pathlib import Path
from typing import Dict, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

CONFIG_DIR = Path.home() / '.mcq26'
SETTINGS_PATH = CONFIG_DIR / 'email_settings.json'
TOKENS_DIR = CONFIG_DIR / 'gmail_tokens'
MCQ26_DIR = Path(__file__).resolve().parent
LEGACY_TOKEN_PATH = MCQ26_DIR / 'token.json'

DEFAULT_SETTINGS = {
    'feedback_account': '',
    'scan_account': '',
    'scan_download_dir': '',
    'scan_prefix_len': 12,
    'scan_consecutive': False,
}

SETUP_INSTRUCTIONS = """\
<b>Gmail access for {account} is not set up yet.</b>
<p>MCQ26 needs two things to use a Gmail account:</p>
<ol>
<li><b>An OAuth client file (credentials.json)</b> &mdash; created once and
shared by every account you use.{credentials_status}
<ol type="a">
<li>Go to <a href="https://console.cloud.google.com/">console.cloud.google.com</a>
and create (or pick) a project.</li>
<li>APIs &amp; Services &rarr; Library &rarr; search for <b>Gmail API</b> &rarr; Enable.</li>
<li>APIs &amp; Services &rarr; OAuth consent screen (Google Auth Platform):
choose <b>External</b>, fill in an app name and your email, and under
<b>Audience / Test users</b> add every Gmail address you will use
(e.g. {account}).</li>
<li>APIs &amp; Services &rarr; Credentials &rarr; Create credentials &rarr;
<b>OAuth client ID</b> &rarr; Application type <b>Desktop app</b> &rarr;
Create &rarr; <b>Download JSON</b>.</li>
<li>Save the downloaded file as <code>{credentials_target}</code>.</li>
</ol></li>
<li><b>A sign-in token for {account}</b> &mdash; click <b>Sign in</b> below.
A browser window opens; choose {account}, accept the "Google hasn't
verified this app" warning (Advanced &rarr; Continue) and allow access.
The token is saved to <code>{token_path}</code>.</li>
</ol>
<p>Note: while the app's publishing status is "Testing", Google expires
sign-ins after about 7 days; MCQ26 will then ask you to sign in again.
Publishing the app (OAuth consent screen &rarr; Publish app) avoids this.</p>
"""


def load_settings() -> Dict:
    settings = dict(DEFAULT_SETTINGS)
    if SETTINGS_PATH.exists():
        try:
            settings.update(json.loads(SETTINGS_PATH.read_text()))
        except Exception as e:
            logger.error(f"Could not read {SETTINGS_PATH}: {e}")
    return settings


def save_settings(updates: Dict) -> Dict:
    settings = load_settings()
    settings.update(updates)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2))
    return settings


def get_account(role: str) -> str:
    """Return the configured account for 'feedback' or 'scan' ('' if unset)."""
    return (load_settings().get(f'{role}_account') or '').strip().lower()


def token_path(account: str) -> Path:
    return TOKENS_DIR / f'{account.strip().lower()}.json'


def credentials_path() -> Optional[Path]:
    for path in (CONFIG_DIR / 'credentials.json', MCQ26_DIR / 'credentials.json'):
        if path.exists():
            return path
    return None


def setup_instructions(account: str) -> str:
    creds_file = credentials_path()
    if creds_file:
        status = f' <i>Found: <code>{creds_file}</code> &mdash; skip to step 2.</i>'
    else:
        status = ' <i>Not found.</i>'
    return SETUP_INSTRUCTIONS.format(
        account=account or 'this account',
        credentials_status=status,
        credentials_target=CONFIG_DIR / 'credentials.json',
        token_path=token_path(account) if account else TOKENS_DIR / '<account>.json',
    )


def _profile_email(creds: Credentials) -> str:
    service = build('gmail', 'v1', credentials=creds, cache_discovery=False)
    return service.users().getProfile(userId='me').execute()['emailAddress'].lower()


def _save_token(account: str, creds: Credentials) -> None:
    TOKENS_DIR.mkdir(parents=True, exist_ok=True)
    path = token_path(account)
    path.write_text(creds.to_json())
    os.chmod(path, 0o600)


def _load_valid_token(path: Path) -> Optional[Credentials]:
    if not path.exists():
        return None
    try:
        creds = Credentials.from_authorized_user_info(json.loads(path.read_text()), SCOPES)
    except Exception as e:
        logger.error(f"Error loading token {path}: {e}")
        return None
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            return creds
        except Exception as e:
            logger.error(f"Failed to refresh token {path}: {e}")
    return None


def _migrate_legacy_token(account: str) -> Optional[Credentials]:
    """Adopt MCQ26/token.json for `account` if it is signed in to that account."""
    creds = _load_valid_token(LEGACY_TOKEN_PATH)
    if creds is None:
        return None
    try:
        if _profile_email(creds) != account:
            return None
    except Exception as e:
        logger.error(f"Could not check legacy token account: {e}")
        return None
    _save_token(account, creds)
    logger.info(f"Copied legacy token.json to {token_path(account)}")
    return creds


def load_credentials(account: str) -> Optional[Credentials]:
    """Return valid saved credentials for `account`, or None (no browser flow)."""
    account = account.strip().lower()
    if not account:
        return None
    path = token_path(account)
    creds = _load_valid_token(path)
    if creds is not None:
        _save_token(account, creds)
        return creds
    if not path.exists():
        return _migrate_legacy_token(account)
    return None


def sign_in(account: str) -> Credentials:
    """Run the browser OAuth flow for `account` and save its token.

    Raises FileNotFoundError if credentials.json is missing and ValueError
    if the user signs in to a different account.
    """
    account = account.strip().lower()
    creds_file = credentials_path()
    if creds_file is None:
        raise FileNotFoundError(
            f'OAuth client file not found. Save it as {CONFIG_DIR / "credentials.json"}.')
    flow = InstalledAppFlow.from_client_secrets_file(str(creds_file), SCOPES)
    creds = flow.run_local_server(port=0, login_hint=account, prompt='consent')
    signed_in = _profile_email(creds)
    if signed_in != account:
        raise ValueError(f'You signed in as {signed_in}, not {account}.')
    _save_token(account, creds)
    return creds


def get_service(account: str):
    """Return a Gmail API service for `account`, or None if it is not signed in."""
    creds = load_credentials(account)
    if creds is None:
        return None
    return build('gmail', 'v1', credentials=creds, cache_discovery=False)


def get_service_for_role(role: str):
    """Return (account, service) for role 'feedback' or 'scan'."""
    account = get_account(role)
    return account, (get_service(account) if account else None)
