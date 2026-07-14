/**
 * Titan VRAM Scheduler panel — GPU overview, jobs, services, config, LLM proxy.
 */
const SCHED_API = "/api/titan/scheduler";
const HUB_API = "/api/titan/hub";

async function schedFetch(path, opts = {}) {
  const r = await fetch(`${SCHED_API}${path}`, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    credentials: "same-origin",
    ...opts,
  });
  const text = await r.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    data = { raw: text };
  }
  if (!r.ok) {
    const msg = data.detail || data.error || text || r.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

async function hubFetch(path, opts = {}) {
  const r = await fetch(`${HUB_API}${path}`, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    credentials: "same-origin",
    ...opts,
  });
  const text = await r.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    data = { raw: text };
  }
  if (!r.ok) {
    const msg = data.detail || data.error || text || r.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

function el(tag, cls, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  return e;
}

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

const SCHED_ICON =
  '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="2" x2="9" y2="4"/><line x1="15" y1="2" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="22"/><line x1="15" y1="20" x2="15" y2="22"/><line x1="20" y1="9" x2="22" y2="9"/><line x1="20" y1="14" x2="22" y2="14"/><line x1="2" y1="9" x2="4" y2="9"/><line x1="2" y1="14" x2="4" y2="14"/></svg>';

const SCHED_TABS = [
  { id: "overview", label: "Přehled" },
  { id: "jobs", label: "External jobs" },
  { id: "services", label: "Služby" },
  { id: "tts", label: "TTS" },
  { id: "config", label: "Konfigurace" },
  { id: "llm", label: "LLM proxy" },
];

let _schedDragWired = false;
let _pollTimer = null;

function wireSchedDrag(modal) {
  if (_schedDragWired) return;
  const content = modal.querySelector(".modal-content");
  const header = modal.querySelector(".modal-header");
  if (!content || !header) return;
  import("/static/js/windowDrag.js")
    .then((m) => {
      const fn = m.makeWindowDraggable || (m.default && m.default.makeWindowDraggable);
      if (typeof fn !== "function") return;
      fn(modal, {
        content,
        header,
        skipSelector: "button, input, select, label, textarea",
        enableDock: true,
        enableLeftDock: true,
      });
      _schedDragWired = true;
    })
    .catch(() => {});
}

function injectSchedulerModal() {
  if (document.getElementById("scheduler-panel-modal")) return;
  const modal = el("div", "modal hidden");
  modal.id = "scheduler-panel-modal";
  const tabsHtml = SCHED_TABS.map(
    (t, i) =>
      `<button type="button" class="memory-tab${i === 0 ? " active" : ""}" data-sched-tab="${t.id}">${t.label}</button>`,
  ).join("");
  const panelsHtml = SCHED_TABS.map(
    (t, i) =>
      `<div class="memory-tab-panel${i === 0 ? "" : " hidden"}" data-sched-panel="${t.id}" id="sched-panel-${t.id}"></div>`,
  ).join("");
  modal.innerHTML = `
    <div class="modal-content memory-modal-content" role="dialog" aria-label="VRAM Scheduler" style="background:var(--bg)">
      <div class="modal-header">
        <h4>${SCHED_ICON}VRAM Scheduler</h4>
        <button class="close-btn" id="close-scheduler-panel" aria-label="Close">✖</button>
      </div>
      <div class="modal-body memory-modal-body">
        <div class="memory-tabs">${tabsHtml}</div>
        ${panelsHtml}
      </div>
    </div>`;
  document.body.appendChild(modal);
  modal.querySelector("#close-scheduler-panel").onclick = () => closeSchedulerPanel();
  modal.querySelectorAll(".memory-tab").forEach((btn) => {
    btn.onclick = () => {
      modal.querySelectorAll(".memory-tab").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const tab = btn.dataset.schedTab;
      modal.querySelectorAll(".memory-tab-panel").forEach((p) => p.classList.add("hidden"));
      const panel = modal.querySelector(`[data-sched-panel="${tab}"]`);
      if (panel) panel.classList.remove("hidden");
      refreshSchedTab(tab);
    };
  });
  wireSchedDrag(modal);
}

function closeSchedulerPanel() {
  const modal = document.getElementById("scheduler-panel-modal");
  if (modal) modal.classList.add("hidden");
  if (_pollTimer) {
    clearInterval(_pollTimer);
    _pollTimer = null;
  }
}

function openSchedulerPanel(tabId = null) {
  injectSchedulerModal();
  const modal = document.getElementById("scheduler-panel-modal");
  modal.classList.remove("hidden");
  modal.style.removeProperty("display");
  modal.style.zIndex = "12001";
  const tabs = modal.querySelectorAll(".memory-tab");
  const targetTab = tabId || "overview";
  let targetIdx = 0;
  tabs.forEach((b, i) => {
    if (b.dataset.schedTab === targetTab) targetIdx = i;
  });
  tabs.forEach((b, i) => b.classList.toggle("active", i === targetIdx));
  modal.querySelectorAll(".memory-tab-panel").forEach((p, i) => p.classList.toggle("hidden", i !== targetIdx));
  const activeTab = tabs[targetIdx]?.dataset.schedTab || "overview";
  refreshSchedTab(activeTab);
  import("/static/js/modalManager.js")
    .then((Modals) => {
      const bag = Modals.default || Modals;
      if (!bag.isRegistered?.("scheduler-panel-modal")) {
        bag.register("scheduler-panel-modal", {
          openFn: openSchedulerPanel,
          closeFn: closeSchedulerPanel,
          railBtnId: "rail-scheduler",
          sidebarBtnId: "tool-scheduler-btn",
        });
      }
      bag.onOpen?.("scheduler-panel-modal");
    })
    .catch(() => {});
  if (_pollTimer) clearInterval(_pollTimer);
  _pollTimer = setInterval(() => {
    const active = modal.querySelector(".memory-tab.active");
    if (!active || modal.classList.contains("hidden")) return;
    refreshSchedTab(active.dataset.schedTab, true);
  }, 3000);
}

async function refreshSchedTab(tab, quiet = false) {
  const panel = document.getElementById(`sched-panel-${tab}`);
  if (!panel) return;
  if (!quiet) panel.innerHTML = "<p>Loading…</p>";
  try {
    if (tab === "overview") await renderOverview(panel, quiet);
    else if (tab === "jobs") await renderJobs(panel, quiet);
    else if (tab === "services") await renderServices(panel, quiet);
    else if (tab === "tts") await renderTts(panel, quiet);
    else if (tab === "config") await renderConfig(panel, quiet);
    else if (tab === "llm") await renderLlmProxy(panel, quiet);
  } catch (e) {
    panel.innerHTML = `<p style="color:#c0392b;">Error: ${e.message}</p>`;
  }
}

function vramBar(used, total) {
  const u = Number(used) || 0;
  const t = Number(total) || 1;
  const pct = Math.min(100, Math.round((u / t) * 100));
  return `<div style="background:color-mix(in srgb, var(--fg) 12%, transparent);border-radius:4px;height:8px;overflow:hidden;margin:4px 0 8px;">
    <div style="width:${pct}%;height:100%;background:var(--brand-color, var(--red));transition:width 0.3s;"></div>
  </div><p style="font-size:12px;opacity:0.8;margin:0;">${esc(u)} / ${esc(total)} MB (${pct}%)</p>`;
}

async function renderOverview(panel, quiet) {
  const [st, cached] = await Promise.all([
    schedFetch("/status"),
    hubFetch("/cached").catch(() => ({ tts: [] })),
  ]);
  const badge = window.titanSchedulerStatus ? window.titanSchedulerStatus.formatBadge(st) : null;
  const llm = st.llm || {};
  const sd = st.sd || {};
  const state = st.state || {};
  const ttsInstalled = (cached.tts || []).some((m) => m.on_disk || m.exists);
  let ttsRuntime = "—";
  try {
    const ttsStats = await fetch("/api/tts/stats", { credentials: "same-origin" });
    if (ttsStats.ok) {
      const ts = await ttsStats.json();
      ttsRuntime = ts.supertonic_ready ? "ready" : "model present";
    }
  } catch (_) {
    ttsRuntime = ttsInstalled ? "model on disk" : "not installed";
  }
  const html = `
    <div style="display:grid;gap:10px;">
      <p><strong>Stav:</strong> ${esc(badge ? badge.text : state.phase || "?")}</p>
      ${vramBar(st.vram_used_mb, st.gpu_total_mb)}
      <p><strong>LLM</strong> (${esc(llm.profile)}): ${llm.active ? "▶ běží" : "⏹ stop"}</p>
      <p><strong>SD</strong> (${esc(sd.profile)}): ${sd.active ? "▶ běží" : "⏹ stop"}</p>
      <p><strong>TTS</strong> (Supertonic-3, CPU): ${esc(ttsRuntime)}</p>
      <p><strong>Fáze:</strong> ${esc(state.phase || "idle")}${state.busy ? " (busy)" : ""}</p>
      <p><strong>Fronta jobů:</strong> ${esc(state.external_jobs_queued || 0)}</p>
      ${state.last_error ? `<p style="color:#c0392b;"><strong>Chyba:</strong> ${esc(state.last_error)}</p>` : ""}
      <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:8px;">
        <button class="btn" id="sched-ensure-llm">Ensure LLM</button>
        <button class="btn" id="sched-open-tts">TTS záložka</button>
        <button class="btn" id="sched-refresh">Obnovit</button>
      </div>
    </div>`;
  if (!quiet || !panel.querySelector("#sched-refresh")) panel.innerHTML = html;
  else {
    const wrap = panel.firstElementChild;
    if (wrap) wrap.outerHTML = html;
  }
  panel.querySelector("#sched-ensure-llm")?.addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    btn.disabled = true;
    try {
      await schedFetch("/ensure-llm", { method: "POST", body: "{}" });
      await renderOverview(panel);
    } catch (e) {
      alert(e.message);
    } finally {
      btn.disabled = false;
    }
  });
  panel.querySelector("#sched-refresh")?.addEventListener("click", () => renderOverview(panel));
  panel.querySelector("#sched-open-tts")?.addEventListener("click", () => {
    const modal = document.getElementById("scheduler-panel-modal");
    const btn = modal?.querySelector('[data-sched-tab="tts"]');
    if (btn) btn.click();
  });
}

