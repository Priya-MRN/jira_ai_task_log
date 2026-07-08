"""Core engine for JIRA AI Bridge.

Defines the :class:`WorkEvent` data model and the :class:`BridgeEngine`, which
ingests work events, deduplicates them, persists them to a local SQLite store,
infers concise task titles/descriptions/issue-types from raw input, and pushes
them to JIRA via a pluggable client.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

VALID_STATUSES = ("pending", "synced", "done", "error")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class WorkEvent:
    """A single unit of work captured by a watcher.

    Attributes:
        id: Stable unique identifier (uuid4 hex).
        title: Concise human-readable summary (inferred if not supplied).
        description: Longer description / context.
        source: Where the event came from -- "git", "claude", or "cli".
        project_key: JIRA project key this event belongs to.
        started_at: ISO timestamp when the work started.
        ended_at: ISO timestamp when the work ended.
        files_changed: List of file paths touched.
        status: One of VALID_STATUSES.
        jira_key: The JIRA issue key once synced (e.g. "PROJ-12" / "LOCAL-3").
        tags: Free-form tags / labels.
        issue_type: Inferred JIRA issue type ("Task", "Bug", ...).
        dedup_hash: Content hash used for deduplication.
    """

    title: str
    description: str = ""
    source: str = "cli"
    project_key: str = "LOCAL"
    started_at: str = field(default_factory=_now_iso)
    ended_at: str = field(default_factory=_now_iso)
    files_changed: List[str] = field(default_factory=list)
    status: str = "pending"
    jira_key: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    issue_type: str = "Task"
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    dedup_hash: Optional[str] = None

    def compute_hash(self) -> str:
        """Compute a stable content hash for deduplication.

        Two events are considered duplicates when they share the same source,
        title and the same sorted set of changed files within the same minute
        window. This keeps repeated polls from a watcher from producing dupes.
        """
        minute_bucket = (self.ended_at or "")[:16]  # YYYY-MM-DDTHH:MM
        payload = "|".join(
            [
                self.source,
                self.title.strip().lower(),
                ",".join(sorted(self.files_changed)),
                minute_bucket,
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def duration_minutes(self) -> int:
        """Best-effort duration in minutes between started_at and ended_at."""
        try:
            start = datetime.fromisoformat(self.started_at)
            end = datetime.fromisoformat(self.ended_at)
            delta = (end - start).total_seconds() / 60.0
            return max(0, int(round(delta)))
        except Exception:
            return 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "WorkEvent":
        return cls(
            id=row["id"],
            title=row["title"],
            description=row["description"],
            source=row["source"],
            project_key=row["project_key"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            files_changed=json.loads(row["files_changed"] or "[]"),
            status=row["status"],
            jira_key=row["jira_key"],
            tags=json.loads(row["tags"] or "[]"),
            issue_type=row["issue_type"],
            dedup_hash=row["dedup_hash"],
        )


# ---------------------------------------------------------------------------
# Title / description / issue-type inference (simple heuristics)
# ---------------------------------------------------------------------------

# Keyword -> JIRA issue type. Order matters (first match wins).
_ISSUE_TYPE_KEYWORDS = [
    ("Bug", ["fix", "bug", "hotfix", "patch", "crash", "error", "regression"]),
    ("Story", ["feature", "add", "implement", "create", "build", "story"]),
    ("Task", ["refactor", "update", "chore", "cleanup", "docs", "test", "tweak"]),
]

# Coarse mapping from file extension / path fragment to a tag.
_FILE_TAGS = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "frontend",
    ".jsx": "frontend",
    ".css": "styling",
    ".html": "frontend",
    ".md": "docs",
    ".sql": "database",
    ".yml": "config",
    ".yaml": "config",
    ".json": "config",
    ".toml": "config",
}


def infer_issue_type(text: str) -> str:
    """Infer a JIRA issue type from free text using keyword heuristics."""
    lowered = (text or "").lower()
    for issue_type, keywords in _ISSUE_TYPE_KEYWORDS:
        if any(re.search(rf"\b{re.escape(k)}", lowered) for k in keywords):
            return issue_type
    return "Task"


def infer_tags(files: Iterable[str]) -> List[str]:
    """Derive a small set of tags from a list of changed files."""
    tags: List[str] = []
    for f in files:
        suffix = Path(f).suffix.lower()
        tag = _FILE_TAGS.get(suffix)
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def summarize_files(files: List[str], limit: int = 3) -> str:
    """Produce a short human summary of a list of changed files."""
    if not files:
        return "no files"
    names = [Path(f).name for f in files]
    if len(names) <= limit:
        return ", ".join(names)
    shown = ", ".join(names[:limit])
    return f"{shown} +{len(names) - limit} more"


def infer_title(
    raw_message: str = "",
    files: Optional[List[str]] = None,
    source: str = "cli",
) -> str:
    """Infer a concise task title from a raw message and/or file list.

    Strategy:
      * If a message is present, use its first non-empty line (commit subject
        style), trimmed to a sensible length.
      * Otherwise synthesise a title from the changed files.
    """
    files = files or []
    message = (raw_message or "").strip()
    if message:
        first_line = message.splitlines()[0].strip()
        # Strip conventional-commit prefixes for readability but keep the gist.
        first_line = re.sub(r"^(feat|fix|chore|docs|refactor|test)(\([^)]*\))?:\s*", "", first_line, flags=re.I)
        title = first_line[:120].strip()
        if title:
            return title[0].upper() + title[1:]
    if files:
        verb = {"git": "Committed", "claude": "AI session on", "cli": "Worked on"}.get(
            source, "Worked on"
        )
        return f"{verb} {summarize_files(files)}"
    return "Untitled work session"


def infer_description(
    raw_message: str = "",
    files: Optional[List[str]] = None,
    source: str = "cli",
    extra: str = "",
) -> str:
    """Build a structured description body."""
    files = files or []
    parts: List[str] = []
    if raw_message and raw_message.strip():
        parts.append(raw_message.strip())
    if files:
        listed = "\n".join(f"  - {f}" for f in files)
        parts.append(f"Files changed ({len(files)}):\n{listed}")
    if extra:
        parts.append(extra.strip())
    parts.append(f"Captured automatically by JIRA AI Bridge ({source}).")
    return "\n\n".join(parts)


def build_event(
    raw_message: str = "",
    files: Optional[List[str]] = None,
    source: str = "cli",
    project_key: str = "LOCAL",
    started_at: Optional[str] = None,
    ended_at: Optional[str] = None,
    extra: str = "",
    tags: Optional[List[str]] = None,
) -> WorkEvent:
    """Construct a fully-inferred :class:`WorkEvent` from raw watcher input."""
    files = files or []
    title = infer_title(raw_message, files, source)
    description = infer_description(raw_message, files, source, extra)
    issue_type = infer_issue_type(f"{raw_message} {title}")
    derived_tags = list(tags or []) + infer_tags(files)
    # de-dupe tags preserving order
    seen: set = set()
    final_tags = [t for t in derived_tags if not (t in seen or seen.add(t))]
    event = WorkEvent(
        title=title,
        description=description,
        source=source,
        project_key=project_key,
        files_changed=files,
        issue_type=issue_type,
        tags=final_tags,
        started_at=started_at or _now_iso(),
        ended_at=ended_at or _now_iso(),
    )
    event.dedup_hash = event.compute_hash()
    return event


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS work_events (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    description  TEXT,
    source       TEXT,
    project_key  TEXT,
    started_at   TEXT,
    ended_at     TEXT,
    files_changed TEXT,
    status       TEXT,
    jira_key     TEXT,
    tags         TEXT,
    issue_type   TEXT,
    dedup_hash   TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_events_ended ON work_events(ended_at);
"""


