/**
 * Titan Model Hub — replaces Cookbook (capture-phase intercept + modal UI).
 */
const API = "/api/titan/hub";
const TITAN_VERSION = "20260626a";

async function hubFetch(path, opts = {}) {
  const r = await fetch(`${API}${path}`, {
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

function mergeTtsPresets(presets, installed) {
  const byId = new Map((installed || []).map((m) => [m.id, m]));
  return (presets || []).map((p) => ({ ...p, ...(byId.get(p.id) || {}) }));
}

function rebrandCookbookLabels() {
  const rail = document.getElementById("rail-cookbook");
  if (rail) rail.title = "Model Hub";
  const label = document.querySelector("#tool-cookbook-btn .grow");
  if (label) label.textContent = "Model Hub";
  const visLabel = document.querySelector('[data-ui-key="tool-cookbook"]')?.closest(".vis-row")?.querySelector(".vis-label");
  if (visLabel) visLabel.textContent = "Model Hub";
  const path = window.location.pathname;
  if (path === "/cookbook" || path === "/model-hub") {
    document.title = "Model Hub — TITAN";
  }
}

function blockCookbookModal() {
  const modal = document.getElementById("cookbook-modal");
  if (!modal) return;
  if (!modal.classList.contains("hidden")) {
    modal.classList.add("hidden");
  }
  modal.style.setProperty("display", "none", "important");
  modal.style.setProperty("visibility", "hidden", "important");
  modal.style.setProperty("pointer-events", "none", "important");
}

// SVG glyphs mirror the Brain tab style (14px, currentColor strokes).
const TAB_ICONS = {
  llm: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:5px"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',
  sd: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:5px"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/></svg>',
  tts: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:5px"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/></svg>',
  api: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:5px"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>',
};
const HUB_ICON =
  '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="2" x2="9" y2="4"/><line x1="15" y1="2" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="22"/><line x1="15" y1="20" x2="15" y2="22"/><line x1="20" y1="9" x2="22" y2="9"/><line x1="20" y1="14" x2="22" y2="14"/><line x1="2" y1="9" x2="4" y2="9"/><line x1="2" y1="14" x2="4" y2="14"/></svg>';

const HUB_TABS = [
  { id: "llm", label: "LLM" },
  { id: "sd", label: "SD" },
  { id: "tts", label: "TTS" },
  { id: "api", label: "API" },
];

let _hubDragWired = false;
function wireHubDrag(modal) {
  if (_hubDragWired) return;
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
        skipSelector: "button, input, select, label",
        enableDock: true,
        enableLeftDock: true,
      });
      _hubDragWired = true;
    })
    .catch(() => {});
}

function injectModal() {
  if (document.getElementById("model-hub-modal")) return;
  const modal = el("div", "modal hidden");
  modal.id = "model-hub-modal";
  const tabsHtml = HUB_TABS.map(
    (t, i) =>
      `<button type="button" class="memory-tab${i === 0 ? " active" : ""}" data-hub-tab="${t.id}">${TAB_ICONS[t.id] || ""}${t.label}</button>`,
  ).join("");
  const panelsHtml = HUB_TABS.map(
    (t, i) =>
      `<div class="memory-tab-panel${i === 0 ? "" : " hidden"}" data-hub-panel="${t.id}" id="hub-panel-${t.id}"></div>`,
  ).join("");
  modal.innerHTML = `
    <div class="modal-content memory-modal-content" role="dialog" aria-label="Model Hub" style="background:var(--bg)">
      <div class="modal-header" style="display:flex;align-items:center;gap:8px;">
        <h4 style="margin:0;">${HUB_ICON}Model Hub</h4>
        <button type="button" class="btn" id="hub-open-scheduler" style="margin-left:auto;font-size:12px;">VRAM Scheduler</button>
        <button class="close-btn" id="close-model-hub" aria-label="Close Model Hub">✖</button>
      </div>
      <div class="modal-body memory-modal-body">
        <div class="memory-tabs">${tabsHtml}</div>
        ${panelsHtml}
      </div>
    </div>`;
  document.body.appendChild(modal);
  modal.querySelector("#close-model-hub").onclick = () => modal.classList.add("hidden");
  const schedBtn = modal.querySelector("#hub-open-scheduler");
  if (schedBtn) {
    schedBtn.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (window.titanSchedulerPanel?.open) window.titanSchedulerPanel.open();
      else window.__titanOpenSchedulerPending = true;
    };
  }
  modal.querySelectorAll(".memory-tab").forEach((btn) => {
    btn.onclick = () => {
      modal.querySelectorAll(".memory-tab").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const tab = btn.dataset.hubTab;
      modal.querySelectorAll(".memory-tab-panel").forEach((p) => p.classList.add("hidden"));
      const panel = modal.querySelector(`[data-hub-panel="${tab}"]`);
      if (panel) panel.classList.remove("hidden");
      refreshTab(tab);
    };
  });
  wireHubDrag(modal);
}

