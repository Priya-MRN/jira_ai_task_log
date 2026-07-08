"""Tests for the report generator and the Claude hook watcher."""

from datetime import datetime, timezone

from jira_bridge.core import build_event
from jira_bridge.report import report_all, report_today, report_week
from jira_bridge.watchers.claude_hook import ClaudeHookWatcher


def test_report_empty(engine):
    out = report_today(engine)
    assert "no work logged" in out.lower()


def test_report_lists_events(engine):
    now = datetime.now(timezone.utc).isoformat()
    engine.ingest(
        build_event("Implement feature X", ["a.py"], source="cli", ended_at=now),
        sync=True,
    )
    out = report_today(engine)
    assert "Implement feature X" in out
    assert "Tasks: 1" in out
    # The synced event should carry a local JIRA key.
    assert "LOCAL-1" in out or "ENG-1" in out


def test_report_week_includes_today(engine):
    now = datetime.now(timezone.utc).isoformat()
    engine.ingest(build_event("weekly work", ["b.py"], source="git", ended_at=now),
                  sync=False)
    assert "Weekly work" in report_week(engine)
    assert "Weekly work" in report_all(engine)


def test_claude_hook_parses_payload(engine, monkeypatch):
    payload = {
        "hook_event_name": "Stop",
        "session_id": "abcdef123456",
        "cwd": "/tmp/project",
        "note": "Implemented the export feature",
    }
    watcher = ClaudeHookWatcher(engine, project_key="ENG", payload=payload)
    # Avoid touching real git in the test environment.
    monkeypatch.setattr(watcher, "_git_changed_files", lambda cwd: ["export.py"])
    events = watcher.poll()
    assert len(events) == 1
    ev = events[0]
    assert ev.source == "claude"
    assert "Implemented the export feature" in ev.title
    assert "claude" in ev.tags
    assert "export.py" in ev.files_changed


def test_claude_hook_handles_empty_stdin(engine, monkeypatch):
    import io
    watcher = ClaudeHookWatcher(engine, project_key="ENG", stream=io.StringIO(""))
    monkeypatch.setattr(watcher, "_git_changed_files", lambda cwd: [])
    events = watcher.poll()
    assert len(events) == 1
    assert events[0].source == "claude"
