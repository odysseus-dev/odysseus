/** Briefly ignore backdrop dismiss after open — avoids open-then-flash-close races (#4938). */

export const MODAL_BACKDROP_GUARD_MS = 350;

export function armModalBackdropGuard(modal, ms = MODAL_BACKDROP_GUARD_MS, now = performance.now()) {
  if (!modal) return;
  modal.dataset.suppressBackdropUntil = String(now + ms);
}

export function shouldSuppressBackdropClose(modal, now = performance.now()) {
  if (!modal) return false;
  const until = Number(modal.dataset.suppressBackdropUntil || 0);
  return now < until;
}