async function renderJobs(panel, quiet) {
  const [jobsRes, pipesRes] = await Promise.all([
    schedFetch("/external/jobs"),
    schedFetch("/pipelines").catch(() => ({ pipelines: {} })),
  ]);
  const jobs = jobsRes.jobs || [];
  const pipelines = pipesRes.pipelines || {};
  const pipeOptions = Object.entries(pipelines)
    .map(([id, spec]) => `<option value="${esc(id)}">${esc(spec.label || id)}</option>`)
    .join("");

  const rows = jobs
    .map((j) => {
      const cmd = Array.isArray(j.command) ? j.command.join(" ") : esc(j.command);
      return `<tr>
        <td><code>${esc(j.id)}</code></td>
        <td>${esc(j.status)}</td>
        <td>${esc(j.name || cmd.slice(0, 40))}</td>
        <td>${j.queue_position != null ? esc(j.queue_position) : "—"}</td>
        <td><button class="btn sched-job-detail" data-id="${esc(j.id)}">Log</button></td>
      </tr>`;
    })
    .join("");

  const html = `
    <div style="display:grid;gap:12px;">
      <form id="sched-submit-job" style="display:grid;gap:8px;padding:10px;border:1px solid var(--border);border-radius:8px;">
        <strong>Nový job</strong>
        <label>Pipeline
          <select id="sched-pipeline" name="pipeline">${pipeOptions || '<option value="">—</option>'}</select>
        </label>
        <label>Cesta (path)
          <input id="sched-path" name="path" value="." style="width:100%">
        </label>
        <label class="titan-checkbox-row">
          <span>--update (graphify)</span>
          <input type="checkbox" class="titan-native-checkbox" id="sched-update" name="update">
        </label>
        <button class="btn" type="submit">Zařadit do fronty</button>
      </form>
      <div style="display:flex;justify-content:flex-end;"><button class="btn" id="sched-jobs-refresh">Refresh</button></div>
      <table style="width:100%;font-size:12px;border-collapse:collapse;">
        <thead><tr><th>ID</th><th>Status</th><th>Název</th><th>#</th><th></th></tr></thead>
        <tbody>${rows || '<tr><td colspan="5">Žádné joby</td></tr>'}</tbody>
      </table>
      <pre id="sched-job-log" style="max-height:200px;overflow:auto;font-size:11px;background:var(--panel);padding:8px;display:none;"></pre>
    </div>`;

  if (!quiet || !panel.querySelector("#sched-submit-job")) panel.innerHTML = html;
  else {
    const tbody = panel.querySelector("tbody");
    if (tbody) tbody.innerHTML = rows || '<tr><td colspan="5">Žádné joby</td></tr>';
  }

  const form = panel.querySelector("#sched-submit-job");
  if (form && !form._wired) {
    form._wired = true;
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const pipeline = panel.querySelector("#sched-pipeline")?.value;
      if (!pipeline) {
        alert("Vyber pipeline");
        return;
      }
      const body = {
        pipeline,
        path: panel.querySelector("#sched-path")?.value || ".",
        update: !!panel.querySelector("#sched-update")?.checked,
      };
      try {
        await schedFetch("/external/jobs", { method: "POST", body: JSON.stringify(body) });
        await renderJobs(panel);
      } catch (err) {
        alert(err.message);
      }
    });
  }
  panel.querySelector("#sched-jobs-refresh")?.addEventListener("click", () => renderJobs(panel));
  panel.querySelectorAll(".sched-job-detail").forEach((btn) => {
    btn.onclick = async () => {
      const logEl = panel.querySelector("#sched-job-log");
      if (!logEl) return;
      logEl.style.display = "block";
      logEl.textContent = "Loading…";
      try {
        const detail = await schedFetch(`/external/jobs/${btn.dataset.id}`);
        logEl.textContent = detail.log_tail || detail.error || JSON.stringify(detail, null, 2);
      } catch (err) {
        logEl.textContent = err.message;
      }
    };
  });
}

