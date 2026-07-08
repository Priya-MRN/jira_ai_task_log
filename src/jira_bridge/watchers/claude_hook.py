"""Claude Code hook watcher.

This watcher is designed to be invoked from a Claude Code *hook* (typically the
``Stop`` hook, fired when Claude finishes responding, or ``PostToolUse``). The
hook payload is delivered to the command on **stdin** as JSON; this module
parses it and emits a single :class:`WorkEvent` describing the AI session.

Hook setup (add to ``~/.claude/settings.json`` or ``.claude/settings.json``)::

    {
      "hooks": {
        "Stop": [
          {
            "matcher": "",
            "hooks": [
              {
                "type": "command",
                "command": "python -m jira_bridge hook"
              }
            ]
          }
        ]
      }
    }

The ``Stop`` payload typically contains keys such as ``session_id``,
``transcript_path`` and ``cwd``. We additionally try to read recent git changes
in ``cwd`` to enrich the event with the files that were touched during the
session, so the resulting JIRA task is meaningful even when the transcript is
not parsed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any, Dict, List, Optional

from ..core import BridgeEngine, WorkEvent, build_event
from .base import BaseWatcher


class ClaudeHookWatcher(BaseWatcher):
    """Reads a Claude Code hook payload from stdin and emits a WorkEvent."""

    source = "claude"

    def __init__(
        self,
        engine: BridgeEngine,
        project_key: str = "LOCAL",
        payload: Optional[Dict[str, Any]] = None,
        stream: Optional[IO[str]] = None,
    ):
        super().__init__(engine)
        self.project_key = project_key
        self._payload = payload
        self._stream = stream or sys.stdin

    # -- payload handling --------------------------------------------------

    def _read_payload(self) -> Dict[str, Any]:
        if self._payload is not None:
            return self._payload
        try:
            raw = self._stream.read()
        except Exception:
            raw = ""
        if not raw or not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Treat non-JSON stdin as a free-form note.
            return {"note": raw.strip()}

    def _git_changed_files(self, cwd: str) -> List[str]:
        """Best-effort list of recently changed files in the session's repo."""
        try:
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=cwd,
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
            if files:
                return files
            # No working-tree changes -> fall back to files in the last commit.
            show = subprocess.run(
                ["git", "show", "--name-only", "--pretty=format:", "HEAD"],
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
            )
            return [l.strip() for l in show.stdout.splitlines() if l.strip()]
        except Exception:
            return []

    # -- watcher interface -------------------------------------------------

    def poll(self) -> List[WorkEvent]:
        payload = self._read_payload()

        cwd = payload.get("cwd") or str(Path.cwd())
        session_id = payload.get("session_id", "")
        hook_event = payload.get("hook_event_name", "Stop")
        note = payload.get("note") or payload.get("last_message") or ""

        # Build a message: prefer an explicit note, else describe the session.
        if note:
            message = note
        else:
            message = f"AI coding session ({hook_event})"

        files = self._git_changed_files(cwd)

        extra_bits = []
        if session_id:
            extra_bits.append(f"Claude session {session_id[:12]}")
        if cwd:
            extra_bits.append(f"Working dir: {cwd}")
        extra = "\n".join(extra_bits)

        now = datetime.now(timezone.utc).isoformat()
        event = build_event(
            raw_message=message,
            files=files,
            source=self.source,
            project_key=self.project_key,
            started_at=now,
            ended_at=now,
            extra=extra,
            tags=["claude", "ai-session"],
        )
        return [event]
