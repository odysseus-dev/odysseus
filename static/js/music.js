/**
 * Music module — local library playback (mpv on PipeWire).
 * Sidebar dock + library modal, same patterns as Tasks/Notes.
 */

import uiModule from './ui.js?v=20260916largetoolscroll1';
import * as Modals from './modalManager.js';
import { makeWindowDraggable } from './windowDrag.js';
import { topPortalZ } from './toolWindowZOrder.js';

const _svg = (paths, size = 14) => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
const ICON_NOTE = _svg('<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>');
const ICON_FOLDER = _svg('<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>');
const ICON_HEART = _svg('<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l1 1L12 21l7.8-7.6 1-1a5.5 5.5 0 0 0 0-7.8z"/>');

function _displayName(name) {
  return String(name || '').replace(/\.(flac|wav|mp3|m4a|aac|ogg|opus|aiff?|alac|wv|ape)$/i, '');
}

function _initials(name) {
  const base = _displayName(name).replace(/[_]+/g, ' ');
  const parts = base.split(/\s+/).filter((part) => /[\p{L}\p{N}]/u.test(part));
  return ((parts[0]?.[0] || '?') + (parts[1]?.[0] || '')).toUpperCase();
}

const API_BASE = window.location.origin;
let _open = false;
let _status = null;
let _browsePath = '';
let _search = '';
let _pollTimer = null;
let _uiClockTimer = null;
let _playbackAnchor = null;
let _escHandler = null;
let _prevStatus = null;
let _volumeDragging = false;
let _seekDragging = false;

window.addEventListener('pointerup', () => {
  _seekDragging = false;
  _volumeDragging = false;
});
window.addEventListener('pointercancel', () => {
  _seekDragging = false;
  _volumeDragging = false;
});
let _activeTab = 'library';
let _modalShellReady = false;
let _qualityCheck = null;
let _qualityCheckKey = '';
let _currentLyrics = null;
let _currentLyricsKey = '';
let _libraryViewMode = (typeof localStorage !== 'undefined' && localStorage.getItem('music_library_view')) || 'tree';
let _flatOffset = 0;
let _flatLimit = 100;
let _flatTotal = 0;
let _flatLoading = false;
let _flatExhausted = false;
let _librarySortMode = (typeof localStorage !== 'undefined' && localStorage.getItem('music_sort_mode')) || 'folder_asc';
let _flatAbortController = null;

const _isLocalClient = ['localhost', '127.0.0.1', '::1'].includes(window.location.hostname);
let _remoteAudio = (typeof localStorage !== 'undefined' && localStorage.getItem('odysseus_music_remote') !== null)
  ? localStorage.getItem('odysseus_music_remote') === '1'
  : !_isLocalClient;
let _remoteAudioEl = null;
let _lastStreamTrackPath = '';

function _ensureRemoteAudioElement() {
  if (_remoteAudioEl) return _remoteAudioEl;
  _remoteAudioEl = new Audio();
  _remoteAudioEl.id = 'odysseus-remote-audio-player';
  _remoteAudioEl.preload = 'auto';
  _remoteAudioEl.addEventListener('ended', () => {
    if (_remoteAudio) {
      _control('next').catch(() => {});
    }
  });
  _remoteAudioEl.addEventListener('error', (e) => {
    console.debug('Odysseus remote audio event/error:', e);
  });
  return _remoteAudioEl;
}

function _syncRemoteAudio() {
  if (!_status) return;
  const audio = _ensureRemoteAudioElement();
  if (!_remoteAudio) {
    if (!audio.paused) {
      audio.pause();
    }
    _lastStreamTrackPath = '';
    return;
  }

  const trackPath = _currentTrackPath();
  const playing = _status.state === 'playing';

  if (!trackPath) {
    if (!audio.paused) audio.pause();
    return;
  }

  if (_lastStreamTrackPath !== trackPath) {
    _lastStreamTrackPath = trackPath;
    audio.src = `${API_BASE}/api/music/stream?path=${encodeURIComponent(trackPath)}`;
    const startPos = Number(_status.position_sec || 0);
    if (startPos > 0) {
      audio.currentTime = startPos;
    }
    if (playing) {
      audio.play().catch(() => {});
    }
    return;
  }

  if (playing && audio.paused) {
    const curPos = Number(_status.position_sec || 0);
    if (Math.abs(audio.currentTime - curPos) > 3) {
      audio.currentTime = curPos;
    }
    audio.play().catch(() => {});
  } else if (!playing && !audio.paused) {
    audio.pause();
  }
}

async function _toggleRemoteAudio() {
  _remoteAudio = !_remoteAudio;
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem('odysseus_music_remote', _remoteAudio ? '1' : '0');
  }
  _updateRemoteChrome();
  _syncRemoteAudio();
  try {
    await _api('/api/music/options', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ updates: { remote_audio: _remoteAudio } }),
    });
  } catch (e) {
    console.debug('Failed updating remote_audio option:', e);
  }
}

function _updateRemoteChrome() {
  const btn = document.getElementById('music-dock-remote');
  if (btn) {
    btn.classList.toggle('active', _remoteAudio);
    btn.title = _remoteAudio
      ? 'Audio: Remoto (Tablet/Móvil) · Clic para oír en PC'
      : 'Audio: Local (PC / FiiO K7) · Clic para oír en Tablet';
    const pcSvg = btn.querySelector('.music-dock-icon-pc');
    const tabletSvg = btn.querySelector('.music-dock-icon-tablet');
    if (pcSvg && tabletSvg) {
      pcSvg.classList.toggle('hidden', _remoteAudio);
      tabletSvg.classList.toggle('hidden', !_remoteAudio);
    }
  }
}

function _esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

async function _api(path, opts) {
  const res = await fetch(`${API_BASE}${path}`, Object.assign({ credentials: 'same-origin' }, opts || {}));
  let data = null;
  try { data = await res.json(); } catch { data = null; }
  if (!res.ok) {
    const msg = (data && (data.detail || data.error || data.message)) || `HTTP ${res.status}`;
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
  }
  return data;
}

let _downloadsPollTimer = null;

function _currentTrackPath() {
  return _status?.logical_track?.path || _status?.relative_path || _status?.path || _status?.filename || '';
}

function _updateFavoriteChrome(isFav) {
  const dockBtn = document.getElementById('music-dock-favorite');
  if (dockBtn) {
    dockBtn.classList.toggle('active', !!isFav);
    dockBtn.title = isFav ? 'Remover de Favoritos' : 'Marcar como Favorita';
  }
  const modal = document.getElementById('music-modal');
  if (modal) {
    const nowBtn = modal.querySelector('[data-music-now-favorite]');
    if (nowBtn) {
      nowBtn.classList.toggle('active', !!isFav);
      nowBtn.title = isFav ? 'Remover de Favoritos' : 'Marcar como Favorita';
    }
  }
}

