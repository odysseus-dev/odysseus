import { extractNarrativeForTts, normalizeTextForTts } from './GmTtsPreprocessor.js';
import { isTtsActive, normalizeTtsPrefs } from './ttsSettings.js';

const MAX_SYNTH_CHARS = 4000;

function parsePlayTarget(target) {
  if (target == null) return { button: null, turnNumber: null };
  if (target instanceof HTMLElement) return { button: target, turnNumber: null };
  return {
    button: target.button ?? null,
    turnNumber: target.turnNumber ?? null,
  };
}

function chunkTextForSynthesis(text, maxLen = MAX_SYNTH_CHARS) {
  if (text.length <= maxLen) return [text];
  const chunks = [];
  let rest = text;
  while (rest.length > maxLen) {
    let splitAt = rest.lastIndexOf('. ', maxLen);
    if (splitAt < maxLen * 0.5) splitAt = rest.lastIndexOf(' ', maxLen);
    if (splitAt <= 0) splitAt = maxLen;
    chunks.push(rest.slice(0, splitAt).trim());
    rest = rest.slice(splitAt).trim();
  }
  if (rest) chunks.push(rest);
  return chunks.filter(Boolean);
}

export class FugassaTtsManager {
  constructor(prefs = {}, { onActivityChange } = {}) {
    this.prefs = normalizeTtsPrefs(prefs);
    this.ready = false;
    this._readyPromise = null;
    this._voicesByLang = {};
    this.currentAudio = null;
    this.isPlaying = false;
    this._processing = false;
    this._activeTurn = null;
    this._cache = new Map();
    this._lastAutoTurn = null;
    this._autoRetryTurn = null;
    this._playToken = 0;
    this._onActivityChange = typeof onActivityChange === 'function' ? onActivityChange : null;
  }

  needsAutoRetry() {
    return this._autoRetryTurn != null;
  }

  _clearAutoRetryPoll() {
    if (this._autoRetryTimer) {
      clearInterval(this._autoRetryTimer);
      this._autoRetryTimer = null;
    }
  }

  _scheduleAutoRetry(pending) {
    if (this._autoRetryTimer || !pending) return;
    this._autoRetryTimer = setInterval(async () => {
      if (!this._autoRetryTurn) {
        this._clearAutoRetryPoll();
        return;
      }
      const ready = await this.checkReady();
      if (!ready) return;
      this._clearAutoRetryPoll();
      await this.maybeAutoPlay(pending.messages, pending.context);
    }, 2000);
  }

  updatePrefs(prefs) {
    this.prefs = normalizeTtsPrefs(prefs);
  }

  async ensureReady(lang = this.prefs.lang) {
    if (this.ready) return true;
    if (this._readyPromise) return this._readyPromise;
    this._readyPromise = this.checkReady(lang).finally(() => {
      this._readyPromise = null;
    });
    return this._readyPromise;
  }

  async checkReady(lang = this.prefs.lang) {
    const l = (lang || 'cs').toLowerCase();
    try {
      const res = await fetch(`/api/tts/voices?engine=supertonic&lang=${encodeURIComponent(l)}`);
      if (!res.ok) {
        this.ready = false;
        return false;
      }
      const data = await res.json();
      this.ready = Boolean(data.ready);
      if (Array.isArray(data.voices)) {
        this._voicesByLang[l] = data.voices;
      }
      return this.ready;
    } catch {
      this.ready = false;
      return false;
    }
  }

  getVoices(lang = this.prefs.lang) {
    return this._voicesByLang[(lang || 'cs').toLowerCase()] || [];
  }

  getActivity() {
    if (!this._processing && !this.isPlaying) return null;
    return {
      turnNumber: this._activeTurn,
      phase: this.isPlaying ? 'playing' : 'loading',
    };
  }

  _notifyActivity() {
    this._onActivityChange?.(this.getActivity());
  }

  stop() {
    this._playToken += 1;
    this._processing = false;
    this._clearAutoRetryPoll();
    this._autoRetryTurn = null;
    if (this.currentAudio) {
      this.currentAudio.pause();
      this.currentAudio.currentTime = 0;
      this.currentAudio = null;
    }
    this.isPlaying = false;
    this._activeTurn = null;
    this._notifyActivity();
  }

  /** Mark the latest GM turn so auto mode does not re-read it on submit. */
  suppressAutoForLastGm(messages) {
    const lastGm = [...(messages || [])].reverse().find((m) => m?.role === 'assistant');
    const turn = Number(lastGm?.turn_number);
    if (Number.isFinite(turn)) {
      this._lastAutoTurn = turn;
      this._autoRetryTurn = null;
      this._clearAutoRetryPoll();
    }
  }

