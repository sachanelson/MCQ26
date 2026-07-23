"""Sync MCQ26 quiz sessions with a Moodle Choice activity.

This module uses Playwright with a persistent Chrome profile so the user can
log in to Moodle/SSO/Duo once and reuse the session across syncs. It publishes
active MCQ26 quiz sessions as options in the "Quiz Session Signup" Choice
activity and imports student responses back into the MCQ26 database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set
from urllib.parse import urljoin

from playwright.sync_api import Page, sync_playwright

from database26 import (
    Engine,
    Session,
    SessionSignup,
    find_student_by_email_or_name,
    get_all_classrooms,
    get_upcoming_quiz_sessions,
)
from qsession_signup26 import assign_students_to_qsession


DEFAULT_CHOICE_NAME = "Quiz Session Signup"
DEFAULT_PROFILE_DIR = Path.home() / ".mcq26" / "moodle_profile"


class MoodleSyncError(Exception):
    """Raised when Moodle interaction fails."""


@dataclass(frozen=True)
class SessionOption:
    """A Choice option derived from a MCQ26 QuizSession."""

    session_id: int
    label: str
    limit: int


@dataclass(frozen=True)
class MoodleResponse:
    """A single student response parsed from the Choice report."""

    user_name: str
    user_email: str
    option_label: str


@dataclass
class SyncResult:
    """Result of a Moodle Choice sync."""

    published_options: int
    imported_signups: int
    unmatched_users: List[str]
    errors: List[str]


def format_session_label(session: Dict) -> str:
    """Build the text shown for a Choice option from a QuizSession dict."""
    date_obj = datetime.strptime(session["date"], "%Y-%m-%d")
    date_display = date_obj.strftime("%a %b %d")
    return (
        f"{session['session_type'].capitalize()} {date_display} "
        f"{session['start_time']}-{session['end_time']} ({session['room']})"
    )


def _normalize_name(name: str) -> str:
    return " ".join(name.lower().split())


def _base_url(course_url: str) -> str:
    """Return the protocol+host portion of a Moodle course URL."""
    parsed = urljoin(course_url, "/")
    return parsed.rstrip("/")


def _extract_cmid_from_href(href: str) -> int:
    match = re.search(r"[?&]id=(\d+)", href)
    if not match:
        raise MoodleSyncError(f"Could not extract cmid from link: {href}")
    return int(match.group(1))


class MoodleChoiceSync:
    """Playwright-based controller for the Moodle Choice activity."""

    def __init__(
        self,
        course_url: str,
        choice_name: str = DEFAULT_CHOICE_NAME,
        profile_dir: Optional[Path] = None,
        headless: bool = False,
    ):
        self.course_url = course_url
        self.choice_name = choice_name
        self.profile_dir = Path(profile_dir) if profile_dir else DEFAULT_PROFILE_DIR
        self.headless = headless
        self._playwright = None
        self._browser = None
        self._page: Optional[Page] = None

    def __enter__(self) -> "MoodleChoiceSync":
        self._playwright = sync_playwright().start()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._browser = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._page = self._browser.new_page()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    @property
    def page(self) -> Page:
        if self._page is None:
            raise MoodleSyncError("MoodleChoiceSync context not active")
        return self._page

    def _ensure_logged_in(self) -> None:
        """Navigate to the course page and require an active Moodle session."""
        self.page.goto(self.course_url, wait_until="networkidle")
        url = self.page.url.lower()
        if "login" in url or "auth" in url:
            raise MoodleSyncError(
                "Moodle login required. Please log in via the opened browser "
                "and try again. Your session will be saved for future syncs."
            )
        if "duo" in url or "sso" in url:
            raise MoodleSyncError(
                "SSO/Duo authentication required. Please complete login in the "
                "opened browser and try again."
            )

    def find_choice_cmid(self) -> int:
        """Locate the Choice activity on the course page and return its cmid."""
        self._ensure_logged_in()
        links = self.page.locator(f"a:has-text('{self.choice_name}')")
        count = links.count()
        if count == 0:
            raise MoodleSyncError(
                f"Choice activity '{self.choice_name}' not found on course page."
            )
        href = links.first.get_attribute("href")
        if not href:
            raise MoodleSyncError(
                f"Choice activity '{self.choice_name}' has no href on course page."
            )
        return _extract_cmid_from_href(href)

    def _choice_edit_url(self, cmid: int) -> str:
        base = _base_url(self.course_url)
        return f"{base}/course/modedit.php?update={cmid}&return=1&sr=0"

    def _responses_url(self, cmid: int) -> str:
        base = _base_url(self.course_url)
        return f"{base}/mod/choice/report.php?id={cmid}"

    def read_existing_options(self, cmid: int) -> Dict[str, int]:
        """Return the current Choice options as {label: limit}."""
        self.page.goto(self._choice_edit_url(cmid), wait_until="networkidle")
        existing: Dict[str, int] = {}
        option_inputs = self.page.locator("input[name^='option[']")
        for i in range(option_inputs.count()):
            name_attr = option_inputs.nth(i).get_attribute("name")
            if name_attr is None:
                continue
            text = option_inputs.nth(i).input_value().strip()
            if not text:
                continue
            limit_input = self.page.locator(f"input[name='limit[{i}]']")
            limit = 0
            if limit_input.count() > 0:
                try:
                    limit = int(limit_input.input_value() or "0")
                except ValueError:
                    limit = 0
            existing[text] = limit
        return existing

    def _enable_limits(self) -> None:
        """Check the 'limitanswers' setting if it is present and unchecked."""
        checkbox = self.page.locator("input[name='limitanswers']")
        if checkbox.count() > 0:
            try:
                if not checkbox.is_checked():
                    checkbox.check()
            except Exception:
                pass

    def _add_option_rows(self, needed: int) -> None:
        """Click Moodle's 'Add fields' button until enough option rows exist."""
        while True:
            count = self.page.locator("input[name^='option[']").count()
            if count >= needed:
                break
            add_button = self.page.locator(
                "input[value='Add 3 fields to form'], "
                "button:has-text('Add 3 fields to form'), "
                "input[value*='Add'], button:has-text('Add fields')"
            )
            if add_button.count() == 0:
                break
            add_button.first.click()
            self.page.wait_for_timeout(500)

    def set_choice_options(self, cmid: int, options: List[SessionOption]) -> None:
        """Replace the Choice activity options with the supplied list."""
        self.page.goto(self._choice_edit_url(cmid), wait_until="networkidle")
        self._enable_limits()
        self._add_option_rows(len(options))

        for i, opt in enumerate(options):
            option_input = self.page.locator(f"input[name='option[{i}]']")
            option_input.fill(opt.label)
            limit_input = self.page.locator(f"input[name='limit[{i}]']")
            if limit_input.count() > 0:
                limit_input.fill(str(opt.limit))

        # Clear any leftover rows so stale options are removed.
        total = self.page.locator("input[name^='option[']").count()
        for i in range(len(options), total):
            option_input = self.page.locator(f"input[name='option[{i}]']")
            option_input.fill("")
            limit_input = self.page.locator(f"input[name='limit[{i}]']")
            if limit_input.count() > 0:
                limit_input.fill("0")

        submit = self.page.locator(
            "input#id_submitbutton, button#id_submitbutton, "
            "input[type='submit'][value='Save and display'], "
            "button:has-text('Save and display')"
        )
        if submit.count() == 0:
            raise MoodleSyncError("Could not find the Choice save button.")
        submit.first.click()
        self.page.wait_for_load_state("networkidle")

        # Detect simple error messages Moodle surfaces on the form page.
        error_region = self.page.locator(".error, .alert-danger, .notifyproblem")
        if error_region.count() > 0:
            messages = [el.inner_text().strip() for el in error_region.all()]
            if any(messages):
                raise MoodleSyncError("Moodle reported an error: " + "; ".join(messages))

    def read_responses(self, cmid: int) -> List[MoodleResponse]:
        """Parse the Choice responses report into a list of MoodleResponse."""
        self.page.goto(self._responses_url(cmid), wait_until="networkidle")
        responses: List[MoodleResponse] = []
        tables = self.page.locator("table.generaltable")
        for t in range(tables.count()):
            rows = tables.nth(t).locator("tr").all()
            for row in rows[1:]:  # skip header
                cells = row.locator("td").all()
                if len(cells) < 2:
                    continue
                name = cells[0].inner_text().strip()
                option_label = cells[1].inner_text().strip()
                if not name or not option_label:
                    continue
                email = ""
                try:
                    mailto = cells[0].locator("a[href^='mailto:']").get_attribute("href")
                    if mailto:
                        email = mailto.replace("mailto:", "").strip()
                except Exception:
                    pass
                responses.append(MoodleResponse(name, email, option_label))
        return responses

    def build_session_options(
        self,
        engine: Engine,
        from_date: Optional[str] = None,
        days_ahead: Optional[int] = None,
    ) -> List[SessionOption]:
        """Create SessionOption objects from active MCQ26 QuizSession rows.

        Args:
            engine: SQLAlchemy engine.
            from_date: Optional YYYY-MM-DD start date; defaults to today.
            days_ahead: If given, only include sessions up to this many days
                after *from_date*.
        """
        sessions = get_upcoming_quiz_sessions(engine, from_date)
        if days_ahead is not None and from_date is None:
            from_date = datetime.now().strftime("%Y-%m-%d")
        if days_ahead is not None:
            to_date = (
                datetime.strptime(from_date, "%Y-%m-%d") + timedelta(days=days_ahead)
            ).strftime("%Y-%m-%d")
            sessions = [s for s in sessions if s["date"] <= to_date]
        classrooms = {c["name"]: c["capacity"] for c in get_all_classrooms(engine)}
        options = []
        for session in sessions:
            label = format_session_label(session)
            # Prefer the explicit session capacity; fall back to the room lookup.
            capacity = session.get("capacity", 0) or classrooms.get(session.get("room", ""), 0)
            options.append(SessionOption(session["session_id"], label, capacity))
        return options

    def sync(
        self,
        engine: Engine,
        module_number: int,
        course_folder: str,
        from_date: Optional[str] = None,
        days_ahead: Optional[int] = None,
    ) -> SyncResult:
        """Publish sessions to Moodle and import student responses.

        Args:
            engine: SQLAlchemy engine.
            module_number: Module number to assign to imported signups.
            course_folder: Course folder path used to locate available quizzes.
            from_date: Optional YYYY-MM-DD start date; defaults to today.
            days_ahead: Optional number of days into the future to publish.
        """
        options = self.build_session_options(engine, from_date, days_ahead)
        label_to_option = {opt.label: opt for opt in options}

        cmid = self.find_choice_cmid()
        self.set_choice_options(cmid, options)

        responses = self.read_responses(cmid)
        result = SyncResult(
            published_options=len(options),
            imported_signups=0,
            unmatched_users=[],
            errors=[],
        )

        session_students: Dict[int, List[int]] = {}
        for response in responses:
            option = label_to_option.get(response.option_label)
            if option is None:
                result.errors.append(
                    f"Response '{response.option_label}' from {response.user_name} "
                    "does not match any current session option."
                )
                continue

            student = find_student_by_email_or_name(
                engine, response.user_email, response.user_name
            )
            if student is None:
                result.unmatched_users.append(
                    f"{response.user_name} <{response.user_email}>"
                )
                continue

            session_students.setdefault(option.session_id, []).append(student["student_id"])

        # Import signups per session, deduplicating against the database.
        for session_id, student_ids in session_students.items():
            unique_ids = self._filter_new_signups(engine, session_id, student_ids)
            if not unique_ids:
                continue
            try:
                assignment = assign_students_to_qsession(
                    engine,
                    session_id,
                    unique_ids,
                    module_number,
                    course_folder,
                )
                result.imported_signups += assignment['created']
                if assignment['missing_student_ids']:
                    result.errors.append(
                        f"Session {session_id}: "
                        f"{len(assignment['missing_student_ids'])} student(s) had no available quiz PDF."
                    )
            except Exception as exc:
                result.errors.append(
                    f"Failed to assign students to session {session_id}: {exc}"
                )

        return result

    @staticmethod
    def _filter_new_signups(
        engine: Engine, session_id: int, student_ids: Iterable[int]
    ) -> List[int]:
        """Return student_ids that do not already have a signup for the session."""
        existing: Set[int] = set()
        with Session(engine) as session:
            rows = (
                session.query(SessionSignup.student_id)
                .filter(SessionSignup.session_id == session_id)
                .distinct()
                .all()
            )
            existing = {row[0] for row in rows}
        return [sid for sid in student_ids if sid not in existing]


def sync_moodle_choice(
    engine: Engine,
    module_number: int,
    course_folder: str,
    course_url: str,
    choice_name: str = DEFAULT_CHOICE_NAME,
    headless: bool = False,
    profile_dir: Optional[Path] = None,
    from_date: Optional[str] = None,
    days_ahead: Optional[int] = None,
) -> SyncResult:
    """Convenience entry point: open the browser, sync, and close it."""
    with MoodleChoiceSync(
        course_url=course_url,
        choice_name=choice_name,
        profile_dir=profile_dir,
        headless=headless,
    ) as sync:
        return sync.sync(
            engine=engine,
            module_number=module_number,
            course_folder=course_folder,
            from_date=from_date,
            days_ahead=days_ahead,
        )
