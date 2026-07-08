"""Base watcher interface.

A watcher observes some source of work (git, an AI coding session, a manual
trigger) and emits :class:`~jira_bridge.core.WorkEvent` objects into a
:class:`~jira_bridge.core.BridgeEngine`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from ..core import BridgeEngine, WorkEvent


class BaseWatcher(ABC):
    """Common interface implemented by all watchers."""

    #: Source identifier stamped onto emitted events ("git" / "claude" / "cli").
    source: str = "base"

    def __init__(self, engine: BridgeEngine):
        self.engine = engine

    @abstractmethod
    def poll(self) -> List[WorkEvent]:
        """Collect work since the last poll and return new events.

        Implementations should be idempotent where possible; the engine also
        deduplicates, so emitting the same logical event twice is safe.
        """

    def run_once(self) -> List[WorkEvent]:
        """Poll once and ingest every produced event into the engine."""
        events = self.poll()
        return self.engine.ingest_many(events)
