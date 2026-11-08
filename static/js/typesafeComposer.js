/**
 * Structured input for TypeSafe Jev routes.
 *
 * When the selected model is served by typesafe.ai, the free-text composer is
 * replaced by a form that builds `{ state, questions }`. Question shapes:
 *   choice: { type, instructions, criteria: { <id>: <label>, ... } }
 *   score:  { type, instructions, criteria: [ <label>, ... ] }
 *   noul:   { type, instructions }
 * On submit the JSON is written into the (hidden) #message textarea as a fenced
 * block and sent through the normal chat form, so the backend path is unchanged.
 *
 * Styling reuses the app's existing classes (admin-card, settings-input,
 * settings-select, settings-row, settings-col, admin-btn-*, admin-error). The
 * only classes defined for this feature are state hooks, not component styles.
 */

const QUESTION_TYPES = ['choice', 'score', 'noul'];

const ICON_CHEVRON = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>';

let _root = null;
let _mode = 'form'; // 'form' | 'text', only meaningful on Jev routes
let _routeTypesafe = null;
let _initialized = false;

// Last answer the route check gave with a known endpoint. Kept across transient
// gaps (e.g. right after the first message, when the session list is refreshing
// and the endpoint URL is briefly unavailable) so the form does not disappear.
let _lastKnownTypesafe = false;

function _endpointIsTypesafe() {
  let url = '';
  try {
    url = window.sessionModule?.getCurrentEndpointUrl?.() || '';
  } catch (_) {
    return null;
  }
  // A pending or just-created chat may not carry its endpoint yet: use the model
  // the user last picked, which is updated on every picker selection.
  if (!url) url = window.__odysseusLastPickedRoute?.endpoint_url || '';
  if (!url) return null; // unknown, not "not Jev"
  try {
    const host = new URL(url).hostname.toLowerCase();
    return host === 'typesafe.ai' || host.endsWith('.typesafe.ai');
  } catch (_) {
    return false;
  }
}

export function isTypesafeRoute() {
  const known = _endpointIsTypesafe();
  if (known !== null) _lastKnownTypesafe = known;
  return _lastKnownTypesafe;
}

function _el(tag, className, attrs = {}) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  for (const [key, value] of Object.entries(attrs)) {
    if (key === 'text') node.textContent = value;
    else node.setAttribute(key, value);
  }
  return node;
}

function _button(className, text, title, onClick) {
  const btn = _el('button', className, { type: 'button', text });
  if (title) btn.setAttribute('aria-label', title);
  if (title) btn.title = title;
  btn.addEventListener('click', onClick);
  return btn;
}

function _input(placeholder, label) {
  return _el('input', 'settings-input', { type: 'text', placeholder, 'aria-label': label });
}

/* ── Criteria editors ──────────────────────────────────────────────── */

// choice: rows of [id] [label]
function _choiceCriteriaEditor() {
  const wrap = _el('div', 'settings-col', { 'data-kind': 'choice' });
  const add = _button('admin-btn-sm', '+ Option', 'Add option', () => addRow());

  const addRow = (id = '', label = '') => {
    const row = _el('div', 'settings-row');
    const idInput = _input('id (e.g. apple)', 'Option id');
    const labelInput = _input('label (e.g. una mela)', 'Option label');
    idInput.dataset.role = 'id';
    labelInput.dataset.role = 'label';
    idInput.value = id;
    labelInput.value = label;
    row.append(idInput, labelInput, _button('admin-btn-delete', '×', 'Remove option', () => row.remove()));
    wrap.insertBefore(row, add);
  };

  wrap.appendChild(add);
  addRow();
  addRow();
  return wrap;
}

// score: rows of [label]
function _scoreCriteriaEditor() {
  const wrap = _el('div', 'settings-col', { 'data-kind': 'score' });
  const add = _button('admin-btn-sm', '+ Level', 'Add level', () => addRow());

  const addRow = (label = '') => {
    const row = _el('div', 'settings-row');
    const labelInput = _input('level (e.g. 0 euro)', 'Score level');
    labelInput.dataset.role = 'label';
    labelInput.value = label;
    row.append(labelInput, _button('admin-btn-delete', '×', 'Remove level', () => row.remove()));
    wrap.insertBefore(row, add);
  };

  wrap.appendChild(add);
  addRow();
  addRow();
  return wrap;
}

function _readChoiceCriteria(wrap, qname) {
  const criteria = {};
  for (const row of wrap.querySelectorAll(':scope > .settings-row')) {
    const id = row.querySelector('[data-role="id"]').value.trim();
    const label = row.querySelector('[data-role="label"]').value.trim();
    if (!id && !label) continue;
    if (!id || !label) throw new Error(`"${qname}": every option needs an id and a label`);
    if (Object.prototype.hasOwnProperty.call(criteria, id)) {
      throw new Error(`"${qname}": duplicate option id "${id}"`);
    }
    criteria[id] = label;
  }
  if (Object.keys(criteria).length < 2) throw new Error(`"${qname}": choice needs at least 2 options`);
  return criteria;
}