async function renderServices(panel, quiet) {
  const [st, cfg] = await Promise.all([schedFetch("/status"), hubFetch("/config").catch(() => ({}))]);
  const llm = st.llm || {};
  const sd = st.sd || {};
  const llmProfiles = ((cfg.launch_profiles && cfg.launch_profiles.llm) || []).filter((p) => p.unit === "llama-qwen");
  const sdProfiles = (cfg.launch_profiles && cfg.launch_profiles.sd) || [];
  const llmButtons = llmProfiles
    .map((p) => `<button class="btn sched-llm-load" data-id="${esc(p.id)}">${esc(p.display_name || p.id)}</button>`)
    .join(" ");
  const sdButtons = sdProfiles
    .map((p) => `<button class="btn sched-sd-load" data-id="${esc(p.id)}">SD ${esc(p.display_name || p.id)}</button>`)
    .join(" ");
  const html = `
    <div style="display:grid;gap:12px;">
      <section>
        <h4 style="margin:0 0 8px;">LLM (llama-qwen)</h4>
        <p>${llm.active ? "▶ běží" : "⏹ stop"} — profil <strong>${esc(llm.profile)}</strong></p>
        <div style="display:flex;flex-wrap:wrap;gap:8px;">
          <button class="btn" id="sched-load-general">Load general</button>
          ${llmButtons}
          <button class="btn" id="sched-unload-llm">Stop LLM</button>
        </div>
      </section>
      <section>
        <h4 style="margin:0 0 8px;">SD (diffusion)</h4>
        <p>${sd.active ? "▶ běží" : "⏹ stop"} — profil <strong>${esc(sd.profile)}</strong></p>
        <div style="display:flex;flex-wrap:wrap;gap:8px;">${sdButtons}</div>
        <button class="btn" id="sched-unload-sd" style="margin-top:8px;">Stop SD + restore LLM</button>
      </section>
      <button class="btn" id="sched-services-refresh">Obnovit</button>
    </div>`;
  if (!quiet || !panel.querySelector("#sched-load-general")) panel.innerHTML = html;
  const load = (body) => hubFetch("/load", { method: "POST", body: JSON.stringify(body) }).then(() => renderServices(panel));
  panel.querySelector("#sched-load-general")?.addEventListener("click", () => load({ role: "general" }));
  panel.querySelectorAll(".sched-llm-load").forEach((b) => {
    b.addEventListener("click", () => load({ profile_id: b.dataset.id, kind: "llm" }));
  });
  panel.querySelector("#sched-unload-llm")?.addEventListener("click", () =>
    hubFetch("/unload", { method: "POST", body: JSON.stringify({ unit: "llama-qwen" }) }).then(() => renderServices(panel)),
  );
  panel.querySelectorAll(".sched-sd-load").forEach((b) => {
    b.addEventListener("click", () => load({ profile_id: b.dataset.id, kind: "sd" }));
  });
  panel.querySelector("#sched-unload-sd")?.addEventListener("click", () =>
    hubFetch("/image-studio/release", { method: "POST", body: "{}" }).catch(() =>
      hubFetch("/unload", { method: "POST", body: JSON.stringify({ unit: "diffusion" }) }),
    ).then(() => renderServices(panel)),
  );
  panel.querySelector("#sched-services-refresh")?.addEventListener("click", () => renderServices(panel));
}

