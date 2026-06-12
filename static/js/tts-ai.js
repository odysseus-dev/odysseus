// static/js/tts-ai.js
// AI Text-to-Speech Module — supports server TTS and browser Web Speech API

import { markdownToSpeech } from './ttsText.js';

// Read-aloud button glyphs (ChatGPT/Gemini style speaker + pause/resume)
const ICON_SPEAKER = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor" stroke="none"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/></svg>';
const ICON_PAUSE = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none"><rect x="5" y="4" width="5" height="16" rx="1.5"/><rect x="14" y="4" width="5" height="16" rx="1.5"/></svg>';
const ICON_RESUME = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none"><polygon points="6 3 20 12 6 21 6 3"/></svg>';
const ICON_LOADING = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="9" stroke-dasharray="42" stroke-dashoffset="12" stroke-linecap="round"><animateTransform attributeName="transform" type="rotate" from="0 12 12" to="360 12 12" dur="0.8s" repeatCount="indefinite"/></circle></svg>';

/** @typedef {'idle'|'loading'|'playing'|'paused'} PlaybackState */

class AITTSManager {
    constructor() {
        this.available = false;
        this.useBrowserTTS = false;
        this.browserVoice = '';
        this.playbackSpeed = 1;
        this._provider = 'disabled';
        this.autoPlay = false;
        this.cache = new Map();

        // Monotonic session id — incremented on every stop(). Async work checks
        // this so stale synthesize/play promises cannot touch UI or audio after
        // the user switches messages, changes voice, or cancels.
        this._gen = 0;

        /** @type {null | { gen:number, button:HTMLElement, resetFn:Function, audio:HTMLAudioElement|null, utterance:SpeechSynthesisUtterance|null, state:PlaybackState, plainText:string }} */
        this._playback = null;

        // Sequential queue (streaming auto-play only)
        this._queue = [];
        this._queueGen = 0;
        this._queueRunning = false;

        // Streaming sentence-by-sentence TTS state
        this._streamSentencesSent = 0;
        this._streamActive = false;
        this._streamButton = null;
        this._streamResetFn = null;
        this._streamDebounceTimer = null;

        this.ready = this.checkAvailability();
    }

    // ── Legacy flags for callers that still read them (chat.js, keyboard-shortcuts) ──

    get isPlaying() {
        return this._playback != null && this._playback.state === 'playing';
    }

    get isPaused() {
        return this._playback != null && this._playback.state === 'paused';
    }

    get _processing() {
        return this._queueRunning || (this._playback != null && this._playback.state === 'loading');
    }

    // ── Availability ──

    async checkAvailability() {
        try {
            try {
                const settingsRes = await fetch('/api/auth/settings', { credentials: 'same-origin' });
                const settings = await settingsRes.json();
                if (settings.tts_enabled === false) {
                    this.available = false;
                    this._provider = 'disabled';
                    return;
                }
            } catch {}

            const response = await fetch('/api/tts/stats');
            const stats = await response.json();
            this.available = stats.available && stats.ready;
            this.playbackSpeed = stats.speed || 1;
            this._provider = stats.provider || 'disabled';

            if (stats.provider === 'browser') {
                this.useBrowserTTS = true;
                this.browserVoice = stats.voice || '';
                this.available = 'speechSynthesis' in window;
            } else if (this.available) {
                this.useBrowserTTS = false;
            }
        } catch (error) {
            console.error('Failed to check TTS availability:', error);
            this.available = false;
        }
    }

    extractPlainText(content) {
        return markdownToSpeech(content);
    }

    getCacheKey(text) {
        const keySource = `${this._provider}|${this.playbackSpeed}|${text}`;
        let hash = 0;
        for (let i = 0; i < keySource.length; i++) {
            hash = ((hash << 5) - hash) + keySource.charCodeAt(i);
            hash = hash & hash;
        }
        return hash.toString(36);
    }

    // ── Synthesis ──

