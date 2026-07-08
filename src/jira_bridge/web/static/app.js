/* JIRA AI Bridge — dashboard front-end (vanilla JS).
   Fetches JSON from the REST API, renders the timeline + stats, handles
   range toggling, sync buttons, live auto-refresh and dark mode. */

(() => {
  "use strict";

  let currentRange = "today";
  let refreshTimer = null;
  const REFRESH_MS = 8000;

  const $ = (sel) => document.querySelector(sel);
  const el = (tag, cls, html) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html !== undefined) n.innerHTML = html;
    return n;
  };

  function fmtTime(iso) {
    try {
      const d = new Date(iso);
      return d.toLocaleString(undefined, {
        weekday: "short", hour: "2-digit", minute: "2-digit",
        month: "short", day: "numeric",
      });
    } catch (e) { return iso; }
  }

  function toast(msg) {
    let t = $("#toast");
    if (!t) {
      t = el("div", "toast");
      t.id = "toast";
      document.body.appendChild(t);
    }
    t.textContent = msg;
    t.classList.add("show");
    setTimeout(() => t.classList.remove("show"), 2600);
  }

  async function getJSON(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }

  async function postJSON(url, body) {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }

  // ----- Rendering ---------------------------------------------------------

  function renderSummary(s) {
    $("#stat-today").textContent = s.tasks_today;
    $("#stat-week").textContent = s.tasks_week;
    $("#stat-total").textContent = s.tasks_total;
    $("#stat-time").textContent = s.total_time_human;
    $("#stat-pending").textContent = s.pending;

    const renderChips = (target, obj) => {
      const c = $(target);
      c.innerHTML = "";
      const entries = Object.entries(obj || {});
      if (!entries.length) { c.appendChild(el("span", "muted", "none")); return; }
      entries.forEach(([k, v]) => c.appendChild(el("span", "chip", `${k} · ${v}`)));
    };
    renderChips("#by-source", s.by_source);
    renderChips("#by-project", s.by_project);
  }

  function eventCard(ev) {
    const card = el("article", `event-card src-${ev.source}`);

    // Main column
    const main = el("div", "event-main");
    main.appendChild(el("h3", "event-title", escapeHtml(ev.title)));

    const meta = el("div", "event-meta");
    meta.appendChild(el("span", `badge badge-${ev.source}`, ev.source));
    meta.appendChild(el("span", "badge badge-type", escapeHtml(ev.issue_type)));
    meta.appendChild(el("span", null, fmtTime(ev.ended_at)));
    meta.appendChild(el("span", null, `${ev.files_count} file${ev.files_count === 1 ? "" : "s"}`));
    if (ev.duration_minutes > 0)
      meta.appendChild(el("span", null, `${ev.duration_minutes}m`));
    main.appendChild(meta);

    if (ev.tags && ev.tags.length) {
      const tags = el("div", "tags");
      ev.tags.forEach((t) => tags.appendChild(el("span", "tag", escapeHtml(t))));
      main.appendChild(tags);
    }

    // Side column
    const side = el("div", "event-side");
    if (ev.jira_key) {
      if (ev.jira_url) {
        const a = el("a", "jira-key", escapeHtml(ev.jira_key));
        a.href = ev.jira_url; a.target = "_blank"; a.rel = "noopener";
        side.appendChild(a);
      } else {
        side.appendChild(el("span", "jira-key", escapeHtml(ev.jira_key)));
      }
    } else {
      side.appendChild(el("span", "jira-key", "—"));
    }
    side.appendChild(el("span", `status status-${ev.status}`, ev.status));

    if (ev.status === "pending" || ev.status === "error") {
      const btn = el("button", "btn btn-sm btn-primary", "Sync to JIRA");
      btn.addEventListener("click", () => syncOne(ev.id, btn));
      side.appendChild(btn);
    }

    card.appendChild(main);
    card.appendChild(side);
    return card;
  }

  function escapeHtml(str) {
    return String(str == null ? "" : str)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function renderTimeline(events) {
    const tl = $("#timeline");
    tl.innerHTML = "";
    if (!events.length) {
      tl.appendChild(el("div", "empty", "No work events in this period yet. Run a watcher or seed the demo data."));
      return;
    }
    events.forEach((ev) => tl.appendChild(eventCard(ev)));
  }

  // ----- Data flow ---------------------------------------------------------

  async function refresh() {
    try {
      const [summary, list] = await Promise.all([
        getJSON("/api/summary"),
        getJSON(`/api/events?range=${currentRange}`),
      ]);
      renderSummary(summary);
      renderTimeline(list.events);
    } catch (e) {
      console.error(e);
    }
  }

  async function syncOne(id, btn) {
    if (btn) { btn.disabled = true; btn.textContent = "Syncing…"; }
    try {
      const res = await postJSON("/api/sync", { id });
      toast(res.synced ? `Synced ${res.events[0].jira_key}` : "Nothing to sync");
      await refresh();
    } catch (e) {
      toast("Sync failed");
      if (btn) { btn.disabled = false; btn.textContent = "Sync to JIRA"; }
    }
  }

  async function syncAll() {
    const btn = $("#sync-all");
    btn.disabled = true; btn.textContent = "Syncing…";
    try {
      const res = await postJSON("/api/sync", {});
      toast(res.synced ? `Synced ${res.synced} task(s)` : "Nothing pending");
      await refresh();
    } catch (e) {
      toast("Sync failed");
    } finally {
      btn.disabled = false; btn.textContent = "Sync all pending";
    }
  }

  function startAutoRefresh() {
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(refresh, REFRESH_MS);
  }

  // ----- Theme -------------------------------------------------------------

  function initTheme() {
    const saved = localStorage.getItem("jb-theme");
    if (saved) document.documentElement.setAttribute("data-theme", saved);
    $("#theme-toggle").addEventListener("click", () => {
      const cur = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", cur);
      localStorage.setItem("jb-theme", cur);
      $("#theme-toggle").innerHTML = cur === "dark" ? "&#9790;" : "&#9728;";
    });
  }

  // ----- Wire up -----------------------------------------------------------

  document.addEventListener("DOMContentLoaded", () => {
    initTheme();

    document.querySelectorAll("#range-toggle .seg").forEach((b) => {
      b.addEventListener("click", () => {
        document.querySelectorAll("#range-toggle .seg").forEach((x) => x.classList.remove("active"));
        b.classList.add("active");
        currentRange = b.dataset.range;
        refresh();
      });
    });

    $("#sync-all").addEventListener("click", syncAll);

    refresh();
    startAutoRefresh();
  });
})();
