/** Shared lifecycle guard for frame-style pointer interactions. */

export function createDirectManipulationSession({
  name = 'interaction',
  getContext = () => null,
  isContextCurrent = () => true,
  onCancel = () => {},
} = {}) {
  let active = null;
  let nextId = 1;

  const pointerMatches = event => {
    if (!active || active.pointerId == null || event?.pointerId == null) return true;
    return active.pointerId === event.pointerId;
  };

  const releaseCapture = session => {
    if (!session?.captureTarget || session.pointerId == null) return;
    try { session.captureTarget.releasePointerCapture(session.pointerId); } catch {}
  };

  const contextIsCurrent = () => active && isContextCurrent(active.context, active.data);

  function begin(event, data = {}, options = {}) {
    if (active) cancel('restarted');
    const pointerId = Number.isFinite(event?.pointerId) ? event.pointerId : null;
    const captureTarget = options.captureTarget || null;
    active = {
      id: nextId++,
      name,
      pointerId,
      captureTarget,
      context: getContext(),
      data,
    };
    if (captureTarget && pointerId != null) {
      try { captureTarget.setPointerCapture(pointerId); } catch {}
    }
    return active;
  }

  function update(event, callback) {
    if (!active || !pointerMatches(event)) return false;
    if (!contextIsCurrent()) {
      cancel('stale-context');
      return false;
    }
    callback?.(active.data, active);
    return true;
  }

  function commit(event, callback) {
    if (!active || !pointerMatches(event)) return false;
    if (!contextIsCurrent()) {
      cancel('stale-context');
      return false;
    }
    const completed = active;
    active = null;
    releaseCapture(completed);
    callback?.(completed.data, completed);
    return true;
  }

  function cancel(reason = 'cancelled') {
    if (!active) return false;
    const cancelled = active;
    active = null;
    releaseCapture(cancelled);
    onCancel(cancelled.data, reason, cancelled);
    return true;
  }

  return {
    begin,
    update,
    commit,
    cancel,
    get active() { return active; },
  };
}
