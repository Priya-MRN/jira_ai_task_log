# JIRA AI Bridge

> **Stop logging your work twice.** You already do the work *and* tell your AI
> agent what to do — JIRA AI Bridge watches that activity and creates + logs the
> JIRA tasks for you, then shows a beautiful dashboard of *"what I did today /
> this week."*

This is the elevated version of the CodeAlpha **Task Automation** task: instead
of automating one chore, it automates the most tedious chore in modern,
AI-assisted development — **manual JIRA bookkeeping**. It is a *bridge* between
your AI coding sessions (Claude Code, AI editors) and JIRA.

- **Zero manual logging** — watchers capture commits, AI sessions and manual
  notes and turn them into JIRA issues automatically.
- **Always runs** — with no JIRA credentials it falls back to a fully local
  mock JIRA, so the dashboard and reports work offline out of the box.
- **Three modular watchers** — git, Claude Code hook, and a manual CLI trigger.
- **Modern responsive dashboard** — timeline of work, summary stats, one-click
  sync, dark mode.

---

## Architecture

```
                 ┌──────────────────────────────────────────────┐
                 │                  WATCHERS                      │
                 │                                                │
   git repo ───► │  GitWatcher   ─┐                               │
   Claude Code ─►│  ClaudeHook   ─┤── emit WorkEvent ──┐          │
   you (manual)─►│  CliWatcher   ─┘                    │          │
                 └─────────────────────────────────────┼─────────┘
                                                        ▼
                                       ┌────────────────────────────┐
                                       │        BridgeEngine          │
                                       │  • infer title/type/tags     │
                                       │  • deduplicate (content hash) │
                                       │  • persist  -> worklog.db     │
                                       │  • sync     -> JiraClient     │
                                       └───────────┬───────────┬─────┘
                                                   │           │
                                  ┌────────────────▼──┐   ┌────▼─────────────┐
                                  │  RealJiraClient    │   │  MockJiraClient  │
                                  │  JIRA Cloud REST   │   │  local SQLite    │
                                  │  api/3 (requests)  │   │  LOCAL-1, LOCAL-2 │
                                  └────────────────────┘   └──────────────────┘
                                                   ▲
                          get_client() auto-selects Real if creds + reachable,
                          else falls back to Mock (always works).

                 ┌──────────────────────────────────────────────┐
                 │   Flask dashboard  +  REST API  +  CLI         │
                 │   /  /api/events  /api/summary  /api/sync       │
                 └──────────────────────────────────────────────┘
```

### Project layout

```
jira_ai_bridge/
├─ src/jira_bridge/
│  ├─ core.py            # WorkEvent + BridgeEngine (persist, dedup, infer, sync)
│  ├─ jira_client.py     # JiraClient ABC, RealJiraClient, MockJiraClient, get_client()
│  ├─ config.py          # .env loading + Settings
│  ├─ report.py          # text "what I did" reports
│  ├─ cli.py             # argparse CLI
│  ├─ __main__.py        # python -m jira_bridge
│  ├─ watchers/
│  │  ├─ base.py         # BaseWatcher interface
│  │  ├─ git_watcher.py  # polls git log / status
│  │  ├─ claude_hook.py  # reads Claude Code hook payload from stdin
│  │  └─ cli_watcher.py  # manual one-shot trigger
│  └─ web/
│     ├─ app.py          # Flask app factory + JSON API
│     ├─ templates/dashboard.html
│     └─ static/style.css, app.js
├─ tests/                # pytest suite (no network / git required)
├─ demo_seed.py          # seed ~12 sample events for an instant great demo
├─ requirements.txt
├─ pyproject.toml
└─ .env.example
```

---

## Install

```bash
cd jira_ai_bridge
python -m pip install -e .          # installs deps + `jira-bridge` console script
# or, without an editable install:
python -m pip install -r requirements.txt
```

Python 3.10+.

---

## Quickstart (offline, 30 seconds)

```bash
python demo_seed.py                 # seed sample work events
python -m jira_bridge report --today
python -m jira_bridge dashboard     # open http://127.0.0.1:5050
```

Everything above runs with **no JIRA account** — it uses the local mock store.

---

## Connecting real JIRA (optional)

1. Copy `.env.example` to `.env`.
2. Fill in:
   ```
   JIRA_BASE_URL=https://your-team.atlassian.net
   JIRA_EMAIL=you@example.com
   JIRA_API_TOKEN=<token from id.atlassian.com/manage-profile/security/api-tokens>
   JIRA_PROJECT_KEY=ENG
   ```
3. Check it:
   ```bash
   python -m jira_bridge status
   ```

### Real-vs-mock fallback

`get_client()` picks the backend automatically:

- **Real** — when all credentials are present *and* the instance responds to
  `GET /rest/api/3/myself`.
