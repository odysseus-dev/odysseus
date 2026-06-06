// static/js/model/modelStreamQuirks.js
//
// Model-specific streaming quirks — keep in sync with src/model_stream_quirks.py.

/** @typedef {{ thinkingOnlyStallMs?: number, autoContinueOnThinkingOnly?: boolean }} ModelStreamQuirk */

export const DEFAULT_THINKING_ONLY_STALL_MS = 15_000;

/** @type {Record<string, ModelStreamQuirk>} */
export const MODEL_STREAM_QUIRKS = {
  'gemma4:e4b': {
    thinkingOnlyStallMs: DEFAULT_THINKING_ONLY_STALL_MS,
    autoContinueOnThinkingOnly: true,
  },
  'gemma4:*': {
    thinkingOnlyStallMs: DEFAULT_THINKING_ONLY_STALL_MS,
    autoContinueOnThinkingOnly: true,
  },
};

export const MIN_REPLY_AFTER_THINKING_CHARS = 24;

function _fnmatch(name, pattern) {
  const escaped = pattern.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*');
  return new RegExp(`^${escaped}$`, 'i').test(name);
}

/**
 * Return { pattern, quirk } for the most specific matching entry, or null.
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
