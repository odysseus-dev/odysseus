// Pure helpers for model controls backed by canonical provider evidence.

const AUTO_ONLY = ['auto'];
const SUPPORTED_STATUSES = new Set(['claimed', 'verified']);

export function normalizeModelControlValue(value) {
  let token = String(value || '').trim().toLowerCase().replace(/-/g, '_');
  if (token === 'none') token = 'off';
  if (token === 'x_high') token = 'xhigh';
  if (!token || token === 'auto' || token === 'default') return 'auto';
  return /^[a-z][a-z0-9_]{0,31}$/.test(token) ? token : 'auto';
}

function controlsFor(record) {
  return record && Array.isArray(record.deterministic_controls)
    ? record.deterministic_controls
    : [];
}

export function modelCapabilityForContext(items, { model = '', endpointId = '', endpointUrl = '' } = {}) {
  if (!model || !Array.isArray(items)) return null;
  const routeItems = endpointId
    ? items.filter(item => String(item.endpoint_id || '') === String(endpointId))
    : items.filter(item => endpointUrl && String(item.url || '') === String(endpointUrl));
  const matches = [];
  routeItems.forEach(item => {
    (item.model_capabilities || []).forEach(record => {
      if (record && record.model_id === model) matches.push(record);
    });
  });
  if (!matches.length) return null;
  const canonical = JSON.stringify(matches[0]);
  return matches.every(record => JSON.stringify(record) === canonical) ? matches[0] : null;
}

export function modelControlCapabilities(key, { model = '', modelCapability = null } = {}) {
  const unavailable = reason => ({ supported: false, allowed: [...AUTO_ONLY], reason });
  if (!model) return unavailable('Select a model first');
  if (!modelCapability || modelCapability.model_id !== model) {
    return unavailable(`${key === 'verbosity' ? 'Verbosity' : 'Reasoning'} controls are unavailable for this model endpoint`);
  }

  const control = controlsFor(modelCapability).find(item => (
    item
    && item.control === key
    && SUPPORTED_STATUSES.has(item.status)
    && item.evidence
    && Array.isArray(item.evidence.allowed_values)
  ));
  if (!control) return unavailable(`${key === 'verbosity' ? 'Verbosity' : 'Reasoning'} controls are unavailable for this model endpoint`);

  const allowed = ['auto'];
  control.evidence.allowed_values.forEach(value => {
    const normalized = normalizeModelControlValue(value);
    if (/^[a-z][a-z0-9_]{0,31}$/.test(normalized) && normalized !== 'auto' && !allowed.includes(normalized)) {
      allowed.push(normalized);
    }
  });
  return allowed.length > 1
    ? { supported: true, allowed, reason: '' }
    : unavailable(`${key === 'verbosity' ? 'Verbosity' : 'Reasoning'} controls are unavailable for this model endpoint`);
}
