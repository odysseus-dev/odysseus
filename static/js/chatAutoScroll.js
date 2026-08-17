// Chat transcript auto-scroll state. Kept DOM-light so streaming callers can
// share one animation without dropping later height changes from tool cards.

export function createChatAutoScroller({
  getBox = () => document.getElementById('chat-history'),
  requestFrame = (callback) => requestAnimationFrame(callback),
  cancelFrame = (frameId) => cancelAnimationFrame(frameId),
  isCompactViewport = () => window.innerWidth <= 768,
} = {}) {
  let enabled = true;
  let frameId = null;
  let box = null;

  function resolveBox() {
    if (!box || !box.isConnected) box = getBox();
    return box;
  }

  function step() {
    frameId = null;
    const targetBox = resolveBox();
    if (!targetBox || !enabled) return;

    // Recalculate on every frame. A tool card, progress tail, or reply bubble
    // can grow while this animation is already in flight.
    const target = Math.max(0, targetBox.scrollHeight - targetBox.clientHeight);
    const current = targetBox.scrollTop;
    const diff = target - current;
    if (Math.abs(diff) <= 1) {
      targetBox.scrollTop = target;
      return;
    }

    const factor = isCompactViewport() ? 0.4 : 0.2;
    targetBox.scrollTop = current + diff * factor;
    frameId = requestFrame(step);
  }

  function scroll() {
    if (!enabled || !resolveBox() || frameId !== null) return;
    frameId = requestFrame(step);
  }

  function scrollInstant() {
    const targetBox = resolveBox();
    if (!targetBox) return;
    targetBox.scrollTop = Math.max(0, targetBox.scrollHeight - targetBox.clientHeight);
  }

  function setEnabled(nextEnabled) {
    enabled = !!nextEnabled;
    if (!enabled && frameId !== null) {
      cancelFrame(frameId);
      frameId = null;
    }
  }

  return {
    scroll,
    scrollInstant,
    setEnabled,
    isEnabled: () => enabled,
    isAnimating: () => frameId !== null,
  };
}

export default createChatAutoScroller;
