// Titan LLM guard: when the general LLM (host :8000) is down, intercept chat
// sends and offer to start it — with profile choice based on free VRAM.
(function () {
  "use strict";

  const HEALTH_URL = "/api/titan/hub/llm-health";
  const LOAD_URL = "/api/titan/hub/load";
  const CHAT_RE = /\/api\/chat_stream(\?|$)/;

  // Fallback when llm-health omits profiles (stale worker / offline).
  const HUB_CONFIG_URL = "/api/titan/hub/config";

  async function profilesFromHub(freeMb) {
    try {
      const r = await origFetch(HUB_CONFIG_URL + "?_=" + Date.now(), {
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!r.ok) return [];
      const hub = await r.json();
      const general = (hub.roles && hub.roles.general) || {};
      const modelId = general.model_id;
      const model = ((hub.models && hub.models.llm) || []).find((m) => m.id === modelId);
      const vram = (model && model.vram_mb) || {};
      return ((hub.launch_profiles && hub.launch_profiles.llm) || [])
        .filter((p) => p.unit === "llama-qwen" && (!modelId || p.model_id === modelId))
        .map((p) => {
          const required_mb = Number(vram[p.id]) || 12000;
          return {
            id: p.id,
            display_name: p.display_name || p.id,
            required_mb,
            fits: freeMb >= required_mb,
          };
        });
    } catch (_) {
      return [];
    }
  }

  if (window.__titanLlmGuardInstalled) return;
  window.__titanLlmGuardInstalled = true;

  const origFetch = window.fetch.bind(window);
  let _upUntil = 0;
  let _overlay = null;

  function urlOf(input) {
    if (typeof input === "string") return input;
    if (input && typeof input.url === "string") return input.url;
    try { return String(input); } catch (_) { return ""; }
  }

  function methodOf(input, init) {
    if (init && init.method) return String(init.method).toUpperCase();
    if (input && typeof input.method === "string") return input.method.toUpperCase();
    return "GET";
  }

  function sseError(text) {
    const body =
      "event: error\n" +
      "data: " + JSON.stringify({ error: text, status: 503, kind: "llm_down" }) + "\n\n";
    return new Response(body, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    });
  }

  function fmtMb(mb) {
    if (mb == null || mb === "?") return "?";
    const n = Number(mb);
    if (!Number.isFinite(n)) return String(mb);
    return n >= 1000 ? (n / 1000).toFixed(1).replace(/\.0$/, "") + " GB" : n + " MB";
  }

  function annotateProfiles(profiles, freeMb) {
    return profiles.map((p) => ({
      ...p,
      fits: freeMb >= (p.required_mb || 0),
    }));
  }

  function normalizeHealth(h) {
    if (!h || h.up) return h;
    const freeMb = Number(h.free_mb);
    const hasFree = Number.isFinite(freeMb);

    let profiles = Array.isArray(h.profiles) ? h.profiles.slice() : [];
    if (profiles.length && hasFree) {
      profiles = profiles.map((p) =>
        typeof p.fits === "boolean" ? p : { ...p, fits: freeMb >= (p.required_mb || 0) },
      );
    }

    let loadable = Array.isArray(h.loadable_profiles) ? h.loadable_profiles.slice() : [];
    if (!loadable.length && profiles.length) {
      loadable = profiles.filter((p) => p.fits);
    }

    let reason = h.reason || "";
    if (
      loadable.length &&
      (!reason || /not enough to start safely|needs \d+ MB/i.test(reason))
    ) {
      const saved = h.configured_profile_id || "fast";
      const savedFits = profiles.some((p) => p.id === saved && p.fits);
      reason = savedFits
        ? "Volno " + freeMb + " MB VRAM — uložený profil „" + saved + "“ se vejde."
        : "Volno " + freeMb + " MB VRAM — uložený profil „" + saved + "“ se nevejde, vyberte menší profil níže.";
    }

    return {
      ...h,
      profiles,
      loadable_profiles: loadable,
      can_load: loadable.length > 0,
      reason,
      configured_profile_id: h.configured_profile_id || "fast",
      _needsHubProfiles: !profiles.length && hasFree,
    };
  }

  async function normalizeHealthAsync(h) {
    const base = normalizeHealth(h);
    if (!base || base.up || !base._needsHubProfiles) return base;
    const freeMb = Number(base.free_mb);
    const profiles = annotateProfiles(await profilesFromHub(freeMb), freeMb);
    const loadable = profiles.filter((p) => p.fits);
    delete base._needsHubProfiles;
    return {
      ...base,
      profiles,
      loadable_profiles: loadable,
      can_load: loadable.length > 0,
    };
  }

  async function currentModel() {
    try {
      const m = await import("/static/js/sessions.js");
      if (m && typeof m.getCurrentModel === "function") {
        return String(m.getCurrentModel() || "").trim();
      }
    } catch (_) {}
    return "";
  }

  function targetsHostLlm(cur, hostModel) {
    if (!cur) return true;
    if (!hostModel) return true;
    if (cur === hostModel) return true;
    return cur.indexOf(hostModel) !== -1 || hostModel.indexOf(cur) !== -1;
  }

  async function health() {
    const r = await origFetch(HEALTH_URL + "?_=" + Date.now(), {
      credentials: "same-origin",
      cache: "no-store",
    });
    return normalizeHealthAsync(await r.json());
  }

  async function pollHealthUp(timeoutMs) {
    const deadline = Date.now() + (timeoutMs || 150000);
    while (Date.now() < deadline) {
      await new Promise((res) => setTimeout(res, 2500));
      try {
        const h = await health();
        if (h && h.up) return true;
      } catch (_) {}
    }
    return false;
  }

  function defaultProfileId(h) {
    const profiles = h.profiles || [];
    const configured = h.configured_profile_id;
    if (configured && profiles.some((p) => p.id === configured && p.fits)) {
      return configured;
    }
    const loadable = profiles.filter((p) => p.fits);
    if (!loadable.length) return null;
    return loadable.reduce((best, p) =>
      (p.required_mb || 0) > (best.required_mb || 0) ? p : best
    ).id;
  }

  function profileRowsHtml(h) {
    const profiles = h.profiles || [];
    if (!profiles.length) {
      return '<div style="opacity:.75;font-size:13px;">Profily se nepodařilo načíst.</div>';
    }
    const selected = defaultProfileId(h);
    let html = '<div data-titan-profiles style="margin:12px 0;display:flex;flex-direction:column;gap:8px;">';
    for (const p of profiles) {
      const fits = !!p.fits;
      const checked = fits && p.id === selected ? " checked" : "";
      const disabled = fits ? "" : " disabled";
      const note = fits
        ? fmtMb(p.required_mb)
        : fmtMb(p.required_mb) + " — nevejde se";
      const opacity = fits ? "1" : ".45";
      html +=
        '<label style="display:flex;align-items:flex-start;gap:8px;cursor:' +
        (fits ? "pointer" : "not-allowed") +
        ";opacity:" + opacity + ';">' +
        '<input type="radio" name="titan-llm-profile" value="' + p.id + '"' +
        checked + disabled + ' style="margin-top:3px;">' +
        "<span><strong>" + (p.display_name || p.id) + "</strong><br>" +
        '<span style="font-size:12px;opacity:.8;">' + note + "</span></span></label>";
    }
    html += "</div>";
    return html;
  }

  function buildModal(h, mode) {
    const overlay = document.createElement("div");
    overlay.style.cssText =
      "position:fixed;inset:0;z-index:100000;display:flex;align-items:center;" +
      "justify-content:center;background:rgba(0,0,0,.5);";
    const card = document.createElement("div");
    card.style.cssText =
      "background:var(--color-surface,var(--bg,#1e1e22));color:var(--color-text,#e8e8ea);" +
      "border:1px solid var(--color-border,#3a3a40);border-radius:12px;padding:20px 22px;" +
      "max-width:480px;width:92%;box-shadow:0 12px 40px rgba(0,0,0,.4);font:14px/1.5 system-ui,sans-serif;";

    const free = fmtMb(h.free_mb);
    const reason = h.reason || "";

    if (mode === "blocked") {
      card.innerHTML =
        '<div style="font-size:16px;font-weight:600;margin-bottom:8px;">Nedostatek VRAM</div>' +
        '<div style="opacity:.85;margin-bottom:8px;">Qwen neběží a žádný profil se teď nevejde.</div>' +
        '<div style="opacity:.8;font-size:13px;margin-bottom:12px;">' + reason + "</div>" +
        profileRowsHtml(h) +
        '<div style="opacity:.7;font-size:12px;margin-bottom:16px;">Volno: ' + free + "</div>" +
        '<div data-titan-actions style="display:flex;gap:10px;justify-content:flex-end;">' +
        '  <button data-titan-cancel style="padding:7px 14px;border-radius:8px;border:1px solid var(--color-border,#3a3a40);background:transparent;color:inherit;cursor:pointer;">Zavřít</button>' +
        "</div>";
    } else {
      const saved = h.configured_profile_id || "fast";
      card.innerHTML =
        '<div style="font-size:16px;font-weight:600;margin-bottom:8px;">Spustit Qwen?</div>' +
        '<div style="opacity:.85;margin-bottom:6px;">Obecný model (Qwen) neběží.</div>' +
        '<div style="opacity:.75;font-size:13px;margin-bottom:4px;">Naposledy uložený profil: <strong>' +
        saved + "</strong> · volno " + free + "</div>" +
        (reason ? '<div style="opacity:.8;font-size:13px;margin-bottom:8px;">' + reason + "</div>" : "") +
        profileRowsHtml(h) +
        '<div data-titan-status style="display:none;opacity:.85;margin:12px 0 0;">Načítám Qwen… obvykle 15–30 s.</div>' +
        '<div data-titan-actions style="display:flex;gap:10px;justify-content:flex-end;margin-top:16px;">' +
        '  <button data-titan-cancel style="padding:7px 14px;border-radius:8px;border:1px solid var(--color-border,#3a3a40);background:transparent;color:inherit;cursor:pointer;">Zrušit</button>' +
        '  <button data-titan-start style="padding:7px 14px;border-radius:8px;border:none;background:var(--color-accent,#4f7cff);color:#fff;cursor:pointer;font-weight:600;">Spustit</button>' +
        "</div>";
    }
    overlay.appendChild(card);
    return overlay;
  }

  function selectedProfileId() {
    if (!_overlay) return null;
    const picked = _overlay.querySelector('input[name="titan-llm-profile"]:checked');
    return picked ? picked.value : null;
  }

  function promptLoad(h) {
    const loadable = (h.loadable_profiles || []).length;
    const mode = loadable > 0 ? "start" : "blocked";
    return new Promise((resolve) => {
      _overlay = buildModal(h, mode);
      const cancel = _overlay.querySelector("[data-titan-cancel]");
      const start = _overlay.querySelector("[data-titan-start]");
      cancel.addEventListener("click", () => {
        closeModal();
        resolve({ ok: false, cancelled: true, blocked: mode === "blocked" });
      });
      if (start) {
        start.addEventListener("click", () => {
          const pid = selectedProfileId();
          if (!pid) return;
          resolve({ ok: true, profile_id: pid });
        });
      }
      document.body.appendChild(_overlay);
    });
  }

  function showLoadingState(profileId) {
    if (!_overlay) return;
    const status = _overlay.querySelector("[data-titan-status]");
    const actions = _overlay.querySelector("[data-titan-actions]");
    if (status) {
      status.style.display = "block";
      status.textContent = "Načítám profil „" + profileId + "“… obvykle 15–30 s.";
    }
    if (actions) actions.style.display = "none";
  }

  function closeModal() {
    if (_overlay && _overlay.parentNode) _overlay.parentNode.removeChild(_overlay);
    _overlay = null;
  }

  function replayInit(init) {
    if (!init) return init;
    const next = Object.assign({}, init);
    delete next.signal;
    return next;
  }

  window.fetch = async function (input, init) {
    const url = urlOf(input);
    if (methodOf(input, init) !== "POST" || !CHAT_RE.test(url)) {
      return origFetch(input, init);
    }

    if (Date.now() < _upUntil) {
      return origFetch(input, init);
    }

    let h;
    try {
      h = await health();
    } catch (_) {
      return origFetch(input, init);
    }

    if (!h || typeof h.up !== "boolean") {
      return origFetch(input, init);
    }
    if (h.up) {
      _upUntil = Date.now() + 10000;
      return origFetch(input, init);
    }

    if (window.titanSchedulerStatus) {
      try {
        const st = await window.titanSchedulerStatus.fetchStatus();
        const phase = window.titanSchedulerStatus.phaseFromStatus(st);
        if (phase === "generating" || phase === "swapping" || phase === "restoring_llm") {
          return origFetch(input, init);
        }
      } catch (_) {}
    }

    const cur = await currentModel();
    if (!targetsHostLlm(cur, h.model)) {
      return origFetch(input, init);
    }

    window.__titanLlmGuardBusy = true;
    try {
      const choice = await promptLoad(h);
      if (!choice.ok) {
        if (choice.blocked) {
          return sseError(h.reason || "Qwen neběží — nedostatek VRAM na jakýkoli profil.");
        }
        return sseError("Qwen neběží — spuštění zrušeno.");
      }

      const profileId = choice.profile_id;
      showLoadingState(profileId);
      try {
        const loadRes = await origFetch(LOAD_URL, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ profile_id: profileId, kind: "llm" }),
        });
        if (!loadRes.ok) {
          const errBody = await loadRes.text().catch(() => "");
          closeModal();
          return sseError("Nepodařilo se spustit Qwen: " + (errBody || loadRes.status));
        }
      } catch (e) {
        closeModal();
        return sseError("Nepodařilo se spustit Qwen: " + ((e && e.message) || e));
      }

      const up = await pollHealthUp(180000);
      closeModal();
      if (!up) {
        return sseError(
          "Qwen se nespustil včas — zkontrolujte Model Hub (záložka VRAM) nebo systemd log."
        );
      }
      _upUntil = Date.now() + 10000;
      return origFetch(input, replayInit(init));
    } finally {
      window.__titanLlmGuardBusy = false;
    }
  };

  console.info("[titan] LLM guard installed (profile picker)");
})();