    async synthesize(text, gen) {
        if (!this.available) throw new Error('AI TTS service not available');

        const plainText = this.extractPlainText(text);
        if (!plainText) throw new Error('No text to synthesize');

        if (this.useBrowserTTS) return '__browser_tts__';

        const cacheKey = this.getCacheKey(plainText);
        if (this.cache.has(cacheKey)) return this.cache.get(cacheKey);

        const response = await fetch('/api/tts/synthesize', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: plainText, format: 'audio' }),
        });

        if (gen !== this._gen) throw new Error('aborted');

        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            if (error.detail?.fallback === 'browser' && 'speechSynthesis' in window) {
                this.useBrowserTTS = true;
                this._provider = 'browser';
                return '__browser_tts__';
            }
            throw new Error(error.detail?.message || 'Synthesis failed');
        }

        const audioBlob = await response.blob();
        if (gen !== this._gen) throw new Error('aborted');

        const audioUrl = URL.createObjectURL(audioBlob);
        this.cache.set(cacheKey, audioUrl);
        return audioUrl;
    }

    _findBrowserVoice() {
        if (!this.browserVoice) return null;
        const voices = window.speechSynthesis.getVoices();
        const target = this.browserVoice.toLowerCase();
        return voices.find(v => v.name.toLowerCase() === target) ||
               voices.find(v => v.name.toLowerCase().includes(target)) ||
               null;
    }

    // ── UI helpers ──

    _applyButtonState(button, state) {
        if (!button) return;
        button.classList.remove('playing', 'loading');
        if (state === 'loading') {
            button.innerHTML = ICON_LOADING;
            button.classList.add('loading');
            button.style.color = '#ccc';
            button.title = 'Loading...';
        } else if (state === 'playing') {
            button.innerHTML = ICON_PAUSE;
            button.classList.add('playing');
            button.style.color = '#ccc';
            button.title = 'Pause';
        } else if (state === 'paused') {
            button.innerHTML = ICON_RESUME;
            button.classList.add('playing');
            button.style.color = '#ccc';
            button.title = 'Resume';
        } else {
            button.innerHTML = ICON_SPEAKER;
            button.style.color = '#6b7280';
            button.title = 'Read aloud';
        }
    }

    _resetPlaybackButton(pb) {
        if (pb && pb.resetFn) pb.resetFn();
        else if (pb && pb.button) this._applyButtonState(pb.button, 'idle');
    }

    _stopMedia() {
        if ('speechSynthesis' in window) {
            try { window.speechSynthesis.cancel(); } catch {}
        }
        const audio = this._playback?.audio;
        if (audio) {
            audio.onended = null;
            audio.onerror = null;
            audio.onpause = null;
            try {
                audio.pause();
                audio.currentTime = 0;
            } catch {}
        }
        if (this._playback) {
            this._playback.audio = null;
            this._playback.utterance = null;
        }
    }

    /** End the current clip only — does not flush the streaming queue. */
    _endSession() {
        this._gen++;
        this._stopMedia();
        this._resetPlaybackButton(this._playback);
        this._playback = null;
    }

    /** Hard stop — aborts in-flight work, clears the queue, resets all playback UI. */
    stop() {
        this._endSession();
        this._queueGen++;

        this._streamActive = false;
        if (this._streamDebounceTimer) {
            clearTimeout(this._streamDebounceTimer);
            this._streamDebounceTimer = null;
        }
        this._streamSentencesSent = 0;

        for (const item of this._queue) {
            if (item.resetFn) item.resetFn();
        }
        this._queue = [];
        this._queueRunning = false;

        this._stopMedia();
        this._resetPlaybackButton(this._playback);
        this._playback = null;
    }

    /** True when `button` owns the current playback session (playing or paused). */
    isActiveButton(button) {
        return !!button && this._playback && this._playback.button === button &&
            (this._playback.state === 'playing' || this._playback.state === 'paused' || this._playback.state === 'loading');
    }

    pause() {
        const pb = this._playback;
        if (!pb || pb.state !== 'playing') return;

        if (pb.audio) {
            pb.audio.pause();
        } else if (pb.utterance && 'speechSynthesis' in window) {
            window.speechSynthesis.pause();
        }
        pb.state = 'paused';
        this._applyButtonState(pb.button, 'paused');
    }

    resume() {
        const pb = this._playback;
        if (!pb || pb.state !== 'paused') return;

        if (pb.audio) {
            pb.audio.play().catch(err => console.error('TTS resume failed:', err));
        } else if (pb.utterance && 'speechSynthesis' in window) {
            window.speechSynthesis.resume();
        }
        pb.state = 'playing';
        this._applyButtonState(pb.button, 'playing');
    }

    /**
     * Start read-aloud for one message. Stops any previous playback first so
     * switching between responses is always a clean hand-off.
     */
    async play(text, button, resetFn) {
        this.stop();
        await this._runPlayback(text, button, resetFn);
    }

    /** Play one clip without clearing the streaming queue (used by enqueue). */
    async _runPlayback(text, button, resetFn) {
        this._endSession();
        const gen = this._gen;
        const plainText = this.extractPlainText(text);
        if (!plainText) return;

        this._playback = {
            gen, button, resetFn, audio: null, utterance: null,
            state: 'loading', plainText,
        };
        this._applyButtonState(button, 'loading');

        try {
            const audioUrl = await this.synthesize(text, gen);
            if (gen !== this._gen || !this._playback || this._playback.gen !== gen) return;

            if (this.useBrowserTTS || audioUrl === '__browser_tts__') {
                await this._playBrowserSession(gen, plainText);
            } else {
                await this._playAudioSession(gen, audioUrl);
            }
        } catch (err) {
            if (err.message !== 'aborted') console.error('TTS play error:', err);
            if (gen === this._gen && this._playback && this._playback.gen === gen) {
                this._resetPlaybackButton(this._playback);
                this._playback = null;
            }
        }
    }

    async _playAudioSession(gen, audioUrl) {
        const pb = this._playback;
        if (!pb || pb.gen !== gen) return;

        const audio = new Audio(audioUrl);
        pb.audio = audio;

        await new Promise((resolve, reject) => {
            const finish = () => {
                audio.onended = null;
                audio.onerror = null;
                resolve();
            };

            audio.onended = () => {
                if (gen !== this._gen || this._playback?.gen !== gen) { finish(); return; }
                pb.state = 'idle';
                finish();
            };
            audio.onerror = () => {
                if (gen !== this._gen) { finish(); return; }
                reject(new Error('Audio playback error'));
            };

            audio.play()
                .then(() => {
                    if (gen !== this._gen || this._playback?.gen !== gen) {
                        audio.pause();
                        finish();
                        return;
                    }
                    pb.state = 'playing';
                    this._applyButtonState(pb.button, 'playing');
                })
                .catch(reject);
        });

        if (gen === this._gen && this._playback && this._playback.gen === gen) {
            this._resetPlaybackButton(pb);
            this._playback = null;
        }
    }

    async _playBrowserSession(gen, plainText) {
        const pb = this._playback;
        if (!pb || pb.gen !== gen) return;

        await new Promise((resolve, reject) => {
            const utterance = new SpeechSynthesisUtterance(plainText);
            pb.utterance = utterance;
            const voice = this._findBrowserVoice();
            if (voice) utterance.voice = voice;
            utterance.rate = this.playbackSpeed;

            utterance.onstart = () => {
                if (gen !== this._gen || this._playback?.gen !== gen) return;
                pb.state = 'playing';
                this._applyButtonState(pb.button, 'playing');
            };
            utterance.onend = () => {
                if (gen !== this._gen || this._playback?.gen !== gen) { resolve(); return; }
                pb.state = 'idle';
                resolve();
            };
            utterance.onerror = (e) => {
                if (gen !== this._gen) { resolve(); return; }
                if (e.error === 'interrupted' || e.error === 'canceled') { resolve(); return; }
                reject(new Error('Browser TTS error: ' + e.error));
            };

            window.speechSynthesis.speak(utterance);
        });

        if (gen === this._gen && this._playback && this._playback.gen === gen) {
            this._resetPlaybackButton(pb);
            this._playback = null;
        }
    }

    // ── Queue (streaming auto-play) ──

    enqueue(text, button, resetFn) {
        this._queue.push({ text, button, resetFn });
        if (!this._queueRunning) this._processQueue();
    }

    async _processQueue() {
        if (this._queueRunning) return;
        this._queueRunning = true;
        const queueGen = this._queueGen;

        while (this._queue.length > 0 && queueGen === this._queueGen) {
            const item = this._queue.shift();
            try {
                await this._runPlayback(item.text, item.button, item.resetFn);
            } catch (err) {
                console.error('TTS queue item error:', err);
            }
            if (queueGen !== this._queueGen) break;
        }

        this._queueRunning = false;
    }

    // ── Streaming TTS (sentence-by-sentence) ──

    streamingStart() {
        this._streamSentencesSent = 0;
        this._streamActive = true;
        this._streamButton = null;
        this._streamResetFn = null;
    }

    streamingUpdate(accumulatedText) {
        if (!this._streamActive || !this.available || !this.autoPlay) return;
        if (this._streamDebounceTimer) return;
        this._streamDebounceTimer = setTimeout(() => {
            this._streamDebounceTimer = null;
            this._processStreamingSentences(accumulatedText);
        }, 150);
    }

    _processStreamingSentences(accumulatedText) {
        if (!this._streamActive) return;

        var text = accumulatedText
            .replace(/```[\s\S]*?```/g, '')
            .replace(/```[\s\S]*$/g, '');

        var plainText = this.extractPlainText(text);
        if (!plainText || plainText.length <= this._streamSentencesSent) return;

        var newRegion = plainText.substring(this._streamSentencesSent);
        var sentences = [];
        var current = '';
        for (var i = 0; i < newRegion.length; i++) {
            current += newRegion[i];
            var ch = newRegion[i];
            var next = newRegion[i + 1];
            if ((ch === '.' || ch === '!' || ch === '?') && next && /\s/.test(next)) {
                var lastWord = current.trim().split(/\s/).pop() || '';
                if (/^\d+\.$/.test(lastWord)) continue;
                if (/^[A-Z][a-z]?\.$/.test(lastWord)) continue;
                sentences.push(current.trim());
                current = '';
            }
        }

        if (sentences.length === 0) return;

        var advancedChars = 0;
        for (var j = 0; j < sentences.length; j++) {
            var sentence = sentences[j];
            if (sentence.length < 15) {
                advancedChars += sentence.length + 1;
                continue;
            }
            var btn = this._streamButton || this._createPlaceholderButton();
            var resetFn = this._streamResetFn || function() {};
            this.enqueue(sentence, btn, resetFn);
            advancedChars += sentence.length + 1;
        }

        this._streamSentencesSent += advancedChars;
    }

    _createPlaceholderButton() {
        var btn = document.createElement('button');
        btn.style.display = 'none';
        btn.className = 'ai-tts-button streaming-placeholder';
        return btn;
    }

    streamingAttachButton(button, resetFn) {
        this._streamButton = button;
        this._streamResetFn = resetFn;
        if (this._playback && this._playback.button?.classList?.contains('streaming-placeholder')) {
            this._playback.button = button;
            this._playback.resetFn = resetFn;
            this._applyButtonState(button, this._playback.state === 'paused' ? 'paused' : 'playing');
        }
        for (var i = 0; i < this._queue.length; i++) {
            if (this._queue[i].button?.classList?.contains('streaming-placeholder')) {
                this._queue[i].button = button;
                this._queue[i].resetFn = resetFn;
            }
        }
    }

    streamingEnd(finalText) {
        if (!this._streamActive) return;
        this._streamActive = false;
        if (this._streamDebounceTimer) {
            clearTimeout(this._streamDebounceTimer);
            this._streamDebounceTimer = null;
        }

        var text = finalText
            .replace(/```[\s\S]*?```/g, '')
            .replace(/```[\s\S]*$/g, '');

        var plainText = this.extractPlainText(text);
        if (!plainText) return;

        var remaining = plainText.substring(this._streamSentencesSent).trim();
        if (remaining.length >= 15) {
            var btn = this._streamButton || this._createPlaceholderButton();
            var resetFn = this._streamResetFn || function() {};
            this.enqueue(remaining, btn, resetFn);
        }
        this._streamSentencesSent = 0;
    }

    clearCache() {
        for (const url of this.cache.values()) {
            URL.revokeObjectURL(url);
        }
        this.cache.clear();
    }
}

