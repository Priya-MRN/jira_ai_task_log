"""JIRA clients for the bridge.

Two implementations sit behind a common :class:`JiraClient` interface:

* :class:`RealJiraClient` talks to the JIRA Cloud REST API v3 using basic auth
  (email + API token) and ``requests``.
* :class:`MockJiraClient` is a fully local, SQLite-backed store that mimics the
  behaviour of JIRA (issues get keys like ``LOCAL-1``) and requires no network.

The :func:`get_client` factory auto-selects the real client when credentials
are present and the instance is reachable, otherwise it falls back to the mock
so the tool *always* runs.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import List, Optional

from .config import Settings, get_settings

logger = logging.getLogger("jira_bridge.jira_client")

try:
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore


class JiraClient(ABC):
    """Abstract base for all JIRA clients."""

    #: Human-readable backend name, e.g. "real" or "mock".
    kind: str = "abstract"

    @abstractmethod
    def create_issue(
        self,
        summary: str,
        description: str = "",
        project_key: str = "LOCAL",
        issue_type: str = "Task",
        labels: Optional[List[str]] = None,
    ) -> str:
        """Create an issue and return its key (e.g. ``PROJ-12``)."""

    @abstractmethod
    def transition_issue(self, key: str, status: str) -> None:
        """Move an issue to a new status (e.g. 'In Progress', 'Done')."""

    @abstractmethod
    def add_comment(self, key: str, body: str) -> None:
        """Add a comment to an issue."""

    @abstractmethod
    def add_worklog(self, key: str, minutes: int, comment: str = "") -> None:
        """Log time (in minutes) against an issue."""

    def issue_url(self, key: str) -> Optional[str]:
        """Return a browser URL for the issue, when one exists."""
        return None


# ---------------------------------------------------------------------------
# Real JIRA Cloud client
# ---------------------------------------------------------------------------


def _adf(text: str) -> dict:
    """Wrap plain text in Atlassian Document Format (ADF) for API v3 fields."""
    paragraphs = []
    for line in (text or "").split("\n"):
        if line.strip() == "":
            paragraphs.append({"type": "paragraph", "content": []})
        else:
            paragraphs.append(
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": line}],
                }
            )
    if not paragraphs:
        paragraphs = [{"type": "paragraph", "content": []}]
    return {"type": "doc", "version": 1, "content": paragraphs}


class RealJiraClient(JiraClient):
    """Client backed by the live JIRA Cloud REST API v3."""

    kind = "real"

    def __init__(self, settings: Settings):
        if requests is None:  # pragma: no cover
            raise RuntimeError("The 'requests' package is required for RealJiraClient.")
        self.settings = settings
        self.base_url = (settings.jira_base_url or "").rstrip("/")
        self.auth = (settings.jira_email or "", settings.jira_api_token or "")
        self.session = requests.Session()
        self.session.headers.update(
            {"Accept": "application/json", "Content-Type": "application/json"}
        )

    # -- helpers -----------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def ping(self, timeout: float = 5.0) -> bool:
        """Return True if the instance is reachable and credentials work."""
        try:
            resp = self.session.get(
                self._url("/rest/api/3/myself"), auth=self.auth, timeout=timeout
            )
            return resp.status_code == 200
        except Exception as exc:
            logger.warning("JIRA reachability check failed: %s", exc)
            return False

    # -- interface ---------------------------------------------------------

    def create_issue(
        self,
        summary: str,
        description: str = "",
        project_key: str = "LOCAL",
        issue_type: str = "Task",
        labels: Optional[List[str]] = None,
    ) -> str:
        payload = {
            "fields": {
                "project": {"key": project_key},
                "summary": summary[:255],
                "description": _adf(description),
                "issuetype": {"name": issue_type},
                "labels": [l.replace(" ", "-") for l in (labels or [])],
            }
        }
        resp = self.session.post(
            self._url("/rest/api/3/issue"), json=payload, auth=self.auth, timeout=15
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"JIRA create_issue failed ({resp.status_code}): {resp.text[:300]}"
            )
        return resp.json()["key"]

    def transition_issue(self, key: str, status: str) -> None:
        # Look up available transitions and find one matching the target name.
        resp = self.session.get(
            self._url(f"/rest/api/3/issue/{key}/transitions"),
            auth=self.auth,
            timeout=15,
        )
        resp.raise_for_status()
        transitions = resp.json().get("transitions", [])
        match = next(
            (t for t in transitions if t.get("name", "").lower() == status.lower()),
            None,
        )
        if not match:
            match = next(
                (
                    t
                    for t in transitions
                    if t.get("to", {}).get("name", "").lower() == status.lower()
                ),
                None,
            )
        if not match:
            raise RuntimeError(f"No transition to '{status}' available for {key}")
        self.session.post(
            self._url(f"/rest/api/3/issue/{key}/transitions"),
            json={"transition": {"id": match["id"]}},
            auth=self.auth,
            timeout=15,
        ).raise_for_status()

    def add_comment(self, key: str, body: str) -> None:
        self.session.post(
            self._url(f"/rest/api/3/issue/{key}/comment"),
            json={"body": _adf(body)},
            auth=self.auth,
            timeout=15,
        ).raise_for_status()

    def add_worklog(self, key: str, minutes: int, comment: str = "") -> None:
        payload = {"timeSpentSeconds": max(60, minutes * 60)}
        if comment:
            payload["comment"] = _adf(comment)
        self.session.post(
            self._url(f"/rest/api/3/issue/{key}/worklog"),
            json=payload,
            auth=self.auth,
            timeout=15,
        ).raise_for_status()

    def issue_url(self, key: str) -> Optional[str]:
        if not key:
            return None
        return f"{self.base_url}/browse/{key}"


# ---------------------------------------------------------------------------
# Mock client (local SQLite store mimicking JIRA)
# ---------------------------------------------------------------------------

_MOCK_SCHEMA = """
CREATE TABLE IF NOT EXISTS mock_issues (
    key          TEXT PRIMARY KEY,
    project_key  TEXT,
    seq          INTEGER,
    summary      TEXT,
    description  TEXT,
    issue_type   TEXT,
    status       TEXT,
    labels       TEXT,
    created_at   TEXT
);
CREATE TABLE IF NOT EXISTS mock_comments (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    key       TEXT,
    body      TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS mock_worklogs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    key       TEXT,
    minutes   INTEGER,
    comment   TEXT,
    created_at TEXT
);
"""


class MockJiraClient(JiraClient):
    """Local SQLite-backed stand-in for JIRA. Always available, no network."""

    kind = "mock"

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_MOCK_SCHEMA)
        self._conn.commit()

    def _next_seq(self, project_key: str) -> int:
        cur = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS m FROM mock_issues WHERE project_key = ?",
            (project_key,),
        )
        return int(cur.fetchone()["m"]) + 1

    def create_issue(
        self,
        summary: str,
        description: str = "",
        project_key: str = "LOCAL",
        issue_type: str = "Task",
        labels: Optional[List[str]] = None,
    ) -> str:
        with self._lock:
            seq = self._next_seq(project_key)
            key = f"{project_key}-{seq}"
            self._conn.execute(
                """INSERT INTO mock_issues
                   (key, project_key, seq, summary, description, issue_type,
                    status, labels, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    key,
                    project_key,
                    seq,
                    summary,
                    description,
                    issue_type,
                    "To Do",
                    ",".join(labels or []),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            self._conn.commit()
            return key

    def transition_issue(self, key: str, status: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE mock_issues SET status = ? WHERE key = ?", (status, key)
            )
            self._conn.commit()

    def add_comment(self, key: str, body: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO mock_comments (key, body, created_at) VALUES (?,?,?)",
                (key, body, datetime.now(timezone.utc).isoformat()),
            )
            self._conn.commit()

    def add_worklog(self, key: str, minutes: int, comment: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO mock_worklogs (key, minutes, comment, created_at) "
                "VALUES (?,?,?,?)",
                (key, minutes, comment, datetime.now(timezone.utc).isoformat()),
            )
            self._conn.commit()

    # -- inspection helpers (used in tests / dashboard) --------------------

    def get_issue(self, key: str) -> Optional[dict]:
        cur = self._conn.execute("SELECT * FROM mock_issues WHERE key = ?", (key,))
        row = cur.fetchone()
        return dict(row) if row else None

    def list_issues(self) -> List[dict]:
        cur = self._conn.execute("SELECT * FROM mock_issues ORDER BY created_at DESC")
        return [dict(r) for r in cur.fetchall()]

    def comments_for(self, key: str) -> List[dict]:
        cur = self._conn.execute(
            "SELECT * FROM mock_comments WHERE key = ? ORDER BY id", (key,)
        )
        return [dict(r) for r in cur.fetchall()]

    def worklogs_for(self, key: str) -> List[dict]:
        cur = self._conn.execute(
            "SELECT * FROM mock_worklogs WHERE key = ? ORDER BY id", (key,)
        )
        return [dict(r) for r in cur.fetchall()]

    def issue_url(self, key: str) -> Optional[str]:
        # Local issues have no browser URL; the dashboard renders them as badges.
        return None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_client(
    settings: Optional[Settings] = None, check_reachable: bool = True
) -> JiraClient:
    """Return the best available client.

    Selects :class:`RealJiraClient` when credentials are present and (optionally)
    the instance is reachable; otherwise gracefully falls back to
    :class:`MockJiraClient`. Never raises on a missing/unreachable JIRA.
    """
    settings = settings or get_settings()

    if settings.has_real_jira and requests is not None:
        try:
            client = RealJiraClient(settings)
            if not check_reachable or client.ping():
                logger.info("Using RealJiraClient -> %s", settings.jira_base_url)
                return client
            logger.warning(
                "JIRA credentials present but instance unreachable; "
                "falling back to MockJiraClient."
            )
        except Exception as exc:
            logger.warning("Failed to init RealJiraClient (%s); using mock.", exc)

    # Mock store lives next to the worklog DB but in its own file.
    if settings.db_path == ":memory:":
        mock_path = ":memory:"
    else:
        mock_path = settings.db_path.replace("worklog.db", "mock_jira.db")
        if mock_path == settings.db_path:
            mock_path = settings.db_path + ".mock"
    logger.info("Using MockJiraClient -> %s", mock_path)
    return MockJiraClient(mock_path)
