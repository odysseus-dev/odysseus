// static/js/githubTrending.js
// GitHub Trending inline page for Odysseus.
// Renders directly in #chat-container. DB-cached with history + AI interpretation.

let _open = false;
let _currentPeriod = 'daily';
let _currentDate = null;
let _historyDates = [];
let _currentRepos = [];   // keep loaded repos for AI interpret
let _currentDataDate = null;
let _escHandler = null;
const _WRAPPER_ID = 'gh-trending-page';

const LANG_COLORS = {
  Python: '#3572A5', JavaScript: '#f1e05a', TypeScript: '#3178c6',
  Go: '#00ADD8', Rust: '#dea584', Java: '#b07219',
  'C++': '#f34b7d', C: '#555555', Swift: '#F05138',
  Kotlin: '#A97BFF', Vue: '#41b883', Shell: '#89e051',
  Ruby: '#701516', PHP: '#4F5D95', Dart: '#00B4AB',
};

const PERIOD_LABELS = { daily: '今日', weekly: '本周', monthly: '本月' };

function openPanel() {
  if (_open) return;
  _open = true;

  _injectStyles();

  const container = document.getElementById('chat-container');
  if (container) {
    Array.from(container.children).forEach(child => {
      if (child.id === _WRAPPER_ID) return;
      if (child.style.display === 'none') return;
      child.dataset.ghHidden = '1';
      child.style.display = 'none';
    });
    container.classList.add('gh-trending-active');
  }

  const wrapper = document.createElement('div');
  wrapper.id = _WRAPPER_ID;
  wrapper.className = 'gh-trending-page';
  wrapper.innerHTML = _buildHTML();
  if (container) container.appendChild(wrapper);

  _wireEvents();

  _escHandler = (e) => { if (e.key === 'Escape') closePanel(); };
  document.addEventListener('keydown', _escHandler);

  _loadHistory();
  _loadTrending();
}

function closePanel() {
  if (!_open) return;
  _open = false;

  const wrapper = document.getElementById(_WRAPPER_ID);
  if (wrapper) wrapper.remove();

  const container = document.getElementById('chat-container');
  if (container) {
    container.classList.remove('gh-trending-active');
    Array.from(container.children).forEach(child => {
      if (child.dataset.ghHidden === '1') {
        delete child.dataset.ghHidden;
        child.style.display = '';
      }
    });
  }

  if (_escHandler) {
    document.removeEventListener('keydown', _escHandler);
    _escHandler = null;
  }
  _currentDate = null;
  _currentRepos = [];
  _currentDataDate = null;
}

function isOpen() {
  return _open;
}

function _buildHTML() {
  return `
    <div class="gh-header">
      <div class="gh-header-left">
        <svg class="gh-header-icon" width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.92-.19-.42-.42-.89-.89-1.09-.31-.09-.72-.2-.14-.92.49-.49.93-.72.93-1.71 0-.95-.49-1.47-.49-2.45 0-.61.23-1.13.64-1.4-.02-.06-.14-.71.06-1.48 0 0 .52-.17 1.7.63.49-.14 1.02-.21 1.54-.21s1.04.07 1.54.21c1.18-.79 1.7-.63 1.7-.63.2.77.08 1.42.06 1.48.41.28.64.79.64 1.4 0 .98-.45 1.5-.45 2.45 0 .99.44 1.22.93 1.71.58.72.17.83-.14.92-.47.2-.7.67-.89 1.09-.16.43-.68.49-2.69.92 0 .67.01 1.3.01 1.49 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg>
        <span class="gh-header-title">GitHub 热榜</span>
      </div>
      <div class="gh-header-right">
        <div class="gh-tabs">
          <button class="gh-tab active" data-period="daily">今日</button>
          <button class="gh-tab" data-period="weekly">本周</button>
          <button class="gh-tab" data-period="monthly">本月</button>
        </div>
        <select class="gh-date-select" id="gh-date-select" title="历史日期">
          <option value="">最新</option>
        </select>
        <button class="gh-ai-btn" id="gh-ai-btn" title="AI 中文解读">AI 解读</button>
        <button class="gh-refresh-btn" id="gh-refresh-btn" title="强制刷新">刷新</button>
        <button class="gh-close-btn" id="gh-close-btn" title="关闭">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      </div>
    </div>
    <div class="gh-body" id="gh-body"></div>`;
}

