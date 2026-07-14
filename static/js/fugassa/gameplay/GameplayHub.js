/**
 * Gameplay HUD — Fugassa II layout (Phase 4–5).
 *
 * Turn UX (ADR C8):
 * - `turn_phase` (state.turn_phase): idle | processing | reading — drives composer
 *   unlock and auto-TTS. Composer unlocks when interactive_turn completes
 *   (`interactive_unlocked`), before background SD jobs finish.
 * - `campaign_phase` (state.campaign_phase): broader pipeline label for StatusBar
 *   (e.g. generating_assets). UI composer unlock ≠ assets done.
 */

import uiModule from '../../ui.js';
import * as api from '../fugassaApi.js';
import { saveSession } from '../sessionStore.js';
import { createGameplayOverlay } from './overlay/GameplayOverlay.js';
import { mountChatPanel } from './hud/ChatPanel.js';
import { mountCenterView } from './hud/CenterView.js';
import { mountPartyBar } from './hud/PartyBar.js';
import { applyHudSplits, clampHudSplits, wireHudSplitters } from './hud/ResizableHud.js';
import { mountRightSidebar } from './hud/RightSidebar.js';
import { mountStatusBar } from './hud/StatusBar.js';
import { createPipelineWaitModal } from './hud/PipelineWaitModal.js';
import { createSceneReadyPopup, cleanupOrphanScenePopups } from './hud/SceneReadyPopup.js';
import { applyDisplaySettings } from './displaySettings.js';

const PIPELINE_POLL_MS = 2000;
const INTERACTIVE_UNLOCK_TIMEOUT_MS = 600_000;

function questHasUpdates(quest) {
  if (!quest || typeof quest !== 'object') return false;
  if ((quest.companions_joined || []).length) return true;
  if ((quest.quests_completed || []).length) return true;
  if ((quest.quests_failed || []).length) return true;
  if ((quest.objectives_completed || []).length) return true;
  return Object.keys(quest.rewards_granted || {}).length > 0;
}

function showQuestFeedback(quest, ui) {
  if (!quest || typeof quest !== 'object') return;
  const joined = quest.companions_joined || [];
  joined.forEach((name) => {
    ui?.showToast?.(`${name} joined your party`, { duration: 4000, leadingIcon: 'check' });
  });
  (quest.quests_completed || []).forEach((title) => {
    ui?.showToast?.(`Quest completed: ${title}`, { duration: 4500, leadingIcon: 'check' });
  });
  (quest.quests_failed || []).forEach((item) => {
    const title = typeof item === 'string' ? item : item.quest || item.title || 'Quest';
    ui?.showToast?.(`Quest failed: ${title}`, { duration: 4500 });
  });
  (quest.objectives_completed || []).forEach((item) => {
    const questName = item?.quest || 'Quest';
    const objective = item?.objective || 'objective';
    ui?.showToast?.(`${questName}: ${objective}`, { duration: 3800, leadingIcon: 'check' });
  });
  Object.values(quest.rewards_granted || {}).flat().forEach((line) => {
    if (!line) return;
    ui?.showToast?.(`Reward: ${line}`, { duration: 4200, leadingIcon: 'check' });
  });
}

function questFromPipeline(res) {
  const jobs = res?.jobs || [];
  const turnJob = jobs.find((j) => j.job_type === 'interactive_turn' && j.status === 'completed');
  return turnJob?.result?.quest || res?.quest || null;
}

function scheduleAutoTts(chatCtrl, history, phase) {
  if (phase !== 'reading') {
    chatCtrl?.setMessages(history, { syncAssets: false, autoTts: false, forceBottom: true });
    return;
  }
  chatCtrl?.setMessages(history, { syncAssets: false, autoTts: false, forceBottom: true });
  requestAnimationFrame(() => {
    window.setTimeout(() => {
      chatCtrl?.setMessages(history, { syncAssets: false, autoTts: true, forceBottom: true });
    }, 300);
  });
}

