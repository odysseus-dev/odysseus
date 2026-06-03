const knowledgeGraph = (function() {
  'use strict';

  var _initialized = false;
  var _nodes = [];
  var _edges = [];
  var _currentFilter = 'all';
  var _searchQuery = '';

  function init() {
    if (_initialized) return;
    _initialized = true;

    var tabBtn = document.querySelector('.memory-tab[data-memory-tab="graph"]');
    if (tabBtn) {
      tabBtn.addEventListener('click', function() {
        showGraphTab();
      });
    }

    var refreshBtn = document.getElementById('graph-refresh-btn');
    if (refreshBtn) {
      refreshBtn.addEventListener('click', function() {
        loadGraphData();
      });
    }

    var searchInput = document.getElementById('graph-search');
    if (searchInput) {
      searchInput.addEventListener('input', function() {
        _searchQuery = this.value.trim().toLowerCase();
        renderNodeList();
      });
    }

    var typeFilters = document.querySelectorAll('#graph-type-filters .memory-cat-chip');
    typeFilters.forEach(function(chip) {
      chip.addEventListener('click', function() {
        typeFilters.forEach(function(c) { c.classList.remove('active'); });
        this.classList.add('active');
        _currentFilter = this.getAttribute('data-type') || 'all';
        renderNodeList();
      });
    });

    var sortSelect = document.getElementById('graph-sort');
    if (sortSelect) {
      sortSelect.addEventListener('change', function() {
        renderNodeList();
      });
    }
  }

  function showGraphTab() {
    loadGraphData();
  }

  function loadGraphData() {
    fetch('/api/knowledge-graph/nodes?limit=100')
      .then(function(r) { return r.json(); })
      .then(function(data) {
        _nodes = Array.isArray(data) ? data : [];
        updateCounts();
        renderNodeList();
      })
      .catch(function(e) {
        console.warn('Knowledge graph load failed:', e);
        _nodes = [];
        updateCounts();
        renderNodeList();
      });
  }

  function updateCounts() {
    var count = _nodes.length;
    var countEl = document.getElementById('graph-count');
    if (countEl) countEl.textContent = count;
    var countH2 = document.getElementById('graph-count-h2');
    if (countH2) countH2.textContent = count;
  }

  function renderNodeList() {
    var list = document.getElementById('graph-node-list');
    var emptyMsg = document.getElementById('graph-empty-msg');
    if (!list) return;

    var filtered = _nodes.filter(function(n) {
      if (_currentFilter !== 'all' && n.type !== _currentFilter) return false;
      if (_searchQuery) {
        var haystack = (n.content || n.id || n.type || '').toLowerCase();
        if (haystack.indexOf(_searchQuery) === -1) return false;
      }
      return true;
    });

    var sortSelect = document.getElementById('graph-sort');
    var sortBy = sortSelect ? sortSelect.value : 'recent';
    if (sortBy === 'type') {
      filtered.sort(function(a, b) { return (a.type || '').localeCompare(b.type || ''); });
    } else {
      filtered.sort(function(a, b) {
        var ta = a.updated_at || a.created_at || '';
        var tb = b.updated_at || b.created_at || '';
        return ta < tb ? 1 : ta > tb ? -1 : 0;
      });
    }

    list.innerHTML = '';

    if (filtered.length === 0) {
      if (emptyMsg) emptyMsg.style.display = 'block';
      return;
    }
    if (emptyMsg) emptyMsg.style.display = 'none';

    filtered.forEach(function(node) {
      var item = document.createElement('div');
      item.className = 'memory-item';

      var title = document.createElement('div');
      title.className = 'memory-item-title';
      title.textContent = node.content || node.id || node.type;

      var meta = document.createElement('div');
      meta.className = 'memory-item-text';
      meta.style.fontSize = '0.8em';
      meta.style.opacity = '0.6';
      meta.textContent = node.type + (node.source_system ? ' · ' + node.source_system : '');

      var actions = document.createElement('div');
      actions.className = 'memory-item-actions';

      var typeChip = document.createElement('span');
      typeChip.className = 'memory-cat-chip';
      typeChip.textContent = node.type;
      typeChip.style.marginRight = '4px';
      actions.appendChild(typeChip);

      if (node.locked) {
        var lockedChip = document.createElement('span');
        lockedChip.className = 'memory-cat-chip';
        lockedChip.textContent = 'locked';
        lockedChip.style.opacity = '0.5';
        actions.appendChild(lockedChip);
      }

      item.appendChild(title);
      item.appendChild(meta);
      item.appendChild(actions);
      list.appendChild(item);
    });
  }

  function refresh() {
    loadGraphData();
  }

  return {
    init: init,
    refresh: refresh,
    loadGraphData: loadGraphData
  };
})();

export default knowledgeGraph;