function _readScoreCriteria(wrap, qname) {
  const levels = [];
  for (const row of wrap.querySelectorAll(':scope > .settings-row')) {
    const label = row.querySelector('[data-role="label"]').value.trim();
    if (label) levels.push(label);
  }
  if (levels.length < 2) throw new Error(`"${qname}": score needs at least 2 levels`);
  return levels;
}

/* ── Questions ─────────────────────────────────────────────────────── */

function _buildQuestionRow() {
  const card = _el('div', 'admin-card', { 'data-role': 'question' });

  const head = _el('div', 'settings-row');
  const name = _input('name (e.g. cart)', 'Question name');
  name.dataset.role = 'name';
  const type = _el('select', 'settings-select', { 'aria-label': 'Question type' });
  type.dataset.role = 'type';
  for (const value of QUESTION_TYPES) {
    type.appendChild(_el('option', '', { value, text: value }));
  }
  head.append(name, type, _button('admin-btn-delete', '×', 'Remove question', () => card.remove()));

  const instructionsRow = _el('div', 'settings-row');
  const instructions = _input('Question (instructions)', 'Question text');
  instructions.dataset.role = 'instructions';
  instructionsRow.appendChild(instructions);

  const fields = _el('div', 'settings-col');
  fields.dataset.role = 'fields';

  type.addEventListener('change', () => _renderTypeFields(fields, type.value));
  _renderTypeFields(fields, type.value);

  card.append(head, instructionsRow, fields);
  return card;
}

function _renderTypeFields(container, type) {
  container.textContent = '';
  if (type === 'choice') container.appendChild(_choiceCriteriaEditor());
  else if (type === 'score') container.appendChild(_scoreCriteriaEditor());
  // noul: no extra fields.
}

function _readQuestion(card) {
  const name = card.querySelector('[data-role="name"]').value.trim();
  const type = card.querySelector('[data-role="type"]').value;
  const instructions = card.querySelector('[data-role="instructions"]').value.trim();
  if (!name) throw new Error('Every question needs a name');
  if (!instructions) throw new Error(`"${name}": the question text is required`);

  const spec = { type, instructions };
  const criteriaWrap = card.querySelector('[data-role="fields"] > .settings-col');
  if (type === 'choice') spec.criteria = _readChoiceCriteria(criteriaWrap, name);
  else if (type === 'score') spec.criteria = _readScoreCriteria(criteriaWrap, name);
  return { name, spec };
}

function _parseState(text) {
  const raw = text.trim();
  // State is optional: an empty state is sent as an empty string, so the key stays present.
  if (!raw) return '';
  // The API accepts a string, object or array. A bare number/true/null that
  // happens to be valid JSON (e.g. "42") must stay a string.
  try {
    const parsed = JSON.parse(raw);
    if (parsed !== null && typeof parsed === 'object') return parsed;
  } catch (_) {
    // not JSON: plain text state
  }
  return raw;
}

function _buildPayload() {
  const state = _parseState(_root.querySelector('[data-role="state"]').value);

  const questions = {};
  for (const card of _root.querySelectorAll('[data-role="question"]')) {
    const { name, spec } = _readQuestion(card);
    if (Object.prototype.hasOwnProperty.call(questions, name)) {
      throw new Error(`Duplicate question name "${name}"`);
    }
    questions[name] = spec;
  }
  if (!Object.keys(questions).length) throw new Error('Add at least one question');

  return { state, questions };
}

/* ── Form plumbing ─────────────────────────────────────────────────── */

function _setError(message) {
  const box = _root.querySelector('[data-role="error"]');
  box.textContent = message || '';
  box.hidden = !message;
}

function _submit() {
  let payload;
  try {
    payload = _buildPayload();
  } catch (err) {
    _setError(err.message);
    return;
  }
  _setError('');

  const fenced = '```json\n' + JSON.stringify(payload, null, 2) + '\n```';

  const input = document.getElementById('message');
  const form = document.getElementById('chat-form');
  if (!input || !form) return;

  input.value = fenced;
  input.dispatchEvent(new Event('input'));

  if (form.requestSubmit) form.requestSubmit();
  else form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));

  _resetForm();
}

/** Clear the form back to its empty state, with one blank question. */
function _resetForm() {
  _root.querySelector('[data-role="state"]').value = '';
  const list = _root.querySelector('[data-role="question-list"]');
  list.textContent = '';
  list.appendChild(_buildQuestionRow());
  _setError('');
}

