"""Flask web dashboard for JIRA AI Bridge.

Serves a modern, responsive dashboard plus a small JSON REST API:

    GET  /                 -> dashboard HTML
    GET  /api/events       -> list events (filter via ?range=today|week|all)
    GET  /api/summary      -> summary statistics
    POST /api/sync         -> sync one event ({"id": ...}) or all pending ({})
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from flask import Flask, jsonify, render_template, request

from ..config import get_settings
from ..core import BridgeEngine, WorkEvent
from ..jira_client import get_client


def _event_payload(event: WorkEvent, client) -> Dict[str, Any]:
    """Serialise an event for the API, including a JIRA URL when available."""
    url = None
    if event.jira_key and client is not None:
        url = client.issue_url(event.jira_key)
    data = event.to_dict()
    data["jira_url"] = url
    data["duration_minutes"] = event.duration_minutes()
    data["files_count"] = len(event.files_changed)
    return data


def create_app() -> Flask:
    """Application factory."""
    settings = get_settings()
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )

    # A single shared client; the engine is created per-request so SQLite
    # connections are not shared across threads.
    client = get_client(settings, check_reachable=True)

    def engine() -> BridgeEngine:
        return BridgeEngine(settings.db_path, client=client, auto_sync=False)

    @app.route("/")
    def index():
        return render_template(
            "dashboard.html",
            client_kind=client.kind,
            project_key=settings.jira_project_key,
            jira_base_url=settings.jira_base_url or "",
        )

    @app.route("/api/events")
    def api_events():
        rng = request.args.get("range", "all")
        eng = engine()
        try:
            if rng == "today":
                events = eng.events_today()
            elif rng == "week":
                events = eng.events_this_week()
            else:
                events = eng.all_events()
            payload = [_event_payload(e, client) for e in events]
        finally:
            eng.close()
        return jsonify({"range": rng, "count": len(payload), "events": payload})

    @app.route("/api/summary")
    def api_summary():
        eng = engine()
        try:
            data = eng.summary()
            data["client_kind"] = client.kind
        finally:
            eng.close()
        return jsonify(data)

    @app.route("/api/sync", methods=["POST"])
    def api_sync():
        body = request.get_json(silent=True) or {}
        eng = engine()
        try:
            if body.get("id"):
                result = eng.sync_by_id(body["id"])
                synced = [result] if result else []
            else:
                synced = eng.sync_pending()
            payload = [_event_payload(e, client) for e in synced]
        finally:
            eng.close()
        return jsonify({"synced": len(payload), "events": payload})

    @app.route("/api/health")
    def api_health():
        return jsonify({"ok": True, "time": datetime.now().isoformat()})

    return app


# Convenience for ``flask run`` style usage.
app = None  # populated lazily if needed


if __name__ == "__main__":  # pragma: no cover
    create_app().run(host="127.0.0.1", port=get_settings().web_port)
