import { resolveReturn } from '/static/js/provider-return.js';
import { sharedJson } from '/static/js/hermes-http.js';

const endpoint = '/api/provider-sync';

(async function setBackLink() {
  const raw = new URLSearchParams(location.search).get('return');
  if (!raw) return;
  let hermesOrigin = null;
  try {
    const status = await sharedJson('modes', '/api/application-modes', { timeout: 10000, retries: 1, label: 'The Hermes status check' });
    if (status && status.enabled && status.url) hermesOrigin = new URL(status.url).origin;
  } catch (_) { /* the proxied Hermes address and same-origin returns still work */ }
  const target = resolveReturn(raw, location.origin, location.port, hermesOrigin);
  if (!target) return;
  const back = document.getElementById('back');
  back.href = target.href;
  back.textContent = target.app === 'hermes' ? 'Back to Hermes' : 'Back to Odysseus';
})();

const STATUS = {
  connected: 'Connected', requires_authentication: 'Needs authentication', unsupported: 'Not supported',
  offline: 'Offline', disabled: 'Disabled', unknown: 'Not checked',
};
const SYNC = {
  in_sync: 'In sync', models_differ: 'Model lists differ', sharing_off: 'Sharing off',
  pending_hermes: 'Waiting to be added to Hermes', pending_odysseus: 'Waiting to be added to Odysseus',
  deleted_on_hermes: 'Removed in Hermes (not re-added)', deleted_on_odysseus: 'Removed in Odysseus (not re-added)',
  not_shareable: 'Not shareable', not_shareable_protocol: 'Own protocol: not OpenAI-compatible', not_shareable_credential: 'Login-based: cannot be shared',
};
const SOURCE = { odysseus: 'Odysseus', hermes: 'Hermes', both: 'Both' };

const body = document.querySelector('#providers tbody');
const banner = document.getElementById('banner');
const busy = new Set();

async function call(path, method = 'GET', payload) {
  // Reading the catalogue is idempotent: identical concurrent reads share one request and transient failures are retried a bounded number of times.
  if (method === 'GET') return sharedJson('catalog' + path, endpoint + path, { timeout: 20000, retries: 2, label: 'The provider list' });
  const response = await fetch(endpoint + path, {
    method, credentials: 'same-origin', signal: AbortSignal.timeout(60000),
    headers: payload ? { 'Content-Type': 'application/json' } : undefined,
    body: payload ? JSON.stringify(payload) : undefined,
  });
  let data = {};
  try { data = await response.json(); } catch (_) { /* non-JSON error body */ }
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
  return data;
}

function cell(text, className) {
  const td = document.createElement('td');
  td.textContent = text;
  if (className) td.className = className;
  return td;
}

function badge(state) {
  const span = document.createElement('span');
  span.className = 'badge ' + state;
  span.textContent = STATUS[state] || state;
  return span;
}

function appCell(status) {
  const td = document.createElement('td');
  td.append(status ? badge(status) : Object.assign(document.createElement('span'), { className: 'badge none', textContent: 'Not added' }));
  return td;
}

function button(label, handler, disabled) {
  const b = document.createElement('button');
  b.type = 'button';
  b.textContent = label;
  b.disabled = !!disabled;
  b.addEventListener('click', handler);
  return b;
}

function modelsCell(row) {
  const td = document.createElement('td');
  const details = document.createElement('details');
  const summary = document.createElement('summary');
  summary.textContent = `${row.models.length} model${row.models.length === 1 ? '' : 's'}`;
  const list = document.createElement('ul');
  for (const model of row.models.slice(0, 200)) {
    const li = document.createElement('li');
    li.textContent = model;
    list.append(li);
  }
  details.append(summary, list);
  td.append(details);
  return td;
}

async function act(row, work, done) {
  if (busy.has(row.id)) return;
  busy.add(row.id);
  banner.textContent = 'Working…';
  try { banner.textContent = (await work()) || done || ''; }
  catch (error) { banner.textContent = error.message; }
  finally { busy.delete(row.id); await load(false); }
}

