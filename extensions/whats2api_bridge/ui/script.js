
    // Compute the API prefix once. ``window.location.origin`` resolves
    // ``/ext-api/...`` correctly whether the page is served from
    // http://localhost:7011 (direct), via the panel iframe, or proxied
    // — without it, a ``fetch("/ext-api/...")`` fails with "Failed to
    // parse URL" when the document has no base URL.
    const API = (window.location.origin || "") + "/ext-api/whats2api_bridge/admin";
    const $ = (id) => document.getElementById(id);

    // ── Stale-document guard ───────────────────────────────────────────
    // The panel iframe caches aggressively; if the user enabled a new
    // version while the old document was still mounted, this code
    // sees the mismatched version marker and forces a clean reload.
    (function() {
      const expected = "w2a v0.5.0";
      const here = (document.body && document.body.innerHTML) || "";
      if (!here.includes(expected)) {
        // Append a hard reload that fires after paint so the user sees
        // the reason before we yank the iframe contents away.
        const note = document.createElement("div");
        note.style.cssText = "padding:24px;text-align:center;font-family:sans-serif;color:#9aa3b2;background:#0f1115;height:100vh;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:12px;";
        note.innerHTML =
          "<div style='font-size:14px;color:#7ad6a3;'>Whats2API Bridge</div>" +
          "<div style='font-size:12px;'>New version deployed — reloading the panel…</div>";
        document.documentElement.innerHTML = note.outerHTML;
        setTimeout(() => { location.replace(location.href.split("?")[0] + "?v=" + Date.now()); }, 200);
      }
    })();

    function escapeHtml(s) {
      return String(s == null ? "" : s).replace(/[&<>"']/g, c => (
        { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[c]
      ));
    }
    function fmtTs(sec) {
      if (!sec) return "—";
      try { return new Date(sec * 1000).toISOString().replace("T", " ").slice(0, 19) + " UTC"; }
      catch { return String(sec); }
    }
    function showMsg(text, kind) {
      const m = $("msg");
      m.textContent = text || "";
      m.className = "msg" + (kind ? " " + kind : "");
      if (kind === "ok") setTimeout(() => { m.className = "msg"; m.textContent = ""; }, 3000);
    }

    async function api(path, opts) {
      // 8s budget per request — without this a stuck server (or a stale
      // 302 to /login) would leave the "loading…" indicator hanging
      // forever. The error surface still propagates fine.
      const ctrl = new AbortController();
      const tid = setTimeout(() => ctrl.abort(), 8000);
      let res;
      try {
        res = await fetch(API + path, Object.assign(
          { credentials: "same-origin", signal: ctrl.signal },
          opts || {}
        ));
      } catch (e) {
        clearTimeout(tid);
        if (e.name === "AbortError") throw new Error("request timed out (8s)");
        throw e;
      }
      clearTimeout(tid);
      const text = await res.text();
      let data;
      try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text }; }
      if (!res.ok) throw new Error(data.detail || data.error || (res.status + " " + res.statusText));
      return data;
    }

    // Defaults applied to the form BEFORE the API responds, so the
    // user never sees blank fields. After the API responds, real values
    // overwrite these (only if the API returned a non-empty value).
    const DEFAULTS = {
      base_url: "https://api.whats2api.com",
      default_connection_id: "",
      request_timeout_seconds: 30,
      retention_days: 7,
      log_inbound_events: true,
    };
    function _applyDefaults() {
      $("cfg-base_url").value = DEFAULTS.base_url;
      $("cfg-default_connection_id").value = DEFAULTS.default_connection_id;
      $("cfg-request_timeout_seconds").value = String(DEFAULTS.request_timeout_seconds);
      $("cfg-retention_days").value = String(DEFAULTS.retention_days);
      $("cfg-log_inbound_events").checked = DEFAULTS.log_inbound_events;
    }

    // ── Config ───────────────────────────────────────────────────────
    async function loadConfig() {
      // Pre-fill defaults so the form is never blank — even if /config
      // 403s, redirects to /login, or the panel iframe loses its origin.
      _applyDefaults();
      try {
        const d = await api("/config");
        // Only overwrite a field if the server explicitly returned a
        // value. ``undefined`` keeps the HTML default; empty string
        // would also keep it (operators expect to see the default URL).
        if (d.base_url) $("cfg-base_url").value = d.base_url;
        if (d.default_connection_id !== undefined && d.default_connection_id !== null)
          $("cfg-default_connection_id").value = d.default_connection_id;
        if (d.request_timeout_seconds) $("cfg-request_timeout_seconds").value = d.request_timeout_seconds;
        if (d.retention_days) $("cfg-retention_days").value = d.retention_days;
        $("cfg-log_inbound_events").checked = !!d.log_inbound_events;
        $("cfg-api_token").value = ""; // never display the existing token
        $("cfg-api_token").placeholder = d.api_token_set ? "(unchanged — type to replace)" : "(not set)";

        // The URL token is the operator's own credential — show it in the
        // input so they always see what the dispatcher is currently
        // accepting. The admin endpoint already exposes it via the full
        // ``webhook_url`` so there's no new surface here.
        {
          const urlToken = (d.webhook_url || "").split("/").pop() || "";
          $("cfg-webhook_url_token").value = urlToken;
        }
        $("cfg-webhook_url_token").placeholder = d.webhook_url_token_set
          ? "type to replace, or click Generate random"
          : "(auto-generated on first enable)";

        // Webhook info
        const kv = $("webhook-info");
        const host = window.location.origin;
        const fullUrl = d.webhook_url ? (host + d.webhook_url) : "—";
        kv.innerHTML =
          `<dt>URL pattern</dt><dd>/api/ext/whats2api_bridge/webhook/&lt;token&gt;</dd>` +
          `<dt>Full URL (with token)</dt><dd class="${d.webhook_url ? "ok" : "warn"}">${escapeHtml(fullUrl)}</dd>`;
        $("copy-webhook").dataset.url = fullUrl;
      } catch (e) {
        showMsg("Failed to load config: " + e.message, "err");
      }
    }

    async function saveConfig() {
      const body = {
        base_url: $("cfg-base_url").value.trim(),
        default_connection_id: $("cfg-default_connection_id").value.trim(),
        request_timeout_seconds: parseInt($("cfg-request_timeout_seconds").value, 10),
        retention_days: parseInt($("cfg-retention_days").value, 10),
        log_inbound_events: $("cfg-log_inbound_events").checked,
      };
      const tok = $("cfg-api_token").value;
      if (tok) body.api_token = tok; // only send when user typed something
      // Always send the URL token: empty string in the input means
      // "rotate to a fresh random one". The current value is always
      // visible in the field (no "unchanged" placeholder), so the
      // operator is in full control — re-sending the same value is a
      // no-op (the dispatcher keeps accepting it).
      body.webhook_url_token = $("cfg-webhook_url_token").value;
      $("cfg-status").textContent = "saving…";
      try {
        await api("/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        $("cfg-status").textContent = "saved ✓";
        $("cfg-api_token").value = "";
        showMsg("Configuration saved.", "ok");
        loadConfig();
      } catch (e) {
        $("cfg-status").textContent = "";
        showMsg("Save failed: " + e.message, "err");
      }
    }

    async function rotateWebhookToken() {
      // Generate a fresh random 32-byte URL-safe token client-side and
      // stage it in the field. The actual save hits POST /config which
      // writes to the pinned file AND patches the live dispatcher.
      const buf = new Uint8Array(32);
      crypto.getRandomValues(buf);
      const tok = btoa(String.fromCharCode(...buf))
        .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
      $("cfg-webhook_url_token").value = tok;
      showMsg("Generated a fresh 32-byte token. Click Save to apply.", "ok");
    }

    // ── Webhook copy ─────────────────────────────────────────────────
    function copyWebhook() {
      const url = $("copy-webhook").dataset.url || "";
      if (!url || url === "—") { showMsg("No webhook URL configured yet.", "err"); return; }
      navigator.clipboard?.writeText(url).then(
        () => showMsg("Webhook URL copied to clipboard.", "ok"),
        () => {
          // Fallback: select+copy via temp textarea
          const ta = document.createElement("textarea");
          ta.value = url; document.body.appendChild(ta); ta.select();
          try { document.execCommand("copy"); showMsg("Copied.", "ok"); } catch {}
          document.body.removeChild(ta);
        }
      );
    }

    // ── Data browser ────────────────────────────────────────────────
    const browser = { tab: "messages", q: "", offset: 0, limit: 25 };

    function tabBtn(tab) {
      document.querySelectorAll(".tab-btn").forEach(b => {
        b.classList.toggle("primary", b.dataset.tab === tab);
      });
    }

    async function refreshBrowser() {
      const list = $("browse-list");
      list.textContent = "loading…";
      tabBtn(browser.tab);
      const qp = new URLSearchParams();
      qp.set("limit", browser.limit);
      qp.set("offset", browser.offset);
      if (browser.q) qp.set("q", browser.q);
      try {
        const d = await api("/" + browser.tab + "?" + qp.toString());
        const rows = d[browser.tab] || [];
        list.innerHTML = "";
        $("browse-count").textContent = `${rows.length} of ${d.total} total`;
        $("browse-pager").style.display = "";
        if (browser.offset <= 0) $("browse-prev").disabled = true; else $("browse-prev").disabled = false;
        if (browser.offset + browser.limit >= d.total) $("browse-next").disabled = true; else $("browse-next").disabled = false;
        if (!rows.length) { list.innerHTML = '<div class="hint">No rows.</div>'; return; }
        for (const r of rows) list.appendChild(renderRow(r));
      } catch (e) {
        list.innerHTML = `<div class="msg err">Load failed: ${escapeHtml(e.message)}</div>`;
      }
    }

    function renderRow(r) {
      const div = document.createElement("div");
      div.className = "data-row";
      div.dataset.id = r.id;
      if (browser.tab === "messages") {
        const text = r.text || "";
        div.innerHTML = `
          <div class="meta">
            <span class="ts">${fmtTs(r.ts)}</span>
            <span>${r.direction === "in" ? "↓" : "↑"} ${escapeHtml(r.connection_id || "")}</span>
            <span class="badge-${r.direction === "in" ? "ok" : "warn"}">${escapeHtml(r.direction || "")}</span>
            <span style="color:var(--fg-dim);">${escapeHtml(r.push_name || "")}</span>
            <span style="color:var(--fg-dim);">${escapeHtml(r.chat_jid || "")}</span>
            <span class="grow"></span>
            <button class="edit">Edit text</button>
            <button class="raw">JSON</button>
            <button class="danger del">Delete</button>
          </div>
          <div class="text" data-text="${escapeHtml(text)}">${escapeHtml(text) || '<span class="placeholder">(no text)</span>'}</div>
        `;
      } else if (browser.tab === "events") {
        div.innerHTML = `
          <div class="meta">
            <span class="ts">${fmtTs(r.received_at)}</span>
            <span class="badge-warn">${escapeHtml(r.event_name || "")}</span>
            <span>${escapeHtml(r.connection_id || "—")}</span>
            <span class="grow"></span>
            <button class="raw">JSON</button>
            <button class="danger del">Delete</button>
          </div>
        `;
      } else { // connections
        const status = (r.status || "—");
        const cls = status === "open" ? "ok" : (status === "close" ? "off" : "warn");
        div.innerHTML = `
          <div class="meta">
            <span class="conn-name">${escapeHtml(r.name || r.id)}</span>
            <span class="badge-${cls}">${escapeHtml(status)}</span>
            <span style="color:var(--fg-dim);">${escapeHtml(r.engine || "")}</span>
            <span style="color:var(--fg-dim);">${escapeHtml(r.phone || "")}</span>
            <span class="ts">${fmtTs(r.updated_at)}</span>
            <span class="grow"></span>
            <button class="raw">JSON</button>
            <button class="danger del">Delete</button>
          </div>
        `;
      }
      // Wire actions
      div.querySelector(".del").addEventListener("click", () => deleteRow(r.id, div));
      div.querySelector(".raw").addEventListener("click", () => {
        const pre = div.querySelector("pre");
        if (pre) { pre.remove(); return; }
        const p = document.createElement("pre");
        p.textContent = JSON.stringify(r.raw_json ? safeParse(r.raw_json) || r.raw_json : r, null, 2);
        div.appendChild(p);
      });
      if (browser.tab === "messages") {
        div.querySelector(".edit").addEventListener("click", () => editText(div, r));
      }
      return div;
    }

    function safeParse(s) { try { return JSON.parse(s); } catch { return null; } }

    async function deleteRow(id, rowEl) {
      if (!confirm("Delete this row? (id=" + id + ")")) return;
      try {
        await api("/" + browser.tab + "/" + encodeURIComponent(id), { method: "DELETE" });
        rowEl.remove();
        showMsg("Deleted.", "ok");
      } catch (e) {
        showMsg("Delete failed: " + e.message, "err");
      }
    }

    function editText(rowEl, r) {
      const textEl = rowEl.querySelector(".text");
      const current = textEl.dataset.text || "";
      const ta = document.createElement("textarea");
      ta.value = current;
      ta.style.cssText = "width:100%;min-height:60px;background:var(--code-bg);color:var(--fg);border:1px solid var(--border);border-radius:4px;padding:4px 6px;font-family:var(--mono);font-size:11px;";
      const save = document.createElement("button");
      save.textContent = "Save";
      save.className = "primary";
      const cancel = document.createElement("button");
      cancel.textContent = "Cancel";
      const actions = document.createElement("div");
      actions.style.cssText = "display:flex;gap:4px;margin-top:4px;";
      actions.appendChild(save); actions.appendChild(cancel);
      textEl.replaceWith(ta);
      rowEl.appendChild(actions);
      save.addEventListener("click", async () => {
        try {
          await api("/messages/" + encodeURIComponent(r.id),
            { method: "PATCH", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ text: ta.value }) });
          showMsg("Updated.", "ok");
          refreshBrowser();
        } catch (e) {
          showMsg("Save failed: " + e.message, "err");
        }
      });
      cancel.addEventListener("click", () => refreshBrowser());
    }

    // ── Wire up ─────────────────────────────────────────────────────
    $("cfg-save").addEventListener("click", saveConfig);
    $("copy-webhook").addEventListener("click", copyWebhook);
    $("cfg-show-token").addEventListener("click", () => {
      const t = $("cfg-api_token");
      t.type = t.type === "password" ? "text" : "password";
    });
    $("cfg-rotate-url-token").addEventListener("click", rotateWebhookToken);
    document.querySelectorAll(".tab-btn").forEach(b => {
      b.addEventListener("click", () => { browser.tab = b.dataset.tab; browser.offset = 0; refreshBrowser(); });
    });
    $("browse-q").addEventListener("input", () => {
      browser.q = $("browse-q").value;
      browser.offset = 0;
      clearTimeout(window.__bt);
      window.__bt = setTimeout(refreshBrowser, 250);
    });
    $("browse-refresh").addEventListener("click", refreshBrowser);
    $("browse-prev").addEventListener("click", () => { browser.offset = Math.max(0, browser.offset - browser.limit); refreshBrowser(); });
    $("browse-next").addEventListener("click", () => { browser.offset += browser.limit; refreshBrowser(); });

    // ── MCP (single whats2api integration) ───────────────────────
    // The card lives in the HTML — ``loadMcp`` only patches text and
    // class names so there's never an empty placeholder while the API
    // call is in flight. The "Enable MCP" / "Disable MCP" button is
    // the operator's only knob, deliberately visible.
    async function loadMcp() {
      const btn = $("mcp-toggle");
      const pill = $("mcp-pill-enabled");
      const toolsPill = $("mcp-pill-tools");
      const connPill = $("mcp-pill-conn");
      const tokenMeta = $("mcp-meta-token");
      const errRow = $("mcp-meta-error-row");
      const errMeta = $("mcp-meta-error");
      const status = $("mcp-status");
      const fields = $("mcp-fields");

      btn.disabled = true;
      btn.textContent = "Loading…";
      pill.textContent = "loading…";
      pill.className = "intg-pill";

      let d = null;
      let fetchErr = null;
      try {
        d = await api("/mcp");
      } catch (e) {
        fetchErr = e.message || String(e);
      }

      btn.disabled = false;

      if (fetchErr) {
        pill.textContent = "unavailable";
        pill.className = "intg-pill enabled-off";
        btn.textContent = "Enable MCP";
        tokenMeta.textContent = "—";
        toolsPill.style.display = "none";
        connPill.style.display = "none";
        errRow.style.display = "";
        errMeta.textContent = fetchErr;
        status.textContent = "Couldn't reach /admin/mcp — " + fetchErr;
        return;
      }

      const on = !!d.enabled;
      const tokenSet = !!d.token_set;

      pill.textContent = on ? "active" : "disabled";
      pill.className = "intg-pill " + (on ? "enabled-on" : "enabled-off");
      btn.textContent = on ? "Disable MCP" : "Enable MCP";
      btn.classList.toggle("primary", !on);
      btn.classList.toggle("danger", on);

      tokenMeta.textContent = tokenSet
        ? "configured (paste a new one to replace)"
        : "not configured — paste a token below";

      if (d.tool_count > 0) {
        toolsPill.textContent = `${d.tool_count} tools`;
        toolsPill.style.display = "";
      } else {
        toolsPill.style.display = "none";
      }

      if (d.status && d.status !== "not_configured") {
        connPill.textContent = d.status;
        connPill.style.display = "";
        connPill.className = "intg-pill " + (d.status === "connected" ? "enabled-on" : "enabled-off");
      } else {
        connPill.style.display = "none";
      }

      if (d.error) {
        errRow.style.display = "";
        errMeta.textContent = d.error;
      } else {
        errRow.style.display = "none";
      }

      const placeholder = tokenSet
        ? "(saved — type to replace)"
        : "paste the token from your whats2api dashboard";
      $("mcp-token-input").placeholder = placeholder;

      // Gate the Enable button until there's actually a token to enable.
      if (!on && !tokenSet) {
        btn.disabled = true;
        btn.title = "save a token first";
      } else {
        btn.disabled = false;
        btn.title = "";
      }

      status.textContent = "";
    }

    $("mcp-toggle").addEventListener("click", async () => {
      // Determine the *desired* state from the current button label.
      // Safer than reading internal state — survives race with reload.
      const btn = $("mcp-toggle");
      const wantsEnable = btn.textContent.trim().toLowerCase().startsWith("enable");
      const status = $("mcp-status");
      status.textContent = wantsEnable ? "enabling…" : "disabling…";
      btn.disabled = true;
      try {
        const r = await api("/mcp", {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: wantsEnable }),
        });
        if (r && r.ok === false) throw new Error(r.error || "toggle failed");
        status.textContent = wantsEnable ? "enabled ✓" : "disabled ✓";
        loadMcp();
      } catch (e) {
        status.textContent = "";
        showMsg("Failed: " + e.message, "err");
        loadMcp();
      }
    });

    $("mcp-save").addEventListener("click", async () => {
      const token = $("mcp-token-input").value;
      if (!token) {
        showMsg("token is required", "err");
        $("mcp-token-input").focus();
        return;
      }
      const status = $("mcp-status");
      status.textContent = "saving token…";
      try {
        const r = await api("/mcp", {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token }),
        });
        if (r && r.ok === false) throw new Error(r.error || "save failed");
        status.textContent = "token saved ✓";
        showMsg("token saved", "ok");
        $("mcp-token-input").value = "";
        loadMcp();
      } catch (e) {
        status.textContent = "";
        showMsg("Save failed: " + e.message, "err");
      }
    });

    loadConfig();
    refreshBrowser();
    loadMcp();
    loadAnalyzer();
    loadAnalyzerRuns();

    // ── Conversation Analyzer ──────────────────────────────────────────
    // Reads status from GET /analyzer, renders into the section above.
    // PATCH /analyzer persists form values + triggers reconcile on the
    // server side (which re-evaluates the ScheduledTask row in the same
    // call). POST /analyzer/run-now triggers an immediate execution via
    // the Odysseus task scheduler — useful when the operator wants to
    // see if the prompt behaves without waiting for the next tick.
    async function loadAnalyzer() {
      const pill = $("analyzer-pill");
      const lastEl = $("analyzer-last-run");
      const nextEl = $("analyzer-next-run");
      const countEl = $("analyzer-run-count");
      const epEl = $("analyzer-endpoint");
      const errEl = $("analyzer-error");

      pill.textContent = "loading…";
      pill.className = "intg-pill";

      let d = null;
      let fetchErr = null;
      try { d = await api("/analyzer"); }
      catch (e) { fetchErr = e.message || String(e); }

      if (fetchErr) {
        pill.textContent = "unavailable";
        pill.className = "intg-pill enabled-off";
        errEl.textContent = "Couldn't reach /analyzer — " + fetchErr;
        errEl.style.display = "";
        return;
      }

      const s = d.settings || {};
      const t = d.task;
      // Cache the server-provided default prompt the first time we see it.
      if (d && typeof d.default_prompt === "string" && d.default_prompt) {
        window.__w2a_default_prompt = d.default_prompt;
      }
      const on = !!s.enabled && !!t && t.status === "active";
      pill.textContent = on
        ? "running every " + (s.interval_minutes || "?") + " min"
        : (s.enabled ? "paused" : "disabled");
      pill.className = "intg-pill " + (on ? "enabled-on" : "enabled-off");

      lastEl.textContent = d.last_run || "—";
      if (!d.last_run) lastEl.classList.add("placeholder");
      else lastEl.classList.remove("placeholder");
      nextEl.textContent = d.next_run || "—";
      if (!d.next_run) nextEl.classList.add("placeholder");
      else nextEl.classList.remove("placeholder");
      countEl.textContent = (d.run_count || 0).toString();
      countEl.classList.remove("placeholder");

      const resolved = d.resolved || ["", ""];
      const ep = t && t.endpoint_url ? t.endpoint_url : (resolved[0] || "—");
      const md = t && t.model ? t.model : (resolved[1] || "—");
      epEl.textContent = `${ep} · ${md}`;
      epEl.classList.remove("placeholder");

      if (d.ok === false) {
        errEl.textContent = d.error || "Analyzer could not be reconciled.";
        errEl.style.display = "";
      } else {
        errEl.style.display = "none";
        errEl.textContent = "";
      }

      // Patch the form. Only fields the user has not manually changed
      // are overwritten — prevents the form from clobbering a half-typed
      // input on a slow refresh.
      function patchIfUntouched(id, value) {
        const el = $(id);
        if (!el || el === document.activeElement) return;
        if (document.body.dataset["az_touch_" + id] === "1") return;
        el.value = value == null ? "" : value;
      }
      const cb = $("analyzer-enabled");
      if (cb && cb !== document.activeElement && document.body.dataset.az_touch_analyzer_enabled !== "1") {
        cb.checked = !!s.enabled;
      }
      patchIfUntouched("analyzer-interval", s.interval_minutes);
      patchIfUntouched("analyzer-lookback",  s.lookback_hours);
      patchIfUntouched("analyzer-min-msg",   s.min_messages);
      patchIfUntouched("analyzer-model",     s.model);
      patchIfUntouched("analyzer-prompt",    s.prompt);
    }

    ["analyzer-enabled", "analyzer-interval", "analyzer-lookback",
     "analyzer-min-msg", "analyzer-model", "analyzer-prompt"].forEach(id => {
      const el = $(id);
      if (el) el.addEventListener("focus", () => {
        document.body.dataset["az_touch_" + id] = "1";
      });
    });

    // Cache the server-provided default prompt so the "Reset" button can
    // restore it without a round trip. Kept on `window` to survive the
    // page-reload cycle (cached for the lifetime of the document).
    window.__w2a_default_prompt = window.__w2a_default_prompt || null;

    const resetBtn = $("analyzer-reset-prompt");
    if (resetBtn) {
      resetBtn.addEventListener("click", () => {
        const ta = $("analyzer-prompt");
        const dflt = window.__w2a_default_prompt || (d && d.default_prompt) || "";
        if (!dflt) {
          showMsg("Default prompt not yet loaded — refresh once", "err");
          return;
        }
        ta.value = dflt;
        document.body.dataset.az_touch_analyzer_prompt = "1";
        showMsg("Default prompt restored (click Save to persist)", "ok");
      });
    }

    $("analyzer-save").addEventListener("click", async () => {
      const status = $("analyzer-status");
      const btn = $("analyzer-save");
      btn.disabled = true;
      status.textContent = "saving…";
      const body = {
        enabled: $("analyzer-enabled").checked,
        interval_minutes: parseInt($("analyzer-interval").value, 10),
        lookback_hours:   parseInt($("analyzer-lookback").value, 10),
        min_messages:     parseInt($("analyzer-min-msg").value, 10),
        model:            $("analyzer-model").value,
        prompt:           $("analyzer-prompt").value,
      };
      try {
        const r = await api("/analyzer", {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (r && r.ok === false) throw new Error(r.error || "save failed");
        status.textContent = r.ok ? `saved (${r.applied}) ✓` : "saved";
        showMsg("Analyzer saved", "ok");
        loadAnalyzer();
      } catch (e) {
        status.textContent = "";
        showMsg("Save failed: " + e.message, "err");
      } finally {
        btn.disabled = false;
      }
    });

    $("analyzer-run-now").addEventListener("click", async () => {
      const status = $("analyzer-status");
      const btn = $("analyzer-run-now");
      btn.disabled = true;
      status.textContent = "running…";
      try {
        const r = await api("/analyzer/run-now", { method: "POST" });
        if (r && r.ok === false) throw new Error(r.error || "trigger failed");
        status.textContent = "run triggered ✓";
        showMsg("Analyzer run triggered", "ok");
        setTimeout(loadAnalyzer, 1500);
        setTimeout(loadAnalyzerRuns, 1200);
      } catch (e) {
        status.textContent = "";
        showMsg("Run failed: " + e.message, "err");
      } finally {
        btn.disabled = false;
      }
    });

    // ── Recent runs + per-run detail ────────────────────────────────
    async function loadAnalyzerRuns() {
      const list = $("analyzer-runs-list");
      const detail = $("analyzer-run-detail");
      if (!list) return;
      list.textContent = "loading…";

      let d = null;
      let err = null;
      try {
        d = await api("/analyzer/runs?limit=20");
      } catch (e) {
        err = e.message || String(e);
      }
      if (err) {
        list.innerHTML = `<span style="color:var(--danger);">Couldn't reach /analyzer/runs — ${escapeHtml(err)}</span>`;
        if (detail) detail.style.display = "none";
        return;
      }
      if (!d || d.ok === false) {
        list.innerHTML = `<span style="color:var(--danger);">${escapeHtml(d && d.error || "error")}</span>`;
        return;
      }
      const rows = d.rows || [];
      if (rows.length === 0) {
        list.innerHTML = '<span class="placeholder">no runs yet — click ▶ Run scheduler now above</span>';
        if (detail) detail.style.display = "none";
        return;
      }
      // Stash once so click handlers can resolve session_id without
      // re-fetching.
      window.__w2a_analyzer_runs = { rows, session_id: d.session_id || null };

      const head = `
        <table style="width:100%; border-collapse: collapse; font-size: 11px;">
          <thead>
            <tr style="text-align:left; color: var(--fg-dim); border-bottom: 1px solid var(--border);">
              <th style="padding:4px 6px;">Started</th>
              <th style="padding:4px 6px;">Status</th>
              <th style="padding:4px 6px;">Duration</th>
              <th style="padding:4px 6px;">Tokens</th>
              <th style="padding:4px 6px;">Preview</th>
              <th style="padding:4px 6px;"></th>
            </tr>
          </thead>
          <tbody>
      `;
      const body = rows.map((r) => {
        const started = r.started_at ? r.started_at.replace("T", " ").slice(0, 19) : "—";
        const status = r.status || "—";
        const statusColor =
          status === "success" ? "var(--accent)" :
          status === "error" ? "var(--danger)" :
          status === "running" ? "var(--accent-2)" : "var(--fg-dim)";
        const dur = r.duration_seconds != null ? `${r.duration_seconds}s` : (status === "running" ? "…" : "—");
        const tokens = r.tokens_used != null ? r.tokens_used.toLocaleString() : "—";
        const preview = (r.result_preview || "").replace(/\s+/g, " ").slice(0, 140) || "<i>(no output)</i>";
        return `
          <tr style="border-bottom: 1px solid var(--border);">
            <td style="padding:4px 6px; font-family: var(--mono); color: var(--fg-dim);">${escapeHtml(started)}</td>
            <td style="padding:4px 6px; color: ${statusColor};">${escapeHtml(status)}</td>
            <td style="padding:4px 6px;">${escapeHtml(dur)}</td>
            <td style="padding:4px 6px;">${escapeHtml(tokens)}</td>
            <td style="padding:4px 6px; color: var(--fg-dim); max-width: 460px; overflow:hidden; text-overflow: ellipsis; white-space: nowrap;">${preview}</td>
            <td style="padding:4px 6px; white-space: nowrap;">
              <button data-run-detail="${escapeHtml(r.id)}" style="padding: 2px 8px; font-size: 11px;">Details</button>
            </td>
          </tr>
        `;
      }).join("");
      const tail = "</tbody></table>";
      list.innerHTML = head + body + tail;

      // Click handlers — one per "Details" button; one global for "Open chat".
      list.querySelectorAll("[data-run-detail]").forEach((btn) => {
        btn.addEventListener("click", () => loadAnalyzerRunDetail(btn.dataset.runDetail));
      });
      const chatBtn = $("analyzer-open-chat");
      if (chatBtn) {
        chatBtn.onclick = () => {
          const sid = (window.__w2a_analyzer_runs || {}).session_id;
          if (!sid) {
            showMsg("No chat session yet — run the analyzer at least once", "err");
            return;
          }
          // The Odysseus SPA renders chats at #/chat/<session_id>; navigate
          // by setting location.hash (works regardless of how the SPA is
          // mounted — parent iframe, full-page, etc).
          try {
            window.parent.location.hash = `/chat/${sid}`;
          } catch (_) {
            window.location.hash = `/chat/${sid}`;
          }
        };
      }

      // Open-chat row at the top of the table (only when a session is bound).
      if (d.session_id) {
        const headerRow = list.querySelector("thead tr");
        if (headerRow && !$("analyzer-open-chat")) {
          const th = document.createElement("th");
          th.colSpan = 6;
          th.style.cssText = "padding:6px; border-bottom:1px solid var(--border); text-align:right;";
          th.innerHTML = '<button id="analyzer-open-chat" style="padding:2px 10px;font-size:11px;">Open chat session →</button>';
          headerRow.appendChild(th);
          // Re-bind after injecting.
          th.querySelector("#analyzer-open-chat").onclick = () => {
            try { window.parent.location.hash = `/chat/${d.session_id}`; }
            catch (_) { window.location.hash = `/chat/${d.session_id}`; }
          };
        }
      }
    }

    async function loadAnalyzerRunDetail(runId) {
      const detail = $("analyzer-run-detail");
      if (!detail) return;
      detail.style.display = "block";
      detail.textContent = "loading…";
      let d = null;
      let err = null;
      try {
        d = await api(`/analyzer/runs/${encodeURIComponent(runId)}`);
      } catch (e) {
        err = e.message || String(e);
      }
      if (err) {
        detail.textContent = `error: ${err}`;
        return;
      }
      if (!d || d.ok === false) {
        detail.textContent = `error: ${(d && d.error) || "unknown"}`;
        return;
      }
      const r = d.run;
      const head = [
        `Run:     ${r.id}`,
        `Started: ${r.started_at || "—"}`,
        `Finished:${r.finished_at || "—"}`,
        `Status:  ${r.status}    Duration: ${r.duration_seconds != null ? r.duration_seconds + "s" : "—"}    Tokens: ${r.tokens_used != null ? r.tokens_used : "—"}    Model: ${r.model || "—"}`,
        `Result length: ${r.result_length || 0} chars${r.result_truncated ? " (truncated for display; full result is in chat session)" : ""}`,
        r.error ? `Error: ${r.error}` : "",
        "─".repeat(60),
        "",
      ].filter(Boolean).join("\n");
      detail.textContent = head + (r.result || "(empty)");
    }

    const refreshRunsBtn = $("analyzer-refresh-runs");
    if (refreshRunsBtn) {
      refreshRunsBtn.addEventListener("click", () => loadAnalyzerRuns());
    }
  