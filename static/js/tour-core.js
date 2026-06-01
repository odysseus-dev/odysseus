export const TourCore = (function() {
  const delay = ms => new Promise(r => setTimeout(r, ms));

  function injectStyles() {
    if (!document.getElementById('tour-styles')) {
      const s = document.createElement('style');
      s.id = 'tour-styles';
      s.textContent = `
        #tour-tooltip{position:fixed;z-index:10001;background:var(--bg);color:var(--fg);
          border:1px solid var(--border);border-radius:8px;padding:12px 14px;max-width:280px;
          font-family:inherit;font-size:0.8rem;line-height:1.5;
          box-shadow:0 2px 12px rgba(0,0,0,0.3);pointer-events:auto;
          opacity:0;transform:translateY(4px);transition:opacity 0.3s ease-out,transform 0.3s ease-out}
        #tour-tooltip.tour-fade-in{opacity:1;transform:translateY(0)}
        #tour-tooltip .tour-text{margin-bottom:8px;opacity:0.8}
        .tour-arrow{position:absolute;width:10px;height:10px;background:var(--bg);
          border:1px solid var(--border);transform:rotate(45deg);pointer-events:none}
        .tour-nav{display:flex;align-items:center;justify-content:space-between}
        .tour-nav button{background:none;border:1px solid var(--border);color:var(--fg);
          cursor:pointer;font-family:inherit;border-radius:4px;transition:all .1s}
        .tour-nav button:hover{background:color-mix(in srgb,var(--fg) 8%,transparent)}
        .tour-nav button:active{background:color-mix(in srgb,var(--fg) 16%,transparent);transform:scale(0.95)}
        .tour-btn-arrow{font-size:1rem;padding:4px 12px;opacity:0.6}
        .tour-btn-arrow:hover{opacity:1}
        .tour-btn-arrow.disabled{opacity:0.15;pointer-events:none}
        .tour-btn-skip{font-size:0.72rem;padding:3px 10px;opacity:0.35;border-color:transparent!important}
        .tour-btn-skip:hover{opacity:0.6}
        .tour-btn-arrow-pulse{opacity:1;border-color:var(--accent,var(--red));color:var(--accent,var(--red));
          animation:tour-arrow-pulse 1.2s ease-in-out infinite}
        @keyframes tour-arrow-pulse{
          0%,100%{box-shadow:0 0 0 0 color-mix(in srgb,var(--accent,var(--red)) 50%,transparent)}
          50%    {box-shadow:0 0 0 6px color-mix(in srgb,var(--accent,var(--red)) 0%,transparent)}
        }
      `;
      document.head.appendChild(s);
    }
  }

  function getTooltip() {
    let tooltip = document.getElementById('tour-tooltip');
    if (!tooltip) {
      tooltip = document.createElement('div');
      tooltip.id = 'tour-tooltip';
      document.body.appendChild(tooltip);
    }
    return tooltip;
  }

  function positionTooltip(tooltip, target) {
    tooltip.querySelector('.tour-arrow')?.remove();
    const r = target.getBoundingClientRect();
    const ttW = 280;
    tooltip.style.visibility = 'hidden';
    tooltip.style.display = '';
    const ttH = tooltip.offsetHeight || 100;

    const arrow = document.createElement('div');
    arrow.className = 'tour-arrow';

    const gap = 12;
    let top, left, arrowSide;

    if (r.bottom + gap + ttH < window.innerHeight - 10) {
      top = r.bottom + gap;
      left = r.left + r.width / 2 - ttW / 2;
      arrowSide = 'top';
    } else if (r.top - gap - ttH > 10) {
      top = r.top - gap - ttH;
      left = r.left + r.width / 2 - ttW / 2;
      arrowSide = 'bottom';
    } else {
      top = r.top + r.height / 2 - ttH / 2;
      left = r.right + gap;
      arrowSide = 'left';
    }

    if (left + ttW > window.innerWidth - 10) left = window.innerWidth - ttW - 10;
    if (left < 10) left = 10;
    if (top < 10) top = 10;

    tooltip.style.top = top + 'px';
    tooltip.style.left = left + 'px';

    if (arrowSide === 'top') {
      arrow.style.cssText = `top:-6px;left:${Math.min(Math.max(r.left + r.width / 2 - left - 5, 10), ttW - 20)}px;border-right:none;border-bottom:none`;
    } else if (arrowSide === 'bottom') {
      arrow.style.cssText = `bottom:-6px;left:${Math.min(Math.max(r.left + r.width / 2 - left - 5, 10), ttW - 20)}px;border-left:none;border-top:none`;
    } else {
      arrow.style.cssText = `left:-6px;top:${Math.min(Math.max(r.top + r.height / 2 - top - 5, 10), ttH - 20)}px;border-right:none;border-top:none`;
    }
    tooltip.appendChild(arrow);
    tooltip.style.visibility = '';
  }

  function streamHTML(el, html, speedMs = 14) {
    el.innerHTML = '';
    let i = 0, out = '';
    let timer = setInterval(() => {
      if (i >= html.length) { clearInterval(timer); timer = null; return; }
      if (html[i] === '<') {
        const end = html.indexOf('>', i);
        if (end === -1) { out += html.slice(i); i = html.length; }
        else { out += html.slice(i, end + 1); i = end + 1; }
      } else {
        out += html[i];
        i++;
      }
      el.innerHTML = out;
    }, speedMs);
    return { cancel: () => { if (timer) { clearInterval(timer); el.innerHTML = html; } } };
  }

  function makeHalo(target) {
    const halo = document.createElement('div');
    halo.className = 'tour-halo';
    document.body.appendChild(halo);
    const update = () => {
      const r = target.getBoundingClientRect();
      halo.style.top    = (r.top - 4) + 'px';
      halo.style.left   = (r.left - 4) + 'px';
      halo.style.width  = (r.width + 8) + 'px';
      halo.style.height = (r.height + 8) + 'px';
    };
    update();
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    requestAnimationFrame(() => halo.classList.add('tour-fade-in'));
    return {
      el: halo,
      update,
      destroy() {
        window.removeEventListener('resize', update);
        window.removeEventListener('scroll', update, true);
        halo.remove();
      },
    };
  }

  class Tour {
    constructor() {
      this.cancelled = false;
      this._onTyped = null;
      this._msgEl = null;
      this._draftObserver = null;
      this._draftPoll = null;
      this._typedDraft = '';
      this._halos = [];
      this.isStreamingFn = null;
    }

    clearTour() {
      document.querySelectorAll('.odysseus-highlight, .odysseus-highlight-click').forEach(e => {
        e.classList.remove('odysseus-highlight', 'odysseus-highlight-click');
      });
      document.querySelectorAll('.tour-halo').forEach(e => e.remove());
      document.getElementById('tour-tooltip')?.remove();
      document.body.classList.remove('tour-active');
      this._clearHalos();
      setTimeout(() => {
        if (this._msgEl && this._onTyped) this._msgEl.removeEventListener('input', this._onTyped);
        if (this._draftObserver) this._draftObserver.disconnect();
        if (this._draftPoll) clearInterval(this._draftPoll);
      }, 3000);
    }

    _clearHalos() {
      this._halos.forEach(h => h.destroy());
      this._halos = [];
    }

    _restoreIfCleared() {
      if (!this._msgEl || !this._typedDraft) return;
      if (this._msgEl.value === '' && this._typedDraft) {
        this._msgEl.value = this._typedDraft;
        this._msgEl.dispatchEvent(new Event('input', { bubbles: true }));
      }
    }

    startDraftObserver() {
      this._typedDraft = '';
      this._msgEl = document.getElementById('message');
      this._onTyped = () => { if (this._msgEl) this._typedDraft = this._msgEl.value; };
      if (this._msgEl) this._msgEl.addEventListener('input', this._onTyped);
      this._draftObserver = new MutationObserver(() => this._restoreIfCleared());
      if (this._msgEl) this._draftObserver.observe(this._msgEl, { attributes: true, attributeFilter: ['value'] });
      this._draftPoll = setInterval(() => this._restoreIfCleared(), 200);
    }

    showStep(sel, text, options = {}) {
      const mode = options.mode || 'next';
      const isFirst = !!options.isFirst;
      const stepOpts = options.stepOpts || {};

      return new Promise(resolve => {
        if (this.cancelled) return resolve('cancel');
        document.querySelectorAll('.odysseus-highlight').forEach(e => e.classList.remove('odysseus-highlight'));
        this._clearHalos();

        const sels = sel.split(',').map(s => s.trim());
        const targets = sels.map(s => document.querySelector(s)).filter(Boolean);
        if (!targets.length) return resolve('skip');

        const clickMode = mode === 'click';
        const waitsForEvent = sels.includes('#message');
        const breathing = clickMode || waitsForEvent;
        const advanceOnClick = !!stepOpts.advanceOnClick;
        const pulseNext = !!stepOpts.pulseNext;

        targets.forEach(t => t.classList.add('odysseus-highlight'));
        this._halos = breathing ? targets.map(makeHalo) : [];
        const tooltip = getTooltip();
        tooltip.classList.remove('tour-fade-in');
        targets[0].scrollIntoView({ behavior: 'smooth', block: 'nearest' });

        tooltip.innerHTML = \`<div class="tour-text">\${text}</div>
          \${breathing ? '<div style="font-size:0.72rem;opacity:0.35;margin-bottom:6px">Click the highlighted element to continue</div>' : ''}
          <div class="tour-nav" style="\${breathing ? 'justify-content:center' : ''}">
            \${breathing ? '' : \`<button class="tour-btn-arrow\${isFirst ? ' disabled' : ''}" data-act="back">\u2190</button>\`}
            <button class="tour-btn-skip" data-act="skip">\${stepOpts.finishLabel ? 'finish tour' : 'skip tour'}</button>
            \${breathing ? '' : \`<button class="tour-btn-arrow\${pulseNext ? ' tour-btn-arrow-pulse' : ''}" data-act="next">\u2192</button>\`}
          </div>\`;

        let streamHandle = null;
        requestAnimationFrame(() => {
          positionTooltip(tooltip, targets[0]);
          tooltip.classList.add('tour-fade-in');
          const textEl = tooltip.querySelector('.tour-text');
          if (textEl) streamHandle = streamHTML(textEl, text);
        });

        let messageInputListener = null;
        let modelListener = null;

        const onClick = (e) => {
          const act = e.target.closest('[data-act]')?.dataset.act;
          if (!act) return;
          cleanup();
          if (act === 'skip') { this.cancelled = true; resolve('cancel'); }
          else resolve(act);
        };

        let _advanced = false;
        const onDocClickCapture = (e) => {
          if (_advanced) return;
          const t = e.target;
          const matches = sels.some(s => {
            try { return t.closest && t.closest(s); } catch { return false; }
          });
          if (!matches) return;
          _advanced = true;
          resolve('clicked');
          try { cleanup(); } catch (err) { console.warn('tour cleanup:', err); }
        };

        const onMessageInput = (e) => {
          if (e.type !== 'keydown') return;
          if (e.key !== 'Enter' || e.shiftKey || e.ctrlKey || e.metaKey || e.altKey) return;
          const ta = document.getElementById('message');
          if (!ta || !ta.value.trim()) return;
          const saved = ta.value;
          e.preventDefault();
          e.stopImmediatePropagation();
          cleanup();
          resolve('next');
          const _restore = () => {
            if (ta && !ta.value && saved) {
              ta.value = saved;
              ta.dispatchEvent(new Event('input', { bubbles: true }));
            }
          };
          _restore();
          Promise.resolve().then(_restore);
          requestAnimationFrame(_restore);
          setTimeout(_restore, 50);
          setTimeout(_restore, 200);
        };

        const onModelPicked = () => { cleanup(); resolve('next'); };

        const cleanup = () => {
          tooltip.removeEventListener('click', onClick);
          ['click', 'pointerdown', 'mousedown'].forEach(evt => {
            document.removeEventListener(evt, onDocClickCapture, true);
            targets.forEach(t => t.removeEventListener(evt, onDocClickCapture, true));
          });
          if (messageInputListener) document.removeEventListener('keydown', messageInputListener, true);
          if (modelListener) document.removeEventListener('odysseus:model-picked', modelListener);
          if (streamHandle) streamHandle.cancel();
          this._clearHalos();
        };

        if (sels.includes('#message')) {
          const msg = document.getElementById('message');
          if (msg) {
            messageInputListener = (e) => {
              if (e.target !== msg) return;
              onMessageInput(e);
            };
            document.addEventListener('keydown', messageInputListener, true);
          }
        }
        if (sels.includes('#model-picker-btn')) {
          modelListener = onModelPicked;
          document.addEventListener('odysseus:model-picked', modelListener, { once: true });
        }

        tooltip.addEventListener('click', onClick);
        if (clickMode || advanceOnClick) {
          ['click', 'pointerdown', 'mousedown'].forEach(evt => {
            document.addEventListener(evt, onDocClickCapture, true);
            targets.forEach(t => t.addEventListener(evt, onDocClickCapture, true));
          });
        }
      });
    }

    async run(steps) {
      this.cancelled = false;
      injectStyles();
      document.body.classList.add('tour-active');
      this.startDraftObserver();

      let i = 0;
      while (i < steps.length) {
        const step = steps[i];
        if (step.before) await step.before();
        const res = await this.showStep(step.sel, step.text, {
          mode: step.mode || 'next',
          isFirst: i === 0,
          isLast: i === steps.length - 1,
          stepOpts: step
        });
        if (res === 'cancel') {
          this.clearTour();
          return false; // Tour cancelled
        }
        if (res === 'back') { if (i > 0) i--; continue; }
        i++;
        await delay(step.afterDelay || 750);
        
        if (step.sel === '#message' && typeof this.isStreamingFn === 'function' && this.isStreamingFn()) {
          document.querySelectorAll('.odysseus-highlight').forEach(e => e.classList.remove('odysseus-highlight'));
          const tooltip = getTooltip();
          tooltip.style.display = 'none';
          await new Promise(r => {
            const check = setInterval(() => { if (!this.isStreamingFn()) { clearInterval(check); r(); } }, 300);
          });
          await delay(400);
        }
      }

      this.clearTour();
      return true; // Tour completed
    }
  }

  const singleton = new Tour();
  
  return {
    run: singleton.run.bind(singleton),
    clearTour: singleton.clearTour.bind(singleton),
    cancelTour: window.cancelActiveTour,
    getTooltip,
    positionTooltip,
    makeHalo,
    _clearHalos: singleton._clearHalos.bind(singleton)
  };
})();
