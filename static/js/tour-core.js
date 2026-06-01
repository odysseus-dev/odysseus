// static/js/tour-core.js

export class Tour {
  constructor() {
    this.cancelled = false;
    this.halos = [];
    this.tooltip = null;
    this.streamHandle = null;
    this._injectStyles();
    document.body.classList.add('tour-active');
  }

  _injectStyles() {
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

  makeHalo(target) {
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
    const boundUpdate = update.bind(this);
    window.addEventListener('resize', boundUpdate);
    window.addEventListener('scroll', boundUpdate, true);
    requestAnimationFrame(() => halo.classList.add('tour-fade-in'));
    return {
      el: halo,
      update: boundUpdate,
      destroy() {
        window.removeEventListener('resize', boundUpdate);
        window.removeEventListener('scroll', boundUpdate, true);
        halo.remove();
      },
    };
  }

  clearHalos() {
    this.halos.forEach(h => h.destroy());
    this.halos = [];
    document.querySelectorAll('.tour-halo').forEach(e => e.remove());
  }

  clear() {
    document.querySelectorAll('.odysseus-highlight, .odysseus-highlight-click').forEach(e => {
      e.classList.remove('odysseus-highlight', 'odysseus-highlight-click');
    });
    this.clearHalos();
    if (this.tooltip) {
      this.tooltip.remove();
      this.tooltip = null;
    }
    document.body.classList.remove('tour-active');
    if (this.streamHandle) this.streamHandle.cancel();
  }

  _streamHTML(el, html, speedMs = 14) {
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

  positionTooltip(target, placement = 'auto') {
    if (!this.tooltip) return;
    const arrowEl = this.tooltip.querySelector('.tour-arrow');
    if (arrowEl) arrowEl.remove();

    const r = target.getBoundingClientRect();
    const ttW = 280;
    this.tooltip.style.visibility = 'hidden';
    this.tooltip.style.display = '';
    const ttH = this.tooltip.offsetHeight || 100;

    if (placement === 'center-above') {
      const top = Math.max(10, window.innerHeight * 0.32 - ttH / 2);
      const left = Math.max(10, window.innerWidth / 2 - ttW / 2);
      this.tooltip.style.top = top + 'px';
      this.tooltip.style.left = left + 'px';
      this.tooltip.style.visibility = '';
      return;
    }

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

    this.tooltip.style.top = top + 'px';
    this.tooltip.style.left = left + 'px';

    if (arrowSide === 'top') {
      arrow.style.cssText = \`top:-6px;left:\${Math.min(Math.max(r.left + r.width / 2 - left - 5, 10), ttW - 20)}px;border-right:none;border-bottom:none\`;
    } else if (arrowSide === 'bottom') {
      arrow.style.cssText = \`bottom:-6px;left:\${Math.min(Math.max(r.left + r.width / 2 - left - 5, 10), ttW - 20)}px;border-left:none;border-top:none\`;
    } else {
      arrow.style.cssText = \`left:-6px;top:\${Math.min(Math.max(r.top + r.height / 2 - top - 5, 10), ttH - 20)}px;border-right:none;border-top:none\`;
    }
    this.tooltip.appendChild(arrow);
    this.tooltip.style.visibility = '';
  }

  showStep(selOrOpts, text = null, opts = {}) {
    let finalOpts = {};
    if (typeof selOrOpts === 'object' && selOrOpts !== null && !text) {
      finalOpts = selOrOpts;
    } else {
      finalOpts = { sel: selOrOpts, text, ...opts };
    }
    const { sel, text: finalText, mode, advanceOnClick, pulseNext, finishLabel, isFirst, isLast, placement, stream, extraSel, interactive } = finalOpts;

    return new Promise(resolve => {
      if (this.cancelled) return resolve('cancel');
      
      this.clearHalos();
      document.querySelectorAll('.odysseus-highlight').forEach(e => e.classList.remove('odysseus-highlight'));
      
      if (!this.tooltip) {
        this.tooltip = document.createElement('div');
        this.tooltip.id = 'tour-tooltip';
        document.body.appendChild(this.tooltip);
      }

      const sels = sel.split(',').map(s => s.trim());
      if (extraSel) sels.push(...extraSel.split(',').map(s => s.trim()));
      const targets = sels.map(s => document.querySelector(s)).filter(Boolean);
      if (!targets.length) return resolve('skip');

      const clickMode = mode === 'click' || interactive;
      const waitsForEvent = sels.includes('#message') || sels.includes('#model-picker-btn');
      const breathing = clickMode || waitsForEvent || advanceOnClick;

      targets.forEach(t => t.classList.add('odysseus-highlight'));
      this.halos = targets.map(t => this.makeHalo(t));

      this.tooltip.classList.remove('tour-fade-in');
      targets[0].scrollIntoView({ behavior: 'smooth', block: 'nearest' });

      let hint = '';
      if (breathing || advanceOnClick) {
        hint = '<div style="font-size:0.72rem;opacity:0.45;margin-bottom:6px;">Click the highlighted element to continue.</div>';
      }

      const finishText = isLast ? 'done' : (finishLabel ? 'finish tour' : 'skip tour');
      const nextArrow = isLast ? '✓' : '→';

      this.tooltip.innerHTML = \`<div class="tour-text">\${finalText}</div>\${hint}
        <div class="tour-nav" style="\${(breathing && !advanceOnClick) ? 'justify-content:center' : ''}">
          \${(breathing && !advanceOnClick) ? '' : \`<button class="tour-btn-arrow\${isFirst ? ' disabled' : ''}" data-act="back">←</button>\`}
          <button class="tour-btn-skip" data-act="skip">\${finishText}</button>
          \${(breathing && !advanceOnClick) ? '' : \`<button class="tour-btn-arrow\${pulseNext ? ' tour-btn-arrow-pulse' : ''}" data-act="next">\${nextArrow}</button>\`}
        </div>\`;

      requestAnimationFrame(() => {
        this.positionTooltip(targets[0], placement);
        this.tooltip.classList.add('tour-fade-in');
        if (this.streamHandle) this.streamHandle.cancel();
        const textEl = this.tooltip.querySelector('.tour-text');
        if (textEl && stream !== false) {
          this.streamHandle = this._streamHTML(textEl, finalText);
        } else if (textEl) {
          textEl.innerHTML = finalText;
        }
      });

      let messageInputListener = null;
      let resolved = false;

      const onClick = (e) => {
        const hit = e.target.closest && e.target.closest('[data-act]');
        const act = hit && hit.dataset.act;
        if (!act) return;
        if (resolved) return;
        resolved = true;
        cleanup();
        if (act === 'skip') { this.cancelled = true; resolve('cancel'); }
        else resolve(act);
      };

      const onTargetClick = (e) => {
        if (resolved) return;
        const t = e.target;
        const matches = sels.some(s => {
          try { return t.closest && t.closest(s); } catch { return false; }
        });
        if (!matches) return;
        resolved = true;
        resolve('next');
        cleanup();
      };

      const onMessageInput = (e) => {
        if (e.type !== 'keydown') return;
        if (e.key !== 'Enter' || e.shiftKey || e.ctrlKey || e.metaKey || e.altKey) return;
        const ta = document.getElementById('message');
        if (!ta || !ta.value.trim()) return;
        const saved = ta.value;
        e.preventDefault();
        e.stopImmediatePropagation();
        resolved = true;
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

      const cleanup = () => {
        if (this.tooltip) this.tooltip.removeEventListener('click', onClick);
        ['click', 'pointerdown', 'mousedown'].forEach(evt => {
          document.removeEventListener(evt, onTargetClick, true);
        });
        if (messageInputListener) document.removeEventListener('keydown', messageInputListener, true);
        if (this.streamHandle) this.streamHandle.cancel();
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

      this.tooltip.addEventListener('click', onClick);
      if (clickMode || advanceOnClick) {
        ['click', 'pointerdown', 'mousedown'].forEach(evt => {
          document.addEventListener(evt, onTargetClick, true);
        });
      }
    });
  }
}