- **Mock** — when credentials are missing, `JIRA_BRIDGE_FORCE_MOCK=1` is set, or
  the instance is unreachable (a warning is logged, then it falls back).

This means the tool **never crashes** because JIRA is down or unconfigured.

---

## The watchers

### 1. Git watcher — works with ANY editor/AI agent

Polls a repo, turns each new commit (and uncommitted changes) into a WorkEvent.

```bash
python -m jira_bridge watch-git --repo /path/to/repo --interval 60
```

### 2. Claude Code hook — capture AI sessions automatically

Add this to `~/.claude/settings.json` (or a project `.claude/settings.json`).
The `Stop` hook fires when Claude finishes responding; the payload arrives on
stdin and is turned into a WorkEvent (enriched with the repo's changed files):

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          { "type": "command", "command": "python -m jira_bridge hook" }
        ]
      }
    ]
  }
}
```

You can also wire it to `PostToolUse` if you prefer per-tool granularity. Test
it manually by piping a payload:

```bash
echo '{"hook_event_name":"Stop","session_id":"abc","cwd":".","note":"Built the dashboard"}' | python -m jira_bridge hook
```

### 3. CLI watcher — manual one-shot log

Reads changed files in a repo plus an optional note, logs one event:

```bash
python -m jira_bridge log --note "Refactored auth module" --repo /path/to/repo
```

---

## Dashboard

```bash
python -m jira_bridge dashboard --port 5050
```

- **Today / This week / All** toggle.
- Timeline cards: title, time, **source badge** (git/claude/cli), files-changed
  count, JIRA key (linked to real JIRA when available), status.
- Summary stats: tasks today, tasks this week, total time inferred, by-source
  and by-project breakdowns.
- **Sync to JIRA** per pending event, plus **Sync all pending**.
- Live auto-refresh (polls the JSON API), responsive layout, dark mode.

### REST API

| Method | Endpoint        | Description                                  |
|--------|-----------------|----------------------------------------------|
| GET    | `/api/events`   | `?range=today|week|all` — list work events   |
| GET    | `/api/summary`  | Summary statistics                           |
| POST   | `/api/sync`     | `{"id": "..."}` to sync one, `{}` for all    |

---

## Reports

```bash
python -m jira_bridge report --today
python -m jira_bridge report --week
python -m jira_bridge report --all
python -m jira_bridge report --range 2026-06-01 2026-06-28
```

---

## Demo Video

A short walkthrough of the dashboard — summary stats, the work timeline, and
one-click **Sync to JIRA**. Click the poster to play [`docs/demo.mp4`](docs/demo.mp4):

[![Watch the demo](docs/demo-thumbnail.png)](docs/demo.mp4)

> On GitHub, drag-and-drop [`docs/demo.mp4`](docs/demo.mp4) into this section on
> github.com for inline playback.

## Screenshots

### Dashboard overview

Summary stats (tasks today / this week / total, time logged, pending sync),
**by-source** and **by-project** breakdowns, MOCK/real mode, and dark-mode toggle.

![Dashboard overview](docs/screenshot-dashboard.png)

### Work timeline

Each captured work event as a card: title, **source badge** (git / claude / cli),
inferred **issue type** (Story / Bug / Task), files changed, time, JIRA key, and
status.

![Work timeline](docs/screenshot-timeline.png)

### One-click sync

Pending events show a **Sync to JIRA** button; syncing assigns a key
(e.g. `ENG-22`) and flips the status to **synced** (toast confirmation shown).

![Sync to JIRA](docs/screenshot-sync.png)

---

## Testing

The suite runs with **no network and no real git** (subprocess is monkeypatched,
temp DBs are used):

```bash
python -m pip install pytest flask requests python-dotenv
python -m pytest -q
```

Covers: title/issue-type inference, dedup, persistence + sync, mock JIRA
create/transition/comment/worklog, the factory fallback, git-watcher parsing,
the report generator, and the Claude hook watcher.

---

## Configuration reference

| Variable                   | Default      | Meaning                              |
|----------------------------|--------------|--------------------------------------|
| `JIRA_BASE_URL`            | —            | JIRA Cloud site URL                  |
| `JIRA_EMAIL`               | —            | JIRA login email                     |
| `JIRA_API_TOKEN`           | —            | JIRA API token                       |
| `JIRA_PROJECT_KEY`         | `LOCAL`      | Project key for new issues           |
| `JIRA_BRIDGE_DB`           | `worklog.db` | Local worklog SQLite path            |
| `JIRA_BRIDGE_FORCE_MOCK`   | `false`      | Force the mock client                |
| `JIRA_BRIDGE_AUTO_SYNC`    | `true`       | Push to JIRA on capture              |
| `JIRA_BRIDGE_GIT_INTERVAL` | `60`         | Git watcher poll interval (seconds)  |
| `JIRA_BRIDGE_PORT`         | `5050`       | Dashboard port                       |

---

## License

MIT.