async function _toggleCurrentFavorite() {
  const path = _currentTrackPath();
  if (!path) return;
  try {
    const res = await _api('/api/music/favorite/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    if (_status) _status.is_favorite = !!res.is_favorite;
    _updateFavoriteChrome(!!res.is_favorite);
    uiModule?.showToast?.(res.is_favorite ? 'Añadida a Favoritos' : 'Removida de Favoritos');
    if (_open && _activeTab === 'library') {
      if (_browsePath === '__favorites__' || _browsePath === '.' || !_browsePath) {
        _openBrowse(_browsePath || '');
      }
    }
  } catch (e) {
    uiModule?.showError?.(`Favoritos: ${e.message || e}`);
  }
}

function _fmtTime(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function _stateLabel(state) {
  if (state === 'playing') return 'Reproduciendo';
  if (state === 'paused') return 'Pausa';
  if (state === 'stopped') return 'Detenido';
  return 'Listo';
}

function _progressFields(st) {
  if (!st) return { pos: 0, dur: 0 };
  const dur = (st.logical_duration_sec > 0 ? st.logical_duration_sec : st.duration_sec) || 0;
  const pos = (st.logical_duration_sec > 0 ? st.logical_position_sec : st.position_sec) || 0;
  return { pos, dur };
}

function _syncPlaybackAnchor(st) {
  if (!st) {
    _playbackAnchor = null;
    return;
  }
  const { pos, dur } = _progressFields(st);
  _playbackAnchor = {
    pos,
    dur,
    at: Date.now(),
    playing: st.state === 'playing',
  };
}

/** Posición mostrada: extrapola +1s entre polls de /api/music/status. */
function _displayProgress() {
  if (_playbackAnchor) {
    const { pos, dur, at, playing } = _playbackAnchor;
    let p = pos;
    if (playing) {
      p = pos + (Date.now() - at) / 1000;
      if (dur > 0) p = Math.min(p, dur);
    }
    return { pos: p, dur };
  }
  return _progressFields(_status);
}

function _tickProgressUi() {
  if (!_status) return;
  const { pos, dur } = _displayProgress();
  const pct = dur > 0 ? Math.max(0, Math.min(100, (pos / dur) * 100)) : 0;

  const dock = document.getElementById('sidebar-music-dock');
  if (dock) {
    const seekInput = dock.querySelector('#music-dock-seek');
    if (seekInput && !_seekDragging) seekInput.value = String(Math.round(pct));
    const timeEl = dock.querySelector('#music-dock-time-el');
    const durEl = dock.querySelector('#music-dock-time-dur');
    if (timeEl) timeEl.textContent = _fmtTime(pos);
    if (durEl) durEl.textContent = dur > 0 ? _fmtTime(dur) : '';
  }

  const modal = document.getElementById('music-modal');
  if (modal && _open) {
    const panel = modal.querySelector('[data-tab-panel="now"]');
    if (panel && !panel.classList.contains('hidden')) {
      panel.querySelectorAll('[data-music-time-pos]').forEach((el) => {
        el.textContent = _fmtTime(pos);
      });
      panel.querySelectorAll('[data-music-time-dur]').forEach((el) => {
        el.textContent = dur > 0 ? _fmtTime(dur) : '0:00';
      });
      panel.querySelectorAll('[data-music-time]').forEach((el) => {
        el.textContent = dur > 0 ? `${_fmtTime(pos)} / ${_fmtTime(dur)}` : _fmtTime(pos);
      });
      const seek = panel.querySelector('[data-music-seek]');
      if (seek && seek !== document.activeElement && !_seekDragging) {
        seek.value = String(Math.round(pct));
        seek.style.setProperty('--music-progress', `${Math.max(0, Math.min(100, pct))}%`);
      }
    }
    if (_activeTab === 'lyrics') {
      _syncLyricsHighlight();
    }
  }
}

function _startUiClock() {
  if (_uiClockTimer) return;
  _uiClockTimer = window.setInterval(_tickProgressUi, 1000);
}

function _stopUiClock() {
  if (!_uiClockTimer) return;
  window.clearInterval(_uiClockTimer);
  _uiClockTimer = null;
}

function _pctFromStatus(st) {
  const { pos, dur } = _progressFields(st);
  if (dur <= 0) return 0;
  return Math.max(0, Math.min(100, (pos / dur) * 100));
}

function _updateProgressChrome(root, st) {
  if (!root || !st) return;
  const { pos, dur } = _progressFields(st);
  const pct = _pctFromStatus(st);
  root.querySelectorAll('[data-music-seek]').forEach((input) => {
    if (input === document.activeElement && _seekDragging) return;
    input.value = String(Math.round(pct));
  });
  root.querySelectorAll('[data-music-time-pos]').forEach((el) => {
    el.textContent = _fmtTime(pos);
  });
  root.querySelectorAll('[data-music-time-dur]').forEach((el) => {
    el.textContent = dur > 0 ? _fmtTime(dur) : '0:00';
  });
  root.querySelectorAll('[data-music-time]').forEach((el) => {
    el.textContent = dur > 0 ? `${_fmtTime(pos)} / ${_fmtTime(dur)}` : _fmtTime(pos);
  });
  const chEl = root.querySelector('[data-music-chapter-label]');
  if (chEl && st.chapter_count > 0) {
    const n = (st.chapter >= 0 ? st.chapter : 0) + 1;
    chEl.textContent = `Pista ${n}/${st.chapter_count}${st.chapter_title ? ` · ${st.chapter_title}` : ''}`;
  }
}

function _bindSeekInput(input) {
  if (!input || input._musicSeekBound) return;
  input._musicSeekBound = true;
  input.addEventListener('pointerdown', () => { _seekDragging = true; });
  input.addEventListener('pointerup', () => { _seekDragging = false; });
  input.addEventListener('input', () => {
    window.clearTimeout(input._seekDebounce);
    input._seekDebounce = window.setTimeout(() => {
      const pct = Number(input.value);
      _control('seek', pct);
      if (_remoteAudio && _remoteAudioEl && _status) {
        const { dur } = _displayProgress();
        if (dur > 0) {
          _remoteAudioEl.currentTime = (pct / 100) * dur;
        }
      }
    }, 120);
  });
}

function _bindVolumeRow(root) {
  const volInput = root.querySelector('[data-music-volume]');
  const volLabel = root.querySelector('[data-music-vol-label]');
  const muteBtn = root.querySelector('[data-music-mute]');
  if (volInput && !volInput._musicVolBound) {
    volInput._musicVolBound = true;
    volInput.addEventListener('pointerdown', () => { _volumeDragging = true; });
    volInput.addEventListener('pointerup', () => { _volumeDragging = false; });
    volInput.addEventListener('input', () => {
      if (volLabel) volLabel.textContent = volInput.value;
      window.clearTimeout(volInput._volDebounce);
      volInput._volDebounce = window.setTimeout(() => {
        _control('volume', Number(volInput.value));
      }, 120);
    });
  }
  muteBtn?.addEventListener('click', () => _control(_status?.muted ? 'unmute' : 'mute'));
}

function _updateVolumeChrome(root, st) {
  if (!root || !st) return;
  const volInput = root.querySelector('[data-music-volume], #music-dock-volume');
  const volLabel = root.querySelector('[data-music-vol-label], #music-dock-vol-label');
  if (volInput && !_volumeDragging && typeof st.volume === 'number') {
    volInput.value = String(Math.round(st.volume));
  }
  if (volLabel && typeof st.volume === 'number') {
    volLabel.textContent = st.muted ? '—' : String(Math.round(st.volume));
  }
  const muteBtn = root.querySelector('[data-music-mute], #music-dock-mute');
  _updateMuteIcon(muteBtn, !!st.muted);
  if (muteBtn && muteBtn.id === 'music-dock-mute' && typeof st.volume === 'number') {
    muteBtn.title = st.muted ? 'Silenciado — clic para activar' : `Volumen ${Math.round(st.volume)}% (pasar cursor para ajustar)`;
  }
}

function _updateMuteIcon(btn, muted) {
  if (!btn) return;
  btn.classList.toggle('active', muted);
  btn.title = muted ? 'Activar sonido' : 'Silenciar';
  btn.innerHTML = muted
    ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/></svg>'
    : '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/></svg>';
}

function _qualityMeta(st) {
  const aq = st?.audio_quality || {};
  const label = aq.quality_label || '';
  if (!label) return '';
  if (_qualityCheck?.state === 'resampling') return `${label} · remuestreo`;
  return label;
}

function _renderDock() {
  const dock = document.getElementById('sidebar-music-dock');
  if (!dock || !_status) return;

  const hasTrack = !!(_status.filename || _status.title);
  const title = _status.title || (_status.filename ? _status.filename.split('/').pop() : '') || 'Biblioteca local';
  const metaParts = [];
  if (_status.artist) metaParts.push(_status.artist);
  if (_status.album) metaParts.push(_status.album);
  const qMeta = _qualityMeta(_status);
  if (qMeta) metaParts.push(qMeta);
  const meta = metaParts.join(' · ') || (hasTrack ? '' : 'Abrir biblioteca');

  const titleEl = dock.querySelector('.music-dock-title');
  const metaEl = dock.querySelector('.music-dock-meta');
  const stateDot = dock.querySelector('#music-dock-state-dot');
  if (titleEl) titleEl.textContent = title;
  if (metaEl) metaEl.textContent = meta;
  if (stateDot) {
    stateDot.title = _stateLabel(_status.state);
    stateDot.dataset.state = _status.state || 'idle';
  }

  const card = dock.querySelector('.music-dock-card');
  const playing = _status.state === 'playing';
  const paused = _status.state === 'paused';
  card?.classList.toggle('music-dock-idle', !hasTrack && !playing && !paused);
  card?.classList.toggle('music-dock-playing', playing);
  card?.classList.toggle('music-dock-paused', paused);

  dock.querySelector('#music-dock-shuffle')?.classList.toggle('active', !!_status.shuffle);
  dock.classList.toggle('music-dock-active', playing || paused);

  dock.querySelector('.music-dock-icon-play')?.classList.toggle('hidden', playing);
  dock.querySelector('.music-dock-icon-pause')?.classList.toggle('hidden', !playing);

  const seekInput = dock.querySelector('#music-dock-seek');
  const { pos, dur } = _displayProgress();
  const pct = dur > 0 ? Math.max(0, Math.min(100, (pos / dur) * 100)) : 0;
  if (seekInput && !_seekDragging) {
    seekInput.value = String(Math.round(pct));
  }
  const timeEl = dock.querySelector('#music-dock-time-el');
  const durEl = dock.querySelector('#music-dock-time-dur');
  if (timeEl) timeEl.textContent = _fmtTime(pos);
  if (durEl) durEl.textContent = dur > 0 ? _fmtTime(dur) : '';
  _updateVolumeChrome(dock, _status);
  _updateFavoriteChrome(!!_status.is_favorite);
  _updateRemoteChrome();
  _syncRemoteAudio();

  const modal = document.getElementById('music-modal');
  if (modal && _open) {
    _updateNowChrome(modal, _status);
    _updateVolumeChrome(modal, _status);
  }
}

function _syncLibraryChrome() {
  if (!_open) return;
  const toggle = document.getElementById('music-shuffle-toggle');
  if (toggle && _status) toggle.checked = !!_status.shuffle;
}

async function _maybeRefreshQualityCheck() {
  if (!_status?.mpv_running || _status.state === 'stopped' || !_status.filename) {
    _qualityCheck = null;
    _qualityCheckKey = '';
    return;
  }
  const key = `${_status.filename}|${_status.state}`;
  if (_qualityCheck && _qualityCheckKey === key) return;
  try {
    _qualityCheck = await _api('/api/music/quality-check');
    _qualityCheckKey = key;
  } catch {
    _qualityCheck = null;
    _qualityCheckKey = '';
  }
}

async function refreshStatus(skipAutoAdvance) {
  try {
    const prev = _prevStatus;
    _status = await _api('/api/music/status');
    _syncPlaybackAnchor(_status);
    await _maybeRefreshQualityCheck();
    _renderDock();
    _syncLibraryChrome();
    if (_open) _updateScopeBadge();
    window.dispatchEvent(new CustomEvent('music-status', { detail: _status }));
    _prevStatus = _status ? { ..._status } : null;

    const isActive = _status && (_status.state === 'playing' || _status.state === 'paused');
    if (isActive || _open) {
      if (!_pollTimer) _startPoll();
    } else if (!_open && _status && _status.state === 'stopped') {
      _stopPoll();
    }

    return _status;
  } catch (e) {
    console.warn('[music] status failed', e);
    return null;
  }
}

let _pollBusy = false;

function _startPoll() {
  _startUiClock();
  if (_pollTimer) return;
  const ms = _status?.music_prefs?.poll_ms || 1000;
  // Una petición de estado a la vez: si la anterior tarda más que el intervalo, no se apilan.
  _pollTimer = window.setInterval(() => {
    if (_pollBusy) return;
    _pollBusy = true;
    Promise.resolve(refreshStatus()).finally(() => { _pollBusy = false; });
  }, ms);
}

function _stopPoll() {
  _stopUiClock();
  if (!_pollTimer) return;
  window.clearInterval(_pollTimer);
  _pollTimer = null;
}

async function _control(action, value) {
  try {
    const body = { action };
    if (value != null && !Number.isNaN(value)) body.value = value;
    await _api('/api/music/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (_remoteAudio && _remoteAudioEl) {
      if (action === 'pause') {
        _remoteAudioEl.pause();
      } else if (action === 'play') {
        _remoteAudioEl.play().catch(() => {});
      } else if (action === 'play-pause') {
        if (_remoteAudioEl.paused) _remoteAudioEl.play().catch(() => {});
        else _remoteAudioEl.pause();
      }
    }
    await refreshStatus();
  } catch (e) {
    uiModule?.showError?.(`Música: ${e.message || e}`);
  }
}

async function _play(path, shuffle, opts = {}) {
  try {
    const payload = { path, shuffle: !!shuffle };
    if (opts.scope !== undefined) payload.scope = opts.scope;
    await _api('/api/music/play', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    await refreshStatus();
    _startPoll();
  } catch (e) {
    uiModule?.showError?.(`No se pudo reproducir: ${e.message || e}`);
  }
}

async function _loadBrowse(path) {
  _browsePath = path || '';
  const q = _browsePath ? `?path=${encodeURIComponent(_browsePath)}` : '';
  return _api(`/api/music/browse${q}`);
}

async function _clearScope() {
  try {
    await _api('/api/music/scope', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scope: null }),
    });
    await refreshStatus(true);
    _updateScopeBadge();
    uiModule?.showToast?.('Alcance restaurado a toda la biblioteca');
  } catch (e) {
    uiModule?.showError?.(`Error: ${e.message || e}`);
  }
}

