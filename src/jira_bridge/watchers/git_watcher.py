"""Git watcher.

Polls a git repository on an interval, groups *new* commits and any
uncommitted (working-tree) changes into work sessions, and emits one
:class:`WorkEvent` per new commit plus an optional event for uncommitted work.

Works with ANY editor or AI agent, because it only observes the repository
state -- it does not care who produced the commits.
"""

from __future__ import annotations

import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from ..core import BridgeEngine, WorkEvent, build_event
from .base import BaseWatcher


class GitWatcher(BaseWatcher):
    """Background watcher that turns git activity into work events."""

    source = "git"

    def __init__(
        self,
        engine: BridgeEngine,
        repo_path: str,
        project_key: str = "LOCAL",
        interval: int = 60,
        include_uncommitted: bool = True,
    ):
        super().__init__(engine)
        self.repo_path = str(Path(repo_path).resolve())
        self.project_key = project_key
        self.interval = interval
        self.include_uncommitted = include_uncommitted
        self._seen_commits: set = set()
        self._running = False

    # -- git plumbing (thin wrappers so tests can monkeypatch) -------------

    def _git(self, *args: str) -> str:
        """Run a git command in the repo and return stdout (stripped)."""
        result = subprocess.run(
            ["git", *args],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "git command failed")
        return result.stdout.strip()

    def _recent_commits(self, limit: int = 25) -> List[dict]:
        """Return recent commits, newest first, as parsed dicts.

        Uses a unit-separator delimited format that is robust to spaces in
        commit subjects.
        """
        fmt = "%H%x1f%an%x1f%aI%x1f%s"
        raw = self._git("log", f"-{limit}", f"--pretty=format:{fmt}")
        commits: List[dict] = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            parts = line.split("\x1f")
            if len(parts) != 4:
                continue
            sha, author, date_iso, subject = parts
            commits.append(
                {"sha": sha, "author": author, "date": date_iso, "subject": subject}
            )
        return commits

    def _files_in_commit(self, sha: str) -> List[str]:
        try:
            raw = self._git("show", "--name-only", "--pretty=format:", sha)
        except Exception:
            return []
        return [l.strip() for l in raw.splitlines() if l.strip()]

    def _uncommitted_files(self) -> List[str]:
        try:
            raw = self._git("status", "--porcelain")
        except Exception:
            return []
        files = []
        for line in raw.splitlines():
            # porcelain format: "XY <path>" (path may be renamed "old -> new")
            path = line[3:].strip()
            if "->" in path:
                path = path.split("->")[-1].strip()
            if path:
                files.append(path)
        return files

    # -- watcher interface -------------------------------------------------

    def poll(self) -> List[WorkEvent]:
        events: List[WorkEvent] = []

        # 1) New commits since last poll.
        try:
            commits = self._recent_commits()
        except Exception:
            commits = []

        # Process oldest-first so chronological order is preserved.
        for commit in reversed(commits):
            sha = commit["sha"]
            if sha in self._seen_commits:
                continue
            self._seen_commits.add(sha)
            files = self._files_in_commit(sha)
            ended = commit.get("date") or datetime.now(timezone.utc).isoformat()
            event = build_event(
                raw_message=commit["subject"],
                files=files,
                source=self.source,
                project_key=self.project_key,
                started_at=ended,
                ended_at=ended,
                extra=f"Commit {sha[:8]} by {commit['author']}",
                tags=["git"],
            )
            events.append(event)

        # 2) Uncommitted working-tree changes (one rolled-up session event).
        if self.include_uncommitted:
            dirty = self._uncommitted_files()
            if dirty:
                now = datetime.now(timezone.utc).isoformat()
                event = build_event(
                    raw_message="Work in progress (uncommitted changes)",
                    files=dirty,
                    source=self.source,
                    project_key=self.project_key,
                    started_at=now,
                    ended_at=now,
                    tags=["git", "wip"],
                )
                events.append(event)

        return events

    def run_forever(self, max_iterations: Optional[int] = None) -> None:
        """Blocking poll loop. Pass ``max_iterations`` for bounded runs/tests."""
        self._running = True
        count = 0
        try:
            while self._running:
                new = self.run_once()
                if new:
                    print(f"[git-watcher] captured {len(new)} event(s)")
                count += 1
                if max_iterations is not None and count >= max_iterations:
                    break
                time.sleep(self.interval)
        except KeyboardInterrupt:
            print("\n[git-watcher] stopped")
        finally:
            self._running = False

    def stop(self) -> None:
        self._running = False
