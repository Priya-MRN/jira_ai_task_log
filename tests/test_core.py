"""Tests for the core engine: inference, dedup, persistence, summary."""

from datetime import datetime, timedelta, timezone

from jira_bridge.core import (
    WorkEvent,
    build_event,
    infer_issue_type,
    infer_tags,
    infer_title,
    summarize_files,
)


def test_infer_issue_type_bug():
    assert infer_issue_type("fix crash in login") == "Bug"
    assert infer_issue_type("hotfix for null pointer") == "Bug"


def test_infer_issue_type_story_and_default():
    assert infer_issue_type("implement new feature") == "Story"
    assert infer_issue_type("just some random text") == "Task"


def test_infer_title_from_message_strips_prefix():
    title = infer_title("feat(api): add rate limiting", ["a.py"], "git")
    assert title == "Add rate limiting"


def test_infer_title_from_files_when_no_message():
    title = infer_title("", ["src/foo.py", "src/bar.py"], "git")
    assert "Committed" in title
    assert "foo.py" in title


def test_infer_tags_from_extensions():
    tags = infer_tags(["a.py", "b.js", "c.md", "d.py"])
    assert "python" in tags
    assert "javascript" in tags
    assert "docs" in tags
    # no duplicates
    assert tags.count("python") == 1


def test_summarize_files_truncates():
    s = summarize_files(["a.py", "b.py", "c.py", "d.py", "e.py"], limit=2)
    assert "+3 more" in s


def test_build_event_populates_hash_and_type():
    ev = build_event("fix the bug", ["x.py"], source="cli", project_key="ENG")
    assert ev.issue_type == "Bug"
    assert ev.dedup_hash
    assert ev.project_key == "ENG"
    assert "python" in ev.tags


def test_engine_dedup(engine):
    ev1 = build_event("same work", ["a.py"], source="git")
    stored1 = engine.ingest(ev1, sync=False)
    # A second event with identical content in the same minute is a duplicate.
    ev2 = build_event(
        "same work", ["a.py"], source="git",
        started_at=ev1.started_at, ended_at=ev1.ended_at,
    )
    stored2 = engine.ingest(ev2, sync=False)
    assert stored1.id == stored2.id
    assert len(engine.all_events()) == 1


def test_engine_persists_and_syncs(engine, mock_client):
    ev = build_event("implement feature", ["a.py"], source="cli", project_key="ENG")
    engine.ingest(ev, sync=True)
    stored = engine.all_events()[0]
    assert stored.status == "synced"
    assert stored.jira_key == "ENG-1"
    # The issue exists in the mock store.
    assert mock_client.get_issue("ENG-1") is not None


def test_engine_summary_counts(engine):
    now = datetime.now(timezone.utc)
    engine.ingest(build_event("a", ["a.py"], source="git",
                              ended_at=now.isoformat()), sync=False)
    engine.ingest(build_event("b", ["b.py"], source="cli",
                              ended_at=now.isoformat()), sync=False)
    old = now - timedelta(days=30)
    engine.ingest(build_event("c", ["c.py"], source="git",
                              started_at=old.isoformat(),
                              ended_at=old.isoformat()), sync=False)
    summ = engine.summary()
    assert summ["tasks_total"] == 3
    assert summ["tasks_today"] == 2
    assert summ["by_source"]["git"] == 2


def test_duration_minutes():
    start = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=45)
    ev = WorkEvent(title="x", started_at=start.isoformat(), ended_at=end.isoformat())
    assert ev.duration_minutes() == 45
