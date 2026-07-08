"""Configuration loading for JIRA AI Bridge.

Loads settings from a ``.env`` file (via python-dotenv) and the process
environment, exposing them through a single :class:`Settings` object with sane
defaults. Nothing here ever raises just because credentials are missing -- the
absence of JIRA credentials simply means the bridge will run against the local
mock store.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:  # python-dotenv is a hard dependency, but degrade gracefully if absent.
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - only hit if dependency missing
    def load_dotenv(*_args, **_kwargs):  # type: ignore
        return False


# Resolve a stable project root: this file lives at
# <root>/src/jira_bridge/config.py -> parents[2] == <root>.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Load .env from the current working directory first, then the project root.
load_dotenv(Path.cwd() / ".env")
load_dotenv(PROJECT_ROOT / ".env")


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    """Resolved runtime settings for the bridge."""

    # JIRA Cloud credentials -- all optional. When all four core values are
    # present the real client is attempted; otherwise the mock is used.
    jira_base_url: Optional[str] = field(
        default_factory=lambda: os.getenv("JIRA_BASE_URL")
    )
    jira_email: Optional[str] = field(
        default_factory=lambda: os.getenv("JIRA_EMAIL")
    )
    jira_api_token: Optional[str] = field(
        default_factory=lambda: os.getenv("JIRA_API_TOKEN")
    )
    jira_project_key: str = field(
        default_factory=lambda: os.getenv("JIRA_PROJECT_KEY", "LOCAL")
    )

    # Storage.
    db_path: str = field(
        default_factory=lambda: os.getenv(
            "JIRA_BRIDGE_DB", str(PROJECT_ROOT / "worklog.db")
        )
    )

    # Behaviour flags.
    force_mock: bool = field(default_factory=lambda: _bool_env("JIRA_BRIDGE_FORCE_MOCK"))
    auto_sync: bool = field(
        default_factory=lambda: _bool_env("JIRA_BRIDGE_AUTO_SYNC", True)
    )

    # Watcher defaults.
    git_poll_interval: int = field(
        default_factory=lambda: int(os.getenv("JIRA_BRIDGE_GIT_INTERVAL", "60"))
    )

    # Web.
    web_port: int = field(
        default_factory=lambda: int(os.getenv("JIRA_BRIDGE_PORT", "5050"))
    )

    @property
    def has_real_jira(self) -> bool:
        """True when enough credentials are present to attempt the real API."""
        return bool(
            self.jira_base_url
            and self.jira_email
            and self.jira_api_token
            and not self.force_mock
        )

    def masked_token(self) -> str:
        if not self.jira_api_token:
            return "(none)"
        token = self.jira_api_token
        if len(token) <= 6:
            return "*" * len(token)
        return f"{token[:3]}{'*' * (len(token) - 6)}{token[-3:]}"


def get_settings() -> Settings:
    """Return a freshly-resolved :class:`Settings` instance."""
    return Settings()