function _wireEvents() {
  const wrapper = document.getElementById(_WRAPPER_ID);
  if (!wrapper) return;

  wrapper.querySelector('#gh-close-btn')?.addEventListener('click', closePanel);

  wrapper.querySelectorAll('.gh-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      wrapper.querySelector('.gh-tab.active')?.classList.remove('active');
      tab.classList.add('active');
      _currentPeriod = tab.dataset.period;
      _currentDate = null;
      const sel = wrapper.querySelector('#gh-date-select');
      if (sel) sel.value = '';
      _loadTrending();
    });
  });

  wrapper.querySelector('#gh-date-select')?.addEventListener('change', (e) => {
    _currentDate = e.target.value || null;
    _loadTrending();
  });

  wrapper.querySelector('#gh-refresh-btn')?.addEventListener('click', () => _loadTrending(true));
  wrapper.querySelector('#gh-ai-btn')?.addEventListener('click', () => _interpretRepos());
}

function _injectStyles() {
  if (document.getElementById('gh-page-styles')) return;
  const s = document.createElement('style');
  s.id = 'gh-page-styles';
  s.textContent = `
    .gh-trending-page {
      display: flex; flex-direction: column;
      height: 100%; width: 100%; overflow: hidden;
    }
    .gh-trending-active { display: flex !important; flex-direction: column !important; }
    .gh-header {
      display: flex; align-items: center; justify-content: space-between;
      padding: 10px 16px; border-bottom: 1px solid var(--border);
      flex-shrink: 0; background: var(--panel); flex-wrap: wrap; gap: 6px;
    }
    .gh-header-left { display: flex; align-items: center; gap: 8px; }
    .gh-header-icon { color: var(--text-muted); }
    .gh-header-title { font-size: 15px; font-weight: 600; color: var(--text); }
    .gh-header-right { display: flex; align-items: center; gap: 8px; }
    .gh-tabs { display: flex; gap: 0; }
    .gh-tab {
      padding: 5px 12px; font-size: 13px; color: var(--text-muted);
      cursor: pointer; background: none; border: none;
      border-bottom: 2px solid transparent; font-family: inherit;
    }
    .gh-tab:hover { color: var(--text); }
    .gh-tab.active { color: var(--text) !important; border-bottom-color: var(--accent) !important; }
    .gh-date-select {
      background: var(--border); color: var(--text); border: none;
      border-radius: 6px; padding: 4px 8px; font-size: 13px;
      cursor: pointer; font-family: inherit; max-width: 140px;
    }
    .gh-date-select:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
    .gh-ai-btn {
      background: var(--accent); color: #fff; border: none;
      border-radius: 6px; padding: 4px 12px; font-size: 13px;
      cursor: pointer; font-family: inherit; font-weight: 500;
      transition: opacity 0.15s;
    }
    .gh-ai-btn:hover { opacity: 0.85; }
    .gh-ai-btn:disabled { opacity: 0.5; cursor: not-allowed; }
    .gh-ai-btn.loading { opacity: 0.7; }
    .gh-refresh-btn {
      background: var(--border); color: var(--text); border: none;
      border-radius: 6px; padding: 4px 12px; font-size: 13px;
      cursor: pointer; font-family: inherit;
    }
    .gh-refresh-btn:hover { opacity: 0.85; }
    .gh-close-btn {
      background: none; border: none; color: var(--text-muted);
      cursor: pointer; padding: 4px; display: flex; align-items: center;
      border-radius: 4px;
    }
    .gh-close-btn:hover { color: var(--text); background: var(--border); }
    .gh-body { flex: 1; overflow-y: auto; padding: 16px 20px; }
    .gh-repo-card {
      display: flex; flex-direction: column; gap: 4px;
      padding: 14px 0; border-bottom: 1px solid var(--border);
    }
    .gh-repo-card:last-child { border-bottom: none; }
    .gh-repo-rank {
      font-size: 12px; color: var(--text-muted); font-weight: 600;
      min-width: 22px; display: inline-block;
    }
    .gh-repo-name {
      font-size: 15px; font-weight: 600; color: var(--accent);
      text-decoration: none;
    }
    .gh-repo-name:hover { text-decoration: underline; }
    .gh-repo-desc { font-size: 13px; color: var(--text-muted); margin-top: 2px; }
    .gh-repo-desc-zh { font-size: 13px; color: var(--text); margin-top: 2px; }
    .gh-repo-interpretation {
      font-size: 12px; color: var(--accent); margin-top: 3px;
      padding: 4px 8px; background: var(--border); border-radius: 4px;
      display: inline-block;
    }
    .gh-repo-meta { display: flex; gap: 14px; margin-top: 6px; flex-wrap: wrap; }
    .gh-meta-item {
      font-size: 12px; color: var(--text-muted);
      display: inline-flex; align-items: center; gap: 4px;
    }
    .gh-lang-dot {
      width: 8px; height: 8px; border-radius: 50%; display: inline-block;
    }
    .gh-loading {
      text-align: center; padding: 60px 20px; color: var(--text-muted);
    }
    .gh-spinner {
      width: 28px; height: 28px; border: 3px solid var(--border);
      border-top-color: var(--accent); border-radius: 50%;
      animation: gh-spin 0.8s linear infinite; margin: 0 auto 12px;
    }
    .gh-error { text-align: center; padding: 60px 20px; color: var(--error,#f85149); }
    .gh-retry-btn {
      margin-top: 12px; background: var(--border); color: var(--text);
      border: 1px solid var(--border); border-radius: 6px;
      padding: 6px 16px; cursor: pointer; font-size: 13px; font-family: inherit;
    }
    .gh-summary {
      color: var(--text-muted); font-size: 12px; margin-bottom: 8px;
    }
    .gh-cached-badge {
      display: inline-block; font-size: 11px; color: var(--accent);
      background: var(--border); border-radius: 4px; padding: 1px 6px;
      margin-left: 8px;
    }
    .gh-ai-status {
      text-align: center; padding: 12px; color: var(--text-muted); font-size: 13px;
      border-bottom: 1px solid var(--border);
    }
    @keyframes gh-spin { to { transform: rotate(360deg); } }
  `;
  document.head.appendChild(s);
}