function _updateScopeBadge() {
  const wrap = document.getElementById('music-scope-badge-wrap');
  if (!wrap) return;
  if (_status?.queue_scope) {
    wrap.innerHTML = `<span class="music-scope-chip" title="Reproduciendo solo esta carpeta">Carpeta: ${_esc(_status.queue_scope)} <button type="button" class="music-scope-clear" data-music-clear-scope title="Volver a toda la biblioteca">✕</button></span>`;
    wrap.querySelector('[data-music-clear-scope]')?.addEventListener('click', _clearScope);
  } else {
    wrap.innerHTML = '';
  }
}

function _removeFlatRow(path) {
  const row = document.querySelector(`.music-library-row-wrap[data-track-path="${CSS.escape(path)}"]`);
  if (row) {
    row.remove();
    _flatTotal = Math.max(0, _flatTotal - 1);
    _flatOffset = Math.max(0, _flatOffset - 1);
    const crumb = document.getElementById('music-library-path');
    if (crumb && _libraryViewMode === 'flat') {
      crumb.textContent = `Vista plana (${_flatOffset} de ${_flatTotal})`;
    }
  }
}

async function _openFlatView(reset = true) {
  const list = document.getElementById('music-library-list');
  if (!list) return;
  if (reset) {
    if (_flatAbortController) {
      _flatAbortController.abort();
      _flatAbortController = null;
    }
    _flatOffset = 0;
    _flatTotal = 0;
    _flatExhausted = false;
    _flatLoading = false;
    list.innerHTML = '<div class="memory-empty">Cargando pistas…</div>';
  }
  if (_flatLoading || _flatExhausted) return;
  _flatLoading = true;
  _flatAbortController = new AbortController();
  const currentSignal = _flatAbortController.signal;
  try {
    const data = await _api(`/api/music/flat?offset=${_flatOffset}&limit=${_flatLimit}&sort=${encodeURIComponent(_librarySortMode)}`, { signal: currentSignal });
    if (currentSignal.aborted) return;
    _flatTotal = data.total || 0;
    const countEl = document.getElementById('music-library-count');
    if (countEl) countEl.textContent = _flatTotal ? `${_flatTotal} pistas` : '';
    const tracks = data.tracks || [];
    if (reset && !tracks.length) {
      list.innerHTML = '<div class="memory-empty">Sin archivos de audio en la biblioteca.</div>';
      _flatLoading = false;
      return;
    }
    const htmlRows = tracks.map((track) => {
      const badge = _qualityBadgeHtml(track.audio_quality);
      const plays = Number(track.play_count || 0);
      const playsBadge = plays > 0 ? `<span class="music-library-badge music-badge-lossy" title="${plays} reproducciones">▶ ${plays}</span>` : '';
      return `<div class="music-library-row-wrap" data-track-path="${_esc(track.path)}">
        <button type="button" class="music-library-row" data-kind="file" data-path="${_esc(track.path)}" title="Reproducir pista (clic o doble clic)">
          <span class="music-library-art" aria-hidden="true">${_initials(track.name)}</span>
          <span class="music-track-main">
            <span class="music-library-name">${_esc(_displayName(track.name))}</span>
            <span class="music-library-folder">${_esc(track.folder || '')}</span>
          </span>
          <span class="music-track-badges">${playsBadge}${badge}</span>
        </button>
        <span class="music-row-actions">
          <button type="button" class="music-row-action" data-music-reveal="${_esc(track.path)}" title="Mostrar en gestor de archivos" aria-label="Mostrar carpeta"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg></button>
          <button type="button" class="music-row-action danger" data-music-delete="${_esc(track.path)}" data-music-delete-name="${_esc(track.name)}" title="Eliminar del disco" aria-label="Eliminar del disco"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button>
        </span>
      </div>`;
    }).join('');

    if (reset) {
      list.innerHTML = htmlRows;
      _bindFlatScrollListener(list);
    } else {
      list.insertAdjacentHTML('beforeend', htmlRows);
    }

    _flatOffset += tracks.length;
    if (_flatOffset >= _flatTotal || tracks.length < _flatLimit) {
      _flatExhausted = true;
    }

    _bindLibraryListActions(list);
    list.querySelectorAll('.music-library-row[data-kind="file"]').forEach((btn) => {
      if (btn._musicPlayBound) return;
      btn._musicPlayBound = true;
      const handleSelect = () => _play(btn.dataset.path, document.getElementById('music-shuffle-toggle')?.checked);
      btn.addEventListener('click', handleSelect);
    });

    const crumb = document.getElementById('music-library-path');
    if (crumb) crumb.textContent = `Vista plana (${_flatOffset} de ${_flatTotal})`;
  } catch (e) {
    if (e.name === 'AbortError') return;
    if (reset) list.innerHTML = `<div class="memory-empty">${_esc(e.message || e)}</div>`;
    uiModule?.showError?.(`Vista plana: ${e.message || e}`);
  } finally {
    _flatLoading = false;
  }
}

function _bindFlatScrollListener(list) {
  if (list._flatScrollBound) return;
  list._flatScrollBound = true;
  list.addEventListener('scroll', () => {
    if (_libraryViewMode !== 'flat' || _flatLoading || _flatExhausted) return;
    if (list.scrollTop + list.clientHeight >= list.scrollHeight - 180) {
      _openFlatView(false);
    }
  });
}

async function _confirmDeleteFromDisk(relativePath, displayName) {
  const name = displayName || relativePath.split('/').pop() || relativePath;
  const ok = await uiModule.styledConfirm(
    `¿Eliminar «${name}» (${relativePath})? Se borra del disco. No se puede deshacer.`,
    {
      title: 'Eliminar archivo de la biblioteca',
      confirmText: 'Eliminar',
      cancelText: 'Cancelar',
      danger: true,
    },
  );
  if (!ok) return false;
  try {
    await _api(`/api/music/file?path=${encodeURIComponent(relativePath)}`, { method: 'DELETE' });
    uiModule?.showToast?.('Archivo eliminado');
    window.dispatchEvent(new CustomEvent('mt-music-refresh'));
    await refreshStatus(true);
    if (_open && _activeTab === 'library') {
      if (_search && _search.trim()) {
        await _runSearch();
      } else if (_libraryViewMode === 'flat') {
        _removeFlatRow(relativePath);
      } else {
        await _openBrowse(_browsePath || '');
      }
    }
    return true;
  } catch (e) {
    uiModule?.showError?.(`No se pudo eliminar: ${e.message || e}`);
    return false;
  }
}

async function _revealPath(relativePath) {
  try {
    await _api('/api/music/reveal', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: relativePath || _browsePath || '' }),
    });
  } catch (e) {
    uiModule?.showError?.(`Carpeta: ${e.message || e}`);
  }
}

function _bindLibraryListActions(list) {
  if (!list || list._musicActionsBound) return;
  list._musicActionsBound = true;
  list.addEventListener('click', (ev) => {
    const playFolderBtn = ev.target.closest('[data-music-play-folder]');
    if (playFolderBtn) {
      ev.preventDefault();
      ev.stopPropagation();
      ev.stopImmediatePropagation();
      const folderPath = playFolderBtn.dataset.musicPlayFolder || '';
      if (folderPath) {
        const shuffleOn = !!document.getElementById('music-shuffle-toggle')?.checked;
        _play(folderPath, shuffleOn, { scope: folderPath });
      }
      return;
    }
    const delBtn = ev.target.closest('[data-music-delete]');
    if (delBtn) {
      ev.preventDefault();
      ev.stopPropagation();
      ev.stopImmediatePropagation();
      const path = delBtn.dataset.musicDelete || '';
      if (path) _confirmDeleteFromDisk(path, delBtn.dataset.musicDeleteName || '');
      return;
    }
    const revBtn = ev.target.closest('[data-music-reveal]');
    if (revBtn) {
      ev.preventDefault();
      ev.stopPropagation();
      _revealPath(revBtn.dataset.musicReveal || '');
    }
  });
}

function _qualityBadgeHtml(aq) {
  if (!aq || !aq.quality_label) return '';
  const isHiRes = !!aq.is_hires;
  const isLossless = !!aq.lossless;
  const cls = isHiRes ? 'music-badge-hires' : (isLossless ? 'music-badge-cd' : 'music-badge-lossy');
  return `<span class="music-library-badge ${cls}" title="${_esc(aq.quality_label)}">${_esc(aq.quality_label)}</span>`;
}

function _fileRowHtml(entry) {
  if (entry.is_virtual && entry.path === '__favorites__') {
    const countBadge = `<span class="music-library-badge music-badge-hires">${entry.track_count || 0} pistas</span>`;
    return `<div class="music-library-row-wrap music-row-virtual-fav" data-folder-path="${_esc(entry.path)}">
      <button type="button" class="music-row-action music-folder-play-action" data-music-play-folder="${_esc(entry.path)}" data-music-play-name="${_esc(entry.name)}" title="Reproducir Favoritos" aria-label="Reproducir Favoritos"><svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><polygon points="6 3 20 12 6 21 6 3"/></svg></button>
      <button type="button" class="music-library-row" data-kind="dir" data-path="${_esc(entry.path)}" title="Abrir Favoritos">
        <span class="music-library-kind" style="color:#e05252">${ICON_HEART}</span>
        <span class="music-track-main"><span class="music-library-name" style="font-weight:600">${_esc(entry.name)}</span></span>
        <span class="music-track-badges">${countBadge}</span>
      </button>
      <span class="music-row-actions"></span>
    </div>`;
  }
  const icon = entry.kind === 'dir' ? ICON_FOLDER : ICON_NOTE;
  if (entry.kind === 'dir') {
    return `<div class="music-library-row-wrap" data-folder-path="${_esc(entry.path)}">
      <button type="button" class="music-row-action music-folder-play-action" data-music-play-folder="${_esc(entry.path)}" data-music-play-name="${_esc(entry.name)}" title="Reproducir carpeta" aria-label="Reproducir carpeta"><svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><polygon points="6 3 20 12 6 21 6 3"/></svg></button>
      <button type="button" class="music-library-row" data-kind="dir" data-path="${_esc(entry.path)}" title="Abrir carpeta">
        <span class="music-library-kind">${icon}</span>
        <span class="music-track-main"><span class="music-library-name">${_esc(entry.name)}</span></span>
        <span class="music-track-badges"></span>
      </button>
      <span class="music-row-actions">
        <button type="button" class="music-row-action" data-music-reveal="${_esc(entry.path)}" title="Mostrar en gestor de archivos" aria-label="Mostrar carpeta"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg></button>
      </span>
    </div>`;
  }
  const badge = _qualityBadgeHtml(entry.audio_quality);
  return `<div class="music-library-row-wrap" data-track-path="${_esc(entry.path)}">
    <button type="button" class="music-library-row" data-kind="file" data-path="${_esc(entry.path)}" title="Reproducir pista (clic o doble clic)">
      <span class="music-library-art" aria-hidden="true">${_initials(entry.name)}</span>
      <span class="music-track-main">
        <span class="music-library-name">${_esc(_displayName(entry.name))}</span>
        <span class="music-library-folder">${_esc(entry.folder || '')}</span>
      </span>
      <span class="music-track-badges">${badge}</span>
    </button>
    <span class="music-row-actions">
      <button type="button" class="music-row-action" data-music-reveal="${_esc(entry.path)}" title="Mostrar en gestor de archivos" aria-label="Mostrar carpeta"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg></button>
      <button type="button" class="music-row-action danger" data-music-delete="${_esc(entry.path)}" data-music-delete-name="${_esc(entry.name)}" title="Eliminar del disco" aria-label="Eliminar del disco"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button>
    </span>
  </div>`;
}

