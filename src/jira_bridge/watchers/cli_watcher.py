"""Manual CLI watcher.

A manual-trigger watcher: it reads the current ``git diff`` / recent changes in
a repository, combines them with an optional free-form note, summarizes, and
emits a single :class:`WorkEvent`. Useful when you finish a chunk of work and
want to log it on demand with one command.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from ..core import BridgeEngine, WorkEvent, build_event
from .base import BaseWatcher


class CliWatcher(BaseWatcher):
    """One-shot watcher driven by the ``log`` CLI command."""

    source = "cli"

    def __init__(
        self,
        engine: BridgeEngine,
        note: str = "",
        repo_path: Optional[str] = None,
        project_key: str = "LOCAL",
    ):
        super().__init__(engine)
        self.note = note
        self.repo_path = str(Path(repo_path).resolve()) if repo_path else None
        self.project_key = project_key

    def _changed_files(self) -> List[str]:
        if not self.repo_path:
            return []
        try:
            # Combine staged + unstaged + untracked for a complete picture.
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=False,
            )
            files = []
            for line in status.stdout.splitlines():
                path = line[3:].strip()
                if "->" in path:
                    path = path.split("->")[-1].strip()
                if path:
                    files.append(path)
            return files
        except Exception:
            return []

    def poll(self) -> List[WorkEvent]:
        files = self._changed_files()
        message = self.note or "Manual work log"
        now = datetime.now(timezone.utc).isoformat()
        event = build_event(
            raw_message=message,
            files=files,
            source=self.source,
            project_key=self.project_key,
            started_at=now,
            ended_at=now,
            tags=["cli", "manual"],
        )
        return [event]
