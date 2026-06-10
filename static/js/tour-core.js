// tour-core.js — shared scaffolding for the onboarding tour helpers
// (tourAutoplay.js + tourHints.js). Both independently duplicated the modal
// open-detection, the visibility check, safe localStorage access, and the
// tour-active guard; this factors them out so there is one implementation to
// reason about. Pure DOM utilities, no app state.

// A modal counts as "open" only when it's actually laid out — not merely
// lacking .hidden (some modals toggle inline display:none instead).
export function isVisible(el) {
  if (!el || el.classList.contains('hidden')) return false;
  if (el.style.display === 'none') return false;
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}

// True while a slash-command tour is running its halos (body.tour-active).
export function isTourActive() {
  return document.body.classList.contains('tour-active');
}

// Safe one-shot "seen" flags in localStorage (private mode / quota throw).
export function seenGet(key) {
  try { return localStorage.getItem(key) === '1'; } catch { return false; }
}
export function seenSet(key) {
  try { localStorage.setItem(key, '1'); } catch { /* ignore */ }
}

// Watch for a modal transitioning hidden->visible and call onOpen(el) once per
// open. `matches(el)` decides which elements qualify (by class, id, prefix, ...).
// Observes modals present now AND any added later (e.g. the research overlay is
// appended on demand). Each call keeps its OWN WeakSet of observed elements, so
// multiple independent watchers (hints + autoplay) can both watch the same
// modals without one starving the other. The onOpen callback owns its own
// timing/guards (settle delays, seen checks).
export function watchModals(matches, onOpen) {
  if (typeof MutationObserver === 'undefined') return;
  const observed = new WeakSet();

  const attrObserver = new MutationObserver((muts) => {
    for (const m of muts) {
      if (m.attributeName !== 'class' && m.attributeName !== 'style') continue;
      const el = m.target;
      if (!(el instanceof HTMLElement) || !matches(el)) continue;
      const wasHidden = !m.oldValue
        || /\bhidden\b/.test(m.oldValue)
        || /display:\s*none/.test(m.oldValue);
      if (wasHidden && isVisible(el)) onOpen(el);
    }
  });

  const observe = (el) => {
    if (!el || observed.has(el)) return;
    observed.add(el);
    attrObserver.observe(el, {
      attributes: true,
      attributeOldValue: true,
      attributeFilter: ['class', 'style'],
    });
    if (isVisible(el)) onOpen(el);   // already open at watch time
  };

  document.querySelectorAll('.modal').forEach((el) => { if (matches(el)) observe(el); });

  const addObserver = new MutationObserver((muts) => {
    for (const m of muts) {
      m.addedNodes.forEach((node) => {
        if (!(node instanceof HTMLElement)) return;
        if (matches(node)) observe(node);
        node.querySelectorAll?.('.modal').forEach((el) => { if (matches(el)) observe(el); });
      });
    }
  });
  addObserver.observe(document.body, { childList: true, subtree: true });
}

export default { isVisible, isTourActive, seenGet, seenSet, watchModals };