function _renderLibraryList(data) {
  const list = document.getElementById('music-library-list');
  if (!list || !data) return;

  const rows = [];
  if (data.parent !== null && data.parent !== undefined) {
    rows.push({ kind: 'up', name: 'Subir', path: data.parent || '' });
  }
  for (const entry of (data.entries || [])) {
    rows.push(entry);
  }

  if (!rows.length) {
    list.innerHTML = '<div class="memory-empty">Sin archivos de audio en esta carpeta.</div>';
    return;
  }

  list.innerHTML = rows.map((entry) => {
    if (entry.kind === 'up') {
      return `<button type="button" class="music-library-row" data-kind="up" data-path="${_esc(entry.path)}"><span class="music-library-kind">↩</span><span class="music-library-name">Subir</span></button>`;
    }
    return _fileRowHtml(entry);
  }).join('');

  _bindLibraryListActions(list);

  list.querySelectorAll('.music-library-row').forEach((btn) => {
    const handleSelect = () => {
      const kind = btn.dataset.kind;
      const path = btn.dataset.path || '';
      if (kind === 'up' || kind === 'dir') _openBrowse(path);
      else _play(path, document.getElementById('music-shuffle-toggle')?.checked);
    };
    btn.addEventListener('click', handleSelect);
  });
}

async function _openBrowse(path) {
  const list = document.getElementById('music-library-list');
  if (list) list.innerHTML = '<div class="memory-empty">Cargando biblioteca…</div>';
  try {
    const data = await _loadBrowse(path);
    _renderLibraryList(data);
    const crumb = document.getElementById('music-library-path');
    if (crumb) {
      const isSub = data.path && data.path !== '.';
      const isFav = data.path === '__favorites__';
      const playIcon = isSub
        ? `<button type="button" class="music-row-action music-header-folder-play" data-music-play-folder="${_esc(data.path)}" title="Reproducir carpeta actual" aria-label="Reproducir carpeta"><svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="6 3 20 12 6 21 6 3"/></svg></button>`
        : '';
      const label = isFav ? '⭐ Favoritos' : (data.path === '.' ? 'Raíz' : _esc(data.path));
      crumb.innerHTML = `${playIcon}<span>${label}</span>`;
      crumb.querySelector('[data-music-play-folder]')?.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const shuffleOn = !!document.getElementById('music-shuffle-toggle')?.checked;
        _play(data.path, shuffleOn, { scope: data.path });
      });
    }
  } catch (e) {
    if (list) list.innerHTML = `<div class="memory-empty">${_esc(e.message || e)}</div>`;
    uiModule?.showError?.(`Biblioteca: ${e.message || e}`);
  }
}

async function _runSearch() {
  const q = (_search || '').trim();
  const list = document.getElementById('music-library-list');
  if (!list) return;
  if (!q) {
    await _openBrowse(_browsePath || '');
    return;
  }
  try {
    const data = await _api(`/api/music/search?q=${encodeURIComponent(q)}`);
    const results = data.results || [];
    if (!results.length) {
      list.innerHTML = '<div class="memory-empty">Sin coincidencias.</div>';
      return;
    }
    list.innerHTML = results.map((hit) => {
      const badge = _qualityBadgeHtml(hit.audio_quality);
      return `<div class="music-library-row-wrap">
        <button type="button" class="music-library-row" data-kind="file" data-path="${_esc(hit.path)}" title="Reproducir pista (clic o doble clic)"><span class="music-library-kind">♪</span><span class="music-library-name">${_esc(hit.name)}</span>${badge}<span class="music-library-folder">${_esc(hit.folder || '')}</span></button>
        <span class="music-row-actions">
          <button type="button" class="music-row-action danger" data-music-delete="${_esc(hit.path)}" data-music-delete-name="${_esc(hit.name)}" title="Eliminar del disco" aria-label="Eliminar del disco"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button>
        </span>
      </div>`;
    }).join('');
    _bindLibraryListActions(list);
    list.querySelectorAll('.music-library-row').forEach((btn) => {
      const handleSelect = () => _play(btn.dataset.path, document.getElementById('music-shuffle-toggle')?.checked);
      btn.addEventListener('click', handleSelect);
    });
    const crumb = document.getElementById('music-library-path');
    if (crumb) crumb.textContent = `Búsqueda: ${q}`;
  } catch (e) {
    uiModule?.showError?.(`Búsqueda: ${e.message || e}`);
  }
}

function _switchTab(tab) {
  _activeTab = tab;
  const modal = document.getElementById('music-modal');
  if (!modal) return;
  modal.querySelectorAll('.music-tab').forEach((btn) => {
    const on = btn.dataset.tab === tab;
    btn.setAttribute('aria-selected', on ? 'true' : 'false');
    btn.classList.toggle('active', on);
  });
  modal.querySelectorAll('.music-tab-panel').forEach((panel) => {
    panel.classList.toggle('hidden', panel.dataset.tabPanel !== tab);
  });
  if (tab === 'library') _renderLibraryListOnly();
  if (tab === 'now') _renderNowPanel();
  if (tab === 'playlists') _renderPlaylistsPanel();
  if (tab === 'lyrics') _renderLyricsPanel();
  if (tab === 'equalizer') _renderEqualizerPanel();
  if (tab === 'queue') _renderQueuePanel();
  if (tab === 'downloads') _renderDownloadsPanel();
  if (tab === 'options') _renderOptionsPanel();
}

async function _renderLyricsPanel() {
  const modal = document.getElementById('music-modal');
  const panel = modal?.querySelector('[data-tab-panel="lyrics"]');
  if (!panel) return;

  const trackKey = `${_status?.artist || ''}::${_status?.title || _status?.filename || ''}`;
  if (_currentLyricsKey === trackKey && _currentLyrics) {
    _renderLyricsLines(panel, _currentLyrics);
    return;
  }

  panel.innerHTML = '<div class="admin-card music-lyrics-card"><div class="memory-empty">Buscando letras en LRCLIB…</div></div>';
  try {
    const artist = _status?.artist || '';
    const title = _status?.title || _status?.filename || '';
    const dur = _status?.duration || 0;
    const res = await _api(`/api/music/lyrics?artist=${encodeURIComponent(artist)}&title=${encodeURIComponent(title)}&duration=${dur}`);
    _currentLyrics = res;
    _currentLyricsKey = trackKey;
    _renderLyricsLines(panel, res);
  } catch (e) {
    panel.innerHTML = `<div class="admin-card music-lyrics-card"><div class="memory-empty">Error al cargar letras: ${_esc(e.message || e)}</div></div>`;
  }
}

function _renderLyricsLines(panel, res) {
  if (!res || (!res.synced && !res.plain)) {
    panel.innerHTML = `
      <div class="admin-card music-lyrics-card">
        <div class="memory-empty">Sin letras encontradas en LRCLIB para esta pista.</div>
      </div>
    `;
    return;
  }
  if (res.synced && res.lines?.length) {
    const linesHtml = res.lines.map((l, idx) =>
      `<div class="music-lyric-line" data-lyric-time="${l.time}" data-lyric-idx="${idx}">${_esc(l.text || '♪')}</div>`
    ).join('');
    panel.innerHTML = `
      <div class="admin-card music-lyrics-card" data-lyrics-scroll-box>
        <div class="music-lyrics-header" style="opacity:0.6;font-size:11px;margin-bottom:12px;">Sincronizado vía LRCLIB · Clic en un verso para saltar</div>
        ${linesHtml}
      </div>
    `;
    panel.querySelectorAll('[data-lyric-time]').forEach((el) => {
      el.addEventListener('click', () => {
        const t = Number(el.dataset.lyricTime);
        if (!isNaN(t)) _control('seek', t);
      });
    });
    _syncLyricsHighlight();
  } else {
    panel.innerHTML = `
      <div class="admin-card music-lyrics-card">
        <div style="opacity:0.6;font-size:11px;margin-bottom:12px;">Texto plano (sin marcas temporales)</div>
        <div style="white-space: pre-wrap; font-size:13px; line-height:1.7;">${_esc(res.plain)}</div>
      </div>
    `;
  }
}

