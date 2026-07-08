"""Tests for the git watcher parsing, using monkeypatched git output."""

from jira_bridge.watchers.git_watcher import GitWatcher


def _make_watcher(engine, monkeypatch, commits, files_map, dirty):
    watcher = GitWatcher(engine, repo_path=".", project_key="ENG", interval=1)

    def fake_recent(limit=25):
        return commits

    def fake_files(sha):
        return files_map.get(sha, [])

    def fake_dirty():
        return dirty

    monkeypatch.setattr(watcher, "_recent_commits", fake_recent)
    monkeypatch.setattr(watcher, "_files_in_commit", fake_files)
    monkeypatch.setattr(watcher, "_uncommitted_files", fake_dirty)
    return watcher


def test_git_watcher_emits_events_for_new_commits(engine, monkeypatch):
    commits = [
        {"sha": "aaa111", "author": "Dev", "date": "2026-06-28T10:00:00+00:00",
         "subject": "feat: add login page"},
        {"sha": "bbb222", "author": "Dev", "date": "2026-06-28T09:00:00+00:00",
         "subject": "fix: handle empty input"},
    ]
    files_map = {"aaa111": ["src/login.py"], "bbb222": ["src/util.py"]}
    watcher = _make_watcher(engine, monkeypatch, commits, files_map, [])

    events = watcher.poll()
    assert len(events) == 2
    titles = [e.title for e in events]
    assert "Add login page" in titles
    # issue type inferred from the fix commit
    types = {e.title: e.issue_type for e in events}
    assert types["Handle empty input"] == "Bug"


def test_git_watcher_dedupes_seen_commits(engine, monkeypatch):
    commits = [
        {"sha": "aaa111", "author": "Dev", "date": "2026-06-28T10:00:00+00:00",
         "subject": "feat: add login page"},
    ]
    watcher = _make_watcher(engine, monkeypatch, commits, {"aaa111": ["a.py"]}, [])
    first = watcher.poll()
    assert len(first) == 1
    second = watcher.poll()  # same commit already seen
    assert len(second) == 0


def test_git_watcher_includes_uncommitted(engine, monkeypatch):
    watcher = _make_watcher(
        engine, monkeypatch, [], {}, ["src/wip.py", "notes.md"]
    )
    events = watcher.poll()
    assert len(events) == 1
    assert "wip" in events[0].tags
    assert len(events[0].files_changed) == 2


def test_git_watcher_run_once_ingests(engine, monkeypatch):
    commits = [
        {"sha": "ccc333", "author": "Dev", "date": "2026-06-28T11:00:00+00:00",
         "subject": "refactor: clean up engine"},
    ]
    watcher = _make_watcher(engine, monkeypatch, commits, {"ccc333": ["core.py"]}, [])
    watcher.run_once()
    assert len(engine.all_events()) == 1
