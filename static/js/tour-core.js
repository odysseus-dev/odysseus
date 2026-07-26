// static/js/tour-core.js
// Shared onboarding tour helper & scaffolding system for Odysseus.

(function (window) {
  'use strict';

  function ensureTourStyles() {
    if (document.getElementById('tour-styles')) return;
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

  function cancelActiveTour(reason) {
    document.querySelectorAll('.odysseus-highlight, .odysseus-highlight-click')
      .forEach(e => e.classList.remove('odysseus-highlight', 'odysseus-highlight-click'));
    document.querySelectorAll('.tour-halo').forEach(e => e.remove());
    document.getElementById('tour-tooltip')?.remove();
    document.body?.classList.remove('tour-active');
  }

  function makeHalo(target) {
    const halo = document.createElement('div');
    halo.className = 'tour-halo';
    document.body.appendChild(halo);
    const update = () => {
      if (!target || !document.body.contains(target)) return;
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

  function positionTooltip(tooltip, target) {
    if (!tooltip || !target) return;
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
    if (!el) return { cancel() {} };
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

  function isTourActive() {
    return document.body.classList.contains('tour-active');
  }

  const TourCore = {
    ensureTourStyles,
    cancelActiveTour,
    makeHalo,
    positionTooltip,
    streamHTML,
    isTourActive,
  };

  window.TourCore = TourCore;
  window.cancelActiveTour = cancelActiveTour;

})(typeof window !== 'undefined' ? window : this);