async function pollHubJob(jobId, el) {
  for (let i = 0; i < 120; i++) {
    await new Promise((r) => setTimeout(r, 3000));
    try {
      const j = await hubFetch(`/downloads/${jobId}`);
      if (el) el.textContent = `Job ${jobId}: ${j.status}${j.error ? " — " + j.error : ""}`;
      if (j.status === "completed" || j.status === "failed") break;
    } catch {
      break;
    }
  }
}

function mergeTtsPresets(presets, installed) {
  const byId = new Map((installed || []).map((m) => [m.id, m]));
  return (presets || []).map((p) => ({ ...p, ...(byId.get(p.id) || {}) }));
}

async function renderTts(panel, quiet) {
  const [cached, cfg] = await Promise.all([
    hubFetch("/cached").catch(() => ({ tts: [] })),
    hubFetch("/config").catch(() => ({})),
  ]);
  const presets = mergeTtsPresets((cfg.models && cfg.models.tts) || [], cached.tts || []);
  let stats = null;
  try {
    const r = await fetch("/api/tts/stats", { credentials: "same-origin" });
    if (r.ok) stats = await r.json();
  } catch (_) {
    stats = null;
  }

  let html = `<h4 style="margin:0 0 8px;">TTS modely (CPU)</h4>
    <p style="font-size:12px;opacity:0.8;margin:0 0 10px;">Fugassa GM = Supertonic-3. Piper lze stáhnout pro testy (zatím bez gameplay API).</p>
    <p><strong>Supertonic runtime:</strong> ${stats?.supertonic_ready ? "✓ ready" : stats ? "model present (sherpa-onnx?)" : "Titan API nedostupné"}</p>`;
  if (stats?.supertonic_model_path) {
    html += `<p style="font-size:12px;"><code>${esc(stats.supertonic_model_path)}</code></p>`;
  }
  html += `<table style="width:100%;font-size:12px;border-collapse:collapse;margin:10px 0;">
    <thead><tr>
      <th style="text-align:left;padding:4px 6px;">Model</th>
      <th style="text-align:left;padding:4px 6px;">Engine</th>
      <th style="text-align:left;padding:4px 6px;">Stav</th>
      <th style="text-align:left;padding:4px 6px;"></th>
    </tr></thead><tbody>`;
  for (const p of presets) {
    const ok = p.on_disk || p.exists;
    const size = p.size_mb ? ` (~${esc(p.size_mb)} MB)` : "";
    html += `<tr>
      <td style="padding:4px 6px;vertical-align:top;"><strong>${esc(p.display_name || p.id)}</strong>${size}</td>
      <td style="padding:4px 6px;vertical-align:top;">${esc(p.engine || "supertonic")}</td>
      <td style="padding:4px 6px;vertical-align:top;">${ok ? "✓ installed" : "✗ not installed"}</td>
      <td style="padding:4px 6px;vertical-align:top;">
        <button class="btn sched-tts-dl" data-id="${esc(p.id)}" ${ok ? "disabled" : ""}>Download</button>
        <div class="sched-tts-dl-status" data-id="${esc(p.id)}" style="margin-top:4px;font-size:11px;"></div>
      </td>
    </tr>`;
  }
  if (!presets.length) {
    html += '<tr><td colspan="4">No TTS presets in titan-models.yaml.</td></tr>';
  }
  html += `</tbody></table>
    <details style="margin:8px 0 12px;font-size:12px;">
      <summary>Vlastní archive URL</summary>
      <div style="display:grid;gap:6px;margin-top:8px;max-width:520px;">
        <input id="sched-tts-custom-url" placeholder="https://…/vits-piper-….tar.bz2" style="width:100%">
        <div style="display:flex;gap:6px;flex-wrap:wrap;">
          <input id="sched-tts-custom-dir" placeholder="archive_dir" style="flex:1;min-width:200px;">
          <select id="sched-tts-custom-engine"><option value="piper">piper</option><option value="supertonic">supertonic</option></select>
          <button class="btn" id="sched-tts-custom-go">Download</button>
        </div>
        <div id="sched-tts-custom-status" style="font-size:11px;"></div>
      </div>
    </details>
    <div style="margin-top:14px;display:grid;gap:8px;max-width:420px;">
      <strong style="font-size:12px;">Test Supertonic (Fugassa)</strong>
      <label>Jazyk
        <select id="sched-tts-lang"><option value="cs">cs</option><option value="en">en</option><option value="uk">uk</option></select>
      </label>
      <label>Hlas (speaker_id 0–9)
        <input id="sched-tts-speaker" type="number" min="0" max="9" value="0" style="width:100%">
      </label>
      <label>Test text
        <input id="sched-tts-text" value="Krátký test hlasu pro Fugassa." style="width:100%">
      </label>
      <button class="btn" id="sched-tts-test">Syntéza + přehrát</button>
      <audio id="sched-tts-audio" controls style="width:100%;display:none;"></audio>
      <p id="sched-tts-msg" style="font-size:12px;margin:0;"></p>
    </div>
    <button class="btn" id="sched-tts-refresh" style="margin-top:12px;">Obnovit</button>`;

  if (!quiet || !panel.querySelector("#sched-tts-test")) panel.innerHTML = html;

  panel.querySelector("#sched-tts-refresh")?.addEventListener("click", () => renderTts(panel));
  panel.querySelectorAll(".sched-tts-dl").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const preset = presets.find((p) => p.id === btn.dataset.id);
      if (!preset?.url) return;
      const statusEl = panel.querySelector(`.sched-tts-dl-status[data-id="${preset.id}"]`);
      btn.disabled = true;
      try {
        const job = await hubFetch("/download", {
          method: "POST",
          body: JSON.stringify({
            source: "archive",
            url: preset.url,
            archive_dir: preset.archive_dir,
            engine: preset.engine || undefined,
          }),
        });
        if (statusEl) statusEl.textContent = `Job ${job.id}: ${job.status}`;
        await pollHubJob(job.id, statusEl);
        await renderTts(panel);
      } catch (e) {
        if (statusEl) statusEl.textContent = e.message;
        btn.disabled = false;
      }
    });
  });
  panel.querySelector("#sched-tts-custom-go")?.addEventListener("click", async () => {
    const url = panel.querySelector("#sched-tts-custom-url")?.value.trim();
    const archiveDir = panel.querySelector("#sched-tts-custom-dir")?.value.trim();
    const engine = panel.querySelector("#sched-tts-custom-engine")?.value || "piper";
    const statusEl = panel.querySelector("#sched-tts-custom-status");
    if (!url) return alert("Zadej URL");
    try {
      const job = await hubFetch("/download", {
        method: "POST",
        body: JSON.stringify({
          source: "archive",
          url,
          archive_dir: archiveDir || undefined,
          engine,
        }),
      });
      if (statusEl) statusEl.textContent = `Job ${job.id}: ${job.status}`;
      await pollHubJob(job.id, statusEl);
      await renderTts(panel);
    } catch (e) {
      if (statusEl) statusEl.textContent = e.message;
    }
  });
  panel.querySelector("#sched-tts-test")?.addEventListener("click", async () => {
    const msg = panel.querySelector("#sched-tts-msg");
    const audio = panel.querySelector("#sched-tts-audio");
    const lang = panel.querySelector("#sched-tts-lang")?.value || "cs";
    const speaker = Number(panel.querySelector("#sched-tts-speaker")?.value || 0);
    const text = panel.querySelector("#sched-tts-text")?.value || "Test.";
    if (msg) msg.textContent = "Syntéza…";
    try {
      const r = await fetch("/api/tts/synthesize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          text,
          format: "audio",
          engine: "supertonic",
          lang,
          speaker_id: speaker,
          speed: 1.0,
        }),
      });
      if (!r.ok) {
        const err = await r.text();
        throw new Error(err || r.statusText);
      }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      if (audio) {
        audio.src = url;
        audio.style.display = "block";
        await audio.play();
      }
      if (msg) msg.textContent = "Přehrávám…";
    } catch (e) {
      if (msg) msg.textContent = e.message;
    }
  });
}