class BridgeEngine:
    """Ingests, persists, deduplicates and syncs :class:`WorkEvent` objects."""

    def __init__(self, db_path: str, client: Optional[Any] = None, auto_sync: bool = True):
        """Create an engine.

        Args:
            db_path: Path to the SQLite worklog database.
            client: A JiraClient instance (real or mock). May be None for
                pure-storage use; sync operations will then be no-ops.
            auto_sync: When True, ``ingest`` immediately attempts to sync.
        """
        self.db_path = db_path
        self.client = client
        self.auto_sync = auto_sync
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    # -- ingestion ---------------------------------------------------------

    def ingest(self, event: WorkEvent, sync: Optional[bool] = None) -> WorkEvent:
        """Persist an event (deduplicating) and optionally sync to JIRA.

        Returns the stored event (which may be a pre-existing duplicate).
        """
        if not event.dedup_hash:
            event.dedup_hash = event.compute_hash()

        existing = self._find_by_hash(event.dedup_hash)
        if existing is not None:
            return existing

        self._insert(event)

        do_sync = self.auto_sync if sync is None else sync
        if do_sync and self.client is not None:
            self.sync_event(event)
        return event

    def ingest_many(self, events: Iterable[WorkEvent], sync: Optional[bool] = None) -> List[WorkEvent]:
        return [self.ingest(e, sync=sync) for e in events]

    # -- sync --------------------------------------------------------------

    def sync_event(self, event: WorkEvent) -> WorkEvent:
        """Push a single event to JIRA via the client; update status/key."""
        if self.client is None:
            return event
        try:
            key = self.client.create_issue(
                summary=event.title,
                description=event.description,
                project_key=event.project_key,
                issue_type=event.issue_type,
                labels=event.tags,
            )
            event.jira_key = key
            event.status = "synced"
            # Log time worked, when non-zero.
            minutes = event.duration_minutes()
            if minutes > 0:
                try:
                    self.client.add_worklog(key, minutes, comment="Logged by JIRA AI Bridge")
                except Exception:
                    pass
        except Exception as exc:  # never crash the engine on a sync failure
            event.status = "error"
            event.description += f"\n\n[sync error: {exc}]"
        self._update(event)
        return event

    def sync_pending(self) -> List[WorkEvent]:
        """Sync every event currently in ``pending`` (or ``error``) status."""
        pending = [e for e in self.all_events() if e.status in ("pending", "error")]
        return [self.sync_event(e) for e in pending]

    def sync_by_id(self, event_id: str) -> Optional[WorkEvent]:
        event = self.get(event_id)
        if event is None:
            return None
        return self.sync_event(event)

    # -- queries -----------------------------------------------------------

    def get(self, event_id: str) -> Optional[WorkEvent]:
        cur = self._conn.execute("SELECT * FROM work_events WHERE id = ?", (event_id,))
        row = cur.fetchone()
        return WorkEvent.from_row(row) if row else None

    def all_events(self) -> List[WorkEvent]:
        cur = self._conn.execute("SELECT * FROM work_events ORDER BY ended_at DESC")
        return [WorkEvent.from_row(r) for r in cur.fetchall()]

    def events_since(self, since: datetime) -> List[WorkEvent]:
        iso = since.astimezone(timezone.utc).isoformat()
        cur = self._conn.execute(
            "SELECT * FROM work_events WHERE ended_at >= ? ORDER BY ended_at DESC",
            (iso,),
        )
        return [WorkEvent.from_row(r) for r in cur.fetchall()]

    def events_today(self) -> List[WorkEvent]:
        now = datetime.now(timezone.utc)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return self.events_since(start)

    def events_this_week(self) -> List[WorkEvent]:
        now = datetime.now(timezone.utc)
        start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return self.events_since(start)

    def events_in_range(self, start: datetime, end: datetime) -> List[WorkEvent]:
        s = start.astimezone(timezone.utc).isoformat()
        e = end.astimezone(timezone.utc).isoformat()
        cur = self._conn.execute(
            "SELECT * FROM work_events WHERE ended_at >= ? AND ended_at <= ? "
            "ORDER BY ended_at DESC",
            (s, e),
        )
        return [WorkEvent.from_row(r) for r in cur.fetchall()]

    # -- summary -----------------------------------------------------------

    def summary(self) -> Dict[str, Any]:
        """Compute dashboard summary statistics."""
        all_events = self.all_events()
        today = self.events_today()
        week = self.events_this_week()

        def by(attr: str, events: List[WorkEvent]) -> Dict[str, int]:
            out: Dict[str, int] = {}
            for e in events:
                key = getattr(e, attr) or "unknown"
                out[key] = out.get(key, 0) + 1
            return out

        total_minutes = sum(e.duration_minutes() for e in all_events)
        return {
            "tasks_today": len(today),
            "tasks_week": len(week),
            "tasks_total": len(all_events),
            "pending": len([e for e in all_events if e.status == "pending"]),
            "total_minutes": total_minutes,
            "total_time_human": _human_minutes(total_minutes),
            "by_source": by("source", all_events),
            "by_project": by("project_key", all_events),
            "by_status": by("status", all_events),
        }

    # -- low level ---------------------------------------------------------

    def _find_by_hash(self, dedup_hash: str) -> Optional[WorkEvent]:
        cur = self._conn.execute(
            "SELECT * FROM work_events WHERE dedup_hash = ?", (dedup_hash,)
        )
        row = cur.fetchone()
        return WorkEvent.from_row(row) if row else None

    def _insert(self, e: WorkEvent) -> None:
        self._conn.execute(
            """INSERT OR IGNORE INTO work_events
               (id, title, description, source, project_key, started_at, ended_at,
                files_changed, status, jira_key, tags, issue_type, dedup_hash)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                e.id,
                e.title,
                e.description,
                e.source,
                e.project_key,
                e.started_at,
                e.ended_at,
                json.dumps(e.files_changed),
                e.status,
                e.jira_key,
                json.dumps(e.tags),
                e.issue_type,
                e.dedup_hash,
            ),
        )
        self._conn.commit()

    def _update(self, e: WorkEvent) -> None:
        self._conn.execute(
            """UPDATE work_events SET
                 title=?, description=?, source=?, project_key=?, started_at=?,
                 ended_at=?, files_changed=?, status=?, jira_key=?, tags=?,
                 issue_type=?, dedup_hash=?
               WHERE id=?""",
            (
                e.title,
                e.description,
                e.source,
                e.project_key,
                e.started_at,
                e.ended_at,
                json.dumps(e.files_changed),
                e.status,
                e.jira_key,
                json.dumps(e.tags),
                e.issue_type,
                e.dedup_hash,
                e.id,
            ),
        )
        self._conn.commit()


def _human_minutes(total: int) -> str:
    """Render a minute count as e.g. '3h 25m'."""
    if total <= 0:
        return "0m"
    hours, minutes = divmod(total, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"