function render(data) {
  body.replaceChildren();
  document.getElementById('empty').hidden = data.providers.length > 0;
  for (const row of data.providers) {
    const tr = document.createElement('tr');
    const name = cell(row.name);
    if (row.base_url) {
      const small = document.createElement('small');
      small.textContent = row.base_url;
      name.append(document.createElement('br'), small);
    }
    if (row.duplicates) {
      const note = document.createElement('small');
      note.textContent = ` (${row.duplicates} duplicate entr${row.duplicates === 1 ? 'y' : 'ies'} hidden)`;
      name.append(note);
    }
    const share = document.createElement('td');
    const toggle = document.createElement('input');
    toggle.type = 'checkbox';
    toggle.checked = row.share_enabled;
    toggle.disabled = !row.portable;
    toggle.setAttribute('aria-label', `Share ${row.name} between Odysseus and Hermes`);
    toggle.addEventListener('change', () => act(row, async () => {
      await call('/share', 'POST', { id: row.id, enabled: toggle.checked });
      return toggle.checked ? 'Sharing enabled. Use "Add" to create it on the other side.' : 'Sharing turned off. Nothing was removed.';
    }));
    share.append(toggle);
    const actions = document.createElement('td');
    const local = row.id.startsWith('ep:http://127.0.0.1');
    actions.append(
      button('Test', () => act(row, async () => {
        const result = await call('/test', 'POST', { id: row.id });
        return result.state === 'online' ? `${row.name}: online, ${result.models.length} models.` : `${row.name}: ${result.message || result.state}`;
      }), !local),
      button('Add', () => act(row, async () => {
        const result = await call('/apply', 'POST', { id: row.id });
        return result.result === 'already_present' ? 'Already present on both sides.' : `Added to ${result.target === 'hermes' ? 'Hermes' : 'Odysseus'}. If it needs a key, enter it there.`;
      }), !row.sync.startsWith('pending_')),
    );
    if (row.key_copy === 'available') {
      actions.append(button('Copy key to Odysseus', () => {
        const ok = window.confirm(`Copy the ${row.name} API key from Hermes into Odysseus?\n\n` +
          'The Odysseus server copies it directly; it is never shown on this page. Hermes keeps its own copy. ' +
          'Odysseus stores it encrypted and anything that can use Odysseus model endpoints will be able to use this key.');
        if (!ok) return;
        act(row, async () => {
          const result = await call('/copy-key', 'POST', { id: row.id, confirm: true });
          return result.result === 'created' ? `${row.name} was added to Odysseus with the copied key.` : `The key was added to the existing ${row.name} endpoint in Odysseus.`;
        });
      }));
    } else if (row.key_copy === 'already') {
      actions.append(Object.assign(document.createElement('small'), { textContent: 'Odysseus has a key' }));
    }
    tr.append(name, cell(SOURCE[row.source] || row.source), modelsCell(row),
      appCell(row.status.odysseus), appCell(row.status.hermes), cell(SYNC[row.sync] || row.sync, 'sync ' + row.sync), share, actions);
    body.append(tr);
  }
  const hermes = data.applications.hermes;
  banner.textContent = hermes === 'ready' ? '' : hermes === 'requires_authentication'
    ? 'Hermes asks for a login, so its providers cannot be read here.' : 'Hermes is not reachable. Odysseus providers are shown; nothing can be added to Hermes.';
}

let loading = null;
async function load(refresh) {
  if (loading && !refresh) return loading;                 // one catalogue read at a time; a manual refresh (a POST) is always honoured
  const run = (async () => {
    try { render(await call(refresh ? '/refresh' : '', refresh ? 'POST' : 'GET')); }
    catch (error) {
      // Rows already on screen stay (render() is only called with a good answer); say what failed and offer a retry. A sign-in problem is not retried.
      const had = body.children.length > 0;
      banner.replaceChildren(`${error.message}${had ? ' Showing the last loaded list.' : ''} `);
      if (error.kind !== 'unauthorized') {
        const button = Object.assign(document.createElement('button'), { type: 'button', textContent: 'Retry' });
        button.addEventListener('click', () => load(false));
        banner.append(button);
      }
    }
  })();
  if (!refresh) loading = run;
  try { await run; } finally { if (loading === run) loading = null; }
}

document.getElementById('refresh').addEventListener('click', () => load(true));
document.getElementById('apply-all').addEventListener('click', async () => {
  banner.textContent = 'Working…';
  try {
    const { results } = await call('/apply-all', 'POST');
    banner.textContent = results.length ? `${results.filter(r => r.result === 'added' || r.result === 'created').length} of ${results.length} added.` : 'Nothing is waiting.';
  } catch (error) { banner.textContent = error.message; }
  await load(false);
});
load(false);
