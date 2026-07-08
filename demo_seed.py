"""Seed the local mock store with realistic sample work events.

Run this once after install so the dashboard and reports look great
immediately::

    python demo_seed.py
    python -m jira_bridge dashboard

It adds ~12 events spread across today and earlier this week, across all three
sources (git / claude / cli) and a couple of projects, then syncs most of them
to the local mock JIRA so they have keys like LOCAL-1, ENG-2, etc.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make ``src`` importable without an editable install.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from jira_bridge.config import get_settings  # noqa: E402
from jira_bridge.core import BridgeEngine, build_event  # noqa: E402
from jira_bridge.jira_client import MockJiraClient  # noqa: E402


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


SAMPLES = [
    # (offset_minutes_ago, duration_min, source, project, message, files)

    # --- Today (recent -> earlier) --------------------------------------
    (15, 25, "claude", "ENG",
     "Add password strength meter to signup form",
     ["src/web/static/signup.js", "src/web/static/style.css",
      "tests/test_signup.py"]),
    (30, 45, "claude", "ENG",
     "Implement OAuth token refresh in auth service",
     ["src/auth/oauth.py", "src/auth/tokens.py", "tests/test_oauth.py"]),
    (75, 30, "git", "ENG",
     "feat: add 'remember me' option to login",
     ["src/auth/session.py", "src/web/templates/login.html"]),
    (120, 35, "git", "ENG",
     "fix: prevent crash when session cookie is missing",
     ["src/web/middleware.py"]),
    (160, 50, "claude", "MOB",
     "Build pull-to-refresh on the mobile feed screen",
     ["mobile/lib/feed/feed_screen.dart", "mobile/lib/feed/feed_api.dart"]),
    (180, 20, "cli", "ENG",
     "Reviewed PR feedback and updated docs",
     ["docs/auth.md", "README.md"]),
    (230, 40, "git", "MOB",
     "fix: avatar images stretched on small screens",
     ["mobile/lib/widgets/avatar.dart"]),
    (260, 60, "claude", "ENG",
     "Add rate limiting to the public API",
     ["src/api/limits.py", "src/api/routes.py", "config/limits.yaml"]),
    (320, 25, "cli", "OPS",
     "Bumped Redis memory limit on prod cache",
     ["infra/redis/prod.tfvars"]),
    (400, 70, "claude", "ENG",
     "Add CSV export to the reports page",
     ["src/reports/export.py", "src/web/templates/reports.html",
      "tests/test_export.py"]),

    # --- Earlier this week ----------------------------------------------
    (1500, 90, "git", "ENG",
     "feat: build dashboard summary endpoint",
     ["src/web/app.py", "src/web/static/app.js", "src/web/static/style.css"]),
    (1560, 30, "claude", "MOB",
     "Cache API responses for offline reading",
     ["mobile/lib/core/cache.dart", "mobile/lib/core/http.dart"]),
    (1600, 25, "cli", "OPS",
     "Rotated staging database credentials",
     ["infra/secrets.tf", "infra/staging.tfvars"]),
    (1750, 40, "claude", "OPS",
     "Refactor deployment pipeline into reusable steps",
     [".github/workflows/deploy.yml", "scripts/deploy.sh"]),
    (1900, 55, "git", "ENG",
     "feat: email notifications for new comments",
     ["src/notify/email.py", "src/notify/templates/comment.html",
      "tests/test_notify.py"]),
    (2100, 35, "cli", "DES",
     "Updated brand colors and spacing tokens",
     ["design/tokens.json", "src/web/static/style.css"]),
    (2900, 55, "git", "ENG",
     "feat: add WorkEvent deduplication by content hash",
     ["src/jira_bridge/core.py", "tests/test_core.py"]),
    (3100, 30, "claude", "ENG",
     "Write integration tests for the mock JIRA client",
     ["tests/test_jira_client.py"]),
    (3300, 45, "git", "MOB",
     "feat: dark mode support across the app",
     ["mobile/lib/theme/theme.dart", "mobile/lib/theme/colors.dart"]),
    (3600, 20, "cli", "DES",
     "Exported new icon set as SVG",
     ["design/icons/export.md"]),
    (4300, 20, "cli", "OPS",
     "Investigated elevated 5xx rate on edge nodes",
     ["infra/monitoring/alerts.yml"]),
    (4500, 75, "git", "ENG",
     "feat: responsive dashboard with dark mode",
     ["src/web/static/style.css", "src/web/templates/dashboard.html"]),
    (4800, 40, "claude", "ENG",
     "Add search + filter to the events timeline",
     ["src/web/static/app.js", "src/web/templates/dashboard.html"]),
    (5200, 30, "git", "OPS",
     "chore: upgrade CI runners to Ubuntu 24.04",
     [".github/workflows/ci.yml"]),
    (5800, 15, "cli", "ENG",
     "Tidied up logging and removed dead code",
     ["src/jira_bridge/config.py"]),
    (6400, 60, "claude", "MOB",
     "Fix push-notification token registration on Android",
     ["mobile/lib/notifications/register.dart",
      "mobile/android/app/build.gradle"]),
]


def main() -> None:
    settings = get_settings()

    # Use a dedicated mock store so seeding never touches a real JIRA, and a
    # clean worklog DB next to it.
    mock_path = settings.db_path.replace("worklog.db", "mock_jira.db")
    if mock_path == settings.db_path:
        mock_path = settings.db_path + ".mock"

    # Start fresh so re-running produces a clean, well-ordered demo.
    for p in (settings.db_path, mock_path):
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass

    client = MockJiraClient(mock_path)
    engine = BridgeEngine(settings.db_path, client=client, auto_sync=False)

    now = datetime.now(timezone.utc)
    created = 0
    for i, (ago, dur, source, project, msg, files) in enumerate(SAMPLES):
        ended = now - timedelta(minutes=ago)
        started = ended - timedelta(minutes=dur)
        event = build_event(
            raw_message=msg,
            files=files,
            source=source,
            project_key=project,
            started_at=iso(started),
            ended_at=iso(ended),
        )
        engine.ingest(event, sync=False)
        # Sync all but the two most recent so the dashboard shows "pending" too.
        if i >= 2:
            engine.sync_event(event)
        created += 1

    engine.close()
    print(f"Seeded {created} work events.")
    print(f"  worklog DB : {settings.db_path}")
    print(f"  mock JIRA  : {mock_path}")
    print("\nNext:")
    print("  python -m jira_bridge report --today")
    print("  python -m jira_bridge dashboard")


if __name__ == "__main__":
    main()
