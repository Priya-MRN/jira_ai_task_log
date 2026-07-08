"""Command-line interface for JIRA AI Bridge.

Commands:
    watch-git --repo PATH --interval N   Background git watcher.
    log --note "..." [--repo PATH]       Manual one-shot work log (cli watcher).
    hook                                 Read a Claude Code hook payload (stdin).
    dashboard [--port 5050]              Launch the web dashboard.
    report --today | --week | --range    Print a text report.
    status                               Show config + active JIRA client.

Run via the ``jira-bridge`` console script or ``python -m jira_bridge``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from typing import List, Optional

from . import __version__
from .config import get_settings
from .core import BridgeEngine
from .jira_client import get_client
from . import report as report_mod
from .watchers.cli_watcher import CliWatcher
from .watchers.claude_hook import ClaudeHookWatcher
from .watchers.git_watcher import GitWatcher


def _make_engine(check_reachable: bool = True) -> BridgeEngine:
    settings = get_settings()
    client = get_client(settings, check_reachable=check_reachable)
    return BridgeEngine(settings.db_path, client=client, auto_sync=settings.auto_sync)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    settings = get_settings()
    client = get_client(settings, check_reachable=True)
    print("JIRA AI Bridge - status")
    print("=" * 40)
    print(f"  version          : {__version__}")
    print(f"  database         : {settings.db_path}")
    print(f"  project key      : {settings.jira_project_key}")
    print(f"  JIRA base url    : {settings.jira_base_url or '(none)'}")
    print(f"  JIRA email       : {settings.jira_email or '(none)'}")
    print(f"  JIRA token       : {settings.masked_token()}")
    print(f"  credentials set  : {settings.has_real_jira}")
    print(f"  active client    : {client.kind.upper()}")
    print(f"  auto-sync        : {settings.auto_sync}")
    if client.kind == "mock":
        print("\n  -> Running in MOCK mode (no/unreachable JIRA). Everything")
        print("     is stored locally; the dashboard and reports work fully.")
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    engine = _make_engine()
    watcher = CliWatcher(
        engine,
        note=args.note or "",
        repo_path=args.repo,
        project_key=args.project or get_settings().jira_project_key,
    )
    events = watcher.run_once()
    for e in events:
        key = e.jira_key or "(pending)"
        print(f"Logged: {e.title}")
        print(f"  source={e.source} status={e.status} jira={key} files={len(e.files_changed)}")
    engine.close()
    return 0


def cmd_hook(args: argparse.Namespace) -> int:
    # Reads JSON payload from stdin (Claude Code hook). Must never fail loudly,
    # so it never disrupts the Claude session that invoked it.
    try:
        engine = _make_engine(check_reachable=False)
        watcher = ClaudeHookWatcher(
            engine, project_key=args.project or get_settings().jira_project_key
        )
        events = watcher.run_once()
        for e in events:
            print(f"[jira-bridge] captured Claude session -> {e.jira_key or e.title}")
        engine.close()
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[jira-bridge] hook error (ignored): {exc}", file=sys.stderr)
    return 0


def cmd_watch_git(args: argparse.Namespace) -> int:
    settings = get_settings()
    engine = _make_engine()
    watcher = GitWatcher(
        engine,
        repo_path=args.repo,
        project_key=args.project or settings.jira_project_key,
        interval=args.interval or settings.git_poll_interval,
    )
    print(f"Watching git repo: {watcher.repo_path}")
    print(f"Interval: {watcher.interval}s   Project: {watcher.project_key}")
    print("Press Ctrl+C to stop.\n")
    watcher.run_forever(max_iterations=args.max_iterations)
    engine.close()
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    engine = _make_engine(check_reachable=False)
    if args.week:
        print(report_mod.report_week(engine))
    elif args.range:
        try:
            start_s, end_s = args.range
            start = datetime.fromisoformat(start_s)
            end = datetime.fromisoformat(end_s)
        except Exception:
            print("--range expects two ISO dates, e.g. --range 2026-06-01 2026-06-28")
            engine.close()
            return 2
        print(report_mod.report_range(engine, start, end))
    elif args.all:
        print(report_mod.report_all(engine))
    else:  # default: today
        print(report_mod.report_today(engine))
    engine.close()
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    # Imported lazily so the rest of the CLI works without Flask installed.
    from .web.app import create_app

    settings = get_settings()
    port = args.port or settings.web_port
    app = create_app()
    print(f"Dashboard running at http://127.0.0.1:{port}  (Ctrl+C to stop)")
    app.run(host="127.0.0.1", port=port, debug=args.debug, use_reloader=False)
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jira-bridge",
        description="Bridge AI coding sessions to JIRA — zero manual logging.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_status = sub.add_parser("status", help="Show config and active JIRA client.")
    p_status.set_defaults(func=cmd_status)

    p_log = sub.add_parser("log", help="Manually log a work session (cli watcher).")
    p_log.add_argument("--note", "-m", help="Free-form note / summary.")
    p_log.add_argument("--repo", help="Repo path to read changed files from.")
    p_log.add_argument("--project", help="Override JIRA project key.")
    p_log.set_defaults(func=cmd_log)

    p_hook = sub.add_parser("hook", help="Read a Claude Code hook payload from stdin.")
    p_hook.add_argument("--project", help="Override JIRA project key.")
    p_hook.set_defaults(func=cmd_hook)

    p_watch = sub.add_parser("watch-git", help="Watch a git repo on an interval.")
    p_watch.add_argument("--repo", required=True, help="Path to the git repository.")
    p_watch.add_argument("--interval", "-i", type=int, help="Poll interval (seconds).")
    p_watch.add_argument("--project", help="Override JIRA project key.")
    p_watch.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        dest="max_iterations",
        help="Stop after N polls (mainly for testing).",
    )
    p_watch.set_defaults(func=cmd_watch_git)

    p_report = sub.add_parser("report", help="Print a text report of work done.")
    g = p_report.add_mutually_exclusive_group()
    g.add_argument("--today", action="store_true", help="Today's work (default).")
    g.add_argument("--week", action="store_true", help="This week's work.")
    g.add_argument("--all", action="store_true", help="All logged work.")
    g.add_argument(
        "--range",
        nargs=2,
        metavar=("START", "END"),
        help="ISO date range, e.g. 2026-06-01 2026-06-28.",
    )
    p_report.set_defaults(func=cmd_report)

    p_dash = sub.add_parser("dashboard", help="Launch the web dashboard.")
    p_dash.add_argument("--port", "-p", type=int, help="Port (default 5050).")
    p_dash.add_argument("--debug", action="store_true", help="Run Flask in debug mode.")
    p_dash.set_defaults(func=cmd_dashboard)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(
        level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s"
    )
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
