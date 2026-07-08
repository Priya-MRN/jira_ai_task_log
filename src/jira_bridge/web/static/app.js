/* JIRA AI Bridge — dashboard front-end (vanilla JS).
   Fetches JSON from the REST API, renders the timeline + stats, handles
   range toggling, sync buttons, live auto-refresh and dark mode. */

(() => {
  "use strict";

  let currentRange = "today";
  let refreshTimer = null;
  let firstLoad = true;
  let prevSyncedKeys = new Set();
  const REFRESH_MS = 8000;

  const prefersReduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const $ = (sel) => document.querySelector(sel);
  const el = (tag, cls, html) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html !== undefined) n.innerHTML = html;
    return n;
  };

  // ----- Inline SVG icons --------------------------------------------------
  const ICON = {
    clock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
    file: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>',
    cal: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>',
    check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>',
    alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 8v5M12 17h.01"/><circle cx="12" cy="12" r="9"/></svg>',
    info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>',
    sync: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/></svg>',
    inbox: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>',
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

  function toast(msg, type) {
    let t = $("#toast");
    if (!t) {
      t = el("div", "toast");
      t.id = "toast";
      t.setAttribute("role", "status");
      t.setAttribute("aria-live", "polite");
      document.body.appendChild(t);
    }
    const kind = type || "info";
    const icon = kind === "success" ? ICON.check : kind === "error" ? ICON.alert : ICON.info;
    t.className = "toast type-" + kind;
    t.innerHTML = `<span class="toast-ico">${icon}</span><span>${escapeHtml(msg)}</span>`;
    // Force reflow so re-triggering the transition works.
    void t.offsetWidth;
    t.classList.add("show");
    clearTimeout(t._hideTimer);
    t._hideTimer = setTimeout(() => t.classList.remove("show"), 2800);
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

  function setLoading(btn, on, loadingText) {
    if (!btn) return;
    if (on) {
      btn.disabled = true;
      btn.classList.add("is-loading");
      btn.setAttribute("aria-busy", "true");
    } else {
      btn.disabled = false;
      btn.classList.remove("is-loading");
      btn.removeAttribute("aria-busy");
    }
  }

  // ----- Rendering ---------------------------------------------------------

  function setStat(id, value) {
    const node = $(id);
    if (!node) return;
    const next = String(value);
    if (node.textContent !== next && !firstLoad && !prefersReduced) {
      node.classList.remove("bump");
      void node.offsetWidth;
      node.classList.add("bump");
    }
    node.textContent = next;
  }

  function renderSummary(s) {
    setStat("#stat-today", s.tasks_today);
    setStat("#stat-week", s.tasks_week);
    setStat("#stat-total", s.tasks_total);
    setStat("#stat-time", s.total_time_human);
    setStat("#stat-pending", s.pending);

    const pendingCard = $("#stat-pending") && $("#stat-pending").closest(".stat-card");
    if (pendingCard) pendingCard.classList.toggle("has-pending", Number(s.pending) > 0);

    const renderChips = (target, obj) => {
      const c = $(target);
      c.innerHTML = "";
      const entries = Object.entries(obj || {});
      if (!entries.length) { c.appendChild(el("span", "muted", "none")); return; }
      entries.forEach(([k, v]) =>
        c.appendChild(el("span", "chip", `${escapeHtml(k)} <span class="chip-n">${v}</span>`)));
    };
    renderChips("#by-source", s.by_source);
    renderChips("#by-project", s.by_project);
  }

  function metaItem(icon, text) {
    return el("span", "meta-item", `${icon}<span>${escapeHtml(text)}</span>`);
  }

  function eventCard(ev) {
    const card = el("article", `event-card src-${ev.source}`);

    // Flash cards that just transitioned into synced since the last render.
    if (ev.status === "synced" && ev.jira_key && !prevSyncedKeys.has(ev.jira_key) && !firstLoad) {
      card.classList.add("just-synced");
    }

    // Main column
    const main = el("div", "event-main");
    main.appendChild(el("h3", "event-title", escapeHtml(ev.title)));

    const meta = el("div", "event-meta");
    meta.appendChild(el("span", `badge badge-${ev.source}`, escapeHtml(ev.source)));
    meta.appendChild(el("span", "badge badge-type", escapeHtml(ev.issue_type)));
    meta.appendChild(metaItem(ICON.cal, fmtTime(ev.ended_at)));
    meta.appendChild(metaItem(ICON.file, `${ev.files_count} file${ev.files_count === 1 ? "" : "s"}`));
    if (ev.duration_minutes > 0)
      meta.appendChild(metaItem(ICON.clock, `${ev.duration_minutes}m`));
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
      side.appendChild(el("span", "jira-key is-empty", "—"));
    }
    side.appendChild(el("span", `status status-${ev.status}`, escapeHtml(ev.status)));

    if (ev.status === "pending" || ev.status === "error") {
      const btn = el("button", "btn btn-sm btn-primary", `${ICON.sync}<span class="btn-text">Sync to JIRA</span>`);
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

  function renderEmpty(tl) {
    const wrap = el("div", "empty");
    wrap.appendChild(el("div", null, ICON.inbox));
    wrap.appendChild(el("div", "empty-title", "No work events in this period"));
    wrap.appendChild(el("div", "empty-sub",
      "Nothing captured for this range yet. Run a watcher or seed the demo data, and your AI work will appear here automatically."));
    tl.appendChild(wrap);
  }

  function renderTimeline(events) {
    const tl = $("#timeline");
    tl.setAttribute("aria-busy", "false");
    tl.innerHTML = "";
    if (!events.length) {
      renderEmpty(tl);
    } else {
      events.forEach((ev) => tl.appendChild(eventCard(ev)));
    }
    // Track which keys are synced so the next render can flash new ones.
    prevSyncedKeys = new Set(
      events.filter((e) => e.status === "synced" && e.jira_key).map((e) => e.jira_key)
    );
  }

  // ----- Data flow ---------------------------------------------------------

  async function refresh(opts) {
    const swap = opts && opts.swap;
    const tl = $("#timeline");
    if (swap && tl && !prefersReduced) tl.classList.add("is-swapping");
    try {
      const [summary, list] = await Promise.all([
        getJSON("/api/summary"),
        getJSON(`/api/events?range=${currentRange}`),
      ]);
      renderSummary(summary);
      renderTimeline(list.events);
      firstLoad = false;
    } catch (e) {
      console.error(e);
      if (firstLoad && tl) {
        tl.setAttribute("aria-busy", "false");
        tl.innerHTML = "";
        const wrap = el("div", "empty");
        wrap.appendChild(el("div", "empty-title", "Couldn’t load events"));
        wrap.appendChild(el("div", "empty-sub", "The dashboard API didn’t respond. It will retry automatically."));
        tl.appendChild(wrap);
      }
    } finally {
      if (tl) tl.classList.remove("is-swapping");
    }
  }

  async function syncOne(id, btn) {
    setLoading(btn, true);
    try {
      const res = await postJSON("/api/sync", { id });
      if (res.synced && res.events[0]) {
        toast(`Synced ${res.events[0].jira_key}`, "success");
      } else {
        toast("Nothing to sync", "info");
      }
      await refresh();
    } catch (e) {
      toast("Sync failed — please retry", "error");
      setLoading(btn, false);
    }
  }

  async function syncAll() {
    const btn = $("#sync-all");
    setLoading(btn, true);
    try {
      const res = await postJSON("/api/sync", {});
      if (res.synced) {
        toast(`Synced ${res.synced} task${res.synced === 1 ? "" : "s"} to JIRA`, "success");
      } else {
        toast("Nothing pending to sync", "info");
      }
      await refresh();
    } catch (e) {
      toast("Sync failed — please retry", "error");
    } finally {
      setLoading(btn, false);
    }
  }

  function startAutoRefresh() {
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(refresh, REFRESH_MS);
  }

  // ----- Theme -------------------------------------------------------------

  const SUN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"></path></svg>';
  const MOON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path></svg>';

  function syncThemeIcon() {
    const isDark = document.documentElement.getAttribute("data-theme") === "dark";
    const tgl = $("#theme-toggle");
    if (tgl) {
      tgl.innerHTML = isDark ? MOON : SUN;
      tgl.setAttribute("aria-label", isDark ? "Switch to light mode" : "Switch to dark mode");
      tgl.setAttribute("aria-pressed", String(isDark));
    }
  }

  function initTheme() {
    const saved = localStorage.getItem("jb-theme");
    if (saved) document.documentElement.setAttribute("data-theme", saved);
    syncThemeIcon();
    $("#theme-toggle").addEventListener("click", () => {
      const cur = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", cur);
      localStorage.setItem("jb-theme", cur);
      syncThemeIcon();
    });
  }

  // ----- Wire up -----------------------------------------------------------

  document.addEventListener("DOMContentLoaded", () => {
    initTheme();

    document.querySelectorAll("#range-toggle .seg").forEach((b) => {
      b.addEventListener("click", () => {
        if (b.classList.contains("active")) return;
        document.querySelectorAll("#range-toggle .seg").forEach((x) => {
          x.classList.remove("active");
          x.setAttribute("aria-selected", "false");
        });
        b.classList.add("active");
        b.setAttribute("aria-selected", "true");
        currentRange = b.dataset.range;
        refresh({ swap: true });
      });
    });

    $("#sync-all").addEventListener("click", syncAll);

    refresh();
    startAutoRefresh();
  });
})();
