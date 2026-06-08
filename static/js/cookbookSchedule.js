// Cookbook Schedule — opens a small inline form (styled with the app's
// existing .cookbook-* classes) that creates a ScheduledTask with
// action=cookbook_serve. Mounted from two places:
//
//   1. The ^ button next to Launch in a serve panel.
//   2. The "Schedule…" entry in the cached-model ⋯ dropdown menu (which
//      programmatically clicks the ^ button so this module owns the
//      single source of truth).
//
// Feedback uses uiModule.showToast() — the same toast the rest of the
// app uses for "Saved", "Favorited", etc. — so the success message
// doesn't introduce a parallel notification style.
//
// To remove: delete this file + the <script> tag in index.html + the
// ^ button in cookbookServe.js + the "cookbook_serve" entry in
// BUILTIN_ACTIONS + src/cookbook_serve_lifecycle.py + its
// registration line in app.py.

try { (function () {
  function _safe(fn) {
    return function () {
      try { return fn.apply(this, arguments); }
      catch (e) { try { console.warn("[cookbookSchedule]", e); } catch (_) {} }
    };
  }
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // Cached handle to the ui.js showToast function. Bound lazily on
  // first use because ui.js is an ES module — it's not on `window`
  // unless something else has explicitly exposed it.
  let _toastFn = null;
  async function _getToast() {
    if (_toastFn) return _toastFn;
    try {
      const m = await import("/static/js/ui.js");
      _toastFn = m.default?.showToast || m.showToast || null;
    } catch (_) { _toastFn = null; }
    return _toastFn;
  }
  // Optional opts: {action, onAction, duration, leadingIcon}
  async function toast(msg, opts) {
    const fn = await _getToast();
    if (fn) {
      try { fn(msg, opts); return; } catch (_) {}
    }
    try { console.log("[toast]", msg); } catch (_) {}
  }

  // Cached handle to the tasks module so the success toast's "Open"
  // action can jump straight to the new task in the Tasks tab.
  let _tasksMod = null;
  async function _getTasksMod() {
    if (_tasksMod) return _tasksMod;
    try { _tasksMod = await import("/static/js/tasks.js"); } catch (_) {}
    return _tasksMod;
  }
  async function openTaskInTasksTab(taskId) {
    const m = await _getTasksMod();
    if (m && typeof m.openTasks === "function") {
      try { m.openTasks(taskId); return; } catch (_) {}
    }
    // Last-resort fallback: click the sidebar Tasks button.
    document.getElementById("tool-tasks-btn")?.click();
  }

  const DAYS = [
    { k: "MO", l: "Mon", idx: 0 },
    { k: "TU", l: "Tue", idx: 1 },
    { k: "WE", l: "Wed", idx: 2 },
    { k: "TH", l: "Thu", idx: 3 },
    { k: "FR", l: "Fri", idx: 4 },
    { k: "SA", l: "Sat", idx: 5 },
    { k: "SU", l: "Sun", idx: 6 },
  ];
  const WEEKDAYS = new Set(["MO","TU","WE","TH","FR"]);

  // Resolve the model identity from the closest .memory-item card —
  // that's the canonical container the cookbook serve UI uses, with
  // the model repo on data-repo. We do NOT grab the title via
  // textContent, because the title row also contains inline status
  // pills ("running", "downloading") and an "HF ↗" link — pulling all
  // of it in turns a clean preset name like "Qwen3.5-397B-A17B-AWQ"
  // into "Qwen3.5-397B-A17B-AWQ running HF ↗", which then fails the
  // preset lookup in action_cookbook_serve.
  function readPanelConfig(arrowBtn) {
    const item = arrowBtn.closest(".memory-item") || arrowBtn.closest(".hwfit-cached-item");
    const panel = arrowBtn.closest(".hwfit-serve-panel");
    const repo = item?.dataset?.repo
      || arrowBtn.closest(".hwfit-serve-panel")?.dataset?.repo
      || "";
    // Title = last segment of the repo (after the final /), which is
    // exactly what the cookbook UI renders in the card title and what
    // the preset registry uses as its short name. e.g.
    //   cyankiwi/Qwen3.5-397B-A17B-AWQ  →  Qwen3.5-397B-A17B-AWQ
    // Falls back to data-modelName or the bare repo for ollama-style
    // entries that don't have a slash.
    let title = "";
    if (repo) {
      title = repo.includes("/") ? repo.split("/").pop() : repo;
    }
    if (!title) {
      title = item?.dataset?.modelName || "model";
    }
    return { panel, item, title, repo_id: repo, host: item?.dataset?.host || "" };
  }

  function buildFormHtml(cfg) {
    return `
      <div class="hwfit-schedule-form cookbook-panel">
        <div class="hwfit-schedule-title">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="4" width="18" height="18" rx="2"/>
            <line x1="16" y1="2" x2="16" y2="6"/>
            <line x1="8" y1="2" x2="8" y2="6"/>
            <line x1="3" y1="10" x2="21" y2="10"/>
          </svg>
          <span class="hwfit-schedule-title-text">Schedule serve: <strong>${esc(cfg.title)}</strong></span>
          <span class="hwfit-schedule-title-spacer"></span>
          <label class="hwfit-schedule-mirror-toggle" title="Also create a calendar event on the Cookbook calendar">
            <span class="hwfit-schedule-mirror-label">Create event in calendar</span>
            <span class="admin-switch hwfit-schedule-mirror-switch">
              <input type="checkbox" class="hwfit-sched-calendar-mirror" />
              <span class="admin-slider"></span>
            </span>
          </label>
        </div>

        <div class="hwfit-schedule-row">
          <label class="hwfit-schedule-field">
            <span>From</span>
            <input type="time" class="hwfit-sched-start cookbook-field-input" value="09:00" />
          </label>
          <label class="hwfit-schedule-field">
            <span>Until</span>
            <input type="time" class="hwfit-sched-end cookbook-field-input" value="17:00" />
          </label>
        </div>

        <div class="hwfit-schedule-row hwfit-schedule-days-row">
          <span class="hwfit-schedule-label">Days</span>
          <div class="hwfit-sched-days">
            ${DAYS.map(d => `
              <button type="button" class="hwfit-sched-day-chip${WEEKDAYS.has(d.k) ? " is-on" : ""}" data-day="${d.k}">${d.l}</button>
            `).join("")}
          </div>
          <span class="hwfit-schedule-actions-spacer"></span>
          <button type="button" class="cookbook-btn hwfit-sched-cancel" title="Cancel">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:5px;flex-shrink:0;"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            <span>Cancel</span>
          </button>
          <button type="button" class="cookbook-btn hwfit-sched-save" title="Save schedule" aria-label="Save schedule">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:5px;flex-shrink:0;"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
            <span>Save</span>
          </button>
        </div>

        <div class="hwfit-sched-err"></div>
      </div>`;
  }

  function openForm(arrowBtn) {
    const cfg = readPanelConfig(arrowBtn);
    const anchor = cfg.panel
      || cfg.item
      || arrowBtn.closest(".cookbook-saved-item")
      || arrowBtn.parentElement?.parentElement
      || arrowBtn.parentElement;
    if (!anchor) {
      toast("Couldn't find a panel to mount the schedule form");
      return;
    }
    // Toggle.
    const existing = anchor.querySelector(".hwfit-schedule-form");
    if (existing) { existing.remove(); return; }
    const tmp = document.createElement("div");
    tmp.innerHTML = buildFormHtml(cfg);
    const form = tmp.firstElementChild;
    anchor.appendChild(form);
    setTimeout(() => {
      try { form.scrollIntoView({ behavior: "smooth", block: "nearest" }); } catch (_) {}
    }, 50);
    wireForm(form, cfg);
  }

  function wireForm(form, cfg) {
    form.querySelectorAll(".hwfit-sched-day-chip").forEach(chip => {
      chip.addEventListener("click", () => chip.classList.toggle("is-on"));
    });
    form.querySelector(".hwfit-sched-cancel").addEventListener("click", () => form.remove());
    form.querySelector(".hwfit-sched-save").addEventListener("click", _safe(async () => {
      const startTime = form.querySelector(".hwfit-sched-start").value;
      const endTime = form.querySelector(".hwfit-sched-end").value;
      const days = Array.from(form.querySelectorAll(".hwfit-sched-day-chip.is-on")).map(c => c.dataset.day);
      const mirrorToCalendar = !!form.querySelector(".hwfit-sched-calendar-mirror")?.checked;
      const errEl = form.querySelector(".hwfit-sched-err");
      errEl.textContent = "";
      errEl.classList.remove("is-visible");

      function fail(msg) {
        errEl.textContent = msg;
        errEl.classList.add("is-visible");
      }
      if (!/^\d\d:\d\d$/.test(startTime) || !/^\d\d:\d\d$/.test(endTime)) {
        return fail("Start and end must be HH:MM");
      }
      if (!days.length) {
        return fail("Pick at least one day");
      }

      const [sh, sm] = startTime.split(":").map(Number);
      const [eh, em] = endTime.split(":").map(Number);
      let dur = (eh * 60 + em) - (sh * 60 + sm);
      if (dur <= 0) dur += 24 * 60;

      // The backend stores scheduled_time as UTC. The user picks
      // wall-clock LOCAL time. Without converting, "09:55" in a UTC+9
      // timezone gets stored as 09:55 UTC = 18:55 local → next-run
      // shows ~9 hours later instead of "in 5 min". Mirror what
      // tasks.js does via its _localTimeToUtc helper.
      const _localHHMMToUtc = (hhmm) => {
        const [h, m] = hhmm.split(":").map(Number);
        const d = new Date();
        d.setHours(h, m, 0, 0);
        return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`;
      };
      const startUtc = _localHHMMToUtc(startTime);
      const [shUtc, smUtc] = startUtc.split(":").map(Number);

      const allDays = days.length === 7;
      const weekdaysOnly = days.length === 5 && ["MO","TU","WE","TH","FR"].every(d => days.includes(d));
      const sched = {};
      if (allDays) {
        sched.schedule = "daily";
        sched.scheduled_time = startUtc;
      } else if (weekdaysOnly) {
        sched.schedule = "cron";
        sched.cron_expression = `${smUtc} ${shUtc} * * 1-5`;
      } else if (days.length === 1) {
        const dayIdx = DAYS.find(d => d.k === days[0]).idx;
        sched.schedule = "weekly";
        sched.scheduled_time = startUtc;
        sched.scheduled_day = dayIdx;
      } else {
        const dayNum = days.map(k => {
          const i = DAYS.find(d => d.k === k).idx;
          return i === 6 ? 0 : i + 1;
        });
        sched.schedule = "cron";
        sched.cron_expression = `${smUtc} ${shUtc} * * ${dayNum.join(",")}`;
      }

      // Name: "Serve: <full model name>" — pulled from .memory-item-title
      // so it's the user's display name (e.g. "Qwen3.5-397B-A17B-AWQ")
      // not a placeholder like "model".
      const fullName = (cfg.title || cfg.repo_id || "").trim() || "model";
      const payload = {
        name: `Serve: ${fullName}`,
        task_type: "action",
        action: "cookbook_serve",
        trigger_type: "schedule",
        prompt: JSON.stringify({
          preset: fullName,
          repo_id: cfg.repo_id || "",
          host: cfg.host || "",
          end_after_min: dur,
        }),
        ...sched,
      };
      const saveBtn = form.querySelector(".hwfit-sched-save");
      saveBtn.disabled = true;
      saveBtn.textContent = "Saving…";
      try {
        const r = await fetch("/api/tasks", {
          method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await r.json();
        if (!r.ok || data.error) {
          fail(data.error || data.detail || `HTTP ${r.status}`);
          saveBtn.disabled = false;
          saveBtn.textContent = "Save schedule";
          toast(`Schedule save failed: ${data.error || data.detail || r.status}`);
          return;
        }
        if (mirrorToCalendar) {
          // Mirror onto a dedicated "Cookbook" calendar so the user can
          // toggle the whole set on/off as a unit in the calendar UI.
          // Best-effort: if anything here fails, we still consider the
          // task creation a success (the task itself works regardless).
          try {
            const calsRes = await fetch("/api/calendar/calendars", { credentials: "same-origin" });
            const calsBody = calsRes.ok ? await calsRes.json() : {};
            let cookbookCal = (calsBody.calendars || []).find(c => (c.name || "").toLowerCase() === "cookbook");
            if (!cookbookCal) {
              const mk = await fetch("/api/calendar/calendars?name=Cookbook&color=%233b82f6", {
                method: "POST", credentials: "same-origin",
              });
              if (mk.ok) {
                const mkData = await mk.json();
                // The create endpoint returns {ok, id, name, color}; the
                // list endpoint returns {href, name, color}. The two map
                // 1:1 (href === id) so we synthesize the same shape.
                cookbookCal = { href: mkData.id, name: mkData.name, color: mkData.color };
              }
            }
            // The `cookbook_task_id:` marker on its own line lets
            // calendar.js's event-form code detect that this event was
            // created from a Cookbook schedule and render an
            // "Open task" button alongside the description, so the user
            // can jump straight to the source task from the calendar UI.
            const evBody = {
              summary: payload.name,
              dtstart: new Date().toISOString(),
              dtend: new Date(Date.now() + dur * 60 * 1000).toISOString(),
              all_day: false,
              description: `Auto-mirrored from Cookbook schedule task ${data.id || ""}.\n`
                + `Edit/delete the task in the Tasks tab — this event will follow.\n`
                + `cookbook_task_id: ${data.id || ""}`,
              rrule: weekdaysOnly
                ? "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
                : (sched.schedule === "weekly" ? `FREQ=WEEKLY;BYDAY=${days.join(",")}`
                  : (sched.schedule === "daily" ? "FREQ=DAILY" : "FREQ=WEEKLY")),
              color: "#3b82f6",
            };
            if (cookbookCal?.href) evBody.calendar_href = cookbookCal.href;
            const evRes = await fetch("/api/calendar/events", {
              method: "POST", credentials: "same-origin",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(evBody),
            });
            const evData = evRes.ok ? await evRes.json() : null;
            // Stash the event uid + calendar href on the task's prompt
            // JSON so the task-delete hook can cascade the calendar
            // cleanup. PATCH the task with an updated prompt.
            if (evData && (evData.uid || evData.id)) {
              const eventUid = evData.uid || evData.id;
              try {
                const updatedPrompt = JSON.stringify({
                  ...JSON.parse(payload.prompt),
                  cookbook_event_uid: eventUid,
                  cookbook_event_calendar: cookbookCal?.href || "",
                });
                // /api/tasks/{id} accepts PUT, not PATCH — sending PATCH
                // here silently failed (no such method on that route), so
                // the task never got the cookbook_event_uid marker and the
                // server-side delete-cascade had nothing to follow when the
                // user later deleted the task.
                await fetch(`/api/tasks/${encodeURIComponent(data.id)}`, {
                  method: "PUT", credentials: "same-origin",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ prompt: updatedPrompt }),
                });
              } catch (_) {}
            }
          } catch (_) {}
        }
        form.remove();
        const newTaskId = data.id || data.task_id || "";
        toast(`Created task: Serve: ${fullName}`, {
          leadingIcon: "check",
          action: "Open",
          duration: 5000,
          onAction: () => openTaskInTasksTab(newTaskId),
        });
      } catch (e) {
        fail(String(e));
        saveBtn.disabled = false;
        saveBtn.textContent = "Save schedule";
        toast(`Schedule save failed: ${e}`);
      }
    }));
  }

  document.addEventListener("click", _safe((e) => {
    const arrow = e.target.closest && e.target.closest(".hwfit-serve-schedule-arrow");
    if (!arrow) return;
    e.preventDefault();
    e.stopPropagation();
    openForm(arrow);
  }));
})(); } catch (e) { try { console.warn("[cookbookSchedule] top-level error:", e); } catch (_) {} }