function _syncLyricsHighlight() {
  const modal = document.getElementById('music-modal');
  const panel = modal?.querySelector('[data-tab-panel="lyrics"]');
  if (!panel || panel.classList.contains('hidden')) return;

  const { pos } = _displayProgress();
  const lines = panel.querySelectorAll('[data-lyric-time]');
  if (!lines.length) return;

  let activeEl = null;
  lines.forEach((el) => {
    const t = Number(el.dataset.lyricTime);
    if (pos >= t) activeEl = el;
  });

  lines.forEach((el) => el.classList.toggle('active', el === activeEl));
  if (activeEl && panel._lastActiveLyric !== activeEl) {
    panel._lastActiveLyric = activeEl;
    activeEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

async function _renderEqualizerPanel() {
  const modal = document.getElementById('music-modal');
  const panel = modal?.querySelector('[data-tab-panel="equalizer"]');
  if (!panel) return;

  panel.innerHTML = '<div class="admin-card"><div class="memory-empty">Cargando ecualizador…</div></div>';
  try {
    const eq = await _api('/api/music/equalizer');
    const freqs = eq.frequencies || [31.25, 62.5, 125, 250, 500, 1000, 2000, 4000, 8000, 16000];
    const bands = eq.bands || [0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
    const preset = eq.preset || 'flat';

    const fmtFreq = (f) => f >= 1000 ? `${f / 1000}k` : `${Math.round(f)}`;

    const colsHtml = freqs.map((f, i) => {
      const val = Number(bands[i] || 0);
      const sign = val > 0 ? `+${val.toFixed(1)}` : `${val.toFixed(1)}`;
      return `
        <div class="music-eq-col">
          <span class="music-eq-val" data-eq-val="${i}">${sign}</span>
          <input type="range" class="preset-range music-eq-slider" min="-12" max="12" step="0.5" value="${val}" data-eq-band="${i}" aria-label="Banda ${fmtFreq(f)}Hz" />
          <span class="music-eq-label">${fmtFreq(f)}Hz</span>
        </div>
      `;
    }).join('');

    const presetOptions = [
      { id: 'flat', label: 'Plano (Directo Bit-Perfect)' },
      { id: 'scarlett_monitors', label: 'Monitores Focusrite Scarlett' },
      { id: 'bass_boost', label: 'Realce de Graves' },
      { id: 'vocal', label: 'Claridad Vocal' },
      { id: 'rock', label: 'Perfil Rock / Enérgico' },
      { id: 'night', label: 'Nocturno (Bajos atenuados)' },
      { id: 'hd660s2', label: 'Sennheiser HD 660S2 (oratory1990)' },
      { id: 'custom', label: 'Personalizado' },
    ].map(p => `<option value="${p.id}" ${p.id === preset ? 'selected' : ''}>${p.label}</option>`).join('');

    panel.innerHTML = `
      <div class="admin-card">
        <div class="music-eq-controls">
          <div>
            <label for="music-eq-preset-select" style="font-weight:600;font-size:12px;margin-right:8px;">Perfil acústico:</label>
            <select id="music-eq-preset-select" class="login-select">${presetOptions}</select>
          </div>
          <button type="button" class="user-bar-btn" id="music-eq-reset-btn" title="Restablecer ecualizador a plano">Restablecer</button>
        </div>
        <div class="music-eq-container">
          ${colsHtml}
        </div>
      </div>
    `;

    const selectEl = panel.querySelector('#music-eq-preset-select');
    selectEl?.addEventListener('change', async () => {
      const p = selectEl.value;
      if (p !== 'custom') {
        await _api('/api/music/equalizer', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ preset: p }),
        });
        _renderEqualizerPanel();
      }
    });

    panel.querySelector('#music-eq-reset-btn')?.addEventListener('click', async () => {
      await _api('/api/music/equalizer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ preset: 'flat' }),
      });
      _renderEqualizerPanel();
    });

    panel.querySelectorAll('[data-eq-band]').forEach((slider) => {
      slider.addEventListener('input', () => {
        const idx = Number(slider.dataset.eqBand);
        const val = Number(slider.value);
        const valEl = panel.querySelector(`[data-eq-val="${idx}"]`);
        if (valEl) valEl.textContent = val > 0 ? `+${val.toFixed(1)}` : `${val.toFixed(1)}`;
        window.clearTimeout(slider._debounce);
        slider._debounce = window.setTimeout(async () => {
          const allBands = Array.from(panel.querySelectorAll('[data-eq-band]')).map(s => Number(s.value));
          if (selectEl) selectEl.value = 'custom';
          await _api('/api/music/equalizer', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ bands: allBands, preset: 'custom' }),
          });
        }, 150);
      });
    });
  } catch (e) {
    panel.innerHTML = `<div class="admin-card"><div class="memory-empty">Error al cargar ecualizador: ${_esc(e.message || e)}</div></div>`;
  }
}

let _lastChapterKey = '';

function _updateNowChrome(modal, st) {
  const panel = modal.querySelector('[data-tab-panel="now"]');
  if (!panel || panel.classList.contains('hidden')) return;
  const titleEl = panel.querySelector('[data-music-now-title]');
  const artistEl = panel.querySelector('[data-music-now-artist]');
  const displayTitle = _displayName(st.title || (st.filename ? st.filename.split('/').pop() : '—'));
  if (titleEl) titleEl.textContent = displayTitle;
  const artEl = panel.querySelector('[data-music-now-art]');
  if (artEl) artEl.textContent = _initials(displayTitle);
  if (artistEl) {
    const bits = [st.artist, st.album].filter(Boolean);
    artistEl.textContent = bits.join(' · ');
    artistEl.classList.toggle('hidden', !bits.length);
  }
  const qualEl = panel.querySelector('[data-music-quality]');
  if (qualEl) {
    const qLabel = _qualityMeta(st) || (st.audio_quality?.quality_label || '');
    qualEl.textContent = qLabel;
    qualEl.classList.toggle('resampling', _qualityCheck?.state === 'resampling');
    qualEl.classList.toggle('hidden', !qLabel);
  }
  const { pos } = _displayProgress();
  const forProgress = { ...st };
  if ((st.logical_duration_sec || 0) > 0) {
    forProgress.logical_position_sec = pos;
  } else {
    forProgress.position_sec = pos;
  }
  _updateProgressChrome(panel, forProgress);
  const chKey = `${st.filename}|${st.chapter_count}|${(st.chapters || []).length}`;
  if (chKey !== _lastChapterKey) {
    _lastChapterKey = chKey;
    _renderChapterList(panel, st);
  } else {
    panel.querySelectorAll('.music-chapter-row').forEach((row) => {
      row.classList.toggle('active', Number(row.dataset.chapter) === st.chapter);
    });
  }
  _updateFavoriteChrome(!!st.is_favorite);
}

function _renderChapterList(panel, st) {
  const list = panel.querySelector('[data-music-chapters]');
  if (!list) return;
  const chapters = st.chapters || [];
  if (!chapters.length) {
    list.innerHTML = '<div class="memory-empty">Sin capítulos en este archivo.</div>';
    return;
  }
  const cur = st.chapter;
  list.innerHTML = chapters.map((ch) =>
    `<button type="button" class="music-library-row music-chapter-row${cur === ch.index ? ' active' : ''}" data-chapter="${ch.index}"><span class="music-library-kind">${ch.index + 1}</span><span class="music-library-name">${_esc(ch.title)}</span><span class="music-library-folder">${_fmtTime(ch.start_sec)}</span></button>`
  ).join('');
  list.querySelectorAll('[data-chapter]').forEach((btn) => {
    btn.addEventListener('click', () => _control('chapter', Number(btn.dataset.chapter)));
  });
}

function _renderNowPanel() {
  const modal = document.getElementById('music-modal');
  const panel = modal?.querySelector('[data-tab-panel="now"]');
  if (!panel) return;
  panel.innerHTML = `
    <div class="music-now-card">
      <div class="music-now-hero">
        <div class="music-now-art" data-music-now-art aria-hidden="true">—</div>
        <div class="music-now-meta">
          <span class="music-now-eyebrow">Reproduciendo</span>
          <h2 class="music-now-title" data-music-now-title>—</h2>
          <p class="music-now-artist" data-music-now-artist></p>
          <div class="music-now-chips">
            <span class="music-quality-label hidden" data-music-quality></span>
            <span class="music-chapter-label" data-music-chapter-label></span>
          </div>
        </div>
      </div>
      <div class="music-now-seek-box">
        <input type="range" data-music-seek class="preset-range music-seek" min="0" max="100" step="0.1" value="0" aria-label="Posición" />
        <div class="music-now-time-row">
          <span data-music-time-pos class="music-dock-time">0:00</span>
          <span data-music-time-dur class="music-dock-time">0:00</span>
        </div>
      </div>
      <div class="music-dock-controls music-now-transport">
        <button type="button" class="user-bar-btn music-transport music-now-fav" data-music-now-favorite title="Marcar como favorita" aria-label="Favorito"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg></button>
        <button type="button" class="user-bar-btn music-transport" data-transport="previous" title="Anterior"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="19 20 9 12 19 4 19 20"/><line x1="5" y1="19" x2="5" y2="5"/></svg></button>
        <button type="button" class="user-bar-btn music-transport music-now-play" data-transport="play-pause" title="Play/Pausa"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg></button>
        <button type="button" class="user-bar-btn music-transport" data-transport="next" title="Siguiente"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 4 15 12 5 20 5 4"/><line x1="19" y1="5" x2="19" y2="19"/></svg></button>
      </div>
      <div class="music-dock-volume-row music-now-volume">
        <button type="button" class="user-bar-btn music-transport" data-music-mute title="Silenciar"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/></svg></button>
        <input type="range" data-music-volume class="preset-range music-dock-volume" min="0" max="100" step="1" value="100" aria-label="Volumen" />
        <span class="music-dock-vol-label" data-music-vol-label>100</span>
      </div>
      <h3 class="music-now-chapters-title">Capítulos</h3>
      <div data-music-chapters class="memory-list music-now-chapters"></div>
      <div class="music-now-delete-row hidden" data-music-now-delete-wrap>
        <button type="button" class="memory-toolbar-btn danger-btn" data-music-now-delete>Eliminar del disco</button>
      </div>
    </div>
  `;
  panel.querySelector('[data-music-now-favorite]')?.addEventListener('click', (e) => {
    e.stopPropagation();
    _toggleCurrentFavorite();
  });
  panel.querySelector('[data-music-now-delete]')?.addEventListener('click', async () => {
    const rel = _status?.logical_track?.path || _status?.relative_path || _status?.path || _status?.filename || '';
    if (!rel) return;
    const name = _status.title || rel.split('/').pop();
    await _confirmDeleteFromDisk(rel, name);
    _renderNowPanel();
  });
  panel.querySelectorAll('[data-transport]').forEach((btn) => {
    btn.addEventListener('click', () => _control(btn.dataset.transport));
  });
  _bindSeekInput(panel.querySelector('[data-music-seek]'));
  _bindVolumeRow(panel);
  if (_status) {
    const delWrap = panel.querySelector('[data-music-now-delete-wrap]');
    if (delWrap) {
      const hasTrack = !!(_status.logical_track?.path || _status.relative_path || _status.path || _status.filename);
      delWrap.classList.toggle('hidden', !hasTrack);
    }
    _updateNowChrome(modal, _status);
  }
}

