// Client side of the scoped "photo_editor" agent path.
//
// The editor Quick Edit popup sends {prompt, session, state, screenshot} to the
// app's editor-agent endpoint. That agent turn is offered ONLY the editor tool
// family (plan_edit / run_editor_command / get_editor_screenshot / list_layers)
// plus ask_user; the model's run_editor_command calls are executed in-process by
// the owner-scoped bridge (/api/editor/bridge/*), which delivers each command to
// this browser subscriber. We stream those executed steps into the popup and
// fold the whole run into a single undo group.
//
// The endpoint is HARNESS-owned; if it is not present yet (404/405) `run()`
// resolves {unavailable:true} so the caller can fall back to the local planner.
//
// Payload: { prompt, session, editor_session, state, screenshot }
//   - session / editor_session : the editor bridge session id (scopes commands)
//   - state                   : document.info + layers.list (+ imageId/name)
//   - screenshot              : data:image/png;base64,... of the canvas
// Response: { ok?, reply?, tools_offered?[], steps?[], trace?[] } (any subset).

export const EDITOR_AGENT_VERSION = '1.0';
export const DEFAULT_AGENT_ENDPOINT = '/api/editor/agent';

import { formatCropRect, isGenericIntent } from './nl-edit.js';

export function createEditorAgent({
  control,
  endpoint = DEFAULT_AGENT_ENDPOINT,
  fetchImpl = null,
  getSession = null,
  getModel = null,
  getAgentModel = null,
  onStep = null,
} = {}) {
  if (!control || typeof control.invoke !== 'function') {
    throw new Error('createEditorAgent requires a control with invoke()');
  }
  const doFetch = fetchImpl || (typeof fetch !== 'undefined' ? fetch : null);
  let activeStep = null;
  let running = false;
  let executed = null;

  // Called by the editor bridge for every queued command it executes. While an
  // agent run is in flight this streams the step into the popup.
  function reportStep(payload, cmd) {
    const op = (cmd && cmd.op) || (payload && payload.op) || '';
    const entry = {
      op,
      ok: !!(payload && payload.ok),
      error: payload && payload.error ? payload.error : null,
      ms: payload && payload.ms != null ? payload.ms : null,
      params: cmd && cmd.params ? JSON.stringify(cmd.params).slice(0, 400) : null,
      screenshot: payload && payload.screenshot ? payload.screenshot : null,
    };
    if (executed) executed.push(entry);
    if (activeStep) { try { activeStep(entry); } catch (_) {} }
    return entry;
  }

  function isRunning() { return running; }

  async function collectState() {
    const state = {};
    try { Object.assign(state, (await control.invoke('document.info')).result || {}); } catch (_) {}
    try {
      const ls = await control.invoke('layers.list');
      if (ls && ls.ok) state.layers = ls.result;
    } catch (_) {}
    return state;
  }

  // Mean absolute pixel delta (0..1) between two data-URL images, computed on a
  // 64px thumbnail. Browser-only; resolves null when it cannot measure.
  async function dataUrlDelta(a, b) {
    try {
      if (typeof document === 'undefined' || !a || !b) return null;
      const load = (url) => new Promise((res, rej) => { const im = new Image(); im.onload = () => res(im); im.onerror = rej; im.src = url; });
      const [ia, ib] = await Promise.all([load(a), load(b)]);
      const w = 64, h = 64;
      const ca = document.createElement('canvas'); ca.width = w; ca.height = h;
      const cb = document.createElement('canvas'); cb.width = w; cb.height = h;
      ca.getContext('2d').drawImage(ia, 0, 0, w, h);
      cb.getContext('2d').drawImage(ib, 0, 0, w, h);
      const da = ca.getContext('2d').getImageData(0, 0, w, h).data;
      const db = cb.getContext('2d').getImageData(0, 0, w, h).data;
      let sum = 0;
      for (let i = 0; i < da.length; i += 4) sum += Math.abs(da[i] - db[i]) + Math.abs(da[i + 1] - db[i + 1]) + Math.abs(da[i + 2] - db[i + 2]);
      return sum / ((da.length / 4) * 3 * 255);
    } catch (_) { return null; }
  }

  // Fraction of near-white (clipped-highlight) pixels in a data-URL image, on a
  // 96px thumbnail. Browser-only; resolves null when it cannot measure.
  async function dataUrlClipFraction(url) {
    try {
      if (typeof document === 'undefined' || !url) return null;
      const load = (u) => new Promise((res, rej) => { const im = new Image(); im.onload = () => res(im); im.onerror = rej; im.src = u; });
      const im = await load(url);
      const w = 96, h = 96;
      const c = document.createElement('canvas'); c.width = w; c.height = h;
      c.getContext('2d').drawImage(im, 0, 0, w, h);
      const d = c.getContext('2d').getImageData(0, 0, w, h).data;
      let clipped = 0, total = 0;
      for (let i = 0; i < d.length; i += 4) {
        total++;
        const lum = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
        if (lum > 250) clipped++;
      }
      return total ? clipped / total : null;
    } catch (_) { return null; }
  }

  // Exposure summary of a data-URL image on a 96px thumbnail: fractions of dark
  // (shadow) and bright (highlight) pixels plus the mean luminance. Used to decide
  // whether a whole-image Auto Tone is actually warranted for a portrait.
  async function dataUrlExposure(url) {
    try {
      if (typeof document === 'undefined' || !url) return null;
      const load = (u) => new Promise((res, rej) => { const im = new Image(); im.onload = () => res(im); im.onerror = rej; im.src = u; });
      const im = await load(url);
      const w = 96, h = 96;
      const c = document.createElement('canvas'); c.width = w; c.height = h;
      c.getContext('2d').drawImage(im, 0, 0, w, h);
      const d = c.getContext('2d').getImageData(0, 0, w, h).data;
      let dark = 0, bright = 0, sum = 0, total = 0;
      for (let i = 0; i < d.length; i += 4) {
        total++;
        const lum = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
        sum += lum;
        if (lum < 42) dark++;
        if (lum > 214) bright++;
      }
      return total ? { dark: dark / total, bright: bright / total, mean: sum / total / 255 } : null;
    } catch (_) { return null; }
  }

  async function run(prompt, { onStep: stepCb = null, signal = null } = {}) {
    if (!prompt || !String(prompt).trim()) throw new Error('empty prompt');
    if (!doFetch || !endpoint) return { unavailable: true, ok: false };
    const session = getSession ? getSession() : 'default';
    let modelInfo = null;
    try { modelInfo = getModel ? getModel() : null; } catch (_) { modelInfo = null; }
    let agentInfo = null;
    try { agentInfo = getAgentModel ? getAgentModel() : null; } catch (_) { agentInfo = null; }
    const report = stepCb || onStep || null;
    activeStep = report;
    executed = [];
    running = true;

    // Honest before-image (captured before any client pre-step) for the
    // imperceptible-edit check, then the history mark scoping the undo group.
    let beforeShot = null;
    try { const s = await control.invoke('screenshot'); beforeShot = s && s.result ? s.result.dataUrl : null; } catch (_) {}
    let mark = 0;
    try { mark = (await control.invoke('history.mark')).result; } catch (_) {}

    // Generic intents may get an Auto Tone/Color strengthen below IF the agent's
    // own plan turned out imperceptible (avoids double-processing a dark image).
    const generic = isGenericIntent(prompt);

    // Portrait/teeth intents: the planner sometimes leads with a whole-image
    // Auto Tone, which lightens and whitens the face ("pale/airbrushed"). Suppress
    // photo.autoTone during the run unless the photo is genuinely under- or
    // over-exposed (checked from the before-image histogram).
    const lp = String(prompt).toLowerCase();
    const portraitIntent = /skin|complexion|blemish|acne|pores|smoot|retouch|look (better|good|amazing)|flawless|face|portrait/.test(lp);
    const teethIntent = /teeth|smile/.test(lp);
    let origInvoke = null;
    const restoreInvoke = () => { if (origInvoke) { control.invoke = origInvoke; origInvoke = null; } };
    if (portraitIntent || teethIntent) {
      const exposure = await dataUrlExposure(beforeShot);
      const needsTone = !!(exposure && (exposure.dark > 0.32 || exposure.bright > 0.32));
      if (!needsTone) {
        origInvoke = control.invoke;
        const prev = origInvoke;
        control.invoke = function (op, params) {
          if (op === 'photo.autoTone') return Promise.resolve({ ok: true, op, result: { skipped: true, reason: 'portrait-no-auto-tone' } });
          return prev.call(this, op, params);
        };
      }
    }

    const state = await collectState();
    let screenshot = null;
    try {
      const shot = await control.invoke('screenshot');
      screenshot = shot && shot.result ? shot.result.dataUrl : null;
    } catch (_) {}

    let res = null;
    try {
      res = await doFetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: signal || undefined,
        body: JSON.stringify({
          prompt, session, editor_session: session, state, screenshot,
          model: (modelInfo && modelInfo.model) || '',
          endpoint: (modelInfo && modelInfo.endpoint) || '',
          model_label: (modelInfo && modelInfo.label) || '',
          agent_model: (agentInfo && agentInfo.agent) || '',
          agent_endpoint: (agentInfo && agentInfo.endpoint) || '',
          agent_label: (agentInfo && agentInfo.label) || '',
        }),
      });
    } catch (e) {
      activeStep = null; running = false; restoreInvoke();
      return { unavailable: true, ok: false, error: String(e && e.message ? e.message : e) };
    }

    // No agent endpoint yet -> let the caller fall back to the local planner.
    if (!res || res.status === 404 || res.status === 405) {
      activeStep = null; running = false; restoreInvoke();
      return { unavailable: true, ok: false };
    }

    let data = null;
    try { data = await res.json(); } catch (_) {}

    const trace = executed;

    // How many history entries did the agent's bridge commands add?
    let applied = 0;
    try {
      const nowMark = (await control.invoke('history.mark')).result;
      applied = Math.max(0, Number(nowMark || 0) - Number(mark || 0));
    } catch (_) {}

    const needsProvider = !!(data && (data.needs_provider || data.requires_provider || data.needsProvider));
    const blocked = !res.ok || !data || data.ok === false || needsProvider || !!(data && data.blocked);
    // A clarification: the planner returned no steps but asked a question. Do NOT
    // run any post-guard (especially the imperceptible-edit tone strengthen),
    // which would silently tweak the image for a prompt the model did not understand.
    const planSteps = Array.isArray(data && data.steps) ? data.steps : [];
    const asked = !blocked && planSteps.length === 0 && !!((data && (data.reply || data.message)));
    activeStep = null; running = false;

    // Safety net: if the agent could not fulfil the request, roll back every
    // partial/substitute edit it applied instead of leaving a wrong edit behind.
    if (blocked && applied > 0) {
      for (let i = 0; i < applied; i++) {
        try { await control.invoke('history.undo'); } catch (_) { break; }
      }
    } else if (!blocked && !asked) {
      // Format/platform requests: guarantee the target aspect crop even when the
      // agent's own plan omitted document.crop (e.g. "make it instagram ready").
      // Do NOT crop when the request is to EXTEND the canvas (that adds area,
      // it does not crop the image).
      const extendIntent = /\b(extend|expand)\b|\badd\b[^.]*\b(canvas|border|space|margin)\b|\b(wider|taller|letterbox)\b/i.test(prompt);
      const docOps = (trace || []).map((t) => t && t.op);
      const alreadyFormatted = extendIntent || docOps.includes('document.crop') || docOps.includes('document.resizeCanvas') || docOps.includes('document.extend');
      try {
        const info = (await control.invoke('document.info')).result || {};
        const rect = alreadyFormatted ? null : formatCropRect(prompt, info.width, info.height);
        if (rect && (Math.abs((info.width || 0) - rect.width) > 2 || Math.abs((info.height || 0) - rect.height) > 2)) {
          await control.invoke('document.crop', rect);
        }
      } catch (_) {}
      // Skin/teeth retouch routing guard: rather than let a portrait request
      // degrade to a destructive global blur, apply the specialised masked,
      // texture-preserving retouch ops when the agent did not.
      try {
        const traceOps = (trace || []).map((t) => t && t.op);
        const lp = String(prompt).toLowerCase();
        const skinReq = /skin|complexion|blemish|acne|pores|smoot|retouch|look (better|good|amazing)|flawless/.test(lp);
        // If the agent softened the WHOLE image as a stand-in for skin retouch,
        // revert that destructive global filter before applying the real one.
        if (skinReq && !traceOps.includes('retouch.skin')) {
          const last = trace[trace.length - 1];
          if (last && last.op === 'filters.apply' && last.ok) {
            try {
              await control.invoke('history.undo');
              executed.push({ op: 'history.undo', ok: true, error: null, source: 'guard:global-blur' });
            } catch (_) {}
          }
        }
        if (skinReq && !traceOps.includes('retouch.skin')) {
          await control.invoke('retouch.skin', {});
          executed.push({ op: 'retouch.skin', ok: true, error: null, source: 'guard' });
        }
        // Portrait chain must not brighten: if a global tone pass runs after the
        // skin retouch, revert it so the face keeps its original luminance.
        if (skinReq) {
          const skinIdx = traceOps.lastIndexOf('retouch.skin');
          const autoIdx = traceOps.lastIndexOf('photo.autoTone');
          const lastOp = traceOps[traceOps.length - 1];
          if (skinIdx >= 0 && autoIdx > skinIdx && lastOp === 'photo.autoTone') {
            try {
              await control.invoke('history.undo');
              executed.push({ op: 'history.undo', ok: true, error: null, source: 'guard:portrait-brighten' });
            } catch (_) {}
          }
        }
        if (/teeth|smile/.test(lp) && !traceOps.includes('retouch.teeth')) {
          await control.invoke('retouch.teeth', {});
          executed.push({ op: 'retouch.teeth', ok: true, error: null, source: 'guard' });
        }
      } catch (_) {}
      // Title/text placement guard: place titles RELATIVE to the image layer
      // bounds (top-centre with a margin) at ~10% of the image height, so they
      // sit inside the photo instead of the empty canvas margin.
      try {
        const titleIntent = /\btitle\b|\bcaption\b|\bheading\b|\bwatermark\b|\bwordmark\b|\badd\s+(?:a\s+)?(?:text|label)\b|\bwrite\b/i.test(String(prompt));
        const traceOps2 = (trace || []).map((t) => t && t.op);
        if (titleIntent && traceOps2.includes('text.add')) {
          await control.invoke('text.layout', { anchor: 'top-center', sizePct: 0.13, marginPct: 0.05 });
          executed.push({ op: 'text.layout', ok: true, error: null, source: 'guard' });
        }
      } catch (_) {}
      // Imperceptible-edit guard: when a GENERIC look/brighten request barely
      // changed the image, strengthen it so generic requests are visible but
      // still natural. Never run this for structural/action prompts (add a
      // layer, resize, add a title, align…) — a tone tweak there is wrong.
      try {
        if (beforeShot && generic) {
          const after = (await control.invoke('screenshot')).result;
          const delta = await dataUrlDelta(beforeShot, after && after.dataUrl);
          if (delta != null && delta < 0.02) {
            try { await control.invoke('photo.autoTone', {}); } catch (_) {}
            await control.invoke('adjustments.apply', { type: 'vibrance', params: { vibrance: 22 } });
            await control.invoke('adjustments.apply', { type: 'brightness-contrast', params: { brightness: 5, contrast: 8 } });
          }
        }
      } catch (_) {}
      // Blown-highlight guard: agents sometimes over-lift exposure/shadows and
      // clip large areas to white; step back up to two ops if that happened.
      try {
        if (beforeShot) {
          const beforeClip = await dataUrlClipFraction(beforeShot);
          let after = (await control.invoke('screenshot')).result;
          let afterClip = await dataUrlClipFraction(after && after.dataUrl);
          for (let guard = 0; guard < 2 && beforeClip != null && afterClip != null && (afterClip - beforeClip) > 0.12; guard++) {
            await control.invoke('history.undo');
            executed.push({ op: 'history.undo', ok: true, error: null, source: 'guard:clipped-highlights' });
            after = (await control.invoke('screenshot')).result;
            afterClip = await dataUrlClipFraction(after && after.dataUrl);
          }
        }
      } catch (_) {}
      // Fold every op the agent ran (via the bridge) into one undo group.
      try { await control.invoke('history.collapse', { mark, label: `AI edit: ${String(prompt).slice(0, 60)}` }); } catch (_) {}
    }

    if (blocked || asked) {
      const message = (asked ? (data && (data.reply || data.message)) : null)
        || (data && (data.message || data.reply)) || (needsProvider
        ? 'Removing or adding objects needs an image generation provider. Enable one in Settings > Models.'
        : ((data && data.error) || `Agent error (${res ? res.status : 'no response'})`));
      restoreInvoke();
      return {
        unavailable: false, ok: false, blocked: true, needsProvider, message,
        asked,
        error: (data && data.error) || null,
        reply: (data && (data.reply || data.message)) || '',
        toolsOffered: (data && (data.tools_offered || data.tools)) || null,
        steps: [], trace, session, applied, rolledBack: applied,
      };
    }
    restoreInvoke();
    return {
      unavailable: false,
      ok: true,
      reply: data.reply || data.message || '',
      toolsOffered: data.tools_offered || data.tools || null,
      steps: Array.isArray(data.steps) ? data.steps : [],
      trace,
      session,
      applied,
    };
  }

  return { version: EDITOR_AGENT_VERSION, endpoint, run, reportStep, isRunning };
}

export default createEditorAgent;