async function renderConfig(panel, quiet) {
  const [cfg, hub] = await Promise.all([
    schedFetch("/config"),
    hubFetch("/config").catch(() => ({})),
  ]);
  const llm = cfg.llm || {};
  const llmProfiles = ((hub.launch_profiles && hub.launch_profiles.llm) || []).filter(
    (p) => p.unit === "llama-qwen",
  );
  const profileOpts = llmProfiles
    .map(
      (p) =>
        `<option value="${esc(p.id)}" ${llm.default_profile === p.id ? "selected" : ""}>${esc(p.display_name || p.id)}</option>`,
    )
    .join("");
  const html = `
    <form id="sched-config-form" style="display:grid;gap:10px;max-width:420px;">
      <label>vram_reserve_mb <input type="number" id="cfg-vram-reserve" value="${esc(cfg.vram_reserve_mb)}"></label>
      <label>llm_ready_timeout_sec <input type="number" step="0.1" id="cfg-llm-ready" value="${esc(cfg.llm_ready_timeout_sec)}"></label>
      <label>sd_ready_timeout_sec <input type="number" step="0.1" id="cfg-sd-ready" value="${esc(cfg.sd_ready_timeout_sec)}"></label>
      <label>llm_stop_wait_sec <input type="number" step="0.1" id="cfg-llm-stop" value="${esc(cfg.llm_stop_wait_sec)}"></label>
      <label class="titan-checkbox-row">
        <span>sd_shutdown_after_default</span>
        <input type="checkbox" class="titan-native-checkbox" id="cfg-sd-shutdown" ${cfg.sd_shutdown_after_default ? "checked" : ""}>
      </label>
      <label>llm.default_profile
        <select id="cfg-llm-profile">${profileOpts || `<option value="fast">fast</option>`}</select>
      </label>
      <p style="font-size:12px;opacity:0.75;">Read-only: gpu_total_mb=${esc(cfg.gpu_total_mb)}, llm.unit=${esc(llm.unit)}, sd.unit=${esc((cfg.sd || {}).unit)}</p>
      <button class="btn" type="submit">Uložit config.yaml</button>
      <p id="sched-config-msg" style="font-size:12px;"></p>
    </form>`;
  if (!quiet || !panel.querySelector("#sched-config-form")) panel.innerHTML = html;
  const form = panel.querySelector("#sched-config-form");
  if (form && !form._wired) {
    form._wired = true;
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const next = { ...cfg };
      next.vram_reserve_mb = Number(panel.querySelector("#cfg-vram-reserve").value);
      next.llm_ready_timeout_sec = Number(panel.querySelector("#cfg-llm-ready").value);
      next.sd_ready_timeout_sec = Number(panel.querySelector("#cfg-sd-ready").value);
      next.llm_stop_wait_sec = Number(panel.querySelector("#cfg-llm-stop").value);
      next.sd_shutdown_after_default = !!panel.querySelector("#cfg-sd-shutdown").checked;
      next.llm = { ...(next.llm || {}), default_profile: panel.querySelector("#cfg-llm-profile").value };
      try {
        await schedFetch("/config", { method: "PUT", body: JSON.stringify(next) });
        panel.querySelector("#sched-config-msg").textContent = "Uloženo. Některé hodnoty se projeví po restartu scheduleru.";
      } catch (err) {
        panel.querySelector("#sched-config-msg").textContent = err.message;
      }
    });
  }
}