async function _renderQueuePanel() {
  const modal = document.getElementById('music-modal');
  const panel = modal?.querySelector('[data-tab-panel="queue"]');
  if (!panel) return;
  panel.innerHTML = '<div class="memory-empty">Cargando cola…</div>';
  try {
    const data = await _api('/api/music/queue');
    const items = data.queue || [];
    if (!items.length) {
      const hint = data.shuffle
        ? 'Sin pistas en biblioteca o cola aún no generada. Reproduce con aleatorio activo.'
        : 'Cola vacía. Reproduce una carpeta o desactiva aleatorio para una cola fija.';
      panel.innerHTML = `<div class="admin-card"><div class="memory-empty">${_esc(hint)}</div></div>`;
      return;
    }
    const shuffleBadge = data.shuffle
      ? '<span class="music-queue-shuffle-badge">Aleatorio</span>'
      : '';
    panel.innerHTML = `
      <div class="admin-card music-queue-card">
        <div class="music-queue-header">
          <h2>Cola ${shuffleBadge}</h2>
          <button type="button" class="memory-toolbar-btn" id="music-queue-clear">Limpiar</button>
        </div>
        <div id="music-queue-list" class="memory-list"></div>
      </div>
    `;
    const list = panel.querySelector('#music-queue-list');
    list.innerHTML = items.map((item, position) => {
      const label = _displayName(String(item.label || '').split('/').pop());
      return `<div class="music-queue-item${item.index === data.queue_index ? ' active' : ''}">
        <span class="music-queue-index">${position + 1}</span>
        <span class="music-queue-name">${_esc(label)}</span>
        <span class="music-queue-actions">
          <button type="button" class="music-row-action" data-queue-jump="${item.index}" title="Ir a esta pista" aria-label="Ir a esta pista"><svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><polygon points="6 3 20 12 6 21 6 3"/></svg></button>
          <button type="button" class="music-row-action danger" data-queue-remove="${item.index}" title="Quitar de la cola" aria-label="Quitar de la cola"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg></button>
        </span>
      </div>`;
    }).join('');
    list.querySelectorAll('[data-queue-jump]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        await _api('/api/music/queue/jump', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ index: Number(btn.dataset.queueJump) }) });
        await refreshStatus();
        _renderQueuePanel();
      });
    });
    list.querySelectorAll('[data-queue-remove]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        await _api('/api/music/queue/remove', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ index: Number(btn.dataset.queueRemove) }) });
        _renderQueuePanel();
      });
    });
    panel.querySelector('#music-queue-clear')?.addEventListener('click', async () => {
      await _api('/api/music/queue/clear', { method: 'POST' });
      _renderQueuePanel();
    });
  } catch (e) {
    panel.innerHTML = `<div class="memory-empty">${_esc(e.message || e)}</div>`;
  }
}

async function _renderPlaylistsPanel() {
  const modal = document.getElementById('music-modal');
  const panel = modal?.querySelector('[data-tab-panel="playlists"]');
  if (!panel) return;
  panel.innerHTML = '<div class="admin-card"><div class="memory-empty">Cargando playlists…</div></div>';
  try {
    const data = await _api('/api/music/playlists');
    const rows = data.playlists || [];
    const list = rows.length ? rows.map((p) => `
      <div class="music-library-row-wrap">
        <button type="button" class="music-library-row" data-playlist-play="${_esc(p.id)}"><span class="music-library-kind">▸</span><span class="music-library-name">${_esc(p.name)}</span><span class="music-library-folder">${p.count} pistas</span></button>
        <span class="music-row-actions"><button type="button" class="music-row-action danger" data-playlist-del="${_esc(p.id)}" title="Eliminar playlist">✕</button></span>
      </div>`).join('') : '<div class="memory-empty">Aún no hay playlists.</div>';
    panel.innerHTML = `
      <div class="admin-card">
        <div style="display:flex;gap:8px;margin-bottom:10px">
          <input id="music-playlist-name" placeholder="Nueva playlist…" style="flex:1;padding:8px;border-radius:8px;border:1px solid var(--border);background:var(--bg-2);color:var(--text)">
          <button type="button" class="btn btn-primary" id="music-playlist-create">Crear</button>
        </div>
        <div class="music-playlist-list">${list}</div>
      </div>`;
    panel.querySelector('#music-playlist-create')?.addEventListener('click', async () => {
      const input = panel.querySelector('#music-playlist-name');
      const name = (input?.value || '').trim();
      if (!name) return;
      await _api('/api/music/playlists', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name})});
      _renderPlaylistsPanel();
    });
    panel.querySelectorAll('[data-playlist-play]').forEach((btn) => btn.addEventListener('click', async () => {
      try { await _api(`/api/music/playlists/${encodeURIComponent(btn.dataset.playlistPlay)}/play`, {method:'POST'}); refreshStatus().catch(() => {}); }
      catch (e) { uiModule?.showError?.(`Playlist: ${e.message || e}`); }
    }));
    panel.querySelectorAll('[data-playlist-del]').forEach((btn) => btn.addEventListener('click', async () => {
      if (!confirm('¿Eliminar esta playlist?')) return;
      await _api(`/api/music/playlists/${encodeURIComponent(btn.dataset.playlistDel)}`, {method:'DELETE'});
      _renderPlaylistsPanel();
    }));
  } catch (e) {
    panel.innerHTML = `<div class="admin-card"><div class="memory-empty">Error: ${_esc(e.message || e)}</div></div>`;
  }
}

async function _renderOptionsPanel() {
  const modal = document.getElementById('music-modal');
  const panel = modal?.querySelector('[data-tab-panel="options"]');
  if (!panel) return;
  let prefs = _status?.music_prefs || {};
  try {
    const data = await _api('/api/music/options');
    prefs = data.prefs || prefs;
  } catch { /* keep cached */ }
  panel.innerHTML = `
    <div class="admin-card music-options-card">
      <h2>Opciones</h2>
      <label class="music-opt-row"><span>Siguiente/anterior por pista lógica (capítulos)</span>
        <select id="music-opt-navigate"><option value="logical">Pista lógica</option><option value="file">Archivo</option></select></label>
      <label class="music-opt-row"><span>Aleatorio incluye capítulos como pistas</span>
        <input type="checkbox" id="music-opt-shuffle-ch" /></label>
      <label class="music-opt-row"><span>Auto-avance al terminar</span>
        <input type="checkbox" id="music-opt-auto-advance" /></label>
      <label class="music-opt-row"><span>Al abrir desde dock</span>
        <select id="music-opt-dock-tab"><option value="auto">Auto</option><option value="now">Ahora</option><option value="library">Biblioteca</option></select></label>
      <label class="music-opt-row"><span>Al arrancar Odysseus</span>
        <select id="music-opt-start-mode">
          <option value="pause_last">Pausar (no autoplay)</option>
          <option value="random_library">Aleatorio al arrancar (solo prefs shuffle)</option>
          <option value="resume_last">No pausar al arrancar Odysseus</option>
        </select></label>
      <label class="music-opt-row"><span>Aleatorio activo por defecto</span>
        <input type="checkbox" id="music-opt-shuffle-enabled" /></label>
      <label class="music-opt-row"><span>Intervalo poll (ms)</span>
        <input type="number" id="music-opt-poll" min="1000" max="10000" step="500" /></label>
      <label class="music-opt-row"><span>Ruta de biblioteca de música</span>
        <input type="text" id="music-opt-root" placeholder="data/music" style="width: 55%; font-family: monospace; font-size: 11px;" /></label>
      <label class="music-opt-row"><span>Volumen por defecto</span>
        <input type="number" id="music-opt-vol" min="0" max="100" /></label>
      <div class="music-options-actions">
        <button type="button" class="memory-toolbar-btn" id="music-opt-reindex">Reindexar capítulos</button>
        <button type="button" class="memory-toolbar-btn" id="music-opt-save">Guardar</button>
      </div>
      <p class="memory-desc music-options-agent-note">Agente: <code>music_control</code> — play, pause, next, seek, volume, chapter N.</p>
    </div>
  `;
  panel.querySelector('#music-opt-root').value = prefs.music_root || '';
  panel.querySelector('#music-opt-navigate').value = prefs.navigate_mode || 'logical';
  panel.querySelector('#music-opt-shuffle-ch').checked = prefs.shuffle_chapters !== false;
  panel.querySelector('#music-opt-auto-advance').checked = prefs.auto_advance !== false;
  panel.querySelector('#music-opt-dock-tab').value = prefs.dock_open_tab || 'auto';
  panel.querySelector('#music-opt-start-mode').value = prefs.service_start_mode || 'pause_last';
  panel.querySelector('#music-opt-shuffle-enabled').checked = prefs.shuffle_enabled !== false;
  panel.querySelector('#music-opt-poll').value = String(prefs.poll_ms || 1000);
  panel.querySelector('#music-opt-vol').value = String(prefs.default_volume ?? 100);
  panel.querySelector('#music-opt-save')?.addEventListener('click', async () => {
    const newRoot = panel.querySelector('#music-opt-root')?.value?.trim();
    const updates = {
      music_root: newRoot || undefined,
      navigate_mode: panel.querySelector('#music-opt-navigate').value,
      shuffle_chapters: panel.querySelector('#music-opt-shuffle-ch').checked,
      auto_advance: panel.querySelector('#music-opt-auto-advance').checked,
      dock_open_tab: panel.querySelector('#music-opt-dock-tab').value,
      service_start_mode: panel.querySelector('#music-opt-start-mode').value,
      shuffle_enabled: panel.querySelector('#music-opt-shuffle-enabled').checked,
      poll_ms: Number(panel.querySelector('#music-opt-poll').value) || 1000,
      default_volume: Number(panel.querySelector('#music-opt-vol').value) || 100,
    };
    await _api('/api/music/options', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ updates }) });
    await refreshStatus();
    _browsePath = '';
    _stopPoll();
    _startPoll();
    uiModule?.showToast?.('Opciones guardadas.');
  });
  panel.querySelector('#music-opt-reindex')?.addEventListener('click', async () => {
    await _api('/api/music/reindex-chapters?force=true', { method: 'POST' });
    uiModule?.showToast?.('Reindexación iniciada.');
  });
}

