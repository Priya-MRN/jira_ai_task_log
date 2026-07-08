"""Text report generation for 'what I did today / this week'."""

from __future__ import annotations

from datetime import datetime
from typing import List

from .core import BridgeEngine, WorkEvent, _human_minutes


_SOURCE_ICON = {"git": "[git]", "claude": "[ai]", "cli": "[cli]"}


def _fmt_time(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%a %d %b %H:%M")
    except Exception:
        return iso


def _render(title: str, events: List[WorkEvent]) -> str:
    lines: List[str] = []
    bar = "=" * 60
    lines.append(bar)
    lines.append(f" {title}")
    lines.append(bar)
    if not events:
        lines.append("  (no work logged in this period)")
        lines.append("")
        return "\n".join(lines)

    total_minutes = sum(e.duration_minutes() for e in events)
    by_source: dict = {}
    for e in events:
        by_source[e.source] = by_source.get(e.source, 0) + 1

    for e in events:
        icon = _SOURCE_ICON.get(e.source, "[?]")
        key = e.jira_key or "(unsynced)"
        files = f"{len(e.files_changed)} file(s)" if e.files_changed else "no files"
        lines.append(f"  {icon} {_fmt_time(e.ended_at)}  {key:<10} [{e.status}]")
        lines.append(f"        {e.title}")
        lines.append(f"        {e.issue_type} - {files} - tags: {', '.join(e.tags) or '-'}")
        lines.append("")

    lines.append("-" * 60)
    lines.append(f"  Tasks: {len(events)}   Time logged: {_human_minutes(total_minutes)}")
    summary_bits = "  ".join(f"{k}={v}" for k, v in sorted(by_source.items()))
    lines.append(f"  By source: {summary_bits}")
    lines.append("")
    return "\n".join(lines)


def report_today(engine: BridgeEngine) -> str:
    return _render("What I did TODAY", engine.events_today())


def report_week(engine: BridgeEngine) -> str:
    return _render("What I did THIS WEEK", engine.events_this_week())


def report_range(engine: BridgeEngine, start: datetime, end: datetime) -> str:
    label = f"Work from {start.date()} to {end.date()}"
    return _render(label, engine.events_in_range(start, end))


def report_all(engine: BridgeEngine) -> str:
    return _render("All logged work", engine.all_events())