async function _loadHistory() {
  try {
    const resp = await fetch('/api/github-trending/history');
    const data = await resp.json();
    _historyDates = data.dates || [];
    _populateDateSelect();
  } catch (_) { /* non-critical */ }
}

function _populateDateSelect() {
  const sel = document.getElementById('gh-date-select');
  if (!sel) return;
  const today = new Date().toISOString().slice(0, 10);
  sel.innerHTML = '<option value="">最新</option>';
  _historyDates.forEach(item => {
    if (item.date === today) return;
    const opt = document.createElement('option');
    opt.value = item.date;
    opt.textContent = item.date;
    sel.appendChild(opt);
  });
}

async function _loadTrending(force = false) {
  const body = document.getElementById('gh-body');
  if (!body) return;

  body.innerHTML = '<div class="gh-loading"><div class="gh-spinner"></div><p>正在获取热榜数据...</p></div>';

  try {
    const params = new URLSearchParams({ period: _currentPeriod });
    if (_currentDate) params.set('date', _currentDate);
    if (force && !_currentDate) params.set('force', '1');

    const resp = await fetch(`/api/github-trending/list?${params}`);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || data.error || '请求失败');

    if (force) _loadHistory();

    _currentRepos = data.repos || [];
    _currentDataDate = data.date || null;
    _renderRepos(_currentRepos, data.cached, data.date);
  } catch (e) {
    body.innerHTML = `<div class="gh-error"><p>获取失败：${e.message}</p><button class="gh-retry-btn" onclick="document.getElementById('gh-refresh-btn').click()">重试</button></div>`;
  }
}

