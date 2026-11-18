// Compatibility gate for managed (Cookbook-served) models. Pure decision helpers; no DOM and no module state, so a
// newly loaded backend is picked up on the very next check without a browser refresh.
export const REQUIRED_MANAGED_CONTRACT = Object.freeze({ serving_api: 1, managed_chat_catalog: 1, typed_readiness: 1 });

const _isObject = v => v !== null && typeof v === 'object' && !Array.isArray(v);

/** Pure: is this /serving/status payload from a backend that satisfies the managed-serving contract? */
export function evaluateBackendContract(status) {
  if (!_isObject(status)) return { ok: false, reason: 'malformed', detail: 'The serving status response was not a JSON object.' };
  if (status.schema_version !== 1) return { ok: false, reason: 'incompatible', detail: `Unsupported serving schema_version ${JSON.stringify(status.schema_version)}.` };
  if (!Array.isArray(status.services)) return { ok: false, reason: 'malformed', detail: 'The serving status response has no services list.' };
  const contract = status.contract;
  if (contract === undefined) return { ok: false, reason: 'missing', detail: 'The running backend does not advertise the managed-serving contract (older version).' };
  if (!_isObject(contract)) return { ok: false, reason: 'malformed', detail: 'The managed-serving contract is not an object.' };
  for (const [key, want] of Object.entries(REQUIRED_MANAGED_CONTRACT)) {
    if (contract[key] !== want) return { ok: false, reason: 'incompatible', detail: `Backend contract ${key}=${JSON.stringify(contract[key])}, this page needs ${want}.` };
  }
  return { ok: true, reason: 'ok', detail: '' };
}

/** Ask the running backend. Any failure (network, auth, HTTP, JSON, contract) is "not compatible"; nothing falls back. */
export async function checkManagedBackend(fetchJson) {
  let res;
  try { res = await fetchJson('/api/cookbook/serving/status'); }
  catch (error) { return { ok: false, reason: 'unavailable', detail: 'The serving status endpoint could not be reached.' }; }
  if (!res || !res.ok) {
    const code = res ? res.status : 'none';
    return { ok: false, reason: 'unavailable', detail: `The serving status endpoint answered HTTP ${code}.` };
  }
  let body;
  try { body = await res.json(); } catch { return { ok: false, reason: 'malformed', detail: 'The serving status response was not valid JSON.' }; }
  return evaluateBackendContract(body);
}

const _sameSet = (a, b) => a.length === b.length && new Set(a).size === a.length && a.every(x => b.includes(x));

export const MANAGED_ENDPOINT_PREFIX = 'local-managed-';
export const isManagedEndpointId = id => typeof id === 'string' && id.startsWith(MANAGED_ENDPOINT_PREFIX);

/**
 * Pure: the single authoritative verdict for one managed endpoint, shared by the launch flow and the ordinary model picker.
 * Requires an owned + listener-attributed service row carrying the same endpoint_id and a chat-catalog entry for that
 * endpoint_id that lists every runtime-reported model id. Friendly labels are never consulted.
 */
export function verifyManagedService({ sessionId, endpointId, status, catalogItems }) {
  const fail = reason => ({ ok: false, reason, modelIds: [] });
  if (!sessionId || !endpointId) return fail('no managed endpoint was registered for this launch');
  if (endpointId !== `${MANAGED_ENDPOINT_PREFIX}${sessionId}`) return fail('endpoint id is not the managed endpoint for this session (a user-configured endpoint is never reused)');
  if (!_isObject(status) || !Array.isArray(status.services)) return fail('serving status unavailable');
  const rows = status.services.filter(s => _isObject(s) && s.id === sessionId);
  if (rows.length !== 1) return fail('serving status has no unique service for this session');
  const row = rows[0];
  if (row.management !== 'odysseus-managed') return fail('service is not Odysseus-managed');
  if (row.process_ownership !== 'owned') return fail(`process ownership is ${row.process_ownership}`);
  if (row.endpoint_ownership_verified !== true) return fail('listener ownership could not be verified');
  if (row.endpoint_id !== endpointId) return fail('serving status endpoint_id differs from the registered endpoint');
  if (row.readiness !== 'ready') return fail(`serving status readiness is ${row.readiness}`);
  const ids = Array.isArray(row.models) ? row.models.filter(m => typeof m === 'string' && m) : [];
  if (!ids.length) return fail('the runtime reported no model id');
  const items = (Array.isArray(catalogItems) ? catalogItems : []).filter(i => _isObject(i) && i.endpoint_id === endpointId);
  if (items.length !== 1) return fail('chat catalog has no unique entry for this endpoint_id');
  const item = items[0];
  if (item.offline) return fail('chat catalog marks the endpoint offline');
  const models = item.models ?? [], extra = item.models_extra ?? [];
  if (!Array.isArray(models) || !Array.isArray(extra)) return fail('chat catalog has an invalid model list');
  const listed = [...models, ...extra];
  if (!ids.every(id => listed.includes(id))) return fail('chat catalog does not list the runtime model id');
  return { ok: true, reason: 'ok', modelIds: ids };
}

/** Pure: launch-time verdict = the typed ready payload plus the shared service verdict. wait-ready alone is never enough. */
export function verifyManagedSelectable({ sessionId, endpointId, ready, status, catalogItems }) {
  if (!_isObject(ready) || ready.ok !== true || ready.state !== 'ready') return { ok: false, reason: 'the runtime did not report ready', modelIds: [] };
  const verdict = verifyManagedService({ sessionId, endpointId, status, catalogItems });
  if (!verdict.ok) return verdict;
  if (!Array.isArray(ready.models) || !_sameSet(verdict.modelIds, ready.models)) return { ok: false, reason: 'ready payload and serving status disagree on model ids', modelIds: [] };
  return verdict;
}

/**
 * Pure: filter a catalog item list for ordinary selection. Non-managed items pass through untouched (same objects, even when
 * they share a managed endpoint's URL). A managed item survives only with a positive shared verdict under a compatible
 * backend contract, and and its catalog entry then lists the runtime-reported ids.
 */
export function filterManagedCatalog(items, status) {
  const list = Array.isArray(items) ? items : [];
  const contract = evaluateBackendContract(status);
  const verdicts = {};
  const out = [];
  for (const item of list) {
    const id = item && item.endpoint_id;
    if (!isManagedEndpointId(id)) { out.push(item); continue; }
    const verdict = contract.ok
      ? verifyManagedService({ sessionId: id.slice(MANAGED_ENDPOINT_PREFIX.length), endpointId: id, status, catalogItems: list })
      : { ok: false, reason: `backend activation required: ${contract.detail}`, modelIds: [] };
    verdicts[id] = verdict;
    if (verdict.ok) out.push(item);   // verified: the entry lists the runtime's own ids (parallel display arrays stay aligned)
  }
  return { items: out, verdicts };
}
