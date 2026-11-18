// Shared plumbing for the model-default cards (Default Chat, Utility, Research):
// where a card reads and writes its stored choice, its load/Retry state and its
// status message. Bodies are lifted from settings.js.

import { createModelDefaultsStore } from './modelDefaults.js';
import { personalModelDefaults } from './access.js';

/**
 * The model-default plumbing for one Settings instance. `resolveAccess` decides
 * which store a caller gets; `catalog` supplies the endpoints offered to them.
 */
export function createModelDefaultCards({ resolveAccess, catalog }) {
  let _modelDefaultsStorePromise = null;

  /**
   * The model-defaults store for this caller. Which store (the caller's own
   * preferences or the server-wide settings) depends on who the caller is, so
   * none is chosen until that is known: this rejects instead, and the cards
   * stay read-only with Retry.
   */
  function getModelDefaultsStore() {
    if (!_modelDefaultsStorePromise) {
      _modelDefaultsStorePromise = resolveAccess().then(function(access) {
        return createModelDefaultsStore({
          personal: personalModelDefaults(access),
          clearOwnOverrides: Boolean(access.authEnabled && access.canManage),
        });
      });
      _modelDefaultsStorePromise.catch(function() { _modelDefaultsStorePromise = null; });
    }
    return _modelDefaultsStorePromise;
  }

  /**
   * Where a model-default card reads and writes, resolved on each load until it
   * succeeds: the store for this caller (none while access is unknown) and,
   * once, the endpoints offered to them. A Retry therefore recovers from a
   * failed status lookup without binding anything again.
   */
  function createSource(label, onEndpoints) {
    var store = null;
    var endpointsLoaded = false;
    return {
      store: function() { return store; },
      load: async function() {
        if (!store) store = await getModelDefaultsStore();
        if (!endpointsLoaded) {
          try {
            var cached = typeof catalog.getLastModelDefaultEndpoints === 'function' ? catalog.getLastModelDefaultEndpoints() : null;
            var endpoints = cached || await catalog.fetchModelDefaultEndpoints();
            onEndpoints(endpoints, store);
            endpointsLoaded = true;
          } catch (e) { console.warn('Failed to load endpoints for ' + label, e); }
        }
        return store.load();
      },
    };
  }

  return { createSource, createLoader: createModelDefaultLoader, showMessage: showCardMessage };
}

/**
 * Load state for a model-default card. The card's controls stay disabled
 * until its stored values load, so a save can never overwrite values the form
 * never showed; a failed load offers Retry. `run()` is also how a card shows
 * the stored values again after a failed save. Controls a card renders itself
 * (fallback rows) follow through `onDisabledChange`.
 */
function createModelDefaultLoader(opts) {
  var loaded = false;
  var retryBtn = null;

  function setControlsDisabled(disabled) {
    opts.controls().forEach(function(control) { if (control) control.disabled = disabled; });
    if (typeof opts.onDisabledChange === 'function') opts.onDisabledChange(disabled);
  }

  function clearFailure() {
    if (!retryBtn) return;
    retryBtn.remove();
    retryBtn = null;
    if (opts.msgEl) opts.msgEl.textContent = '';
  }

  function showFailure() {
    if (!opts.msgEl) return;
    opts.msgEl.textContent = 'Could not load the saved choice. ';
    opts.msgEl.style.color = 'var(--red)';
    retryBtn = document.createElement('button');
    retryBtn.type = 'button';
    retryBtn.className = 'settings-fallback-add settings-model-default-retry';
    retryBtn.textContent = 'Retry';
    retryBtn.addEventListener('click', async function() {
      var button = retryBtn;
      button.disabled = true;
      var ok = await run();
      if (!ok && button.isConnected) button.disabled = false;
      else if (ok && opts.controls()[0]) opts.controls()[0].focus();
    });
    opts.msgEl.appendChild(retryBtn);
  }

  async function run() {
    try {
      var stored = await opts.load();
      opts.apply(stored);
      clearFailure();
      loaded = true;
      setControlsDisabled(false);
      return true;
    } catch (error) {
      console.warn('Failed to load ' + opts.label, error);
      loaded = false;
      setControlsDisabled(true);
      clearFailure();
      showFailure();
      return false;
    }
  }

  setControlsDisabled(true);
  return { run: run, isLoaded: function() { return loaded; } };
}

function showCardMessage(msgEl, text, failed) {
  if (!msgEl) return;
  msgEl.textContent = text;
  msgEl.style.color = failed ? 'var(--red)' : 'var(--fg)';
  if (!failed && text) {
    setTimeout(function() { if (msgEl.textContent === text) msgEl.textContent = ''; }, 2000);
  }
}