async function _renderDownloadsPanel() {
  const modal = document.getElementById('music-modal');
  const panel = modal?.querySelector('[data-tab-panel="downloads"]');
  if (!panel) return;

  panel.innerHTML = `
    <div class="admin-card music-downloads-card">
      <div class="music-downloads-header">
        <h2>Descargas desde YouTube y Hi-Fi</h2>
        <p class="memory-desc">Pega un enlace de YouTube o YouTube Music para descargar el audio a Unsorted y buscar automáticamente el máster FLAC Hi-Fi en Soulseek.</p>
      </div>
      <div class="music-download-form">
        <input type="url" id="music-download-input" class="memory-search-input" placeholder="https://www.youtube.com/watch?v=... o https://youtu.be/..." aria-label="Enlace de YouTube" />
        <button type="button" id="music-download-submit" class="memory-toolbar-btn primary-btn">Descargar</button>
      </div>
      <div class="music-downloads-tasks-section">
        <h3>Historial de descargas</h3>
        <div id="music-downloads-tasks-list" class="memory-list music-tasks-list">
          <div class="memory-empty">Cargando tareas…</div>
        </div>
      </div>
    </div>
  `;

  const input = panel.querySelector('#music-download-input');
  const submitBtn = panel.querySelector('#music-download-submit');

  const handleDownload = async () => {
    const url = (input?.value || '').trim();
    if (!url) return;
    submitBtn.disabled = true;
    submitBtn.textContent = 'Encolando…';
    try {
      await _api('/api/music/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
      });
      if (input) input.value = '';
      uiModule?.showToast?.('Descarga iniciada');
      await _refreshDownloadTasks(panel);
      _startDownloadsPoll(panel);
    } catch (e) {
      uiModule?.showError?.(`Descarga: ${e.message || e}`);
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Descargar';
      }
    }
  };

  submitBtn?.addEventListener('click', handleDownload);
  input?.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') {
      ev.preventDefault();
      handleDownload();
    }
  });

  await _refreshDownloadTasks(panel);
  _startDownloadsPoll(panel);
}

function _startDownloadsPoll(panel) {
  if (_downloadsPollTimer) clearInterval(_downloadsPollTimer);
  _downloadsPollTimer = setInterval(async () => {
    if (_activeTab !== 'downloads' || !_open) {
      clearInterval(_downloadsPollTimer);
      _downloadsPollTimer = null;
      return;
    }
    const hasActive = await _refreshDownloadTasks(panel);
    if (!hasActive) {
      clearInterval(_downloadsPollTimer);
      _downloadsPollTimer = null;
    }
  }, 2500);
}

async function _refreshDownloadTasks(panel) {
  const list = panel?.querySelector('#music-downloads-tasks-list');
  if (!list) return false;
  try {
    const data = await _api('/api/music/download/tasks');
    const tasks = data.tasks || [];
    if (!tasks.length) {
      list.innerHTML = '<div class="memory-empty">No hay descargas registradas.</div>';
      return false;
    }

    const stateLabels = {
      pending: 'En cola',
      downloading_youtube: 'Descargando YouTube…',
      downloaded_youtube: 'En Unsorted',
      searching_soulseek: 'Buscando FLAC Soulseek…',
      upgraded_flac: 'Reemplazado a FLAC Hi-Fi',
      completed_youtube: 'Completado (YouTube)',
      error: 'Error',
    };

    const stateClasses = {
      pending: 'music-badge-cd',
      downloading_youtube: 'music-badge-cd',
      downloaded_youtube: 'music-badge-lossy',
      searching_soulseek: 'music-badge-hires',
      upgraded_flac: 'music-badge-hires',
      completed_youtube: 'music-badge-lossy',
      error: 'music-badge-error',
    };

    list.innerHTML = tasks.map((t) => {
      const label = stateLabels[t.state] || t.state;
      const cls = stateClasses[t.state] || 'music-badge-lossy';
      const playBtn = t.destination_file
        ? `<button type="button" class="music-row-action music-task-play" data-task-play="${_esc(t.destination_file)}" title="Reproducir pista" aria-label="Reproducir pista"><svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><polygon points="6 3 20 12 6 21 6 3"/></svg></button>`
        : '';
      return `
        <div class="music-download-task-item" data-task-id="${_esc(t.id)}">
          <div class="music-task-main">
            <div class="music-task-title-row">
              <span class="music-task-title">${_esc(t.title || t.url)}</span>
              <span class="music-library-badge ${cls}">${label}</span>
            </div>
            <div class="music-task-msg">${_esc(t.message || '')}</div>
          </div>
          <div class="music-task-actions">${playBtn}</div>
        </div>
      `;
    }).join('');

    list.querySelectorAll('[data-task-play]').forEach((btn) => {
      btn.addEventListener('click', () => {
        _play(btn.dataset.taskPlay, false);
      });
    });

    return tasks.some((t) => ['pending', 'downloading_youtube', 'searching_soulseek'].includes(t.state));
  } catch (e) {
    return false;
  }
}

function _renderLibraryListOnly() {
  const host = document.querySelector('.music-library-host');
  if (host && !host.querySelector('#music-library-list')) {
    _renderLibraryBodyHost(host);
  } else {
    _openBrowse(_browsePath || '');
  }
}

function _dockOpenTab() {
  const pref = _status?.music_prefs?.dock_open_tab || 'auto';
  if (pref === 'library') return 'library';
  if (pref === 'now') return 'now';
  if (_status && (_status.state === 'playing' || _status.state === 'paused')) return 'now';
  return 'library';
}

function _ensureDock() {
  const existing = document.getElementById('sidebar-music-dock');
  if (existing && !existing.querySelector('#music-dock-volume-wrap')) {
    existing.remove();
  }
  if (document.getElementById('sidebar-music-dock')) return;
  const userBar = document.getElementById('sidebar-user-bar');
  if (!userBar) return;

  const dock = document.createElement('div');
  dock.id = 'sidebar-music-dock';
  dock.className = 'sidebar-music-dock';
  dock.innerHTML = `
    <div class="music-dock-main">
      <div class="music-dock-card music-dock-idle">
        <button type="button" class="music-dock-open" id="music-dock-open" title="Abrir biblioteca">
          <span class="music-dock-art" aria-hidden="true">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>
          </span>
          <span class="music-dock-copy">
            <span class="music-dock-title">Biblioteca local</span>
            <span class="music-dock-meta">Música en este PC</span>
          </span>
          <span class="music-dock-state-dot" id="music-dock-state-dot" title="Listo"></span>
        </button>
        <div class="music-dock-toolbar">
          <div class="music-dock-transport">
            <button type="button" class="user-bar-btn music-transport music-dock-tbtn" id="music-dock-prev" title="Anterior"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="19 20 9 12 19 4 19 20"/><line x1="5" y1="19" x2="5" y2="5"/></svg></button>
            <button type="button" class="user-bar-btn music-transport music-dock-tbtn music-dock-playbtn" id="music-dock-play" title="Play/Pausa"><svg class="music-dock-icon-play" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg><svg class="music-dock-icon-pause hidden" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg></button>
            <button type="button" class="user-bar-btn music-transport music-dock-tbtn" id="music-dock-next" title="Siguiente"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 4 15 12 5 20 5 4"/><line x1="19" y1="5" x2="19" y2="19"/></svg></button>
          </div>
          <div class="music-dock-toolbar-end">
            <button type="button" class="user-bar-btn music-transport music-dock-tbtn music-dock-fav" id="music-dock-favorite" title="Marcar como favorita" aria-label="Favorito"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg></button>
            <button type="button" class="user-bar-btn music-transport music-dock-tbtn" id="music-dock-shuffle" title="Aleatorio biblioteca"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/><polyline points="21 16 21 21 16 21"/></svg></button>
            <button type="button" class="user-bar-btn music-transport music-dock-tbtn music-dock-remote" id="music-dock-remote" title="Audio: Local (PC) · Clic para oír en Tablet" aria-label="Cambiar salida de audio">
              <svg class="music-dock-icon-pc" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
              <svg class="music-dock-icon-tablet hidden" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="2" width="16" height="20" rx="2" ry="2"/><line x1="12" y1="18" x2="12.01" y2="18"/></svg>
            </button>
            <div class="music-dock-volume-wrap" id="music-dock-volume-wrap">
              <button type="button" class="user-bar-btn music-transport music-dock-tbtn music-dock-mute" id="music-dock-mute" title="Volumen 100%"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/></svg></button>
              <div class="music-dock-volume-pop" id="music-dock-volume-pop">
                <input type="range" id="music-dock-volume" class="music-dock-volume" min="0" max="100" step="1" value="100" aria-label="Volumen" />
                <span class="music-dock-vol-label" id="music-dock-vol-label">100</span>
              </div>
            </div>
          </div>
        </div>
        <div class="music-dock-progress">
          <input type="range" id="music-dock-seek" class="music-dock-seek" min="0" max="100" step="0.1" value="0" aria-label="Posición" />
          <div class="music-dock-time-row">
            <span class="music-dock-time" id="music-dock-time-el">0:00</span>
            <span class="music-dock-time" id="music-dock-time-dur"></span>
          </div>
        </div>
      </div>
    </div>
  `;
  userBar.parentNode.insertBefore(dock, userBar);

  dock.querySelector('#music-dock-open')?.addEventListener('click', () => openMusic(_dockOpenTab()));
  dock.querySelector('#music-dock-favorite')?.addEventListener('click', (e) => { e.stopPropagation(); _toggleCurrentFavorite(); });
  dock.querySelector('#music-dock-remote')?.addEventListener('click', (e) => { e.stopPropagation(); _toggleRemoteAudio(); });
  dock.querySelector('#music-dock-play')?.addEventListener('click', (e) => { e.stopPropagation(); _control('play-pause'); });
  dock.querySelector('#music-dock-next')?.addEventListener('click', (e) => { e.stopPropagation(); _control('next'); });
  dock.querySelector('#music-dock-prev')?.addEventListener('click', (e) => { e.stopPropagation(); _control('previous'); });
  dock.querySelector('#music-dock-shuffle')?.addEventListener('click', (e) => {
    e.stopPropagation();
    const btn = e.currentTarget;
    _control(btn.classList.contains('active') ? 'shuffle_off' : 'shuffle_on');
  });

  const volWrap = dock.querySelector('#music-dock-volume-wrap');
  const volPop = dock.querySelector('#music-dock-volume-pop');
  const volInput = dock.querySelector('#music-dock-volume');
  const setVolOpen = (open) => {
    volWrap?.classList.toggle('music-dock-volume-open', open);
  };
  volWrap?.addEventListener('pointerenter', () => setVolOpen(true));
  volWrap?.addEventListener('pointerleave', () => {
    if (!_volumeDragging) setVolOpen(false);
  });
  volPop?.addEventListener('focusin', () => setVolOpen(true));
  volPop?.addEventListener('focusout', () => {
    window.setTimeout(() => {
      if (!volWrap?.contains(document.activeElement) && !_volumeDragging) setVolOpen(false);
    }, 0);
  });
  if (volInput) {
    volInput.addEventListener('pointerdown', () => { _volumeDragging = true; setVolOpen(true); });
    volInput.addEventListener('pointerup', () => {
      _volumeDragging = false;
    });
    volInput.addEventListener('input', () => {
      const label = dock.querySelector('#music-dock-vol-label');
      if (label) label.textContent = volInput.value;
      window.clearTimeout(volInput._volDebounce);
      volInput._volDebounce = window.setTimeout(() => {
        _control('volume', Number(volInput.value));
      }, 120);
    });
  }
  dock.querySelector('#music-dock-mute')?.addEventListener('click', (e) => {
    e.stopPropagation();
    _control(_status?.muted ? 'unmute' : 'mute');
  });
  _bindSeekInput(dock.querySelector('#music-dock-seek'));
}

