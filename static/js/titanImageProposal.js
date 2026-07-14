/**
 * Titan ImageProposal confirm card (pipeline steps 6–7).
 * Renders Generate / Edit / Cancel; executes via hub API (no LLM).
 */
(function () {
  const API_BASE = window.API_BASE || '';
  const STYLES = {
    realistic: 'ThisIsReal SDXL v3.0',
    anime: 'Nova Anime XL',
    pixelart: 'Pixel Storm XL',
    krea: 'KREA (Dark Beast KREA 2)',
  };

  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  function injectStyles() {
    if (document.getElementById('titan-image-proposal-css')) return;
    const st = document.createElement('style');
    st.id = 'titan-image-proposal-css';
    st.textContent = `
      .titan-image-proposal { margin: 8px 0 4px; padding: 12px 14px; border: 1px solid var(--border,#444); border-radius: 10px; background: var(--surface-2,rgba(255,255,255,.04)); max-width: 520px; }
      .titan-image-proposal h4 { margin: 0 0 8px; font-size: 13px; font-weight: 600; opacity: .9; }
      .titan-image-proposal .tip-row { display: flex; gap: 8px; font-size: 12px; margin: 3px 0; }
      .titan-image-proposal .tip-label { min-width: 72px; opacity: .65; flex-shrink: 0; }
      .titan-image-proposal .tip-val { word-break: break-word; }
      .titan-image-proposal .tip-prompt { max-height: 4.5em; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; }
      .titan-image-proposal-actions { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
      .titan-image-proposal-actions button { font-size: 12px; padding: 6px 12px; border-radius: 6px; cursor: pointer; border: 1px solid var(--border,#555); background: var(--surface-1,#333); color: inherit; }
      .titan-image-proposal-actions button.primary { background: var(--accent,#3b82f6); border-color: transparent; color: #fff; }
      .titan-image-proposal-actions button:disabled { opacity: .45; cursor: not-allowed; }
      .titan-image-proposal.editing .tip-display { display: none; }
      .titan-image-proposal.editing .tip-edit { display: block; }
      .titan-image-proposal .tip-edit { display: none; }
      .titan-image-proposal .tip-edit input, .titan-image-proposal .tip-edit textarea, .titan-image-proposal .tip-edit select { width: 100%; font-size: 12px; padding: 4px 6px; border-radius: 4px; border: 1px solid var(--border,#555); background: var(--surface-1,#222); color: inherit; }
      .titan-image-proposal .tip-edit textarea { min-height: 64px; resize: vertical; }
      .titan-image-proposal .tip-lora { display: none; margin-top: 8px; }
      .titan-image-proposal.lora-enabled .tip-lora { display: block; }
      .titan-image-proposal .tip-lora select { width: 100%; min-height: 72px; font-size: 12px; }
      .titan-image-proposal .tip-ip-refs { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }
      .titan-image-proposal .tip-ip-ref { width: 44px; height: 44px; border-radius: 6px; overflow: hidden; border: 1px solid var(--border,#555); }
      .titan-image-proposal .tip-ip-ref img { width: 100%; height: 100%; object-fit: cover; }
      .titan-image-proposal.cancelled { opacity: .5; pointer-events: none; }
      .titan-image-proposal .tip-status { font-size: 11px; opacity: .7; margin-top: 6px; }
      .chat-input-bar.titan-image-locked { opacity: .55; pointer-events: none; }
      .titan-image-stop-btn { position: fixed; bottom: 88px; right: 24px; z-index: 1200; padding: 8px 14px; border-radius: 8px; background: #b91c1c; color: #fff; border: none; cursor: pointer; font-size: 13px; box-shadow: 0 2px 8px rgba(0,0,0,.35); display: none; }
      .titan-image-stop-btn.visible { display: block; }
      .agent-thread-node.proposal-pending .agent-thread-icon { opacity: .85; }
    `;
    document.head.appendChild(st);
  }

  function resolvedFields(proposal) {
    const r = proposal.resolved || {};
    return {
      prompt: r.prompt || proposal.prompt || '',
      negative_prompt: r.negative_prompt || proposal.negative_prompt || '',
      style: r.style || proposal.style || '',
      quality: r.quality || proposal.quality || 'high',
      size: r.size || proposal.size || '1024x1024',
      steps: r.steps != null ? r.steps : '',
      cfg_scale: r.cfg_scale != null ? r.cfg_scale : '',
      sampler: r.sampler || '',
      seed: r.seed != null ? r.seed : (proposal.seed != null ? proposal.seed : null),
      ip_weight: r.ip_weight != null ? r.ip_weight : (proposal.ip_weight != null ? proposal.ip_weight : null),
      reference_images: proposal.reference_images || r.reference_images || [],
    };
  }

  function refPreviewHtml(refs) {
    if (!refs || !refs.length) return '';
    const thumbs = refs.slice(0, 4).map((ref) => {
      let src = ref.previewUrl || ref.url || '';
      if (!src && ref.gallery_id) src = `${API_BASE}/api/gallery/${ref.gallery_id}`;
      if (!src && ref.path && String(ref.path).includes('/api/generated-image/')) src = ref.path;
      if (!src) return '';
      return `<span class="tip-ip-ref"><img src="${esc(src)}" alt="ref"></span>`;
    }).filter(Boolean).join('');
    if (!thumbs) return `${refs.length} reference(s)`;
    return `<span class="tip-ip-refs">${thumbs}</span>`;
  }

  function row(label, value, cls) {
    return `<div class="tip-row tip-display"><span class="tip-label">${esc(label)}</span><span class="tip-val ${cls || ''}">${esc(value)}</span></div>`;
  }

  function buildCardHtml(proposal) {
    const f = resolvedFields(proposal);
    const seedTxt = f.seed != null && f.seed !== '' ? String(f.seed) : '(random)';
    const styleLbl = STYLES[f.style] || f.style;
    const refs = f.reference_images || [];
    const ipRow = refs.length
      ? row('IP-Adapter', `${refs.length} ref(s)${f.ip_weight != null ? ` · strength ${f.ip_weight}` : ''}`)
      : '';
    const ipThumbs = refs.length
      ? `<div class="tip-row tip-display"><span class="tip-label">Refs</span><span class="tip-val">${refPreviewHtml(refs)}</span></div>`
      : '';
    return `
      <h4>${proposal.fallback ? 'Fallback image proposal' : 'Image proposal'}</h4>
      ${row('Style', styleLbl)}
      ${ipRow}
      ${ipThumbs}
      ${row('Size', f.size)}
      ${row('Quality', f.quality)}
      ${row('Seed', seedTxt)}
      ${row('Prompt', f.prompt, 'tip-prompt')}
      ${f.negative_prompt ? row('Negative', f.negative_prompt, 'tip-prompt') : ''}
      ${f.steps !== '' ? row('Steps', f.steps) : ''}
      ${f.cfg_scale !== '' ? row('CFG', f.cfg_scale) : ''}
      ${f.sampler ? row('Sampler', f.sampler) : ''}
      <div class="tip-edit">
        <label>Style <select data-f="style"><option value="realistic">realistic</option><option value="anime">anime</option><option value="pixelart">pixelart</option><option value="krea">krea</option></select></label>
        <label>Size <input data-f="size" placeholder="1024x1024"></label>
        <label>Quality <select data-f="quality"><option value="low">low</option><option value="medium">medium</option><option value="high">high</option><option value="auto">auto</option></select></label>
        <label>Seed <input data-f="seed" placeholder="empty = random"></label>
        <label>Prompt <textarea data-f="prompt"></textarea></label>
        <label>Negative <textarea data-f="negative_prompt"></textarea></label>
        <label>IP strength <input data-f="ip_weight" type="number" min="0" max="1" step="0.05" placeholder="0.7"></label>
        <div class="tip-lora"><label>LoRA (optional) <select data-f="loras" multiple></select></label></div>
      </div>
      <div class="tip-status"></div>
      <div class="titan-image-proposal-actions">
        <button type="button" class="primary" data-act="generate">Generate</button>
        <button type="button" data-act="edit">Edit</button>
        <button type="button" data-act="cancel">Cancel</button>
      </div>`;
  }

  function readForm(card, proposal) {
    const out = { ...proposal };
    card.querySelectorAll('[data-f]').forEach((el) => {
      const k = el.dataset.f;
      if (k === 'loras') {
        const selected = Array.from(el.selectedOptions || []);
        out.loras = selected.map((opt) => ({
          path: opt.value,
          name: opt.textContent,
          weight: parseFloat(opt.dataset.weight || '0.8') || 0.8,
        }));
        return;
      }
      let v = el.value;
      if (k === 'seed') {
        v = v.trim() === '' ? null : parseInt(v, 10);
        if (Number.isNaN(v)) v = null;
      }
      if (k === 'ip_weight') {
        v = v.trim() === '' ? null : parseFloat(v);
        if (Number.isNaN(v)) v = null;
      }
      out[k] = v;
    });
    out.display_prompt = out.prompt;
    return out;
  }

  function fillForm(card, proposal) {
    const f = resolvedFields(proposal);
    card.querySelectorAll('[data-f]').forEach((el) => {
      const k = el.dataset.f;
      if (k === 'seed') el.value = proposal.seed != null ? proposal.seed : '';
      else if (k === 'ip_weight') el.value = proposal.ip_weight != null ? proposal.ip_weight : (f.ip_weight != null ? f.ip_weight : '');
      else el.value = proposal[k] != null ? proposal[k] : (f[k] || '');
    });
  }

  async function resolveProposal(proposal) {
    try {
      const r = await fetch(`${API_BASE}/api/titan/hub/image-resolve`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(proposal),
      });
      if (!r.ok) return proposal;
      const data = await r.json();
      if (data.resolved) return { ...proposal, resolved: data.resolved };
    } catch (_) { /* ignore */ }
    return proposal;
  }

  function getSessionId() {
    try {
      return window.sessionModule?.getCurrentSessionId?.() || null;
    } catch (_) {
      return null;
    }
  }

  async function maybeEnableLoraUi(card) {
    try {
      const r = await fetch(`${API_BASE}/api/titan/hub/image-pipeline-config`, { credentials: 'same-origin' });
      if (!r.ok) return;
      const cfg = await r.json();
      if (!cfg.lora_ui_enabled) return;
      card.classList.add('lora-enabled');
      const sel = card.querySelector('[data-f="loras"]');
      if (!sel) return;
      const lr = await fetch(`${API_BASE}/api/titan/hub/sd-loras`, { credentials: 'same-origin' });
      if (!lr.ok) return;
      const data = await lr.json();
      (data.loras || []).forEach((item) => {
        const opt = document.createElement('option');
        opt.value = item.path || item.name;
        opt.textContent = item.name || item.path;
        opt.dataset.weight = '0.8';
        sel.appendChild(opt);
      });
    } catch (_) { /* optional UI */ }
  }

  function mountCard(container, proposal, hooks) {
    injectStyles();
    if (!container || !proposal) return null;

    const card = document.createElement('div');
    card.className = 'titan-image-proposal';
    card.dataset.proposalId = proposal.id || '';
    card.innerHTML = buildCardHtml(proposal);
    container.appendChild(card);
    fillForm(card, proposal);
    maybeEnableLoraUi(card);

    const statusEl = card.querySelector('.tip-status');
    const setStatus = (t) => { if (statusEl) statusEl.textContent = t || ''; };

    card.addEventListener('click', async (e) => {
      const btn = e.target.closest('button[data-act]');
      if (!btn || card.classList.contains('cancelled')) return;
      const act = btn.dataset.act;

      if (act === 'cancel') {
        card.classList.add('cancelled');
        setStatus('Cancelled');
        card.querySelectorAll('button').forEach((b) => { b.disabled = true; });
        return;
      }

      if (act === 'edit') {
        card.classList.toggle('editing');
        btn.textContent = card.classList.contains('editing') ? 'Done' : 'Edit';
        return;
      }

      if (act === 'generate') {
        card.querySelectorAll('button').forEach((b) => { b.disabled = true; });
        let p = card.classList.contains('editing') ? readForm(card, proposal) : proposal;
        p = await resolveProposal(p);
        setStatus('Generating…');
        const ig = window.__titanImageGen;
        if (ig && ig.startGenerationWatch) ig.startGenerationWatch(setStatus);

        try {
          const r = await fetch(`${API_BASE}/api/titan/hub/image-execute`, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ...p, session_id: getSessionId() }),
          });
          const data = await r.json().catch(() => ({}));
          if (!r.ok) {
            const err = data.detail || data.error || r.statusText;
            setStatus('Error: ' + (typeof err === 'string' ? err : JSON.stringify(err)));
            card.querySelectorAll('button').forEach((b) => { b.disabled = false; });
            if (ig && ig.stopGenerationWatch) ig.stopGenerationWatch();
            return;
          }
          setStatus('Done');
          card.classList.add('cancelled');
          if (hooks && hooks.onImage && (data.image_url || data.image_urls?.length)) {
            hooks.onImage(data);
          }
          window.dispatchEvent(new CustomEvent('gallery-refresh'));
        } catch (err) {
          setStatus('Error: ' + err.message);
          card.querySelectorAll('button').forEach((b) => { b.disabled = false; });
          if (ig && ig.stopGenerationWatch) ig.stopGenerationWatch();
        }
      }
    });

    return card;
  }

  window.__titanImageProposal = { mountCard, resolveProposal, injectStyles };
})();
