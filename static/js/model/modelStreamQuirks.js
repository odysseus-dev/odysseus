// static/js/model/modelStreamQuirks.js
//
// Thinking-without-action resiliency — keep timing constants in sync with
// src/model_stream_quirks.py.

/** @typedef {{ thinkingOnlyNudgeMs?: number, thinkingOnlyTimeoutMs?: number, thinkingOnlyStallMs?: number, autoContinueOnThinkingOnly?: boolean }} ModelStreamQuirk */

/**
 * Post-</thinking> silence before a silent auto-nudge. Local 8–14B models
 * (Ollama) usually emit the first reply/tool token within ~3–8s after
 * reasoning; stalls produce nothing. 12s avoids false positives on slow TTFT.
 */
export const THINKING_ONLY_NUDGE_MS = 12_000;

/**
 * Hard timeout after reasoning closes with no tool call and almost no visible
 * reply. Observed stuck locals (gemma4, qwen3:14b) never recover; 25s is
 * patient enough for slow hardware but short enough to surface failure clearly.
 */
export const THINKING_ONLY_TIMEOUT_MS = 25_000;

/** @deprecated Use THINKING_ONLY_NUDGE_MS — kept for older imports/tests. */
export const DEFAULT_THINKING_ONLY_STALL_MS = THINKING_ONLY_NUDGE_MS;

export const MIN_REPLY_AFTER_THINKING_CHARS = 24;

/** @typedef {{ nudgeMs: number, timeoutMs: number, autoContinueOnThinkingOnly: boolean }} ThinkingStallPolicy */

export const DEFAULT_THINKING_STALL_POLICY = {
  nudgeMs: THINKING_ONLY_NUDGE_MS,
  timeoutMs: THINKING_ONLY_TIMEOUT_MS,
  autoContinueOnThinkingOnly: true,
};

/** Optional per-model overrides (timing/behavior tuning only). */
/** @type {Record<string, ModelStreamQuirk>} */
export const MODEL_STREAM_QUIRKS = {};

function _fnmatch(name, pattern) {
  const escaped = pattern.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*');
  return new RegExp(`^${escaped}$`, 'i').test(name);
}

/**
 * Return { pattern, quirk } for the most specific matching override, or null.
 * @param {string} model
 */
export function matchModelStreamQuirk(model) {
  const name = (model || '').trim().toLowerCase();
  if (!name) return null;
  let bestPattern = null;
  /** @type {ModelStreamQuirk | null} */
  let bestQuirk = null;
  for (const [pattern, quirk] of Object.entries(MODEL_STREAM_QUIRKS)) {
    if (_fnmatch(name, pattern)) {
      if (!bestPattern || pattern.length > bestPattern.length) {
        bestPattern = pattern;
        bestQuirk = quirk;
      }
    }
  }
  if (!bestQuirk) return null;
  return { pattern: bestPattern, quirk: bestQuirk };
}

/** @param {string} model */
export function getModelStreamQuirk(model) {
  return matchModelStreamQuirk(model)?.quirk ?? null;
}

/**
 * Resolve stall policy for any model. Universal defaults apply when no override
 * is registered — reasoning models do not need to be listed individually.
 * @param {string} [model]
 * @returns {ThinkingStallPolicy}
 */
export function resolveThinkingStallPolicy(model) {
  const quirk = getModelStreamQuirk(model);
  if (!quirk) return { ...DEFAULT_THINKING_STALL_POLICY };
  return {
    nudgeMs: quirk.thinkingOnlyNudgeMs
      ?? quirk.thinkingOnlyStallMs
      ?? THINKING_ONLY_NUDGE_MS,
    timeoutMs: quirk.thinkingOnlyTimeoutMs ?? THINKING_ONLY_TIMEOUT_MS,
    autoContinueOnThinkingOnly: quirk.autoContinueOnThinkingOnly !== false,
  };
}
