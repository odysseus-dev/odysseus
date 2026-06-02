// compare/state.js — shared mutable state for compare modules
const state = {
  API_BASE: '',
  isActive: false,
  _openingSelector: false,        // prevents duplicate compare modals on rapid re-clicks
  _streaming: false,
  _blindMode: true,
  _saveOnClose: false,
  _continueChat: false,
  _timeout: 300,                   // seconds
  _finishOrder: 0,
  _paneElapsed: [],                // per-pane total ms; populated on finish so the
                                   // Fastest badge can be awarded by actual time
                                   // (sequential mode otherwise always picks pane 1)
  _selectedModels: [],             // [{model, endpoint, endpointId, name}, ...]
  _paneSessionIds: [],             // session IDs for each pane
  _paneMetrics: [],                // metrics per pane from last round
  _abortControllers: [],           // per-pane abort controllers
  _sidebarWasHidden: false,
  _compareElements: [],            // elements we added to container (for cleanup)
  _savedToggles: null,             // tool toggle states saved before compare
  _savedIndicatorDisplay: {},      // display state of toolbar indicators before compare
  _savedMode: 'chat',              // agent/chat mode saved before compare
  _hasVisibleResults: false,       // compare results still on screen after close
  _compareMode: 'chat',            // 'chat', 'agent', 'search', or 'research'
  _lastPrompt: '',                 // last prompt sent (for rematch)
  _cachedModels: [],               // cached model list for pane dropdowns
  _probed: new Set(),              // model IDs that have been successfully probed
  _cachedProviders: null,          // cached search providers for search mode
  _searchSynthModels: null,        // per-pane synthesis models for search mode
  _parallel: true,                 // true = run all panes at once, false = one at a time
  _fetchModelsCache: null,
  _fetchModelsCacheTime: 0,
  _expectedAnswer: '',             // when an eval prompt with `answer` is picked,
                                   // stream.js reads this and stamps ✓/✗ per pane
};

/** Reset transient state to defaults — useful for clean restarts. */
export function reset() {
  state._openingSelector = false;
  state._streaming = false;
  state._finishOrder = 0;
  state._paneElapsed = [];
  state._abortControllers.forEach(c => { if (c) c.abort(); });
  state._abortControllers = [];
  state._paneSessionIds = [];
  state._paneMetrics = [];
  state._compareElements = [];
  state._hasVisibleResults = false;
  state._lastPrompt = '';
  state._cachedModels = [];
  state._probed = new Set();
  state._cachedProviders = null;
  state._fetchModelsCache = null;
  state._fetchModelsCacheTime = 0;
}

export default state;
