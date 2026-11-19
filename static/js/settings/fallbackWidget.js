// Shared fallback-chain widget used by the model cards (Default Chat, Utility,
// Vision, ...). Extracted verbatim from settings.js.

import { byId as el } from './dom.js';
import {
  cleanForegroundFallbackCandidates,
  MAX_FOREGROUND_FALLBACKS,
  nextForegroundFallbackCandidate,
  captureFallbackWidgetFocus,
  moveForegroundFallbackCandidate,
  restoreFallbackWidgetFocus,
} from '../foregroundFallbackSettings.js';
import { sortModelIds } from '../modelSort.js';
import { postSettings as _postSettings } from './api.js';

/* Shared fallback-chain widget — mirrors the Default Chat Model fallback UI
 * for other model cards (Utility, Vision, …). Pass in the container/button
 * IDs, the endpoints list, the settings key to persist under, and the
 * model-filter (for Vision we exclude non-chat-capable models).
 */
export function bindFallbackWidget(opts) {
  var fbContainer = el(opts.containerId);
  var addBtn = el(opts.addBtnId);
  var endpointsRef = opts.endpoints;       // mutable list reference
  var modelsFilter = opts.modelsFilter || function() { return true; };
  var settingKey = opts.settingKey;
  var maxItems = Number.isInteger(opts.maxItems) ? opts.maxItems : null;
  var current = opts.cleanCandidates
    ? cleanForegroundFallbackCandidates(
        opts.initial || [],
        maxItems === null ? MAX_FOREGROUND_FALLBACKS : maxItems
      )
    : (opts.initial || []).slice();
  var allowReorder = opts.allowReorder === true;
  var controlsDisabled = opts.disabled === true;
  var supportsEffort = Boolean(opts.supportsEffort);
  var getEffortLevels = opts.getEffortLevels;
  var saveQueue = Promise.resolve();

  if (!fbContainer || !addBtn) return {
    setEndpoints: function() {},
    setInitial: function() {},
    setDisabled: function() {},
    refresh: function() {},
    getCurrent: function() { return []; },
  };

  function enabledEps() { return (endpointsRef() || []).filter(function(e) { return e.is_enabled; }); }
  function selectableEps() {
    if (!opts.requireModel) return enabledEps();
    return enabledEps().filter(function(ep) {
      return Array.isArray(ep.models) && ep.models.some(function(model) {
        return modelsFilter(model, ep);
      });
    });
  }
  function firstSelectableEndpoint() {
    return selectableEps()[0];
  }

  function firstUnusedCandidate() {
    return nextForegroundFallbackCandidate(
      selectableEps().map(function(ep) {
        return {
          id: ep.id,
          is_enabled: ep.is_enabled,
          models: sortModelIds(ep.models).filter(function(model) {
            return modelsFilter(model, ep);
          }),
        };
      }),
      current
    );
  }

  function currentForSave() {
    return opts.cleanCandidates
      ? cleanForegroundFallbackCandidates(
          current,
          maxItems === null ? MAX_FOREGROUND_FALLBACKS : maxItems
        )
      : current.filter(function(item) {
          return item && item.endpoint_id && item.model;
        }).map(function(f) {
          var item = { endpoint_id: f.endpoint_id, model: f.model };
          if (supportsEffort && typeof f.reasoning_effort !== 'undefined') {
            item.reasoning_effort = f.reasoning_effort || '';
          }
          return item;
        });
  }

  function fillModels(selectEl, epId, selected) {
    while (selectEl.options.length) selectEl.remove(0);
    var ep = (endpointsRef() || []).find(function(e) { return e.id === epId; });
    if (ep && ep.models) {
      sortModelIds(ep.models).forEach(function(m) {
        if (!modelsFilter(m, ep)) return;
        var o = document.createElement('option');
        o.value = m;
        o.textContent = m.split('/').pop();
        selectEl.appendChild(o);
      });
    }
    if (
      opts.preserveUnavailable
      && selected
      && !Array.from(selectEl.options).some(function(o) { return o.value === selected; })
    ) {
      var unavailable = document.createElement('option');
      unavailable.value = selected;
      unavailable.textContent = selected.split('/').pop() + ' (unavailable)';
      unavailable.disabled = true;
      selectEl.appendChild(unavailable);
    }
    if (selected) selectEl.value = selected;
  }

  function fillEffort(selectEl, epId, modelId, selected) {
    if (!selectEl) return;
    while (selectEl.options.length) selectEl.remove(0);
    var levels = getEffortLevels ? getEffortLevels(epId, modelId) : [];
    var base = document.createElement('option');
    base.value = '';
    base.textContent = 'Provider default';
    selectEl.appendChild(base);
    levels.forEach(function(lvl) {
      var val = typeof lvl === 'string' ? lvl : (lvl && lvl.effort);
      if (val) {
        var o = document.createElement('option');
        o.value = val.toLowerCase();
        o.textContent = val;
        selectEl.appendChild(o);
      }
    });
    var validValues = [''].concat(levels.map(function(l) { return (typeof l === 'string' ? l : l.effort).toLowerCase(); }));
    selectEl.value = validValues.indexOf((selected || '').toLowerCase()) >= 0 ? (selected || '').toLowerCase() : '';
    selectEl.style.display = levels.length > 0 ? '' : 'none';
  }

  async function persist() {
    var clean = currentForSave();
    if (typeof opts.saveList === 'function') {
      await opts.saveList(clean);
    } else {
      var body = {};
      body[settingKey] = clean;
      var response = await _postSettings(body);
      if (response && !response.ok) throw new Error('HTTP ' + response.status);
    }
    if (typeof opts.onSaved === 'function') opts.onSaved(clean);
  }

  function save() {
    saveQueue = saveQueue
      .catch(function() {})
      .then(persist)
      .catch(function(error) {
        if (typeof opts.onError === 'function') opts.onError(error);
        else console.warn('[fallback] save failed for ' + settingKey, error);
      });
    return saveQueue;
  }

  function render() {
    var focusState = captureFallbackWidgetFocus(fbContainer, document.activeElement);
    if (opts.cleanCandidates) {
      current = cleanForegroundFallbackCandidates(
        current,
        maxItems === null ? MAX_FOREGROUND_FALLBACKS : maxItems
      );
    }
    fbContainer.innerHTML = '';
    current.forEach(function(fb, idx) {
      var row = document.createElement('div');
      row.className = 'settings-fallback-row';

      var num = document.createElement('span');
      num.className = 'settings-fallback-num';
      num.textContent = (idx + 1) + '.';

      var epS = document.createElement('select');
      epS.className = 'settings-select';
      epS.dataset.fallbackFocus = 'endpoint';
      epS.disabled = controlsDisabled;
      epS.setAttribute('aria-label', 'Fallback ' + (idx + 1) + ' endpoint');
      selectableEps().forEach(function(ep) {
        var o = document.createElement('option');
        o.value = ep.id;
        o.textContent = ep.name + (ep.online ? '' : ' (offline)');
        epS.appendChild(o);
      });
      if (
        opts.preserveUnavailable
        && fb.endpoint_id
        && !Array.from(epS.options).some(function(o) { return o.value === fb.endpoint_id; })
      ) {
        var unavailable = document.createElement('option');
        unavailable.value = fb.endpoint_id;
        unavailable.textContent = 'Unavailable endpoint';
        unavailable.disabled = true;
        epS.appendChild(unavailable);
      }
      var first = firstSelectableEndpoint();
      epS.value = fb.endpoint_id || (first ? first.id : '');

      var mS = document.createElement('select');
      mS.className = 'settings-select';
      mS.dataset.fallbackFocus = 'model';
      mS.disabled = controlsDisabled;
      mS.setAttribute('aria-label', 'Fallback ' + (idx + 1) + ' model');
      fillModels(mS, epS.value, fb.model);

      fb.endpoint_id = epS.value;
      fb.model = mS.value;

      var effS = null;
      if (supportsEffort) {
        effS = document.createElement('select');
        effS.className = 'settings-select settings-fallback-effort';
        effS.disabled = controlsDisabled;
        fillEffort(effS, epS.value, mS.value, fb.reasoning_effort);
        fb.reasoning_effort = effS.value;
        effS.addEventListener('change', function() {
          fb.reasoning_effort = effS.value;
          save();
        });
      }

      epS.addEventListener('change', function() {
        fb.endpoint_id = epS.value;
        fillModels(mS, epS.value, '');
        fb.model = mS.value;
        if (effS) {
          fillEffort(effS, epS.value, mS.value, '');
          fb.reasoning_effort = effS.value;
        }
        if (opts.cleanCandidates) render();
        save();
      });
      mS.addEventListener('change', function() {
        fb.model = mS.value;
        if (effS) {
          fillEffort(effS, epS.value, mS.value, fb.reasoning_effort);
          fb.reasoning_effort = effS.value;
        }
        if (opts.cleanCandidates) render();
        save();
      });

      var rm = document.createElement('button');
      rm.type = 'button';
      rm.className = 'settings-fallback-remove';
      rm.dataset.fallbackFocus = 'remove';
      rm.disabled = controlsDisabled;
      rm.title = 'Remove fallback';
      rm.setAttribute('aria-label', 'Remove fallback ' + (idx + 1));
      rm.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>';
      rm.addEventListener('click', function() {
        current.splice(idx, 1);
        render();
        save();
      });

      row.appendChild(num);
      row.appendChild(epS);
      row.appendChild(mS);
      if (effS) row.appendChild(effS);
      if (allowReorder) {
        var actions = document.createElement('span');
        actions.className = 'settings-fallback-actions';
        [
          {
            offset: -1,
            title: 'Move fallback up',
            path: '<path d="M6 15l6-6 6 6"/>',
          },
          {
            offset: 1,
            title: 'Move fallback down',
            path: '<path d="M6 9l6 6 6-6"/>',
          },
        ].forEach(function(action) {
          var move = document.createElement('button');
          move.type = 'button';
          move.className = 'settings-fallback-remove settings-fallback-move';
          var destination = idx + action.offset;
          var inBounds = destination >= 0 && destination < current.length;
          var accessibleTitle = inBounds
            ? action.title + ' ' + (idx + 1) + ' to position ' + (destination + 1)
            : action.title + ' ' + (idx + 1)
              + (action.offset < 0 ? ' (already first)' : ' (already last)');
          move.title = accessibleTitle;
          move.setAttribute('aria-label', accessibleTitle);
          move.dataset.moveOffset = String(action.offset);
          move.dataset.fallbackFocus = action.offset < 0 ? 'move-up' : 'move-down';
          move.disabled = controlsDisabled || !inBounds;
          move.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + action.path + '</svg>';
          move.addEventListener('click', function() {
            var from = idx;
            var to = idx + action.offset;
            current = moveForegroundFallbackCandidate(current, idx, action.offset);
            render();
            var movedRow = fbContainer.children[to];
            if (movedRow) {
              var focusTarget = movedRow.querySelector(
                '.settings-fallback-move[data-move-offset="' + action.offset + '"]:not(:disabled)'
              ) || movedRow.querySelector('.settings-fallback-move:not(:disabled)')
                || movedRow.querySelector('select');
              if (focusTarget) focusTarget.focus();
            }
            if (typeof opts.onReordered === 'function') {
              opts.onReordered({ from: from, to: to, total: current.length });
            }
            save();
          });
          actions.appendChild(move);
        });
        actions.appendChild(rm);
        row.appendChild(actions);
      } else {
        row.appendChild(rm);
      }
      fbContainer.appendChild(row);
    });
    var unusedCandidate = firstUnusedCandidate();
    if (opts.manageAddDisabled) {
      addBtn.disabled = controlsDisabled || !unusedCandidate
        || (maxItems !== null && current.length >= maxItems);
    }
    if (typeof opts.onRender === 'function') {
      opts.onRender(currentForSave());
    }
    restoreFallbackWidgetFocus(fbContainer, addBtn, focusState);
  }

  addBtn.addEventListener('click', function() {
    var unusedCandidate = firstUnusedCandidate();
    if (
      (opts.manageAddDisabled && !unusedCandidate)
      || (maxItems !== null && current.length >= maxItems)
    ) return;
    var first = firstSelectableEndpoint() || enabledEps()[0];
    var nextItem = unusedCandidate
      ? Object.assign({}, unusedCandidate)
      : { endpoint_id: first ? first.id : '', model: '' };
    if (supportsEffort) {
      nextItem.reasoning_effort = '';
    }
    current.push(nextItem);
    render();
    save();
  });

  render();

  return {
    setInitial: function(list) {
      current = opts.cleanCandidates
        ? cleanForegroundFallbackCandidates(
            list,
            maxItems === null ? MAX_FOREGROUND_FALLBACKS : maxItems
          )
        : (list || []).slice();
      render();
    },
    refresh: render,
    getCurrent: currentForSave,
    setDisabled: function(disabled) {
      controlsDisabled = disabled === true;
      render();
    },
  };
}
