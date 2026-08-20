// static/js/tts-ai.js
// AI Text-to-Speech Module — supports server TTS and browser Web Speech API

class AITTSManager {
    constructor() {
        this.currentAudio = null;
        this.isPlaying = false;
        this.available = false;
        this.useBrowserTTS = false;
        this.browserVoice = '';
        this.playbackSpeed = 1;
        this._provider = 'disabled';
        this.autoPlay = false;
        this.cache = new Map(); // Client-side audio cache

        // Queue for sequential auto-play
        this._queue = [];       // Array of { text, button, resetFn }
        this._processing = false;

        // Streaming sentence-by-sentence TTS state
        this._streamSentencesSent = 0;  // chars of plain text already queued
        this._streamActive = false;
        this._streamButton = null;
        this._streamResetFn = null;
        this._streamDebounceTimer = null;

        // Check if TTS service is available
        this.checkAvailability();
    }

    async checkAvailability() {
        try {
            // Check user setting first — if TTS is disabled in settings, don't show buttons
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
                if (!this.available) {
                    console.warn('TTS: browser mode selected but speechSynthesis not supported');
                }
            } else if (this.available) {
                this.useBrowserTTS = false;
            } else {
                console.warn('TTS: not available');
            }
        } catch (error) {
            console.error('Failed to check TTS availability:', error);
            this.available = false;
        }
    }

    extractPlainText(content) {
        // Strip <think>/<thinking> blocks (model reasoning)
        let cleaned = content.replace(/<think(?:ing)?>[\s\S]*?<\/think(?:ing)?>/gi, '');

        // Create a temporary div to parse HTML/markdown
        const temp = document.createElement('div');
        temp.innerHTML = cleaned;

        // Remove code blocks
        temp.querySelectorAll('pre, code').forEach(el => el.remove());

        // Get text content
        let text = temp.textContent || temp.innerText || '';

        // Clean up markdown syntax
        text = text
            .replace(/#{1,6}\s/g, '') // Remove headers
            .replace(/\*\*(.+?)\*\*/g, '$1') // Remove bold
            .replace(/\*(.+?)\*/g, '$1') // Remove italic
            .replace(/\[(.+?)\]\(.+?\)/g, '$1') // Remove links
            .replace(/`(.+?)`/g, '$1') // Remove inline code
            .replace(/\n{3,}/g, '\n\n') // Normalize line breaks
            .trim();

        return text;
    }

    getCacheKey(text) {
        // Simple hash function for cache key
        let hash = 0;
        for (let i = 0; i < text.length; i++) {
            const char = text.charCodeAt(i);
            hash = ((hash << 5) - hash) + char;
            hash = hash & hash;
        }
        return hash.toString(36);
    }

    async synthesize(text, onProgress = null) {
        if (!this.available) {
            throw new Error('AI TTS service not available');
        }

        const plainText = this.extractPlainText(text);

        if (!plainText) {
            throw new Error('No text to synthesize');
        }

        // Browser TTS doesn't use synthesize — handled directly in play()
        if (this.useBrowserTTS) {
            return '__browser_tts__';
        }

        const cacheKey = this.getCacheKey(plainText);

        // Check cache first
        if (this.cache.has(cacheKey)) {
            return this.cache.get(cacheKey);
        }

        try {
            if (onProgress) onProgress('synthesizing');

            const response = await fetch('/api/tts/synthesize', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    text: plainText,
                    format: 'audio'
                })
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail?.message || 'Synthesis failed');
            }

            const audioBlob = await response.blob();
            const audioUrl = URL.createObjectURL(audioBlob);

            // Cache the result
            this.cache.set(cacheKey, audioUrl);

            if (onProgress) onProgress('complete');

            return audioUrl;

        } catch (error) {
            if (onProgress) onProgress('error');
            throw error;
        }
    }

    _findBrowserVoice() {
        if (!this.browserVoice) return null;
        const voices = window.speechSynthesis.getVoices();
        const target = this.browserVoice.toLowerCase();
        // Try exact match first, then partial
        return voices.find(v => v.name.toLowerCase() === target) ||
               voices.find(v => v.name.toLowerCase().includes(target)) ||
               null;
    }

    async play(text) {
        // Stop current audio if playing
        this.stop();

        const plainText = this.extractPlainText(text);
        if (!plainText) return;

        if (this.useBrowserTTS) {
            return this._playBrowser(plainText);
        }

        try {
            const audioUrl = await this.synthesize(text);

            this.currentAudio = new Audio(audioUrl);
            await this.currentAudio.play();
            this.isPlaying = true;
            // Note: onended should be set by the caller (addAITTSButton)
            // to reset button state when audio finishes

        } catch (error) {
            console.error('Failed to play audio:', error);
            throw error;
        }
    }

    _playBrowser(plainText) {
        return new Promise((resolve, reject) => {
            const utterance = new SpeechSynthesisUtterance(plainText);
            const voice = this._findBrowserVoice();
            if (voice) utterance.voice = voice;
            utterance.rate = this.playbackSpeed;

            utterance.onend = () => {
                this.isPlaying = false;
                resolve();
            };
            utterance.onerror = (e) => {
                this.isPlaying = false;
                reject(new Error('Browser TTS error: ' + e.error));
            };

            window.speechSynthesis.speak(utterance);
            this.isPlaying = true;
        });
    }

    stop() {
        // Cancel streaming TTS
        this._streamActive = false;
        if (this._streamDebounceTimer) {
            clearTimeout(this._streamDebounceTimer);
            this._streamDebounceTimer = null;
        }
        this._streamSentencesSent = 0;

        // Clear the entire queue and reset all queued buttons
        for (const item of this._queue) {
            if (item.resetFn) item.resetFn();
        }
        this._queue = [];
        this._processing = false;

        if (this.useBrowserTTS) {
            window.speechSynthesis.cancel();
            this.isPlaying = false;
        }
        if (this.currentAudio) {
            this.currentAudio.pause();
            this.currentAudio.currentTime = 0;
            this.currentAudio = null;
            this.isPlaying = false;
        }
    }

    /**
     * Enqueue a message for auto-play. Plays sequentially — each message
     * finishes before the next starts. Stopping any message clears the queue.
     */
    enqueue(text, button, resetFn) {
        this._queue.push({ text, button, resetFn });
        if (!this._processing) {
            this._processQueue();
        }
    }

    async _processQueue() {
        if (this._processing) return;
        this._processing = true;

        while (this._queue.length > 0) {
            const item = this._queue[0];
            try {
                await this._playQueueItem(item);
            } catch (err) {
                console.error('TTS queue item error:', err);
            }
            if (this._queue.length > 0 && this._queue[0] === item) {
                this._queue.shift();
            }
            if (!this._processing) return;
        }

        this._processing = false;
    }

    async _playQueueItem(item) {
        const { text, button, resetFn } = item;
        const ICON_LOADING = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="9" stroke-dasharray="42" stroke-dashoffset="12" stroke-linecap="round"><animateTransform attributeName="transform" type="rotate" from="0 12 12" to="360 12 12" dur="0.8s" repeatCount="indefinite"/></circle></svg>';
        var ICON_STOP = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none"><rect x="5" y="5" width="14" height="14" rx="2"/></svg>';

        button.innerHTML = ICON_LOADING;
        button.classList.add('loading');
        button.style.color = '#ccc';
        button.title = 'Loading...';

        try {
            if (!this._processing) return;

            const audioUrl = await this.synthesize(text);

            if (!this._processing) return;

            button.innerHTML = ICON_STOP;
            button.classList.remove('loading');
            button.classList.add('playing');
            button.title = 'Stop';

            if (this.useBrowserTTS) {
                const plainText = this.extractPlainText(text);
                await this._playBrowser(plainText);
            } else {
                if (this.currentAudio) {
                    this.currentAudio.pause();
                    this.currentAudio = null;
                }

                await new Promise((resolve, reject) => {
                    const audio = new Audio(audioUrl);
                    if (this._provider === 'local' && this.playbackSpeed !== 1) {
                        audio.playbackRate = this.playbackSpeed;
                    }
                    this.currentAudio = audio;
                    audio.onended = () => {
                        this.isPlaying = false;
                        if (this.currentAudio === audio) this.currentAudio = null;
                        resolve();
                    };
                    audio.onerror = (e) => {
                        this.isPlaying = false;
                        if (this.currentAudio === audio) this.currentAudio = null;
                        reject(new Error('Audio playback error'));
                    };
                    audio.onpause = () => {
                        if (this.currentAudio !== audio) {
                            resolve();
                        }
                    };
                    audio.play().then(() => {
                        this.isPlaying = true;
                    }).catch(reject);
                });
            }
        } finally {
            if (resetFn) resetFn();
        }
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
        for (var i = 0; i < this._queue.length; i++) {
            if (this._queue[i].button && this._queue[i].button.classList.contains('streaming-placeholder')) {
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

// Create global AI TTS manager instance
window.aiTTSManager = new AITTSManager();

// Function to add AI TTS button to a message element's action bar
export function addAITTSButton(messageElement, text) {
    if (!window.aiTTSManager.available || window.aiTTSManager._provider === 'disabled') {
        return;
    }

    if (messageElement.querySelector('.ai-tts-button')) {
        return;
    }

    // Find the msg-actions container in the footer
    const actions = messageElement.querySelector('.msg-actions');
    if (!actions) return;

    var ICON_PLAY = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none"><polygon points="6 3 20 12 6 21 6 3"/></svg>';
    var ICON_STOP = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none"><rect x="5" y="5" width="14" height="14" rx="2"/></svg>';
    var ICON_LOADING = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="9" stroke-dasharray="42" stroke-dashoffset="12" stroke-linecap="round"><animateTransform attributeName="transform" type="rotate" from="0 12 12" to="360 12 12" dur="0.8s" repeatCount="indefinite"/></circle></svg>';

    const playButton = document.createElement('button');
    playButton.className = 'ai-tts-button';
    playButton.type = 'button';
    playButton.title = 'Read aloud';
    playButton.innerHTML = ICON_PLAY;
    playButton.style.cssText = 'background:none;border:none;color:#6b7280;cursor:pointer;padding:2px 6px;border-radius:4px;transition:color .15s;line-height:1;display:inline-flex;align-items:center;';

    playButton.addEventListener('mouseenter', () => { playButton.style.color = '#ccc'; });
    playButton.addEventListener('mouseleave', () => {
        if (!playButton.classList.contains('playing') && !playButton.classList.contains('loading')) playButton.style.color = '#6b7280';
    });

    function resetButton() {
        playButton.innerHTML = ICON_PLAY;
        playButton.classList.remove('playing', 'loading');
        playButton.style.color = '#6b7280';
        playButton.title = 'Read aloud';
    }

    playButton.addEventListener('click', async (e) => {
        e.stopPropagation();
        const mgr = window.aiTTSManager;

        if (mgr.isPlaying || mgr._processing) {
            mgr.stop();
            resetButton();
            return;
        }

        mgr.enqueue(text, playButton, resetButton);
    });

    actions.appendChild(playButton);
}

// Stop audio when navigating away
window.addEventListener('beforeunload', () => {
    if (window.aiTTSManager) {
        window.aiTTSManager.stop();
    }
});

export { AITTSManager };

const ttsModule = { AITTSManager, addAITTSButton };
export default ttsModule;