function openHub() {
  blockCookbookModal();
  injectModal();
  const modal = document.getElementById("model-hub-modal");
  modal.classList.remove("hidden");
  modal.style.removeProperty("display");
  modal.style.zIndex = "12000";
  modal.querySelectorAll(".memory-tab").forEach((b, i) => b.classList.toggle("active", i === 0));
  modal.querySelectorAll(".memory-tab-panel").forEach((p, i) => p.classList.toggle("hidden", i !== 0));
  refreshTab("llm");
}

async function refreshTab(tab) {
  const panel = document.getElementById(`hub-panel-${tab}`);
  if (!panel) return;
  panel.innerHTML = "<p>Loading…</p>";
  try {
    if (tab === "llm") await renderLlm(panel);
    else if (tab === "sd") await renderSd(panel);
    else if (tab === "tts") await renderTts(panel);
    else if (tab === "api") await renderApi(panel);
  } catch (e) {
    panel.innerHTML = `<p style="color:#c0392b;">Error: ${e.message}</p>`;
  }
}

async function renderLlm(panel) {
  const cached = await hubFetch("/cached");
  const cfg = await hubFetch("/config");
  const profiles = (cfg.launch_profiles && cfg.launch_profiles.llm) || [];
  let html = "<h3>Downloaded LLMs</h3><ul>";
  for (const m of cached.llm || []) {
    html += `<li><strong>${m.display_name || m.id}</strong> — ${m.on_disk ? "✓ on disk" : "✗ missing"}</li>`;
  }
  html += "</ul><h3>Launch profiles</h3><ul>";
  for (const p of profiles) {
    html += `<li>${p.display_name || p.id} <button class="btn hub-load-prof" data-id="${p.id}">Launch</button></li>`;
  }
  html += `</ul><h3>Download (Hugging Face)</h3>
    <div style="display:flex;gap:6px;flex-wrap:wrap;">
      <input id="hub-hf-repo" placeholder="org/model" style="flex:1;min-width:200px;" />
      <input id="hub-hf-file" placeholder="file.gguf (optional)" style="flex:1;min-width:160px;" />
      <button class="btn" id="hub-hf-go">Download</button>
    </div>
    <div id="hub-dl-status" style="margin-top:8px;font-size:12px;"></div>`;
  panel.innerHTML = html;
  panel.querySelectorAll(".hub-load-prof").forEach((b) => {
    b.onclick = () =>
      hubFetch("/load", { method: "POST", body: JSON.stringify({ profile_id: b.dataset.id, kind: "llm" }) }).then(() =>
        alert("LLM profile launched"),
      );
  });
  panel.querySelector("#hub-hf-go").onclick = async () => {
    const repo = panel.querySelector("#hub-hf-repo").value.trim();
    const inc = panel.querySelector("#hub-hf-file").value.trim();
    if (!repo) return alert("Enter repo");
    const job = await hubFetch("/download", {
      method: "POST",
      body: JSON.stringify({ source: "huggingface", repo_id: repo, include: inc || undefined }),
    });
    panel.querySelector("#hub-dl-status").textContent = `Job ${job.id}: ${job.status}`;
    pollJob(job.id, panel.querySelector("#hub-dl-status"));
  };
}