async function _interpretRepos() {
  const btn = document.getElementById('gh-ai-btn');
  const body = document.getElementById('gh-body');
  if (!btn || !body || !_currentRepos.length) return;

  btn.disabled = true;
  btn.classList.add('loading');
  btn.textContent = '解读中...';

  // Show status at top
  const statusDiv = document.createElement('div');
  statusDiv.className = 'gh-ai-status';
  statusDiv.innerHTML = '<div class="gh-spinner" style="width:18px;height:18px;margin:0 auto 8px;border-width:2px;"></div>AI 正在解读项目...';
  body.insertBefore(statusDiv, body.firstChild);

  try {
    const params = new URLSearchParams({ period: _currentPeriod });
    if (_currentDataDate) params.set('date', _currentDataDate);

    const resp = await fetch(`/api/github-trending/interpret?${params}`, { method: 'POST' });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || '解读失败');

    _currentRepos = data.repos || _currentRepos;
    _renderRepos(_currentRepos, true, _currentDataDate);

    if (data.ai_cached) {
      // Already all cached — no new interpretation needed
    }
  } catch (e) {
    statusDiv.innerHTML = `<span style="color:var(--error,#f85149);">解读失败：${e.message}</span>`;
    setTimeout(() => statusDiv.remove(), 3000);
  } finally {
    btn.disabled = false;
    btn.classList.remove('loading');
    btn.textContent = 'AI 解读';
  }
}

function _renderRepos(repos, cached, dateStr) {
  const body = document.getElementById('gh-body');
  if (!body) return;
  if (!repos.length) {
    body.innerHTML = '<div class="gh-loading"><p>暂无数据</p></div>';
    return;
  }

  const dateLabel = dateStr || '';
  const cachedBadge = cached ? '<span class="gh-cached-badge">缓存</span>' : '';

  // Count how many have AI data
  const aiCount = repos.filter(r => r.desc_zh || r.interpretation).length;
  const aiBadge = aiCount > 0 ? `<span class="gh-cached-badge">AI ${aiCount}/${repos.length}</span>` : '';

  let html = `<p class="gh-summary">${dateLabel} ${PERIOD_LABELS[_currentPeriod]}热榜 · 共 ${repos.length} 个项目${cachedBadge}${aiBadge}</p>`;

  repos.forEach((repo, i) => {
    const dotColor = LANG_COLORS[repo.lang] || 'var(--text-muted)';

    // Original English desc
    const descHtml = repo.desc ? `<div class="gh-repo-desc">${repo.desc}</div>` : '';

    // Chinese translation
    const descZhHtml = repo.desc_zh ? `<div class="gh-repo-desc-zh">${repo.desc_zh}</div>` : '';

    // AI interpretation
    const interpHtml = repo.interpretation ? `<div class="gh-repo-interpretation">${repo.interpretation}</div>` : '';

    const langHtml = repo.lang ? `<span class="gh-meta-item"><span class="gh-lang-dot" style="background:${dotColor}"></span>${repo.lang}</span>` : '';
    const starsHtml = repo.total_stars ? `<span class="gh-meta-item">⭐ ${repo.total_stars}</span>` : '';
    const todayHtml = repo.stars_today ? `<span class="gh-meta-item">📈 +${repo.stars_today} ${PERIOD_LABELS[_currentPeriod]}</span>` : '';
    const forksHtml = repo.forks ? `<span class="gh-meta-item">🔀 ${repo.forks}</span>` : '';

    html += `
      <div class="gh-repo-card">
        <div>
          <span class="gh-repo-rank">${i + 1}.</span>
          <a class="gh-repo-name" href="${repo.url}" target="_blank" rel="noopener">${repo.name}</a>
        </div>
        ${descHtml}
        ${descZhHtml}
        ${interpHtml}
        <div class="gh-repo-meta">${langHtml}${starsHtml}${todayHtml}${forksHtml}</div>
      </div>`;
  });

  body.innerHTML = html;
}

export { openPanel, closePanel, isOpen };
export default { openPanel, closePanel, isOpen };
