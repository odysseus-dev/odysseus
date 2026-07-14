import * as api from '../../fugassaApi.js';
import uiModule from '../../../ui.js';
import { escapeHtml } from '../screens/InventoryScreen.js';
import { FugassaTtsManager } from '../FugassaTtsManager.js';
import { isTtsActive, normalizeTtsPrefs } from '../ttsSettings.js';
import { normalizeTurnNumber, sceneCastHeaderMarkup } from './chatTurnUtils.js';

const SCROLL_PIN_THRESHOLD = 56;
const MIN_CHAT_COMPOSE = 96;
const MAX_CHAT_COMPOSE = 360;
const DEFAULT_CHAT_COMPOSE = 148;

function clampComposerHeight(value, hostEl) {
  const panelH = hostEl?.getBoundingClientRect?.().height || 640;
  const headH = hostEl?.querySelector('.fugassa-hud-panel-head')?.getBoundingClientRect?.().height || 40;
  const max = Math.min(MAX_CHAT_COMPOSE, Math.max(MIN_CHAT_COMPOSE, panelH - headH - 160));
  const n = Number(value);
  if (!Number.isFinite(n)) return DEFAULT_CHAT_COMPOSE;
  return Math.max(MIN_CHAT_COMPOSE, Math.min(max, Math.round(n)));
}

/**
 * Chat / log panel with per-turn scene image icons (📷 / 🖼).
 *
 * Architecture:
 * - Mount once per gameplay session; update via returned controller methods.
 * - Scene icon clicks use event delegation (survives message re-renders).
 * - Scene preview modal is owned by GameplayHub (passed in as scenePopup).
 */