async function renderSd(panel) {
  const cached = await hubFetch("/cached");
  const cfg = await hubFetch("/config");
  const profiles = (cfg.launch_profiles && cfg.launch_profiles.sd) || [];
  let html = "<h3>SD checkpoints</h3><ul>";
  for (const m of cached.sd || []) {
    const name = m.display_name || m.id || m.path;
    html += `<li>${name} ${m.exists !== false ? "✓" : ""}</li>`;
  }
  html += "</ul><h3>SD profiles</h3><ul>";
  for (const p of profiles) {
    const d = p.chat_defaults || {};
    html += `<li>${p.display_name || p.id} (${d.style || ""}) <button class="btn hub-sd-load" data-id="${p.id}">Launch</button></li>`;
  }
  html += `</ul><h3>Download</h3>
    <p><strong>HF:</strong></p>
    <div style="display:flex;gap:6px;"><input id="hub-sd-hf-repo" placeholder="repo" style="flex:1"/><button class="btn" id="hub-sd-hf-go">HF</button></div>
    <p style="margin-top:8px;"><strong>Civitai URL:</strong></p>
    <div style="display:flex;gap:6px;"><input id="hub-civitai-url" placeholder="https://civitai.com/..." style="flex:1"/><button class="btn" id="hub-civitai-go">Download</button></div>
    <div id="hub-sd-dl" style="margin-top:8px;font-size:12px;"></div>`;
  panel.innerHTML = html;
  panel.querySelectorAll(".hub-sd-load").forEach((b) => {
    b.onclick = () =>
      hubFetch("/load", { method: "POST", body: JSON.stringify({ profile_id: b.dataset.id, kind: "sd" }) }).then(() =>
        alert("SD profile launched"),
      );
  });
  panel.querySelector("#hub-sd-hf-go").onclick = async () => {
    const repo = panel.querySelector("#hub-sd-hf-repo").value.trim();
    if (!repo) return;
    const job = await hubFetch("/download", { method: "POST", body: JSON.stringify({ source: "huggingface", repo_id: repo }) });
    panel.querySelector("#hub-sd-dl").textContent = `Job ${job.id}`;
    pollJob(job.id, panel.querySelector("#hub-sd-dl"));
  };
  panel.querySelector("#hub-civitai-go").onclick = async () => {
    const url = panel.querySelector("#hub-civitai-url").value.trim();
    if (!url) return;
    const job = await hubFetch("/download", { method: "POST", body: JSON.stringify({ source: "civitai", url }) });
    panel.querySelector("#hub-sd-dl").textContent = `Job ${job.id}`;
    pollJob(job.id, panel.querySelector("#hub-sd-dl"));
  };
}

