/**
 * Titan image quick actions (pipeline step 8): Variation, Refine, Upscale, Inpaint.
 * Runs via hub image-execute — no LLM.
 */
(function () {
  const API_BASE = window.API_BASE || '';

  function parseStyle(model, genStyle) {
    if (genStyle === 'realistic' || genStyle === 'anime' || genStyle === 'pixelart' || genStyle === 'krea') return genStyle;
    const m = String(model || '');
    let hit = m.match(/titan-sd:(\w+)/i);
    if (hit) return hit[1].toLowerCase();
    hit = m.match(/style:\s*(realistic|anime|pixelart|krea)/i);
    return hit ? hit[1].toLowerCase() : '';
  }

  function isTitanSdImage(meta) {
    if (!meta) return false;
    if (meta.gen_style === 'realistic' || meta.gen_style === 'anime' || meta.gen_style === 'pixelart' || meta.gen_style === 'krea') return true;
    const model = String(meta.model || '');
    return model.includes('titan-sd') || /style:\s*(realistic|anime|pixelart|krea)/i.test(model);
  }

  function injectStyles() {
    if (document.getElementById('titan-image-actions-css')) return;
    const st = document.createElement('style');
    st.id = 'titan-image-actions-css';
    st.textContent = `
      .titan-image-actions { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0 4px; }
      .titan-image-actions button {
        font-size: 11px; padding: 4px 10px; border-radius: 6px; cursor: pointer;
        border: 1px solid var(--border,#555); background: var(--surface-1,#333); color: inherit;
      }
      .titan-image-actions button:disabled { opacity: .45; cursor: not-allowed; }
      .titan-image-actions .tip-status { width: 100%; font-size: 11px; opacity: .7; margin-top: 2px; }
    `;
    document.head.appendChild(st);
  }

  function metaFromImage(img) {
    if (!img) return null;
    return {
      imageId: img.id || img.imageId,
      imageUrl: img.url || img.imageUrl,
      prompt: img.prompt || '',
      model: img.model || '',
      size: img.size || '1024x1024',
      quality: img.quality || 'high',
      seed: img.gen_seed != null ? img.gen_seed : img.seed,
      gen_style: img.gen_style,
      style: parseStyle(img.model, img.gen_style),
      negative_prompt: img.negative_prompt || '',
      steps: img.steps,
      cfg_scale: img.cfg_scale,
      sampler: img.sampler,
      scheduler: img.scheduler || img.scheduler_name,
    };
  }

  async function enrichMeta(meta) {
    const base = { ...(meta || {}) };
    if (!base.imageId) return base;
    try {
      const r = await fetch(`${API_BASE}/api/gallery/${base.imageId}`, { credentials: 'same-origin' });
      if (r.ok) {
        const img = await r.json();
        const fromGallery = metaFromImage(img);
        return {
          ...base,
          ...fromGallery,
          imageUrl: base.imageUrl || fromGallery.imageUrl,
          prompt: base.prompt || fromGallery.prompt,
        };
      }
    } catch (_) {}
    return base;
  }

  async function openInStudio(meta) {
    const full = await enrichMeta(meta);
    if (!isTitanSdImage(full)) return { ok: false, error: 'Not a Titan SD image' };
    const style = full.style || parseStyle(full.model, full.gen_style);
    if (!style) return { ok: false, error: 'Unknown style' };
    try {
      const mod = await import('./imageStudio/panel.js');
      await mod.openWithRecipe({
        style,
        prompt: full.prompt || '',
        negative_prompt: full.negative_prompt || '',
        seed: full.seed,
        seedLock: full.seed != null && full.seed !== '',
        size: full.size || '1024x1024',
        quality: full.quality || 'high',
        steps: full.steps,
        cfg_scale: full.cfg_scale,
        sampler: full.sampler,
        scheduler: full.scheduler,
        imageUrl: full.imageUrl,
      });
      return { ok: true };
    } catch (err) {
      console.error('[titanImageActions] openInStudio failed', err);
      return { ok: false, error: err.message };
    }
  }

  function buildProposal(op, meta, extra) {
    const style = meta.style || parseStyle(meta.model, meta.gen_style);
    const base = {
      id: crypto.randomUUID ? crypto.randomUUID() : String(Date.now()),
      op,
      prompt: meta.prompt || '',
      negative_prompt: meta.negative_prompt || '',
      style,
      quality: meta.quality || 'high',
      size: meta.size || '1024x1024',
      display_prompt: meta.prompt || '',
    };
    if (op === 'generate') {
      return { ...base, ...(extra || {}) };
    }
    if (!meta.imageId) return null;
    const out = {
      ...base,
      source_image_id: String(meta.imageId),
      ...(extra || {}),
    };
    if (op === 'regenerate' && meta.seed != null && meta.seed !== '') {
      out.seed = meta.seed;
    }
    return out;
  }

  function getSessionId() {
    try {
      return window.sessionModule?.getCurrentSessionId?.() || null;
    } catch (_) {
      return null;
    }
  }

  async function executeProposal(proposal, statusEl) {
    const ig = window.__titanImageGen;
    const setStatus = (t) => { if (statusEl) statusEl.textContent = t || ''; };

    let p = proposal;
    if (window.__titanImageProposal?.resolveProposal) {
      p = await window.__titanImageProposal.resolveProposal(proposal);
    }

    setStatus('Generating…');
    if (ig?.startGenerationWatch) ig.startGenerationWatch(setStatus);

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
        if (ig?.stopGenerationWatch) ig.stopGenerationWatch();
        return { ok: false, error: err };
      }
      setStatus('Done');
      if (ig?.stopGenerationWatch) ig.stopGenerationWatch();
      window.dispatchEvent(new CustomEvent('gallery-refresh'));
      return { ok: true, data };
    } catch (err) {
      setStatus('Error: ' + err.message);
      if (ig?.stopGenerationWatch) ig.stopGenerationWatch();
      return { ok: false, error: err.message };
    }
  }

  async function runAction(op, meta, extra, statusEl) {
    if (!isTitanSdImage(meta)) {
      if (statusEl) statusEl.textContent = 'Actions only for Titan SD images';
      return { ok: false };
    }
    const style = meta.style || parseStyle(meta.model, meta.gen_style);
    if (!style) {
      if (statusEl) statusEl.textContent = 'Unknown style (realistic/anime/pixelart/krea)';
      return { ok: false };
    }
    meta.style = style;
    const proposal = buildProposal(op, meta, extra);
    if (!proposal) {
      if (statusEl) statusEl.textContent = 'Missing gallery image ID';
      return { ok: false };
    }
    return executeProposal(proposal, statusEl);
  }

  async function openInpaintEditor(meta) {
    if (!meta?.imageUrl) return;
    try {
      const [galleryMod, editorMod] = await Promise.all([
        import('./gallery.js'),
        import('./galleryEditor.js'),
      ]);
      galleryMod.default.openGallery();
      const modal = document.getElementById('gallery-modal');
      if (modal) {
        modal.querySelectorAll('.gallery-tab').forEach((t) => t.classList.remove('active'));
        modal.querySelector('.gallery-tab[data-tab="editor"]')?.classList.add('active');
      }
      const imagesContainer = document.getElementById('gallery-images-container');
      const albumsContainer = document.getElementById('gallery-albums-container');
      if (imagesContainer) imagesContainer.style.display = 'none';
      if (albumsContainer) albumsContainer.style.display = 'none';
      const editorContainer = document.getElementById('gallery-editor-container');
      if (editorContainer) editorContainer.style.display = 'flex';
      const label = (meta.prompt || '').trim().slice(0, 60) || 'Inpaint';
      if (meta.gen_style) {
        try { sessionStorage.setItem('ge_titan_gen_style', meta.gen_style); } catch (_) {}
      }
      editorMod.openEditor(meta.imageUrl, meta.imageId || null, null, label);
      const activateInpaint = () => {
        if (editorMod.resetInpaintPanelDock) editorMod.resetInpaintPanelDock();
        const btn = document.querySelector('.ge-tool-btn[data-tool="inpaint"]');
        if (btn) btn.click();
      };
      let tries = 0;
      const wait = setInterval(() => {
        tries += 1;
        const ready = document.querySelector('.ge-tool-btn[data-tool="inpaint"]')
          && document.getElementById('ge-inpaint-section');
        if (ready || tries > 40) {
          clearInterval(wait);
          requestAnimationFrame(() => requestAnimationFrame(activateInpaint));
        }
      }, 100);
    } catch (err) {
      console.error('[titanImageActions] inpaint open failed', err);
    }
  }

  function mountActionBar(container, meta, hooks) {
    injectStyles();
    if (!container || !isTitanSdImage(meta)) return null;

    const bar = document.createElement('div');
    bar.className = 'titan-image-actions';
    const needsId = !meta.imageId;
    bar.innerHTML = `
      <button type="button" data-act="studio" title="Open in Image Studio with generation settings">Open in Studio</button>
      <button type="button" data-act="variation" title="New seed, same prompt"${needsId ? ' disabled' : ''}>Variation</button>
      <button type="button" data-act="regenerate" title="Img2img — refine details"${needsId ? ' disabled' : ''}>Refine</button>
      <button type="button" data-act="upscale" title="Img2img upscale ×2"${needsId ? ' disabled' : ''}>Upscale ×2</button>
      <button type="button" data-act="inpaint" title="Open editor with inpaint tool">Inpaint</button>
      <div class="tip-status"></div>`;
    container.appendChild(bar);

    const statusEl = bar.querySelector('.tip-status');
    bar.addEventListener('click', async (e) => {
      const btn = e.target.closest('button[data-act]');
      if (!btn || btn.disabled) return;
      const act = btn.dataset.act;
      if (act === 'studio') {
        bar.querySelectorAll('button').forEach((b) => { b.disabled = true; });
        statusEl.textContent = 'Opening Studio…';
        const result = await openInStudio(meta);
        bar.querySelectorAll('button').forEach((b) => {
          b.disabled = needsId && ['variation', 'regenerate', 'upscale'].includes(b.dataset.act);
        });
        statusEl.textContent = result.ok ? '' : (result.error || 'Error');
        return;
      }
      if (act === 'inpaint') {
        openInpaintEditor(meta);
        return;
      }
      if (!meta.imageId) return;
      bar.querySelectorAll('button').forEach((b) => { b.disabled = true; });
      let result;
      if (act === 'variation') {
        result = await runAction('generate', meta, { seed: null }, statusEl);
      } else if (act === 'regenerate') {
        result = await runAction('regenerate', meta, null, statusEl);
      } else if (act === 'upscale') {
        result = await runAction('upscale', meta, { upscale_factor: 2 }, statusEl);
      }
      bar.querySelectorAll('button').forEach((b) => {
        b.disabled = needsId && ['variation', 'regenerate', 'upscale'].includes(b.dataset.act);
      });
      if (result?.ok && hooks?.onImage && result.data?.image_url) {
        hooks.onImage(result.data);
      }
    });
    return bar;
  }

  function augmentImageBubble(wrap, meta, hooks) {
    if (!wrap || !meta) return wrap;
    const body = wrap.querySelector('.body') || wrap;
    mountActionBar(body, meta, hooks);
    return wrap;
  }

  function mountGalleryMenuItems(menuEl, img, hooks) {
    const meta = metaFromImage(img);
    if (!menuEl || !isTitanSdImage(meta) || !meta.imageId) return;

    const items = [
      { id: 'gallery-titan-studio', act: 'studio', label: 'Open in Studio' },
      { id: 'gallery-titan-variation', act: 'variation', label: 'Variation (new seed)' },
      { id: 'gallery-titan-regenerate', act: 'regenerate', label: 'Refine (img2img)' },
      { id: 'gallery-titan-upscale', act: 'upscale', label: 'Upscale ×2 (SD)' },
      { id: 'gallery-titan-inpaint', act: 'inpaint', label: 'Inpaint in editor' },
    ];

    const dlBtn = document.getElementById('gallery-download-btn');
    items.forEach(({ id, act, label }) => {
      const btn = document.createElement('button');
      btn.className = 'dropdown-item-compact';
      btn.id = id;
      btn.innerHTML = `<span class="dropdown-icon">✦</span>${label}`;
      btn.addEventListener('click', async () => {
        if (act === 'studio') {
          btn.disabled = true;
          const result = await openInStudio(meta);
          btn.disabled = false;
          if (!result.ok && result.error) window.uiModule?.showError?.(String(result.error));
          return;
        }
        if (act === 'inpaint') {
          openInpaintEditor(meta);
          return;
        }
        btn.disabled = true;
        let result;
        if (act === 'variation') result = await runAction('generate', meta, { seed: null }, null);
        else if (act === 'regenerate') result = await runAction('regenerate', meta, null, null);
        else if (act === 'upscale') result = await runAction('upscale', meta, { upscale_factor: 2 }, null);
        btn.disabled = false;
        if (result?.ok) {
          window.uiModule?.showToast?.('Done');
          if (hooks?.onImage && result.data?.image_url) hooks.onImage(result.data);
          else if (hooks?.onRefresh) hooks.onRefresh(img);
        } else if (result?.error) {
          window.uiModule?.showError?.(String(result.error));
        }
      });
      if (dlBtn && dlBtn.parentElement === menuEl) {
        menuEl.insertBefore(btn, dlBtn);
      } else {
        menuEl.appendChild(btn);
      }
    });
  }

  window.__titanImageActions = {
    isTitanSdImage,
    metaFromImage,
    enrichMeta,
    openInStudio,
    buildProposal,
    runAction,
    executeProposal,
    openInpaintEditor,
    mountActionBar,
    augmentImageBubble,
    mountGalleryMenuItems,
    parseStyle,
  };
})();