export function mountChatPanel(el, {
  saveId,
  messages,
  statusText,
  busy,
  onSubmit,
  onPipelineActivity,
  scenePopup,
  ttsPrefs,
  turnPhase,
  composerHeight,
  onComposerHeightChange,
  getGameTurn,
}) {
  el.className = 'fugassa-hud-chat';
  el.innerHTML = `
    <div class="fugassa-hud-panel-head"><h3>Chat / Log</h3></div>
    <div class="fugassa-hud-chat-messages">
      <div class="fugassa-hud-chat-scroll"></div>
      <button type="button" class="fugassa-hud-chat-jump" hidden aria-label="Scroll to latest messages" title="Jump to latest">↓</button>
    </div>
    <div class="fugassa-hud-chat-split-h" data-chat-split aria-hidden="true"></div>
    <div class="fugassa-hud-chat-compose">
      <div class="fugassa-hud-chat-status fugassa-muted"></div>
      <form class="fugassa-hud-chat-form">
        <textarea class="fugassa-hud-chat-input" rows="4" placeholder="What do you do next?" autocomplete="off"></textarea>
        <button type="submit" class="fugassa-btn fugassa-btn--primary fugassa-btn--sm">Send</button>
      </form>
    </div>
  `;

  const scroll = el.querySelector('.fugassa-hud-chat-scroll');
  const jumpBtn = el.querySelector('.fugassa-hud-chat-jump');
  const compose = el.querySelector('.fugassa-hud-chat-compose');
  const splitHandle = el.querySelector('[data-chat-split]');
  const status = el.querySelector('.fugassa-hud-chat-status');
  const form = el.querySelector('.fugassa-hud-chat-form');
  const input = el.querySelector('.fugassa-hud-chat-input');

  let sceneAssets = {};
  let minActiveTurn = 0;
  let pendingTurns = new Set();
  let notifiedReadyTurns = new Set();
  let prevSceneStatuses = {};
  let sceneAssetsTimer = null;
  /** After first asset sync, ignore already-ready scenes (avoids popup on load/refresh). */
  let sceneAssetsBootstrapped = false;
  let currentTurnPhase = turnPhase || 'idle';
  let stickToBottom = true;
  let prevMessageCount = 0;
  let composeHeight = clampComposerHeight(composerHeight, el);
  let renderMessages = () => {};
  let lastAppliedTtsPrefs = normalizeTtsPrefs(ttsPrefs);
  const ttsManager = new FugassaTtsManager(ttsPrefs, {
    onActivityChange: () => renderMessages(),
  });

  const ttsEnabled = () => isTtsActive(ttsManager.prefs);
  /** Manual ▶ on every GM bubble while narration is enabled (not gated by turn_phase). */
  const ttsShowManualControls = () => ttsEnabled();
  /** Auto-play only during the reading window (not idle / processing). */
  const ttsCanAutoPlay = () => {
    if (!ttsEnabled()) return false;
    if (ttsManager.prefs?.mode !== 'auto') return false;
    return currentTurnPhase === 'reading';
  };

  const normalizeTurn = normalizeTurnNumber;

  const applyComposerHeight = (next, { notify = true } = {}) => {
    composeHeight = clampComposerHeight(next, el);
    compose.style.height = `${composeHeight}px`;
    if (notify) onComposerHeightChange?.(composeHeight);
  };

  const isNearBottom = () => (
    scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight <= SCROLL_PIN_THRESHOLD
  );

  const updateJumpButton = () => {
    jumpBtn.hidden = stickToBottom;
  };

  const scrollToBottom = ({ smooth = false, forcePin = true } = {}) => {
    const top = scroll.scrollHeight;
    if (smooth && scroll.scrollTo) {
      scroll.scrollTo({ top, behavior: 'smooth' });
    } else {
      scroll.scrollTop = top;
    }
    if (forcePin) stickToBottom = true;
    updateJumpButton();
  };

  /** Re-render chat after async TTS readiness probe (enables/disables ▶ buttons). */
  const syncTtsReadyState = ({ autoRetry = false } = {}) => {
    ttsManager.ensureReady().finally(() => {
      renderMessages();
      if (autoRetry && ttsManager.needsAutoRetry()) maybeAutoPlayTts();
    });
  };

  const assetForTurn = (turnNumber) => {
    const turn = normalizeTurn(turnNumber);
    if (turn === null) return null;
    return sceneAssets[turn] ?? sceneAssets[String(turn)] ?? null;
  };

  const sceneImageUrl = (asset) => {
    if (!saveId || !asset?.file_path) return null;
    return `/api/fugassa/saves/${encodeURIComponent(saveId)}/assets/${encodeURIComponent(asset.file_path)}`;
  };

  const normalizeSceneAssets = (raw) => {
    const out = {};
    Object.entries(raw || {}).forEach(([key, asset]) => {
      const turn = normalizeTurn(key);
      if (turn !== null) out[turn] = asset;
    });
    return out;
  };

  const openScenePreview = (turnNumber) => {
    const turn = normalizeTurn(turnNumber);
    if (turn === null || !scenePopup) return;
    const asset = assetForTurn(turn);
    const url = sceneImageUrl(asset);
    if (!url) return;
    scenePopup.show({
      label: `Scene for turn ${turn} is ready.`,
      imageUrl: url,
      onRegen: () => generateScene(turn),
    });
  };

  async function generateScene(turnNumber) {
    const turn = normalizeTurn(turnNumber);
    if (!saveId || turn === null || pendingTurns.has(turn)) return;
    pendingTurns.add(turn);
    renderMessages();
    try {
      await api.generateAsset(saveId, { entityType: 'other', entityId: turn, assetType: 'scene' });
      onPipelineActivity?.();
    } catch (err) {
      uiModule.showToast?.(err?.message || 'Scene generation failed', { duration: 3500 });
    } finally {
      await syncSceneAssets({ notifyReady: true });
      pendingTurns.delete(turn);
    }
  }

  function sceneIconMarkup(turnNumber) {
    const turn = normalizeTurn(turnNumber);
    if (turn === null) return '';
    const asset = assetForTurn(turn);
    if (pendingTurns.has(turn) || asset?.status === 'queued' || asset?.status === 'generating') {
      return `<button type="button" class="fugassa-chat-scene-icon" data-scene-turn="${turn}" data-scene-action="wait" disabled title="Generating…">…</button>`;
    }
    if (asset?.status === 'failed') {
      const err = asset.error ? String(asset.error).replace(/"/g, '&quot;') : 'Generation failed — click to retry';
      return `<button type="button" class="fugassa-chat-scene-icon fugassa-chat-scene-icon--failed" data-scene-turn="${turn}" data-scene-action="generate" title="${err}">⚠</button>`;
    }
    if (asset?.status === 'ready' && asset.file_path) {
      return `<button type="button" class="fugassa-chat-scene-icon fugassa-chat-scene-icon--ready" data-scene-turn="${turn}" data-scene-action="view" title="View scene image">🖼</button>`;
    }
    return `<button type="button" class="fugassa-chat-scene-icon" data-scene-turn="${turn}" data-scene-action="generate" title="Generate scene image">📷</button>`;
  }

  function ttsIconMarkup(msg) {
    if (!ttsShowManualControls() || msg.role !== 'assistant') return '';
    const turn = normalizeTurn(msg.turn_number);
    const turnAttr = turn !== null ? ` data-tts-turn="${turn}"` : '';
    const ready = ttsManager.ready;
    const activity = ttsManager.getActivity();
    const isActive = turn !== null && activity?.turnNumber === turn;
    if (isActive && activity.phase === 'loading') {
      return `<button type="button" class="fugassa-chat-tts-icon fugassa-chat-tts-icon--loading"${turnAttr} title="Cancel narration" aria-busy="true">…</button>`;
    }
    if (isActive && activity.phase === 'playing') {
      return `<button type="button" class="fugassa-chat-tts-icon fugassa-chat-tts-icon--playing"${turnAttr} title="Stop narration">⏹</button>`;
    }
    const warming = !ready && ttsManager._readyPromise;
    const title = ready
      ? 'Read GM message aloud'
      : warming
        ? 'Preparing TTS…'
        : 'TTS not ready — install Supertonic-3 in Model Hub and ensure Titan is running';
    const extraClass = warming ? ' fugassa-chat-tts-icon--loading' : '';
    return `<button type="button" class="fugassa-chat-tts-icon${extraClass}"${turnAttr} title="${title}"${warming ? ' aria-busy="true"' : ''}>${warming ? '…' : '▶'}</button>`;
  }

  function messageForTtsButton(btn) {
    const turn = normalizeTurn(btn.dataset.ttsTurn);
    if (turn !== null) {
      const match = (messages || []).find(
        (m) => m?.role === 'assistant' && normalizeTurn(m.turn_number) === turn,
      );
      if (match) return match;
    }
    const rows = [...scroll.querySelectorAll('.fugassa-hud-chat-msg--gm')];
    const row = btn.closest('.fugassa-hud-chat-msg--gm');
    const idx = rows.indexOf(row);
    if (idx >= 0) {
      const gmMsgs = (messages || []).filter((m) => m?.role === 'assistant');
      return gmMsgs[idx] || null;
    }
    return null;
  }

  renderMessages = function renderMessagesImpl({ forceBottom = false } = {}) {
    const prevTop = scroll.scrollTop;
    const prevHeight = scroll.scrollHeight;
    const wasNearBottom = isNearBottom();
    const countBefore = prevMessageCount;

    scroll.innerHTML = '';
    (messages || []).forEach((msg) => {
      const row = document.createElement('div');
      row.className = `fugassa-hud-chat-msg fugassa-hud-chat-msg--${msg.role === 'assistant' ? 'gm' : 'player'}`;

      const label = document.createElement('div');
      label.className = 'fugassa-hud-chat-role';
      const roleName = msg.role === 'assistant' ? 'GM' : 'You';
      const meta = msg.role === 'assistant' ? String(msg.ingame_time || msg.location || '').trim() : '';
      const castHtml = sceneCastHeaderMarkup(msg, escapeHtml);
      label.innerHTML = meta
        ? `<span class="fugassa-hud-chat-role-name">${roleName}</span>${castHtml}<span class="fugassa-hud-chat-role-meta fugassa-muted">${escapeHtml(meta)}</span>`
        : `<span class="fugassa-hud-chat-role-name">${roleName}</span>${castHtml}`;

      const body = document.createElement('div');
      body.className = 'fugassa-hud-chat-body';
      body.textContent = String(msg.content || '');

      row.append(label, body);

      const turnNumber = normalizeTurn(msg.turn_number);
      if (saveId && msg.role === 'assistant') {
        const ttsHtml = ttsIconMarkup(msg);
        const sceneHtml = turnNumber !== null && turnNumber >= minActiveTurn ? sceneIconMarkup(turnNumber) : '';
        if (ttsHtml || sceneHtml) {
          const actionsRow = document.createElement('div');
          actionsRow.className = 'fugassa-hud-chat-actions-row';
          actionsRow.innerHTML = `${ttsHtml}${sceneHtml}`;
          row.appendChild(actionsRow);
        }
      }

      scroll.appendChild(row);
    });

    const countAfter = (messages || []).length;
    const grew = countAfter > countBefore;
    prevMessageCount = countAfter;

    if (forceBottom || stickToBottom || (grew && wasNearBottom)) {
      requestAnimationFrame(() => scrollToBottom({ forcePin: forceBottom || stickToBottom || grew }));
    } else {
      const heightDelta = scroll.scrollHeight - prevHeight;
      scroll.scrollTop = Math.max(0, prevTop + heightDelta);
      stickToBottom = isNearBottom();
      updateJumpButton();
    }
  };

  function maybeAutoPlayTts() {
    if (!ttsCanAutoPlay()) return;
    const lastGm = [...(messages || [])].reverse().find((m) => m?.role === 'assistant');
    void ttsManager.maybeAutoPlay(messages, {
      turnPhase: currentTurnPhase,
      currentTurn: lastGm?.turn_number,
      gameTurn: getGameTurn?.(),
    });
  }

  async function syncSceneAssets({ notifyReady = true } = {}) {
    if (!saveId) return;
    try {
      const res = await api.getChatSceneAssets(saveId);
      sceneAssets = normalizeSceneAssets(res.assets);
      minActiveTurn = normalizeTurn(res.min_active_turn) ?? 0;

      Object.entries(sceneAssets).forEach(([turnKey, asset]) => {
        const turn = normalizeTurn(turnKey);
        if (turn === null) return;
        const prev = prevSceneStatuses[turn];
        const url = sceneImageUrl(asset);
        const becameReady = asset?.status === 'ready' && url && prev !== 'ready';
        const userWasWaiting = pendingTurns.has(turn) || prev === 'queued' || prev === 'generating';
        if (
          notifyReady
          && sceneAssetsBootstrapped
          && becameReady
          && userWasWaiting
          && !notifiedReadyTurns.has(turn)
        ) {
          notifiedReadyTurns.add(turn);
          openScenePreview(turn);
        }
        if (asset?.status === 'failed' && prev !== 'failed') {
          uiModule.showToast?.(asset.error || `Scene for turn ${turn} failed`, { duration: 4000 });
        }
      });

      prevSceneStatuses = Object.fromEntries(
        Object.entries(sceneAssets).map(([k, a]) => [normalizeTurn(k), a?.status]),
      );
      renderMessages();
    } catch {
      // best-effort — chat still works without scene image icons
    } finally {
      sceneAssetsBootstrapped = true;
    }
  }

  function scheduleSceneAssetsSync(options = {}) {
    if (sceneAssetsTimer) return;
    sceneAssetsTimer = setTimeout(async () => {
      sceneAssetsTimer = null;
      await syncSceneAssets(options);
    }, 300);
  }

  scroll.addEventListener('scroll', () => {
    stickToBottom = isNearBottom();
    updateJumpButton();
  }, { passive: true });

  jumpBtn.addEventListener('click', () => {
    scrollToBottom({ smooth: true, forcePin: true });
  });

  splitHandle.addEventListener('mousedown', (ev) => {
    ev.preventDefault();
    const startY = ev.clientY;
    const startHeight = composeHeight;
    const move = (e) => {
      const delta = startY - e.clientY;
      applyComposerHeight(startHeight + delta);
    };
    const up = () => {
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  });

  scroll.addEventListener('click', async (ev) => {
    const ttsBtn = ev.target.closest('.fugassa-chat-tts-icon');
    if (ttsBtn) {
      ev.preventDefault();
      ev.stopPropagation();
      if (ttsBtn.classList.contains('fugassa-chat-tts-icon--playing')
        || ttsBtn.classList.contains('fugassa-chat-tts-icon--loading')) {
        ttsManager.stop();
        renderMessages();
        return;
      }
      const msg = messageForTtsButton(ttsBtn);
      if (msg?.content) {
        renderMessages();
        await ttsManager.playText(msg.content, {
          button: ttsBtn,
          turnNumber: normalizeTurn(msg.turn_number) ?? normalizeTurn(getGameTurn?.()),
        });
        renderMessages();
      }
      return;
    }

    const btn = ev.target.closest('[data-scene-turn]');
    if (!btn || btn.disabled) return;
    ev.preventDefault();
    ev.stopPropagation();
    const turn = normalizeTurn(btn.dataset.sceneTurn);
    if (turn === null) return;
    const action = btn.dataset.sceneAction || 'generate';
    if (action === 'view') {
      openScenePreview(turn);
      return;
    }
    if (action === 'generate') {
      generateScene(turn);
    }
  });

  form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    const text = input.value.trim();
    if (!text || busy) return;
    stickToBottom = true;
    onSubmit?.(text);
    input.value = '';
    requestAnimationFrame(() => scrollToBottom({ forcePin: true }));
  });

  input.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && !ev.shiftKey) {
      ev.preventDefault();
      form.requestSubmit();
    }
  });

  applyComposerHeight(composeHeight, { notify: false });
  renderMessages({ forceBottom: false });
  syncSceneAssets({ notifyReady: false });
  syncTtsReadyState();
  status.textContent = statusText || '';
  input.disabled = Boolean(busy);
  form.querySelector('button').disabled = Boolean(busy);

  return {
    setMessages(next, { syncAssets = false, autoTts = false, forceBottom = false } = {}) {
      messages = next;
      renderMessages({ forceBottom });
      if (syncAssets) scheduleSceneAssetsSync({ notifyReady: false });
      if (autoTts) maybeAutoPlayTts();
    },
    setTurnPhase(phase) {
      currentTurnPhase = phase || 'idle';
    },
    setTtsPrefs(prefs) {
      const next = normalizeTtsPrefs(prefs);
      const unchanged = JSON.stringify(next) === JSON.stringify(lastAppliedTtsPrefs);
      ttsManager.updatePrefs(next);
      if (unchanged) return;
      lastAppliedTtsPrefs = next;
      renderMessages();
      syncTtsReadyState();
    },
    stopTts() {
      ttsManager.stop();
    },
    /** Prevent auto-read from replaying the current GM bubble when the player sends. */
    suppressAutoForCurrentGm() {
      ttsManager.suppressAutoForLastGm(messages);
    },
    setStatus(text) {
      status.textContent = text || '';
    },
    setBusy(isBusy) {
      input.disabled = isBusy;
      form.querySelector('button').disabled = isBusy;
    },
    scrollTo(st) {
      if (st === undefined || st === null) {
        scrollToBottom({ forcePin: false });
        return;
      }
      scroll.scrollTop = st;
      stickToBottom = isNearBottom();
      updateJumpButton();
    },
    getScrollTop() {
      return scroll.scrollTop;
    },
    getComposerHeight() {
      return composeHeight;
    },
    refreshSceneAssets() {
      return syncSceneAssets({ notifyReady: false });
    },
    destroy() {
      if (sceneAssetsTimer) clearTimeout(sceneAssetsTimer);
      ttsManager.stop();
    },
  };
}