window.aiTTSManager = new AITTSManager();

export function addAITTSButton(messageElement, text) {
    const mgr = window.aiTTSManager;
    if (!mgr) return;
    Promise.resolve(mgr.ready).then(() => {
        _addAITTSButtonNow(messageElement, text);
    });
}

function _addAITTSButtonNow(messageElement, text) {
    if (!window.aiTTSManager.available || window.aiTTSManager._provider === 'disabled') {
        return;
    }

    if (messageElement.querySelector('.ai-tts-button')) {
        return;
    }

    const actions = messageElement.querySelector('.msg-actions');
    if (!actions) return;

    const playButton = document.createElement('button');
    playButton.className = 'ai-tts-button';
    playButton.type = 'button';
    playButton.title = 'Read aloud';
    playButton.innerHTML = ICON_SPEAKER;
    playButton.style.cssText = 'background:none;border:none;color:#6b7280;cursor:pointer;padding:2px 6px;border-radius:4px;transition:color .15s;line-height:1;display:inline-flex;align-items:center;';

    playButton.addEventListener('mouseenter', () => { playButton.style.color = '#ccc'; });
    playButton.addEventListener('mouseleave', () => {
        if (!playButton.classList.contains('playing') && !playButton.classList.contains('loading')) {
            playButton.style.color = '#6b7280';
        }
    });

    function resetButton() {
        window.aiTTSManager._applyButtonState(playButton, 'idle');
    }

    playButton.addEventListener('click', (e) => {
        e.stopPropagation();
        const mgr = window.aiTTSManager;

        // Loading → cancel
        if (mgr.isActiveButton(playButton) && mgr._playback?.state === 'loading') {
            mgr.stop();
            return;
        }

        // Same message → pause / resume
        if (mgr.isActiveButton(playButton)) {
            if (mgr.isPaused) mgr.resume();
            else mgr.pause();
            return;
        }

        // Different message (or idle) → play() stops the previous session first
        const raw = messageElement.closest?.('.msg')?.dataset?.raw
            || messageElement.dataset?.raw || text;
        mgr.play(raw, playButton, resetButton);
    });

    actions.appendChild(playButton);
}

window.addEventListener('beforeunload', () => {
    if (window.aiTTSManager) window.aiTTSManager.stop();
});

export const TTS_ICONS = {
    speaker: ICON_SPEAKER,
    pause: ICON_PAUSE,
    resume: ICON_RESUME,
    loading: ICON_LOADING,
};

export { AITTSManager };

const ttsModule = { AITTSManager, addAITTSButton };
export default ttsModule;
