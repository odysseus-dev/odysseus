// Pure state helpers for the per-user foreground fallback Settings controls.

export const MAX_FOREGROUND_FALLBACKS = 10;

export function createForegroundPreferenceSaveQueue(write, onUnavailable) {
  let queue = Promise.resolve();

  return {
    save(key, value) {
      queue = queue.then(async () => {
        try {
          await write(key, value);
        } catch (error) {
          if (typeof onUnavailable === 'function') onUnavailable(error);
          throw error;
        }
      });
      return queue;
    },
    reset() {
      queue = Promise.resolve();
    },
  };
}

export function cleanForegroundFallbackCandidates(
  value,
  maxItems = MAX_FOREGROUND_FALLBACKS
) {
  if (!Array.isArray(value)) return [];
  const limit = Number.isInteger(maxItems) && maxItems >= 0
    ? maxItems
    : MAX_FOREGROUND_FALLBACKS;
  const seen = new Set();
  return value
    .filter(item => item && typeof item === 'object')
    .map(item => ({
      endpoint_id: typeof item.endpoint_id === 'string' ? item.endpoint_id : '',
      model: typeof item.model === 'string' ? item.model : '',
    }))
    .filter(item => item.endpoint_id && item.model)
    .filter(item => {
      const identity = JSON.stringify([item.endpoint_id, item.model]);
      if (seen.has(identity)) return false;
      seen.add(identity);
      return true;
    })
    .slice(0, limit);
}

export function nextForegroundFallbackCandidate(endpoints, value) {
  const used = new Set(
    cleanForegroundFallbackCandidates(value).map(item =>
      JSON.stringify([item.endpoint_id, item.model])
    )
  );
  for (const endpoint of Array.isArray(endpoints) ? endpoints : []) {
    if (!endpoint || typeof endpoint !== 'object' || endpoint.is_enabled === false) {
      continue;
    }
    const endpointId = typeof endpoint.id === 'string' ? endpoint.id : '';
    if (!endpointId) continue;
    for (const model of Array.isArray(endpoint.models) ? endpoint.models : []) {
      if (typeof model !== 'string' || !model) continue;
      const identity = JSON.stringify([endpointId, model]);
      if (!used.has(identity)) return { endpoint_id: endpointId, model };
    }
  }
  return null;
}

export function summarizeForegroundFallbackCandidateEligibility(value, endpoints) {
  const byEndpoint = new Map();
  for (const endpoint of Array.isArray(endpoints) ? endpoints : []) {
    if (!endpoint || typeof endpoint !== 'object' || endpoint.is_enabled === false) {
      continue;
    }
    const endpointId = typeof endpoint.id === 'string' ? endpoint.id : '';
    if (!endpointId) continue;
    const models = new Set(
      (Array.isArray(endpoint.models) ? endpoint.models : [])
        .filter(model => typeof model === 'string' && model)
    );
    const allowedUnknownModels = Array.isArray(endpoint.allowed_unknown_models)
      ? new Set(endpoint.allowed_unknown_models.filter(model =>
          typeof model === 'string' && model
        ))
      : null;
    byEndpoint.set(endpointId, {
      models,
      catalogUnknown: endpoint.model_catalog_unknown === true,
      allowedUnknownModels,
    });
  }
  const summary = { configured: 0, eligible: 0, unknown: 0, ineligible: 0 };
  for (const item of cleanForegroundFallbackCandidates(value)) {
    summary.configured += 1;
    const endpoint = byEndpoint.get(item.endpoint_id);
    if (!endpoint) summary.ineligible += 1;
    else if (endpoint.models.has(item.model)) summary.eligible += 1;
    else if (
      endpoint.catalogUnknown
      && (
        endpoint.allowedUnknownModels === null
        || endpoint.allowedUnknownModels.has(item.model)
      )
    ) summary.unknown += 1;
    else summary.ineligible += 1;
  }
  return summary;
}

export function countEligibleForegroundFallbackCandidates(value, endpoints) {
  return summarizeForegroundFallbackCandidateEligibility(value, endpoints).eligible;
}

export function captureFallbackWidgetFocus(container, activeElement) {
  if (!container || !activeElement || !container.contains(activeElement)) return null;
  const row = typeof activeElement.closest === 'function'
    ? activeElement.closest('.settings-fallback-row')
    : null;
  const focusKey = activeElement.dataset && activeElement.dataset.fallbackFocus;
  if (!row || !focusKey) return null;
  const index = Array.prototype.indexOf.call(container.children || [], row);
  return index >= 0 ? { index, focusKey } : null;
}

export function restoreFallbackWidgetFocus(container, addButton, state) {
  if (!container || !state) return false;
  const rows = Array.from(container.children || []);
  if (!rows.length) {
    if (!addButton || typeof addButton.focus !== 'function') return false;
    addButton.focus();
    return true;
  }
  const index = Math.max(0, Math.min(Number(state.index) || 0, rows.length - 1));
  const controls = typeof rows[index].querySelectorAll === 'function'
    ? Array.from(rows[index].querySelectorAll('[data-fallback-focus]'))
    : [];
  const target = controls.find(control =>
    control.dataset && control.dataset.fallbackFocus === state.focusKey
  ) || addButton;
  if (!target || typeof target.focus !== 'function') return false;
  target.focus();
  return true;
}

export function normalizeForegroundFallbackPrefs(prefs) {
  const source = prefs && typeof prefs === 'object' && !Array.isArray(prefs)
    ? prefs
    : {};
  return {
    enabled: source.foreground_fallback_enabled === true,
    candidates: cleanForegroundFallbackCandidates(
      source.foreground_model_fallbacks
    ),
  };
}

export function moveForegroundFallbackCandidate(value, index, offset) {
  const candidates = cleanForegroundFallbackCandidates(value);
  const target = Number(index) + Number(offset);
  if (
    !Number.isInteger(index)
    || !Number.isInteger(offset)
    || target < 0
    || target >= candidates.length
  ) {
    return candidates;
  }
  const next = candidates.slice();
  const moved = next.splice(index, 1)[0];
  next.splice(target, 0, moved);
  return next;
}

export function normalizeForegroundFallbackModelCatalog(payload) {
  const items = payload && Array.isArray(payload.items) ? payload.items : [];
  return items
    .filter(item => item && typeof item === 'object')
    .filter(item => !item.model_type || item.model_type === 'llm')
    .map(item => {
      const endpointId = typeof item.endpoint_id === 'string'
        ? item.endpoint_id.trim()
        : '';
      const endpointName = typeof item.endpoint_name === 'string'
        ? item.endpoint_name.trim()
        : '';
      const models = [];
      [...(Array.isArray(item.models) ? item.models : []),
        ...(Array.isArray(item.models_extra) ? item.models_extra : [])]
        .forEach(model => {
          if (typeof model !== 'string') return;
          const clean = model.trim();
          if (clean && !models.includes(clean)) models.push(clean);
        });
      return {
        id: endpointId,
        name: endpointName || 'Model endpoint',
        is_enabled: true,
        models,
        online: item.offline !== true,
        model_catalog_unknown: item.model_catalog_unknown === true,
        allowed_unknown_models: Array.isArray(item.allowed_unknown_models)
          ? item.allowed_unknown_models.filter(model => typeof model === 'string')
          : null,
      };
    })
    .filter(item => item.id);
}