async function renderLlmProxy(panel, quiet) {
  let models = [];
  try {
    const data = await schedFetch("/llm/models");
    models = data.data || [];
  } catch (_) {
    models = [];
  }
  const modelOpts = models.map((m) => `<option value="${esc(m.id)}">${esc(m.id)}</option>`).join("");
  const html = `
    <div style="display:grid;gap:12px;">
      <p>OpenAI proxy: <code>/api/titan/scheduler/llm/*</code> → host llama-server</p>
      <label>Model <select id="sched-llm-model">${modelOpts || '<option value="">(ensure LLM first)</option>'}</select></label>
      <label>Test prompt <input id="sched-llm-prompt" value="Reply with exactly: pong" style="width:100%"></label>
      <button class="btn" id="sched-llm-test">Test completion</button>
      <pre id="sched-llm-out" style="max-height:220px;overflow:auto;font-size:11px;background:var(--panel);padding:8px;"></pre>
    </div>`;
  if (!quiet || !panel.querySelector("#sched-llm-test")) panel.innerHTML = html;
  panel.querySelector("#sched-llm-test")?.addEventListener("click", async () => {
    const out = panel.querySelector("#sched-llm-out");
    const model = panel.querySelector("#sched-llm-model")?.value;
    const prompt = panel.querySelector("#sched-llm-prompt")?.value || "ping";
    out.textContent = "Running…";
    try {
      await schedFetch("/ensure-llm", { method: "POST", body: "{}" });
      const res = await schedFetch("/llm/chat", {
        method: "POST",
        body: JSON.stringify({
          model: model || undefined,
          messages: [{ role: "user", content: prompt }],
          max_tokens: 64,
          temperature: 0,
        }),
      });
      const msg = res.choices?.[0]?.message;
      out.textContent = msg?.content || msg?.reasoning_content || JSON.stringify(res, null, 2);
    } catch (err) {
      out.textContent = err.message;
    }
  });
}