async function renderTts(panel) {
  const [cached, cfg] = await Promise.all([hubFetch("/cached"), hubFetch("/config")]);
  const presets = mergeTtsPresets((cfg.models && cfg.models.tts) || [], cached.tts || []);
  let stats = null;
  try {
    const r = await fetch("/api/tts/stats", { credentials: "same-origin" });
    if (r.ok) stats = await r.json();
  } catch (_) {
    stats = null;
  }

  let html = `<h3>TTS models (CPU)</h3>
    <p class="fugassa-muted" style="font-size:12px;margin:0 0 10px;">
      <strong>Fugassa GM</strong> používá <strong>Supertonic-3</strong>.
      Piper modely lze stáhnout pro testy / budoucí použití (zatím nejsou napojené na gameplay).
    </p>`;
  if (stats) {
    html += `<p style="font-size:12px;margin:0 0 10px;">Runtime Supertonic: ${
      stats.supertonic_ready ? "✓ ready" : "model on disk / sherpa-onnx?"
    }</p>`;
  }
  html += `<table style="width:100%;font-size:12px;border-collapse:collapse;margin-bottom:12px;">
    <thead><tr>
      <th style="text-align:left;padding:4px 6px;">Model</th>
      <th style="text-align:left;padding:4px 6px;">Engine</th>
      <th style="text-align:left;padding:4px 6px;">Jazyky</th>
      <th style="text-align:left;padding:4px 6px;">Stav</th>
      <th style="text-align:left;padding:4px 6px;"></th>
    </tr></thead><tbody>`;
  for (const p of presets) {
    const langs = Array.isArray(p.languages) ? p.languages.join(", ") : "—";
    const ok = p.on_disk || p.exists;
    const size = p.size_mb ? ` (~${esc(p.size_mb)} MB)` : "";
    html += `<tr>
      <td style="padding:4px 6px;vertical-align:top;"><strong>${esc(p.display_name || p.id)}</strong>${size}${
        p.fugassa_default ? ' <span style="opacity:0.7;">(Fugassa)</span>' : ""
      }</td>
      <td style="padding:4px 6px;vertical-align:top;">${esc(p.engine || "supertonic")}</td>
      <td style="padding:4px 6px;vertical-align:top;">${esc(langs)}</td>
      <td style="padding:4px 6px;vertical-align:top;">${ok ? "✓ installed" : "✗ not installed"}</td>
      <td style="padding:4px 6px;vertical-align:top;">
        <button class="btn hub-tts-dl" data-id="${esc(p.id)}" ${ok ? "disabled" : ""}>Download</button>
        <div class="hub-tts-dl-status" data-id="${esc(p.id)}" style="margin-top:4px;font-size:11px;"></div>
      </td>
    </tr>`;
  }
  if (!presets.length) {
    html += '<tr><td colspan="5">No TTS presets in titan-models.yaml.</td></tr>';
  }
  html += `</tbody></table>
    <h4 style="margin:12px 0 6px;">Vlastní archive (sherpa-onnx releases)</h4>
    <div style="display:grid;gap:6px;max-width:560px;">
      <input id="hub-tts-custom-url" placeholder="https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/…tar.bz2" style="width:100%">
      <div style="display:flex;gap:6px;flex-wrap:wrap;">
        <input id="hub-tts-custom-dir" placeholder="archive_dir (např. vits-piper-cs_CZ-jirka-medium-int8)" style="flex:1;min-width:220px;">
        <select id="hub-tts-custom-engine" style="min-width:120px;">
          <option value="piper">piper</option>
          <option value="supertonic">supertonic</option>
        </select>
        <button class="btn" id="hub-tts-custom-go">Download</button>
      </div>
      <div id="hub-tts-custom-status" style="font-size:12px;"></div>
    </div>
    <p style="margin-top:12px;font-size:12px;">Vyžaduje <code>sherpa-onnx</code> (requirements-optional.txt). Test Fugassa: Pause → Audio → Preview.</p>`;
  panel.innerHTML = html;

  panel.querySelectorAll(".hub-tts-dl").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const preset = presets.find((p) => p.id === btn.dataset.id);
      if (!preset || !preset.url) return;
      const statusEl = panel.querySelector(`.hub-tts-dl-status[data-id="${preset.id}"]`);
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
        await pollJob(job.id, statusEl);
        await refreshTab("tts");
      } catch (e) {
        if (statusEl) statusEl.textContent = e.message;
        btn.disabled = false;
      }
    });
  });

  panel.querySelector("#hub-tts-custom-go")?.addEventListener("click", async () => {
    const url = panel.querySelector("#hub-tts-custom-url")?.value.trim();
    const archiveDir = panel.querySelector("#hub-tts-custom-dir")?.value.trim();
    const engine = panel.querySelector("#hub-tts-custom-engine")?.value || "piper";
    const statusEl = panel.querySelector("#hub-tts-custom-status");
    if (!url) return alert("Zadej URL archive");
    if (statusEl) statusEl.textContent = "Starting…";
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
      await pollJob(job.id, statusEl);
      await refreshTab("tts");
    } catch (e) {
      if (statusEl) statusEl.textContent = e.message;
    }
  });
}