export async function mountGameplayHub(root, { saveId, playSession, onBackToMenu, onSessionChange }) {
  cleanupOrphanScenePopups();
  root.innerHTML = '';
  root.className = 'fugassa-main fugassa-play-root';

  const hub = document.createElement('div');
  hub.className = 'fugassa-hud';
  hub.innerHTML = `
    <header class="fugassa-hud-top"></header>
    <div class="fugassa-hud-mid">
      <aside class="fugassa-hud-chat"></aside>
      <div class="fugassa-hud-split-v" data-split="chat" aria-hidden="true"></div>
      <div class="fugassa-hud-center-col">
        <main class="fugassa-hud-center"></main>
        <div class="fugassa-hud-split-h" data-split="party" aria-hidden="true"></div>
        <footer class="fugassa-hud-party"></footer>
      </div>
      <div class="fugassa-hud-split-v" data-split="center" aria-hidden="true"></div>
      <aside class="fugassa-hud-right"></aside>
    </div>
  `;

  const overlayHost = document.createElement('div');
  overlayHost.className = 'fugassa-overlay-host';
  overlayHost.hidden = true;

  root.append(hub, overlayHost);

  let state = null;
  let splits = clampHudSplits(playSession?.hudSplits || {}, hub);
  let rightMode = playSession?.rightSidebarMode || 'explore';
  let chatCtrl = null;
  let busy = false;
  let backgroundPollTimer = null;
  let lastPipelineMeta = {};
  let questsTabHasUpdate = false;

  const markQuestsUpdated = (quest) => {
    if (questHasUpdates(quest)) questsTabHasUpdate = true;
  };

  const pipelineModal = createPipelineWaitModal(root);
  const scenePopup = createSceneReadyPopup(root);

  const setHudLocked = (locked) => {
    hub.classList.toggle('fugassa-hud--pipeline-locked', Boolean(locked));
  };

  async function waitForBatchInteractive(batchId, { onPipeline } = {}) {
    if (!batchId) return null;
    const deadline = Date.now() + INTERACTIVE_UNLOCK_TIMEOUT_MS;
    for (;;) {
      const res = await api.getGameJobs(saveId, batchId);
      onPipeline?.(res);
      if (res?.state) {
        setState(res.state);
        chatCtrl?.setTurnPhase?.(res.state.turn_phase);
      }
      chatCtrl?.setMessages(res.state?.chat_history || state?.chat_history || [], {
        syncAssets: false,
        autoTts: false,
      });
      await enrichMap();
      if (res?.interactive_unlocked) {
        const failed = (res.jobs || []).find(
          (j) => j.job_type === 'interactive_turn' && j.status === 'failed',
        );
        if (failed) throw new Error(failed.error || 'Turn processing failed');
        return res;
      }
      if (Date.now() >= deadline) {
        throw new Error('Turn processing timed out — check pipeline status or retry');
      }
      await new Promise((resolve) => {
        setTimeout(resolve, PIPELINE_POLL_MS);
      });
    }
  }

  function startBackgroundJobPoll() {
    if (backgroundPollTimer) return;
    backgroundPollTimer = setInterval(async () => {
      try {
        const res = await api.getGameJobs(saveId);
        if (res?.state) {
          const prevPhase = state?.turn_phase;
          setState(res.state);
          if (res.state.turn_phase !== prevPhase) {
            chatCtrl?.setTurnPhase?.(res.state.turn_phase);
          }
          if (res.state.turn_phase === 'reading' || res.state.turn_phase === 'idle') {
            chatCtrl?.setMessages(res.state.chat_history || state?.chat_history || [], {
              syncAssets: false,
              autoTts: false,
            });
          }
        }
        lastPipelineMeta = {
          campaignPhase: res?.campaign_phase,
          pipelineLocked: res?.pipeline_locked,
          currentJobLabel: res?.current_job_label,
        };
        await enrichMap();
        refreshPanels();
        centerCtrl?.refreshMeta?.();
        chatCtrl?.refreshSceneAssets?.();
        rightSidebarCtrl?.refreshNpcAssets?.();
        const pipelineIdle = !res?.pipeline_locked && res?.campaign_phase === 'idle';
        const assetsPending = Boolean(res?.queued_assets);
        if (pipelineIdle && !assetsPending) {
          clearInterval(backgroundPollTimer);
          backgroundPollTimer = null;
        }
      } catch {
        // background poll is best-effort
      }
    }, PIPELINE_POLL_MS);
  }

  let centerCtrl = null;
  let rightSidebarCtrl = null;

  const setState = (next) => {
    state = next;
    applyDisplaySettings(root, state?.display_settings);
  };

  const overlay = createGameplayOverlay(overlayHost, {
    saveId,
    getState: () => state,
    onStateChange: (next) => {
      setState(next);
      refreshPanels();
      syncChatFromState();
    },
  });

  const persistPlay = (partial) => {
    const scroll = chatCtrl?.getScrollTop?.() || 0;
    const composer = chatCtrl?.getComposerHeight?.();
    const nextPlay = {
      ...(playSession || {}),
      ...partial,
      hudSplits: { ...splits, ...(partial.hudSplits || {}) },
      chatScrollTop: scroll,
      ...(composer ? { chatComposerHeight: composer } : {}),
    };
    onSessionChange?.({ play: nextPlay });
  };

  const applySplits = () => {
    splits = clampHudSplits(splits, hub);
    applyHudSplits(hub, splits);
    persistPlay({ hudSplits: splits });
  };

  wireHudSplitters(hub, () => splits, (next) => {
    splits = clampHudSplits(next, hub);
    applySplits();
  });
  applySplits();
  window.addEventListener('resize', () => applySplits());

  async function enrichMap() {
    try {
      const map = await api.getGameMap(saveId);
      state = { ...state, minimap: map.minimap };
    } catch {
      // minimap optional
    }
  }

  const hudActions = {
    openInventory: () => overlay.open('inventory'),
    openCrafting: () => overlay.open('crafting'),
    openCharacter: () => overlay.open('character'),
    openCompanion: (member) => {
      const idx = (state?.party || []).findIndex((m) => m === member || m?.name === member?.name);
      overlay.open('character', { memberIndex: idx >= 0 ? idx : 0 });
    },
    companionTalk: (member) => {
      const name = member?.name || 'companion';
      handleSubmit(`I speak with ${name}.`);
    },
    openEstates: () => overlay.open('estates'),
    openMap: () => overlay.open('map'),
    openLocation: () => overlay.open('location'),
    openSummary: () => overlay.open('summary'),
    move: (dir) => runWorldAction(() => api.moveGame(saveId, dir)),
    getInvestigateOptions: () => api.getInvestigateOptions(saveId),
    investigate: (opts) => runWorldAction(() => api.investigateGame(saveId, opts)),
    pickupLoot: (opts) => runWorldAction(() => api.pickupLoot(saveId, opts)),
    startCombat: () => runWorldAction(() => api.startCombat(saveId)),
    endCombat: () => runWorldAction(() => api.endCombat(saveId)),
    combatAction: (label) => handleSubmit(`[Combat action] ${label}`),
    npcTalk: (name) => handleSubmit(`I speak with ${name}.`),
  };

  async function runWorldAction(fn) {
    if (busy) return;
    try {
      busy = true;
      chatCtrl?.setStatus('Updating…');
      const res = await fn();
      if (res.state) setState(res.state);
      await enrichMap();
      refreshPanels();
      if (res.message) {
        chatCtrl?.setStatus(res.message);
        uiModule.showToast?.(res.message, { duration: 2500 });
      } else {
        chatCtrl?.setStatus('');
      }
      showQuestFeedback(res?.quest, uiModule);
      markQuestsUpdated(res?.quest);
      refreshPanels();
      startBackgroundJobPoll();
    } catch (error) {
      chatCtrl?.setStatus(error.message || String(error));
      uiModule.showToast?.(error.message || String(error), { duration: 3500 });
    } finally {
      busy = false;
    }
  }

  const syncChatFromState = ({ autoTts = false } = {}) => {
    const history = state?.chat_history || [];
    chatCtrl?.setTtsPrefs?.(state?.tts_prefs);
    chatCtrl?.setTurnPhase?.(state?.turn_phase);
    if (history.length) {
      chatCtrl?.setMessages(history, { syncAssets: false, autoTts });
    }
  };

  async function refreshPanels() {
    const partySize = Array.isArray(state?.party) ? state.party.length : 0;
    mountStatusBar(hub.querySelector('.fugassa-hud-top'), {
      saveId,
      turn: state?.turn,
      turnPhase: state?.turn_phase,
      campaignPhase: lastPipelineMeta.campaignPhase ?? state?.campaign_phase,
      pipelineLocked: lastPipelineMeta.pipelineLocked,
      currentJobLabel: lastPipelineMeta.currentJobLabel,
      inCombat: Boolean(state?.in_combat),
      partySize,
      canUndo: Boolean(state?.can_undo),
      onBackToMenu: () => {
        persistPlay({});
        onBackToMenu?.();
      },
      onUndo: handleUndo,
      onPause: () => overlay.open('pause'),
    });
    centerCtrl = mountCenterView(hub.querySelector('.fugassa-hud-center'), {
      state,
      saveId,
      onPipelineActivity: startBackgroundJobPoll,
    });
    rightSidebarCtrl = mountRightSidebar(hub.querySelector('.fugassa-hud-right'), {
      state,
      mode: rightMode,
      saveId,
      questsTabHasUpdate,
      onModeChange: (mode) => {
        rightMode = mode;
        if (mode === 'quests') questsTabHasUpdate = false;
        persistPlay({ rightSidebarMode: mode });
        refreshPanels();
      },
      actions: hudActions,
      onPipelineActivity: startBackgroundJobPoll,
    });
    mountPartyBar(hub.querySelector('.fugassa-hud-party'), {
      party: state?.party,
      saveId,
      onMemberClick: (_member, index) => overlay.open('character', { memberIndex: index }),
    });
  }

  async function handleUndo() {
    if (busy || !saveId) return;
    try {
      busy = true;
      chatCtrl?.setBusy(true);
      chatCtrl?.setStatus('Undoing last turn…');
      const res = await api.undoGameTurn(saveId);
      setState(res?.state || res || state);
      await enrichMap();
      refreshPanels();
      syncChatFromState();
      chatCtrl?.setStatus('');
      uiModule.showToast?.('Turn undone', { duration: 2200, leadingIcon: 'check' });
    } catch (error) {
      chatCtrl?.setStatus(error.message || String(error));
      uiModule.showToast?.(error.message || String(error), { duration: 3500 });
    } finally {
      busy = false;
      chatCtrl?.setBusy(false);
    }
  }

  async function handleSubmit(text) {
    if (busy || !saveId) return;
    chatCtrl?.stopTts?.();
    chatCtrl?.suppressAutoForCurrentGm?.();
    chatCtrl?.setTurnPhase?.('processing');
    const history = [...(state?.chat_history || []), { role: 'user', content: text }];
    setState({ ...state, chat_history: history, turn_phase: 'processing' });
    chatCtrl?.setMessages(history, { syncAssets: false, autoTts: false, forceBottom: true });
    try {
      busy = true;
      chatCtrl?.setBusy(true);
      setHudLocked(true);
      pipelineModal.show({ title: 'Your action', step: 'Sending to GM…' });
      const res = await api.submitGameAction(saveId, text);
      if (res?.state) setState({ ...res.state, turn_phase: res.state.turn_phase || 'processing' });
      chatCtrl?.setTurnPhase?.(state?.turn_phase || 'processing');
      chatCtrl?.setMessages(state?.chat_history || [], { syncAssets: false, autoTts: false });
      const unlocked = await waitForBatchInteractive(res?.batch_id, {
        onPipeline: (p) => pipelineModal.updateFromPipeline(p),
      });
      if (unlocked?.state) setState(unlocked.state);
      await enrichMap();
      refreshPanels();
      const unlockedState = unlocked?.state || state;
      const phase = String(unlocked?.turn_phase || unlockedState?.turn_phase || 'reading');
      chatCtrl?.setTurnPhase?.(phase);
      setHudLocked(false);
      pipelineModal.hide();
      scheduleAutoTts(chatCtrl, unlockedState?.chat_history || [], phase);
      const questMeta = questFromPipeline(unlocked);
      showQuestFeedback(questMeta, uiModule);
      markQuestsUpdated(questMeta);
      refreshPanels();
      await chatCtrl?.refreshSceneAssets?.();
      startBackgroundJobPoll();
      chatCtrl?.setStatus('');
    } catch (error) {
      chatCtrl?.setStatus(error.message || String(error));
      uiModule.showToast?.(error.message || String(error), { duration: 3500 });
      pipelineModal.show({ title: 'Error', step: error.message || String(error), error: error.message });
      await new Promise((resolve) => setTimeout(resolve, 3500));
    } finally {
      busy = false;
      chatCtrl?.setBusy(false);
      setHudLocked(false);
      pipelineModal.hide();
    }
  }

  chatCtrl = mountChatPanel(hub.querySelector('.fugassa-hud-chat'), {
    saveId,
    messages: [],
    busy: false,
    onSubmit: handleSubmit,
    onPipelineActivity: startBackgroundJobPoll,
    scenePopup,
    ttsPrefs: state?.tts_prefs,
    turnPhase: state?.turn_phase,
    composerHeight: playSession?.chatComposerHeight,
    onComposerHeightChange: (height) => persistPlay({ chatComposerHeight: height }),
    getGameTurn: () => state?.turn,
  });

  // Mount the status bar (← Menu / Pause) immediately, before the campaign
  // state has even loaded. Every panel below tolerates `state` being null,
  // so there is no reason "← Menu" should ever depend on a successful
  // `getGameState` — a broken/missing save must never strand the player
  // with no way back to the menu (only the load fails, not the escape hatch).
  refreshPanels();

  try {
    chatCtrl.setStatus('Loading campaign…');
    const loaded = await api.getGameState(saveId);
    setState(loaded?.state || loaded);
    await enrichMap();
    refreshPanels();
    syncChatFromState();
    chatCtrl.setStatus('');
    if (playSession?.chatScrollTop) chatCtrl.scrollTo(playSession.chatScrollTop);

    try {
      const jobsRes = await api.getGameJobs(saveId);
      if (jobsRes?.pipeline_locked || jobsRes?.campaign_phase !== 'idle') {
        lastPipelineMeta = {
          campaignPhase: jobsRes.campaign_phase,
          pipelineLocked: jobsRes.pipeline_locked,
          currentJobLabel: jobsRes.current_job_label,
        };
        startBackgroundJobPoll();
      }
    } catch {
      // optional
    }

    if (!(state?.chat_history || []).length) {
      busy = true;
      chatCtrl.setBusy(true);
      setHudLocked(true);
      pipelineModal.show({ title: 'Opening scene', step: 'GM prepares the world…' });
      try {
        const boot = await api.bootstrapGame(saveId);
        if (!boot?.skipped) {
          setState(boot?.state || state);
          if (boot?.batch_id) {
            await waitForBatchInteractive(boot.batch_id, {
              onPipeline: (p) => pipelineModal.updateFromPipeline(p),
            });
          }
        }
        await enrichMap();
        refreshPanels();
        chatCtrl?.setTurnPhase?.(state?.turn_phase || 'reading');
        chatCtrl?.setMessages(state?.chat_history || [], {
          syncAssets: false,
          autoTts: true,
          forceBottom: true,
        });
        await chatCtrl?.refreshSceneAssets?.();
        startBackgroundJobPoll();
      } catch (error) {
        chatCtrl.setStatus(error.message || String(error));
        uiModule.showToast?.(error.message || String(error), { duration: 4000 });
      } finally {
        busy = false;
        chatCtrl.setBusy(false);
        setHudLocked(false);
        pipelineModal.hide();
      }
    }
  } catch (error) {
    chatCtrl.setStatus(error.message || String(error));
    uiModule.showToast?.(error.message || String(error), { duration: 4000 });
  }

  return {
    getState: () => state,
    getScrollTop: () => chatCtrl?.getScrollTop?.() || 0,
    destroy() {
      if (backgroundPollTimer) {
        clearInterval(backgroundPollTimer);
        backgroundPollTimer = null;
      }
      chatCtrl?.destroy?.();
      scenePopup?.destroy?.();
      pipelineModal?.hide?.();
    },
  };
}