export function openMusic(initialTab) {
  _startPoll();
  if (_open) {
    topPortalZ(document.getElementById('music-modal'));
    if (initialTab) _switchTab(initialTab);
    return;
  }
  _open = true;
  document.getElementById('tool-music-btn')?.classList.add('active');
  document.getElementById('rail-music')?.classList.add('active');

  const modal = document.createElement('div');
  modal.className = 'modal';
  modal.id = 'music-modal';
  modal.innerHTML = `
    <div class="modal-content music-modal-content">
      <div class="modal-header">
        <h4><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>Música</h4>
        <button type="button" class="modal-close" id="music-close" aria-label="Cerrar">&times;</button>
      </div>
      <div class="modal-body music-modal-body">
        <div class="memory-tabs music-tabs" role="tablist">
          <button type="button" class="memory-tab music-tab active" data-tab="library" role="tab" aria-selected="true">Biblioteca</button>
          <button type="button" class="memory-tab music-tab" data-tab="now" role="tab" aria-selected="false">Ahora</button>
          <button type="button" class="memory-tab music-tab" data-tab="lyrics" role="tab" aria-selected="false">Letras</button>
          <button type="button" class="memory-tab music-tab" data-tab="playlists" role="tab" aria-selected="false">Playlists</button>
          <button type="button" class="memory-tab music-tab" data-tab="equalizer" role="tab" aria-selected="false">Ecualizador</button>
          <button type="button" class="memory-tab music-tab" data-tab="queue" role="tab" aria-selected="false">Cola</button>
          <button type="button" class="memory-tab music-tab" data-tab="downloads" role="tab" aria-selected="false">Descargas</button>
          <button type="button" class="memory-tab music-tab" data-tab="options" role="tab" aria-selected="false">Opciones</button>
        </div>
        <div class="music-tab-panels">
          <div class="music-tab-panel" data-tab-panel="library"></div>
          <div class="music-tab-panel hidden" data-tab-panel="now"></div>
          <div class="music-tab-panel hidden" data-tab-panel="lyrics"></div>
          <div class="music-tab-panel hidden" data-tab-panel="playlists"></div>
          <div class="music-tab-panel hidden" data-tab-panel="equalizer"></div>
          <div class="music-tab-panel hidden" data-tab-panel="queue"></div>
          <div class="music-tab-panel hidden" data-tab-panel="downloads"></div>
          <div class="music-tab-panel hidden" data-tab-panel="options"></div>
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  topPortalZ(modal);

  const content = modal.querySelector('.music-modal-content');
  const header = modal.querySelector('.modal-header');
  if (content && header) {
    makeWindowDraggable(content, { content, header, skipSelector: 'button, input, select, textarea, label' });
  }

  modal.querySelector('#music-close')?.addEventListener('click', closeMusic);
  modal.addEventListener('click', (ev) => {
    if (ev.target === modal) {
      const confirmOverlay = document.getElementById('styled-confirm-overlay');
      if (confirmOverlay && !confirmOverlay.classList.contains('hidden') && getComputedStyle(confirmOverlay).display !== 'none') {
        return;
      }
      closeMusic();
    }
  });

  _escHandler = (ev) => {
    if (ev.key === 'Escape') {
      const confirmOverlay = document.getElementById('styled-confirm-overlay');
      if (confirmOverlay && !confirmOverlay.classList.contains('hidden') && getComputedStyle(confirmOverlay).display !== 'none') {
        return;
      }
      closeMusic();
    }
  };
  document.addEventListener('keydown', _escHandler);
  Modals.register('music-modal', {
    railBtnId: 'rail-music',
    sidebarBtnId: 'tool-music-btn',
    restoreFn: () => {
      if (!_open) openMusic('library');
      else _switchTab('library');
    },
    closeFn: closeMusic,
  });
  Modals.injectMinimizeButton(modal, 'music-modal');

  modal.querySelectorAll('.music-tab').forEach((btn) => {
    btn.addEventListener('click', () => _switchTab(btn.dataset.tab));
  });

  const libPanel = modal.querySelector('[data-tab-panel="library"]');
  if (libPanel) {
    const body = document.createElement('div');
    body.className = 'music-library-host';
    libPanel.appendChild(body);
    _renderLibraryBodyHost(body);
  }
  _modalShellReady = true;
  refreshStatus().then(() => {
    _switchTab(initialTab || 'library');
  });
}

function _renderLibraryBodyHost(host) {
  if (!host) return;
  host.innerHTML = `
    <div class="music-library-card">
      <div class="music-library-header">
        <div class="music-library-title">
          <h2>Biblioteca</h2>
          <span id="music-library-count" class="music-library-count"></span>
        </div>
        <div class="music-view-switcher" role="radiogroup" aria-label="Modo de vista">
          <button type="button" class="music-view-btn ${_libraryViewMode === 'tree' ? 'active' : ''}" data-view-mode="tree" title="Vista por carpetas">Carpetas</button>
          <button type="button" class="music-view-btn ${_libraryViewMode === 'flat' ? 'active' : ''}" data-view-mode="flat" title="Vista continua sin carpetas">Plana</button>
        </div>
        <span id="music-scope-badge-wrap"></span>
      </div>
      <div class="music-library-toolbar">
        <div class="music-search-wrap">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.2-3.2"/></svg>
          <input type="text" id="music-search" placeholder="Buscar en la biblioteca" value="${_esc(_search)}" />
        </div>
        <label class="music-shuffle-label">
          <input type="checkbox" id="music-shuffle-toggle" ${_status?.shuffle ? 'checked' : ''} />
          Aleatorio
        </label>
        <select id="music-sort-select" class="memory-toolbar-btn music-sort-select" title="Ordenar lista">
          <option value="folder_asc" ${_librarySortMode === 'folder_asc' ? 'selected' : ''}>Carpeta (A-Z)</option>
          <option value="name_asc" ${_librarySortMode === 'name_asc' ? 'selected' : ''}>Nombre (A-Z)</option>
          <option value="name_desc" ${_librarySortMode === 'name_desc' ? 'selected' : ''}>Nombre (Z-A)</option>
          <option value="quality_desc" ${_librarySortMode === 'quality_desc' ? 'selected' : ''}>Mayor calidad</option>
          <option value="quality_asc" ${_librarySortMode === 'quality_asc' ? 'selected' : ''}>Menor calidad</option>
          <option value="plays_desc" ${_librarySortMode === 'plays_desc' ? 'selected' : ''}>Más escuchadas</option>
          <option value="plays_asc" ${_librarySortMode === 'plays_asc' ? 'selected' : ''}>Menos escuchadas</option>
        </select>
      </div>
      <div id="music-library-path" class="music-library-path"></div>
      <div class="music-list-head" aria-hidden="true"><span></span><span>Título</span><span>Calidad</span><span></span></div>
      <div id="music-library-list" class="memory-list"></div>
    </div>
  `;
  const searchEl = host.querySelector('#music-search');
  if (searchEl) {
    searchEl.addEventListener('input', () => {
      _search = searchEl.value;
      window.clearTimeout(searchEl._musicDebounce);
      searchEl._musicDebounce = window.setTimeout(_runSearch, 250);
    });
    searchEl.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') _runSearch();
    });
  }
  host.querySelector('#music-shuffle-toggle')?.addEventListener('change', (ev) => {
    _control(ev.target.checked ? 'shuffle_on' : 'shuffle_off');
  });
  const sortSelect = host.querySelector('#music-sort-select');
  if (sortSelect) {
    sortSelect.addEventListener('change', () => {
      _librarySortMode = sortSelect.value;
      try { localStorage.setItem('music_sort_mode', _librarySortMode); } catch {}
      if (_libraryViewMode === 'flat') {
        _openFlatView(true);
      } else {
        _openBrowse(_browsePath || '');
      }
    });
  }
  host.querySelectorAll('[data-view-mode]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const mode = btn.dataset.viewMode;
      if (mode === _libraryViewMode) return;
      _libraryViewMode = mode;
      try { localStorage.setItem('music_library_view', mode); } catch {}
      host.querySelectorAll('[data-view-mode]').forEach((b) => b.classList.toggle('active', b.dataset.viewMode === mode));
      if (_libraryViewMode === 'flat') {
        _openFlatView(true);
      } else {
        _openBrowse(_browsePath || '');
      }
    });
  });

  _updateScopeBadge();
  if (_libraryViewMode === 'flat') {
    _openFlatView(true);
  } else {
    _openBrowse(_browsePath || '');
  }
}

export function closeMusic() {
  if (!_open) return;
  _open = false;
  if (_downloadsPollTimer) {
    clearInterval(_downloadsPollTimer);
    _downloadsPollTimer = null;
  }
  if (!_status || _status.state === 'stopped') {
    _stopPoll();
  }
  document.getElementById('tool-music-btn')?.classList.remove('active');
  document.getElementById('rail-music')?.classList.remove('active');
  if (_escHandler) {
    document.removeEventListener('keydown', _escHandler);
    _escHandler = null;
  }
  Modals.unregister('music-modal');
  document.getElementById('music-modal')?.remove();
}

export function toggleMusic() {
  if (_open) closeMusic();
  else openMusic('library');
}

export function isMusicOpen() { return _open; }

const musicModule = { openMusic, closeMusic, toggleMusic, isMusicOpen, refreshStatus, openPanel: openMusic, closePanel: closeMusic, isPanelOpen: isMusicOpen };
export default musicModule;
window.musicModule = musicModule;

window.addEventListener('mt-music-refresh', () => {
  refreshStatus().then(() => {
    if (_open && _activeTab === 'library') _openBrowse(_browsePath || '');
  });
});

function _onWindowFocusOrVisible() {
  if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
  refreshStatus(true).then((st) => {
    if (st && (st.state === 'playing' || st.state === 'paused' || _open)) {
      _startPoll();
    }
  });
}

if (typeof window !== 'undefined') {
  window.addEventListener('focus', _onWindowFocusOrVisible);
}

if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
      _onWindowFocusOrVisible();
    }
  });
  const boot = () => {
    _ensureDock();
    refreshStatus().then((st) => {
      if (st && (st.state === 'playing' || st.state === 'paused')) _startPoll();
    });
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
}