async function renderApi(panel) {
  panel.innerHTML = `
    <p>Manage API endpoints in <strong>Admin → Model Endpoints</strong>.</p>
    <p>GPU orchestration and VRAM status live in the <strong>VRAM Scheduler</strong> panel (sidebar or button above).</p>
    <p>Typical URLs:</p>
    <ul>
      <li>LLM host: <code>http://host.docker.internal:8000/v1</code></li>
      <li>SD scheduler: <code>http://host.docker.internal:8150/v1</code></li>
    </ul>
    <button class="btn" id="hub-open-admin">Open Admin</button>
    <button class="btn" id="hub-sync-endpoints" style="margin-left:8px;">Sync endpoints</button>`;
  panel.querySelector("#hub-open-admin").onclick = () => {
    if (typeof window.openAdminPanel === "function") window.openAdminPanel();
    else window.location.href = "/#admin";
  };
  panel.querySelector("#hub-sync-endpoints").onclick = () =>
    hubFetch("/sync-endpoints", { method: "POST", body: "{}" }).then((r) => {
      alert(`Sync OK: LLM ${r.llm_model}`);
    });
}

async function pollJob(jobId, el) {
  for (let i = 0; i < 120; i++) {
    await new Promise((r) => setTimeout(r, 3000));
    try {
      const j = await hubFetch(`/downloads/${jobId}`);
      el.textContent = `Job ${jobId}: ${j.status}${j.error ? " — " + j.error : ""}`;
      if (j.status === "completed" || j.status === "failed") break;
    } catch {
      break;
    }
  }
}

function installCookbookIntercept() {
  if (window.__titanCookbookIntercept) return;
  window.__titanCookbookIntercept = true;
  document.addEventListener(
    "click",
    (e) => {
      const target = e.target && e.target.closest && e.target.closest("#tool-cookbook-btn, #rail-cookbook");
      if (!target) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      openHub();
    },
    true,
  );
}

function watchCookbookModal() {
  const obs = new MutationObserver(() => blockCookbookModal());
  obs.observe(document.documentElement, {
    subtree: true,
    attributes: true,
    attributeFilter: ["class", "style"],
  });
  blockCookbookModal();
}

function patchChatStreamOpenPanel() {
  const tryPatch = () => {
    import("/static/js/chatStream.js")
      .then((mod) => {
        const bag = mod.default || mod;
        if (!bag.handleUIControl || bag.handleUIControl._titanHubPatched) return;
        const orig = bag.handleUIControl;
        bag.handleUIControl = function (uiData) {
          const ev = uiData.ui_event || uiData;
          if (ev === "open_panel") {
            const panel = uiData.panel;
            if (panel === "cookbook" || panel === "model_hub" || panel === "modelhub" || panel === "models") {
              openHub();
              return;
            }
          }
          return orig(uiData);
        };
        bag.handleUIControl._titanHubPatched = true;
      })
      .catch(() => setTimeout(tryPatch, 800));
  };
  tryPatch();
}

function boot() {
  rebrandCookbookLabels();
  installCookbookIntercept();
  injectModal();
  watchCookbookModal();
  patchChatStreamOpenPanel();
  if (window.__titanOpenHubPending) {
    window.__titanOpenHubPending = false;
    openHub();
  }
  if (window.location.pathname === "/cookbook" || window.location.pathname === "/model-hub") {
    setTimeout(openHub, 300);
  }
}

window.titanModelHub = { open: openHub, refresh: refreshTab, version: TITAN_VERSION };

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
