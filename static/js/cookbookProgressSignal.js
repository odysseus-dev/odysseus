// static/js/cookbookProgressSignal.js
/**
 * Liveness signal for a running cookbook download/install. The watchdog treats a
 * task as stalled when this signal stays unchanged for too long, so it must move
 * whenever the task is genuinely making progress.
 *
 * During a model DOWNLOAD the honest signal is the downloaded-byte counter
 * ("1.81G" from "1.81G/2.49G"): it climbs while transferring and freezes when
 * stuck — and unlike a % bar or speed/ETA it doesn't keep animating on a frozen
 * frame. That path is kept exactly as-is.
 *
 * But a dependency install (e.g. vllm) spends long stretches with NO byte
 * counter — pip dependency resolution and the native CUDA build/compile. A
 * byte-only signal freezes there, so the watchdog falsely declares the install
 * stale and restarts it mid-build, looping forever (#1568). When there's no byte
 * counter, fall back to a fingerprint of the output tail: resolver/compile lines
 * keep changing while the process is alive, and only a truly hung process leaves
 * the tail frozen.
 *
 * Pure (string in, string out) so it's unit-testable; cookbookRunning.js pulls
 * in browser-only modules and can't load under node.
 */
export function computeProgressSignal(bytes, dlAgg, lastPct, snapshot) {
  if (bytes) return bytes;
  const base = dlAgg != null ? String(dlAgg) : (lastPct || '0');
  // No byte counter → use the output tail so a build/resolve phase that emits new
  // lines counts as progress instead of a false stall (#1568).
  return base + '|' + String(snapshot || '').slice(-300);
}
