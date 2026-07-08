"""Pytest configuration: make ``src`` importable and provide fixtures."""

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jira_bridge.core import BridgeEngine  # noqa: E402
from jira_bridge.jira_client import MockJiraClient  # noqa: E402


@pytest.fixture()
def mock_client():
    return MockJiraClient(":memory:")


@pytest.fixture()
def engine(tmp_path, mock_client):
    db = tmp_path / "worklog.db"
    eng = BridgeEngine(str(db), client=mock_client, auto_sync=False)
    yield eng
    eng.close()
