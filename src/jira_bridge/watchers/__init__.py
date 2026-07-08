"""Watchers package: pluggable sources of work events."""

from .base import BaseWatcher
from .git_watcher import GitWatcher
from .cli_watcher import CliWatcher
from .claude_hook import ClaudeHookWatcher

__all__ = ["BaseWatcher", "GitWatcher", "CliWatcher", "ClaudeHookWatcher"]
