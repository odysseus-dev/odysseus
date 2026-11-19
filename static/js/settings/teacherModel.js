// Teacher model card. The card is currently hidden in index.html, so this
// returns early until its markup is restored. Extracted verbatim from settings.js.

import { byId as el } from './dom.js';
import {
  fillEndpointSelect as _fillEndpointSelect,
  fillModelSelect as _fillModelSelect,
} from './modelSelects.js';
import { postSettings as _postSettings } from './api.js';

/* ── Teacher Model ── */
// SOTA model called automatically when a self-hosted student model
// fails an agent-mode task. Stored as a single `teacher_model` string
// in the form `model@endpoint_name` so the backend's _resolve_model
// can dispatch directly. Master toggle is the separate
// `teacher_enabled` flag so the user can pause the feature without
// losing their endpoint+model selection.
export async function initTeacherModel({ catalog }) {
  var enabledToggle = el('set-teacherEnabledToggle');
  var epSel = el('set-teacherEpSelect');
  var modelSel = el('set-teacherModelSelect');
  var msg = el('set-teacherChatMsg');
  if (!epSel || !modelSel) return;
  var _endpoints = [];

  try {
    _endpoints = await catalog.fetchModelEndpoints();
    _fillEndpointSelect(epSel, _endpoints, epSel.value, true);
  } catch (e) { console.warn('Failed to load endpoints for teacher model', e); }

  function refreshModels(selectedModel) {
    var epId = epSel.value;
    var ep = _endpoints.find(function(e) { return e.id === epId; });
    _fillModelSelect(modelSel, ep ? ep.models : [], selectedModel, true);
  }

  // Disable / enable the endpoint+model dropdowns based on the
  // master switch. Greys them out so users see at a glance that the
  // selection is dormant.
  function syncEnabled() {
    var off = enabledToggle ? !enabledToggle.checked : true;
    // Dim the card when off as a "dormant" cue, but keep the endpoint+model
    // dropdowns INTERACTIVE — the toggle gates whether escalation runs, not
    // whether you can configure it. (Previously the config was inert when off,
    // so users couldn't pick an endpoint until they'd already enabled it.)
    var card = enabledToggle ? enabledToggle.closest('.admin-card') : null;
    if (card) card.style.opacity = off ? '0.7' : '';
    var wrap = card ? card.querySelector('.settings-col') : null;
    if (wrap) wrap.style.pointerEvents = '';
    epSel.disabled = false;
    modelSel.disabled = false;
  }

  try {
    var res = await fetch('/api/auth/settings', { credentials: 'same-origin' });
    var settings = await res.json();
    if (enabledToggle) enabledToggle.checked = !!settings.teacher_enabled;
    // teacher_model is stored as "model@endpoint_name". Split on the
    // LAST `@` so model ids that contain @ aren't mangled.
    var spec = settings.teacher_model || '';
    var savedModel = spec;
    var savedEpName = '';
    var at = spec.lastIndexOf('@');
    if (at >= 0) {
      savedModel = spec.slice(0, at);
      savedEpName = spec.slice(at + 1);
    }
    if (savedEpName) {
      var match = _endpoints.find(function(ep) {
        return ep.name && ep.name.toLowerCase().indexOf(savedEpName.toLowerCase()) >= 0;
      });
      if (match) epSel.value = match.id;
    }
    refreshModels(savedModel);
    syncEnabled();
  } catch (e) { console.warn('Failed to load teacher model settings', e); }

  async function saveTeacher() {
    try {
      var spec = '';
      if (epSel.value && modelSel.value) {
        var ep = _endpoints.find(function(e) { return e.id === epSel.value; });
        spec = ep ? (modelSel.value + '@' + ep.name) : modelSel.value;
      }
      var enabled = enabledToggle ? !!enabledToggle.checked : false;
      await _postSettings({ teacher_enabled: enabled, teacher_model: spec });
      msg.textContent = enabled ? (spec ? 'Saved' : 'Pick an endpoint + model') : 'Disabled';
      msg.style.color = enabled && !spec ? 'var(--red)' : 'var(--fg)';
      setTimeout(function() { msg.textContent = ''; }, 2000);
    } catch (e) { msg.textContent = e.status === 403 ? 'Admin access is required to change these settings.' : 'Failed to save'; msg.style.color = 'var(--red)'; }
  }

  if (enabledToggle) {
    enabledToggle.addEventListener('change', function() {
      syncEnabled();
      saveTeacher();
    });
  }
  epSel.addEventListener('change', function() { refreshModels(''); saveTeacher(); });
  modelSel.addEventListener('change', saveTeacher);

  catalog.registerAiEndpointRefresh(function(endpoints) {
    _endpoints = endpoints;
    _fillEndpointSelect(epSel, _endpoints, epSel.value, true);
    refreshModels(modelSel.value);
  });
}