  _cacheKey(text) {
    const p = this.prefs;
    return `${p.lang}|${p.speaker_id}|${p.speed}|${text}`;
  }

  async _synthesize(text) {
    const key = this._cacheKey(text);
    if (this._cache.has(key)) return this._cache.get(key);

    const p = this.prefs;
    const res = await fetch('/api/tts/synthesize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text,
        format: 'audio',
        engine: 'supertonic',
        lang: p.lang,
        speaker_id: p.speaker_id,
        speed: p.speed,
      }),
    });
    if (!res.ok) {
      let detail = 'Synthesis failed';
      try {
        const err = await res.json();
        detail = err.detail?.message || err.detail || detail;
      } catch {
        // ignore
      }
      throw new Error(detail);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    this._cache.set(key, url);
    return url;
  }

  async _playUrl(url, token) {
    if (this.currentAudio) {
      this.currentAudio.pause();
      this.currentAudio = null;
    }
    const audio = new Audio(url);
    if (this.prefs.speed !== 1) {
      audio.playbackRate = this.prefs.speed;
    }
    this.currentAudio = audio;
    this.isPlaying = true;
    this._notifyActivity();
    await new Promise((resolve, reject) => {
      audio.onended = () => {
        if (token !== this._playToken) {
          resolve();
          return;
        }
        this.isPlaying = false;
        if (this.currentAudio === audio) this.currentAudio = null;
        this._notifyActivity();
        resolve();
      };
      audio.onerror = () => {
        if (token !== this._playToken) {
          resolve();
          return;
        }
        this.isPlaying = false;
        this._notifyActivity();
        reject(new Error('Audio playback failed'));
      };
      audio.play().catch(reject);
    });
  }

  async playText(rawText, target = null) {
    if (!isTtsActive(this.prefs)) return false;
    const narrative = normalizeTextForTts(extractNarrativeForTts(rawText));
    if (!narrative) return false;

    const { turnNumber: explicitTurn } = parsePlayTarget(target);
    let turnNumber = explicitTurn;
    if (turnNumber == null && target instanceof HTMLElement && target?.dataset?.ttsTurn != null) {
      const parsed = Number(target.dataset.ttsTurn);
      if (Number.isFinite(parsed)) turnNumber = parsed;
    }

    this.stop();
    const token = this._playToken;
    this._activeTurn = turnNumber;
    this._processing = true;
    this._notifyActivity();

    const ready = await this.ensureReady();
    if (!ready || token !== this._playToken) {
      this._processing = false;
      this._activeTurn = null;
      this._notifyActivity();
      return false;
    }

    const chunks = chunkTextForSynthesis(narrative);
    let started = false;
    try {
      for (let i = 0; i < chunks.length; i += 1) {
        if (token !== this._playToken) break;
        const url = await this._synthesize(chunks[i]);
        if (token !== this._playToken) break;
        started = true;
        this._processing = false;
        this._notifyActivity();
        await this._playUrl(url, token);
        if (token !== this._playToken) break;
        if (i < chunks.length - 1) {
          this._processing = true;
          this._notifyActivity();
        }
      }
    } catch (err) {
      console.warn('Fugassa TTS:', err);
      return false;
    } finally {
      if (token === this._playToken) {
        this._processing = false;
        this.isPlaying = false;
        this._activeTurn = null;
        this._notifyActivity();
      }
    }
    return started;
  }

  async maybeAutoPlay(messages, { turnPhase, currentTurn, gameTurn } = {}) {
    const prefs = normalizeTtsPrefs(this.prefs);
    if (!prefs.enabled || prefs.mode !== 'auto') return false;
    if (turnPhase !== 'reading') return false;

    const lastGm = [...(messages || [])].reverse().find((m) => m?.role === 'assistant');
    if (!lastGm) return false;

    const msgTurn = Number(lastGm.turn_number);
    const resolvedTurn = Number.isFinite(msgTurn)
      ? msgTurn
      : Number.isFinite(Number(currentTurn))
        ? Number(currentTurn)
        : Number.isFinite(Number(gameTurn))
          ? Number(gameTurn)
          : NaN;
    if (!Number.isFinite(resolvedTurn)) return false;
    if (this._lastAutoTurn === resolvedTurn) return false;

    const started = await this.playText(lastGm.content || '', { turnNumber: resolvedTurn });
    if (started) {
      this._lastAutoTurn = resolvedTurn;
      this._autoRetryTurn = null;
      this._clearAutoRetryPoll();
    } else if (!this.ready) {
      this._autoRetryTurn = resolvedTurn;
      this._scheduleAutoRetry({
        messages,
        context: { turnPhase, currentTurn, gameTurn },
      });
    } else {
      this._autoRetryTurn = null;
      this._clearAutoRetryPoll();
    }
    return started;
  }
}
