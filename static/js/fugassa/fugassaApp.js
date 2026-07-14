import * as api from './fugassaApi.js';
import { mountGameplayHub } from './gameplay/GameplayHub.js';
import { cleanupOrphanScenePopups } from './gameplay/hud/SceneReadyPopup.js';
import { mountMenuCenterStage } from './menu/MenuCenterStage.js';
import { clearSession, loadSession, saveSession } from './sessionStore.js';
import { loadDraft, isDraftResumable } from './wizard/draft.js';

let rootEl = null;
let open = false;
let renderToken = 0;
let gameplayHub = null;

function ensureRoot() {
  if (!rootEl) rootEl = document.getElementById('fugassa-main');
  return rootEl;
}

function currentSession() {
  return loadSession();
}

async function syncRemoteManifest(partial) {
  try {
    await api.syncSessionManifest({
      mode: partial.mode,
      menuScreen: partial.menuScreen,
      wizardTab: partial.wizardStep,
      activeSaveId: partial.activeSaveId,
      lastTool: partial.lastTool || 'fugassa',
      play: partial.play,
    });
  } catch {
    // local session remains the source of truth if backend sync fails
  }
}

async function render() {
  const root = ensureRoot();
  if (!root || !open) return;
  const token = ++renderToken;
  root.hidden = false;
  root.classList.add('is-open');
  const session = currentSession();
  const handleSessionChange = async (partial) => {
    const prev = currentSession();
    const next = saveSession(partial);
    await syncRemoteManifest(next);
    // Remount only when entering/leaving gameplay — menu sub-screens update DOM in place.
    const enteringPlay = partial.mode === 'play';
    const leavingPlay = prev.mode === 'play' && partial.mode === 'menu';
    if (enteringPlay || leavingPlay) {
      await render();
    }
  };
  if (session.mode === 'play') {
    gameplayHub?.destroy?.();
    gameplayHub = await mountGameplayHub(root, {
      saveId: session.activeSaveId,
      playSession: session.play,
      onBackToMenu: async () => {
        await handleSessionChange({ mode: 'menu', menuScreen: 'home' });
      },
      onSessionChange: handleSessionChange,
    });
    return;
  }
  gameplayHub?.destroy?.();
  gameplayHub = null;
  await mountMenuCenterStage(root, {
    session,
    onHide: hideFugassa,
    onSessionChange: handleSessionChange,
  });
  if (token !== renderToken) return;
}

export async function openFugassa() {
  open = true;
  document.body.classList.add('fugassa-view');
  saveSession({ lastTool: 'fugassa' });
  await render();
}

export function hideFugassa() {
  const root = ensureRoot();
  open = false;
  document.body.classList.remove('fugassa-view');
  gameplayHub?.destroy?.();
  gameplayHub = null;
  cleanupOrphanScenePopups();
  if (root) {
    root.hidden = true;
    root.classList.remove('is-open');
    root.innerHTML = '';
  }
}

export async function toggleFugassa() {
  if (open) {
    hideFugassa();
  } else {
    await openFugassa();
  }
}

export function isFugassaOpen() {
  return open;
}

export async function tryRestoreFugassa() {
  try {
    const local = currentSession();
    const remote = await api.loadSessionManifest().catch(() => null);
    if (remote && typeof remote === 'object') {
      saveSession({
        mode: remote.mode || local.mode,
        menuScreen: remote.menuScreen || local.menuScreen,
        wizardStep: remote.wizardTab ?? local.wizardStep,
        activeSaveId: remote.activeSaveId ?? local.activeSaveId,
        play: remote.play || local.play,
        lastTool: 'fugassa',
      });
    }
    const session = currentSession();
    const draft = await loadDraft().catch(() => null);
    const shouldOpen = session.lastTool === 'fugassa'
      || session.mode === 'play'
      || session.activeSaveId
      || (draft && isDraftResumable(draft));
    if (shouldOpen) {
      await openFugassa();
    }
  } catch {
    // optional startup restoration only
  }
}

export async function resetFugassaSession() {
  clearSession();
  hideFugassa();
  // Clearing only `localStorage` is not enough — `tryRestoreFugassa()` on
  // the next load re-fetches the server-side `session_manifest.json` and
  // would immediately snap straight back into the stale `play`/`activeSaveId`
  // it still holds, with no menu/back button in between (the exact lockout
  // this function exists to fix). Explicit `null` requires the backend PUT
  // to use `exclude_unset` rather than `exclude_none`, or it gets dropped.
  await syncRemoteManifest({ mode: 'menu', menuScreen: 'home', activeSaveId: null });
}

export default {
  openFugassa,
  hideFugassa,
  toggleFugassa,
  isFugassaOpen,
  tryRestoreFugassa,
  resetFugassaSession,
};