function _build() {
  const input = document.getElementById('message');
  if (!input || !input.parentNode) return null;

  const root = _el('div', 'typesafe-composer');
  root.hidden = true;
  root.classList.add('is-collapsed');

  // Header: accordion toggle on the left, free-text switch on the right.
  const head = _el('div', 'settings-row');
  const accordion = _button('admin-btn-sm', 'Jev input', 'Collapse or expand', () => {
    const collapsed = root.classList.toggle('is-collapsed');
    accordion.setAttribute('aria-expanded', String(!collapsed));
    _syncLayout();
  });
  accordion.setAttribute('aria-expanded', 'false');
  accordion.dataset.role = 'accordion';
  const chevron = _el('span', 'ts-chevron', { 'aria-hidden': 'true' });
  chevron.innerHTML = ICON_CHEVRON;
  accordion.appendChild(chevron);
  const toggle = _button('admin-btn-sm', 'Structured form', 'Switch input mode', () => {
    _setMode(_mode === 'form' ? 'text' : 'form');
  });
  toggle.dataset.role = 'toggle';
  head.append(accordion, toggle);

  const body = _el('div', 'settings-col');
  body.classList.add('ts-body');

  const stateRow = _el('div', 'settings-row');
  const state = _el('textarea', 'settings-input', {
    rows: '3',
    spellcheck: 'false',
    placeholder: 'Scene description, or a JSON object / array',
    'aria-label': 'State',
  });
  state.dataset.role = 'state';
  stateRow.appendChild(state);

  const list = _el('div', 'settings-col');
  list.dataset.role = 'question-list';

  const actions = _el('div', 'settings-row');
  const add = _button('admin-btn-sm', '+ Question', 'Add question', () => list.appendChild(_buildQuestionRow()));
  const send = _button('admin-btn-add', 'Send to Jev', 'Send to Jev', _submit);
  send.style.marginLeft = 'auto';
  actions.append(add, send);

  const error = _el('div', 'admin-error', { role: 'alert' });
  error.dataset.role = 'error';
  error.hidden = true;

  body.append(stateRow, list, error, actions);
  root.append(head, body);
  input.insertAdjacentElement('afterend', root);
  _root = root;

  // Start with one question so the form is usable at once.
  list.appendChild(_buildQuestionRow());
  return root;
}

// Bar stays visible on Jev routes in both modes, so the switch back to the form
// is always reachable. Outside Jev routes the bar is hidden entirely.
function _setMode(mode) {
  const input = document.getElementById('message');
  if (!_root || !input) return;
  _mode = mode;
  const typesafe = isTypesafeRoute();
  const formMode = typesafe && mode === 'form';
  _root.hidden = !typesafe;
  _root.classList.toggle('ts-text-mode', !formMode);
  input.style.display = formMode ? 'none' : '';
  if (!formMode) {
    input.style.height = ''; // textarea comes back at its natural size
    _setError('');
  }
  _root.querySelector('[data-role="toggle"]').textContent = formMode ? 'Free text' : 'Structured form';
  _syncLayout();
}

// Full-width single column only while the structured form is expanded; the model
// picker then moves to its own row. Collapsed or free-text states keep the default layout.
function _syncLayout() {
  const input = document.getElementById('message');
  if (!_root || !input) return;
  const expanded = !_root.hidden
    && !_root.classList.contains('ts-text-mode')
    && !_root.classList.contains('is-collapsed');
  input.parentNode?.classList.toggle('ts-layout', expanded);
}

/** Re-apply the composer mode for the current route. Safe to call repeatedly. */
/** Close the form (Jev routes only). Used when a chat or the app is opened. */
function _collapse() {
  if (!_root || !isTypesafeRoute()) return;
  _root.classList.add('is-collapsed');
  _root.querySelector('[data-role="accordion"]')?.setAttribute('aria-expanded', 'false');
  _syncLayout();
}

export function refreshTypesafeComposer() {
  if (!_root) return;
  const typesafe = isTypesafeRoute();
  if (typesafe !== _routeTypesafe) {
    // Entering or leaving a Jev route: Jev routes start in form mode.
    _routeTypesafe = typesafe;
    _mode = 'form';
    if (typesafe) _collapse();
  }
  _setMode(_mode);
}

export function initTypesafeComposer() {
  if (_initialized) return;
  _initialized = true;
  if (!_build()) {
    _initialized = false;
    return;
  }

  // Model changes update the picker label; re-check the route when it does.
  const label = document.getElementById('model-picker-label');
  if (label && typeof MutationObserver !== 'undefined') {
    new MutationObserver(() => {
      refreshTypesafeComposer();
      setTimeout(refreshTypesafeComposer, 300); // session data may land after the label
    }).observe(label, { childList: true, characterData: true, subtree: true });
  }
  window.addEventListener('focus', refreshTypesafeComposer);
  window.addEventListener('odysseus:session-changed', () => {
    refreshTypesafeComposer();
    _collapse();
  });
  refreshTypesafeComposer();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initTypesafeComposer);
} else {
  initTypesafeComposer();
}
