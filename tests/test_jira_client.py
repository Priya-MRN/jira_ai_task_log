"""Tests for the MockJiraClient and the get_client factory fallback."""

from jira_bridge.config import Settings
from jira_bridge.jira_client import MockJiraClient, get_client


def test_mock_create_issue_increments_keys(mock_client):
    k1 = mock_client.create_issue("first", project_key="ENG")
    k2 = mock_client.create_issue("second", project_key="ENG")
    assert k1 == "ENG-1"
    assert k2 == "ENG-2"


def test_mock_create_separate_projects(mock_client):
    a = mock_client.create_issue("a", project_key="ENG")
    b = mock_client.create_issue("b", project_key="OPS")
    assert a == "ENG-1"
    assert b == "OPS-1"


def test_mock_transition(mock_client):
    key = mock_client.create_issue("task", project_key="ENG")
    assert mock_client.get_issue(key)["status"] == "To Do"
    mock_client.transition_issue(key, "Done")
    assert mock_client.get_issue(key)["status"] == "Done"


def test_mock_comment_and_worklog(mock_client):
    key = mock_client.create_issue("task", project_key="ENG")
    mock_client.add_comment(key, "looks good")
    mock_client.add_worklog(key, 30, comment="did the thing")
    comments = mock_client.comments_for(key)
    worklogs = mock_client.worklogs_for(key)
    assert len(comments) == 1 and comments[0]["body"] == "looks good"
    assert len(worklogs) == 1 and worklogs[0]["minutes"] == 30


def test_get_client_falls_back_to_mock_without_creds():
    settings = Settings(
        jira_base_url=None, jira_email=None, jira_api_token=None,
        db_path=":memory:",
    )
    client = get_client(settings, check_reachable=False)
    assert isinstance(client, MockJiraClient)
    assert client.kind == "mock"


def test_get_client_force_mock(tmp_path):
    settings = Settings(
        jira_base_url="https://example.atlassian.net",
        jira_email="me@example.com",
        jira_api_token="secret-token",
        force_mock=True,
        db_path=str(tmp_path / "worklog.db"),
    )
    # force_mock disables has_real_jira, so we always get the mock.
    assert settings.has_real_jira is False
    client = get_client(settings, check_reachable=False)
    assert isinstance(client, MockJiraClient)