function patchChatStreamOpenPanel() {
  const tryPatch = () => {
    import("/static/js/chatStream.js")
      .then((mod) => {
        const bag = mod.default || mod;
        if (!bag.handleUIControl || bag.handleUIControl._titanSchedulerPatched) return;
        const orig = bag.handleUIControl;
        bag.handleUIControl = function (uiData) {
          const ev = uiData.ui_event || uiData;
          if (ev === "open_panel") {
            const p = uiData.panel;
            if (p === "scheduler" || p === "vram" || p === "vram_scheduler") {
              openSchedulerPanel();
              return;
            }
          }
          return orig(uiData);
        };
        bag.handleUIControl._titanSchedulerPatched = true;
      })
      .catch(() => setTimeout(tryPatch, 800));
  };
  tryPatch();
}

function bootSchedulerPanel() {
  injectSchedulerModal();
  patchChatStreamOpenPanel();
  if (window.__titanOpenSchedulerPending) {
    window.__titanOpenSchedulerPending = false;
    openSchedulerPanel();
  }
  if (window.location.pathname === "/scheduler") {
    const hash = (window.location.hash || "").replace(/^#/, "");
    const tab = hash === "tts" ? "tts" : null;
    setTimeout(() => openSchedulerPanel(tab), 300);
  }
}

window.titanSchedulerPanel = { open: openSchedulerPanel, close: closeSchedulerPanel };

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", bootSchedulerPanel);
} else {
  bootSchedulerPanel();
}
