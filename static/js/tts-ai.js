// static/js/tts-ai.js
// AI Text-to-Speech Module — supports server TTS and browser Web Speech API

import { getSettings } from './appConfig.js';
import spinnerModule from './spinner.js';

// Tokens are cleaned of punctuation for matching only. The visible span keeps
// the original token (including punctuation) so the answer's text is unchanged.
function normalizeReadalongToken(token) {
    let text = String(token == null ? '' : token);
    if (text.normalize) text = text.normalize('NFKC');
    return text.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '');
}

// Align the spoken tokens with the visible answer tokens. Markdown remnants
// (list bullets, blockquote markers, table pipes) and stripped code can make
// one side longer than the other, so skip ahead on whichever side diverges.
function alignReadalongWords(ttsWords, bodyTokens) {
    const indexTokens = (tokens) => {
        const index = new Map();
        tokens.forEach((word, position) => {
            if (!word.norm) return;
            const positions = index.get(word.norm);
            if (positions) positions.push(position);
            else index.set(word.norm, [position]);
        });
        return index;
    };
    const nextPosition = (index, norm, after) => {
        const positions = index.get(norm);
        if (!positions) return -1;
        let lo = 0;
        let hi = positions.length;
        while (lo < hi) {
            const mid = (lo + hi) >> 1;
            if (positions[mid] <= after) lo = mid + 1;
            else hi = mid;
        }
        return lo < positions.length ? positions[lo] : -1;
    };

    const ttsIndex = indexTokens(ttsWords);
    const bodyIndex = indexTokens(bodyTokens);
    let ti = 0;
    let bi = 0;
    while (ti < ttsWords.length && bi < bodyTokens.length) {
        const ttsWord = ttsWords[ti];
        const bodyWord = bodyTokens[bi];

        if (!ttsWord.norm) { ti += 1; continue; }
        if (!bodyWord.norm) { bi += 1; continue; }

        if (ttsWord.norm === bodyWord.norm) {
            ttsWord.el = bodyWord.el;
            ti += 1;
            bi += 1;
            continue;
        }

        // Skip whichever side has a known match closer ahead. This keeps a
        // long code block (spoken but not highlighted) from drifting the rest
        // of the answer out of sync.
        const bodyAhead = nextPosition(bodyIndex, ttsWord.norm, bi);
        const ttsAhead = nextPosition(ttsIndex, bodyWord.norm, ti);

        if (bodyAhead >= 0 && (ttsAhead < 0 || bodyAhead - bi <= ttsAhead - ti)) {
            bi = bodyAhead;
        } else if (ttsAhead >= 0) {
            ti = ttsAhead;
        } else {
            ti += 1;
            bi += 1;
        }
    }
    return ttsWords;
}

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
        this._synthInflight = new Map(); // Deduplicates concurrent synthesis requests
        this._browserUtterance = null;
        this._paused = false;

        // Web Audio analyser for amplitude-reactive wave bars
        this._audioContext = null;
        this._analyser = null;
        this._analyserSource = null;
        this._analyserAudio = null;
        this._analyserButton = null;
        this._analyserBars = [];
        this._analyserData = null;
        this._analyserRaf = null;

        // In-place read-along word highlighter (wraps the existing answer body)
        this._readalongMessage = null;
        this._readalongBody = null;
        this._readalongWordSpans = [];
        this._readalongWords = [];
        this._readalongIndex = -1;
        this._readalongTotalWeight = 1;
        this._readalongActive = false;
        this._readalongProvider = '';
        this._readalongAudio = null;
        this._readalongAudioBound = null;
        this._readalongTimeupdate = null;
        this._readalongRaf = null;
        this._readalongScrollTimer = null;
        this._readalongScrollMarked = false;

        // Queue for sequential auto-play
        this._queue = [];       // Array of { text, button, resetFn }
        this._processing = false;

        // Streaming sentence-by-sentence TTS state
        this._streamSentencesSent = 0;  // chars of plain text already queued
        this._streamActive = false;
        this._streamButton = null;
        this._streamResetFn = null;
        this._streamMessageElement = null;
        this._streamDebounceTimer = null;

        // Check if TTS service is available
        this.checkAvailability();
    }

    async checkAvailability() {
        try {
            // Check user setting first — if TTS is disabled in settings, don't show buttons.
            // settings.js re-calls this right after saving TTS settings; it invalidates
            // the shared cache before doing so, so this still sees the new value.
            try {
                const settings = await getSettings();
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
        // Strip <think>/<thinking> blocks (model reasoning), including a block
        // that is still streaming and has not received its closing tag yet.
        let cleaned = String(content == null ? '' : content)
            .replace(/<think(?:ing)?\b[^>]*>[\s\S]*?<\/think(?:ing)?>/gi, '')
            .replace(/<think(?:ing)?\b[^>]*>[\s\S]*$/gi, '')
            .replace(/<\/think(?:ing)?>/gi, '');

        // Create a temporary div to parse HTML/markdown
        const temp = document.createElement('div');
        temp.innerHTML = cleaned;

        // Remove reasoning UI/custom elements before reading text, then code.
        temp.querySelectorAll('think, .thinking-section, .thinking-content, .thinking-header')
            .forEach(el => el.remove());
        temp.querySelectorAll('pre, code').forEach(el => el.remove());

        // Get text content
        let text = temp.textContent || temp.innerText || '';
        text = text
            .replace(/^\s*(?:view\s+)?thinking process(?:\s+\d+(?:\.\d+)?\s*s)?\s*$/gim, '')
            .replace(/^\s*thought for \d+(?:\.\d+)?\s*s\s*$/gim, '')
            .replace(/\bthinking process\s+\d+(?:\.\d+)?\s*s\b/gi, '')
            .replace(/\bthought for\s+\d+(?:\.\d+)?\s*s\b/gi, '');

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

        // Reuse an in-flight request for the same sentence. This lets the
        // streaming queue prefetch the next sentence while the current one
        // plays without ever synthesizing it twice.
        if (this._synthInflight.has(cacheKey)) {
            return this._synthInflight.get(cacheKey);
        }

        const request = (async () => {
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
            } finally {
                this._synthInflight.delete(cacheKey);
            }
        })();

        this._synthInflight.set(cacheKey, request);
        return request;
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

    _prefersReducedMotion() {
        try {
            return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
        } catch (_) {
            return false;
        }
    }

    _getAudioContext() {
        if (this._audioContext) return this._audioContext;
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (!AudioCtx) return null;
        this._audioContext = new AudioCtx();
        return this._audioContext;
    }

    _resumeAudioContext() {
        const ctx = this._audioContext;
        if (ctx && ctx.state === 'suspended' && typeof ctx.resume === 'function') {
            try {
                const resumed = ctx.resume();
                if (resumed && typeof resumed.catch === 'function') resumed.catch(() => {});
            } catch (_) {}
        }
    }

    _clearAnalyserBars() {
        for (const bar of this._analyserBars) {
            if (bar && bar.style) bar.style.transform = '';
        }
        this._analyserBars = [];
    }

    _bindAnalyserButton(button) {
        if (!button) return;
        if (this._analyserButton && this._analyserButton !== button) {
            this._clearAnalyserBars();
            this._analyserButton.classList.remove('analyser');
        }
        this._analyserButton = button;
        this._analyserBars = Array.prototype.slice.call(button.querySelectorAll('.tts-wave-bar'));
        if (this._analyser && this._analyserBars.length) {
            button.classList.add('analyser');
        }
    }

    _startAnalyserLoop() {
        if (this._analyserRaf) cancelAnimationFrame(this._analyserRaf);
        const tick = () => {
            const analyser = this._analyser;
            const data = this._analyserData;
            if (!analyser || !data) {
                this._analyserRaf = null;
                return;
            }

            let bars = this._analyserBars;
            // The live-stream button can swap its play icon for the wave icon
            // after the analyser is already attached; pick the bars up then.
            if ((!bars || !bars.length) && this._analyserButton) {
                bars = Array.prototype.slice.call(this._analyserButton.querySelectorAll('.tts-wave-bar'));
                this._analyserBars = bars;
                if (bars.length) this._analyserButton.classList.add('analyser');
            }
            if (!bars || !bars.length) {
                this._analyserRaf = requestAnimationFrame(tick);
                return;
            }

            analyser.getByteFrequencyData(data);
            const binCount = data.length;
            const startBin = binCount > 8 ? 1 : 0; // skip the DC/lowest bin
            // Voice energy lives in the lower half of the spectrum; spread the
            // four bars across those bins so every bar reacts to speech.
            const usableBins = Math.max(1, Math.min(binCount - startBin, 16));
            const floor = 0.25;
            const ceiling = 1.4;

            for (let i = 0; i < bars.length; i++) {
                const from = startBin + Math.floor((usableBins * i) / bars.length);
                const to = Math.max(from + 1, startBin + Math.floor((usableBins * (i + 1)) / bars.length));
                let sum = 0;
                let count = 0;
                for (let j = from; j < to && j < binCount; j++) {
                    sum += data[j];
                    count++;
                }
                const level = count ? (sum / count) / 255 : 0;
                const shaped = Math.pow(level, 0.6);
                const scale = floor + Math.min(1, shaped * 2.2) * (ceiling - floor);
                if (bars[i] && bars[i].style) {
                    bars[i].style.transform = 'scaleY(' + scale.toFixed(3) + ')';
                }
            }

            this._analyserRaf = requestAnimationFrame(tick);
        };
        this._analyserRaf = requestAnimationFrame(tick);
    }

    _startAnalyser(audio, button) {
        if (!audio || !button) return false;
        if (this._prefersReducedMotion()) return false;

        this._teardownAnalyser();

        let ctx = null;
        let source = null;
        try {
            ctx = this._getAudioContext();
            if (!ctx || typeof ctx.createMediaElementSource !== 'function' || typeof ctx.createAnalyser !== 'function') {
                return false;
            }

            const analyser = ctx.createAnalyser();
            analyser.fftSize = 64;
            analyser.smoothingTimeConstant = 0.72;

            source = ctx.createMediaElementSource(audio);
            this._analyserSource = source;
            this._analyserAudio = audio;
            this._analyser = analyser;
            this._analyserData = new Uint8Array(analyser.frequencyBinCount);

            source.connect(analyser);
            analyser.connect(ctx.destination);
            this._resumeAudioContext();

            this._bindAnalyserButton(button);
            this._startAnalyserLoop();
            return true;
        } catch (err) {
            console.warn('TTS: audio analyser unavailable, using CSS wave fallback', err);
            if (this._analyserRaf) {
                cancelAnimationFrame(this._analyserRaf);
                this._analyserRaf = null;
            }
            this._clearAnalyserBars();
            if (button) button.classList.remove('analyser');

            // Creating a MediaElementAudioSourceNode reroutes the element's audio.
            // If setup failed after that, connect it straight to the destination
            // so playback is not silenced by the fallback.
            if (source) {
                try { source.connect(ctx.destination); } catch (_) {}
                this._analyserSource = source;
                this._analyserAudio = audio;
            }
            this._analyser = null;
            this._analyserButton = null;
            this._analyserData = null;
            return false;
        }
    }

    _teardownAnalyser(audio) {
        if (audio && this._analyserAudio && this._analyserAudio !== audio) return;
        if (this._analyserRaf) {
            cancelAnimationFrame(this._analyserRaf);
            this._analyserRaf = null;
        }
        this._clearAnalyserBars();
        if (this._analyserButton) {
            this._analyserButton.classList.remove('analyser');
        }
        try {
            if (this._analyserSource) this._analyserSource.disconnect();
        } catch (_) {}
        try {
            if (this._analyser) this._analyser.disconnect();
        } catch (_) {}
        this._analyser = null;
        this._analyserSource = null;
        this._analyserAudio = null;
        this._analyserButton = null;
        this._analyserData = null;
    }

    _resolveReadalongBody(messageElement) {
        if (!messageElement || messageElement.nodeType !== 1) return null;
        if (messageElement.classList && messageElement.classList.contains('body')) {
            return messageElement;
        }
        const msg = messageElement.closest ? (messageElement.closest('.msg') || messageElement) : messageElement;
        const body = msg.querySelector('.body');
        if (!body) return null;
        if (!body.isConnected) return null;
        const text = body.textContent || '';
        return text.trim() ? body : null;
    }

    _readalongExcludedSelector() {
        return [
            '.thinking-section', '.thinking-content', '.thinking-header',
            '.sources-section', '.rag-sources', '.chat-images',
            '.followup-suggestions', '.msg-footer', '.msg-actions',
            '.stopped-indicator', '.edited-indicator', '.variant-nav',
            '.continue-research-wrap', '.tts-word',
            '.sources-panel', '.chat-cta', '.report-footer', '.research-trace',
            'pre', 'code', 'script', 'style', 'textarea', 'input', 'select', 'button', 'svg',
        ].join(', ');
    }

    _wrapReadalongWords(body) {
        const excluded = this._readalongExcludedSelector();
        const textNodes = [];
        const walker = document.createTreeWalker(body, 4, {
            acceptNode: (node) => {
                if (!node.nodeValue || !node.nodeValue.trim()) return 2;
                const parent = node.parentElement;
                if (!parent || parent.closest(excluded)) return 2;
                return 1;
            },
        });
        while (walker.nextNode()) textNodes.push(walker.currentNode);

        const spans = [];
        const bodyTokens = [];
        const tokenRe = /\S+/g;

        for (const node of textNodes) {
            const text = node.nodeValue;
            const parent = node.parentNode;
            if (!parent) continue;

            const frag = document.createDocumentFragment();
            let last = 0;
            let match;
            tokenRe.lastIndex = 0;
            while ((match = tokenRe.exec(text))) {
                if (match.index > last) {
                    frag.appendChild(document.createTextNode(text.slice(last, match.index)));
                }
                const token = match[0];
                const norm = normalizeReadalongToken(token);
                if (norm) {
                    const span = document.createElement('span');
                    span.className = 'tts-word';
                    span.textContent = token;
                    frag.appendChild(span);
                    spans.push(span);
                    bodyTokens.push({ el: span, token, norm });
                } else {
                    frag.appendChild(document.createTextNode(token));
                }
                last = match.index + token.length;
            }
            if (last < text.length) {
                frag.appendChild(document.createTextNode(text.slice(last)));
            }
            if (frag.childNodes.length) parent.replaceChild(frag, node);
        }

        this._readalongWordSpans = spans;
        return bodyTokens;
    }

    _buildReadalong(text, bodyTokens) {
        const words = [];
        const re = /\S+/g;
        let match;
        let totalWeight = 0;

        while ((match = re.exec(text))) {
            const token = match[0];
            const weight = Math.max(1, token.length) + (/[.!?;:,]["')\]]*$/.test(token) ? 2 : 0);
            words.push({
                el: null,
                token,
                norm: normalizeReadalongToken(token),
                start: match.index,
                end: match.index + token.length,
                weightStart: totalWeight,
                weightEnd: totalWeight + weight,
            });
            totalWeight += weight;
        }

        alignReadalongWords(words, bodyTokens);
        this._readalongWords = words;
        this._readalongTotalWeight = totalWeight || 1;
        this._readalongIndex = -1;
    }

    _startReadalong(text, provider, audio, messageElement) {
        this._stopReadalong();

        const plain = String(text == null ? '' : text);
        if (!plain.trim()) return;

        const body = this._resolveReadalongBody(messageElement);
        if (!body) return;

        this._readalongMessage = messageElement || null;
        this._readalongBody = body;

        const bodyTokens = this._wrapReadalongWords(body);
        if (!bodyTokens.length) {
            this._stopReadalong();
            return;
        }

        this._buildReadalong(plain, bodyTokens);
        if (!this._readalongWords.length) {
            this._stopReadalong();
            return;
        }

        this._readalongActive = true;
        this._readalongProvider = provider;
        this._readalongAudio = provider === 'audio' ? audio : null;

        if (provider === 'audio' && audio) {
            this._bindReadalongAudio(audio);
            this._startReadalongRaf();
            this._syncReadalongFromAudio(audio);
        }
        // The pause button checked visibility before wrapping the body, so let
        // it recompute now that read-along has a target element.
        try { window.dispatchEvent(new CustomEvent('odysseus-tts-state-change')); } catch (_) {}
    }

    _stopReadalongTimer() {
        if (this._readalongRaf) {
            cancelAnimationFrame(this._readalongRaf);
            this._readalongRaf = null;
        }
    }

    _bindReadalongAudio(audio) {
        if (!audio) return;
        this._readalongAudioBound = audio;
        this._readalongTimeupdate = () => {
            if (this._readalongAudio === audio) this._syncReadalongFromAudio(audio);
        };
        audio.addEventListener('timeupdate', this._readalongTimeupdate);
    }

    _unbindReadalongAudio() {
        if (this._readalongAudioBound && this._readalongTimeupdate) {
            try {
                this._readalongAudioBound.removeEventListener('timeupdate', this._readalongTimeupdate);
            } catch (_) {}
        }
        this._readalongAudioBound = null;
        this._readalongTimeupdate = null;
    }

    _startReadalongRaf() {
        this._stopReadalongTimer();
        const tick = () => {
            const audio = this._readalongAudio;
            if (!this._readalongActive || !audio) {
                this._readalongRaf = null;
                return;
            }
            this._syncReadalongFromAudio(audio);
            this._readalongRaf = requestAnimationFrame(tick);
        };
        this._readalongRaf = requestAnimationFrame(tick);
    }

    _syncReadalongFromAudio(audio) {
        const duration = audio.duration;
        if (!isFinite(duration) || duration <= 0) return;
        const progress = Math.max(0, Math.min(1, audio.currentTime / duration));
        this._highlightReadalongWeight(progress * this._readalongTotalWeight);
    }

    _highlightReadalongWeight(weight) {
        const words = this._readalongWords;
        if (!words.length) return;

        let lo = 0;
        let hi = words.length - 1;
        let index = words.length - 1;
        while (lo <= hi) {
            const mid = (lo + hi) >> 1;
            const word = words[mid];
            if (weight < word.weightEnd) {
                index = mid;
                hi = mid - 1;
            } else {
                lo = mid + 1;
            }
        }

        const word = words[index];
        let fraction = 1;
        if (word && word.weightEnd > word.weightStart) {
            fraction = Math.max(0, Math.min(1, (weight - word.weightStart) / (word.weightEnd - word.weightStart)));
        }
        this._setReadalongWord(index, fraction);
    }

    _setReadalongCharIndex(charIndex) {
        const words = this._readalongWords;
        if (!words.length) return;

        const pos = Math.max(0, Number(charIndex) || 0);
        let lo = 0;
        let hi = words.length - 1;
        let index = -1;
        while (lo <= hi) {
            const mid = (lo + hi) >> 1;
            const word = words[mid];
            if (pos < word.start) {
                hi = mid - 1;
            } else if (pos >= word.end) {
                lo = mid + 1;
            } else {
                index = mid;
                break;
            }
        }
        if (index < 0) {
            index = 0;
            for (let i = 0; i < words.length; i++) {
                if (words[i].start <= pos) index = i;
                else break;
            }
        }
        this._setReadalongWord(index, 1);
    }

    _setReadalongWord(index, fraction) {
        const words = this._readalongWords;
        const current = words[index];
        if (!this._readalongActive) return;

        if (current && current.el && (!current.el.isConnected || !this._readalongBody || !this._readalongBody.contains(current.el))) {
            // The message was re-rendered/canonicalized under us. Restore what
            // is left of the old DOM and stop touching it.
            this._stopReadalong();
            return;
        }

        if (index === this._readalongIndex) {
            if (current && current.el) {
                current.el.style.setProperty('--tts-word-progress', fraction.toFixed(3));
            }
            return;
        }

        const previous = words[this._readalongIndex];
        if (previous && previous.el) {
            previous.el.classList.remove('tts-word-active');
            previous.el.style.removeProperty('--tts-word-progress');
        }

        this._readalongIndex = index;
        if (!current || !current.el) return;
        current.el.style.setProperty('--tts-word-progress', fraction.toFixed(3));
        current.el.classList.add('tts-word-active');
        this._scrollReadalongWord(current);
    }

    _markReadalongScroll(ms = 900) {
        const ui = window.uiModule;
        if (!ui) return;
        if (!this._readalongScrollMarked) {
            this._readalongScrollMarked = true;
            try { ui.beginProgrammaticScroll?.(ms); } catch (_) {}
        }
        if (this._readalongScrollTimer) clearTimeout(this._readalongScrollTimer);
        this._readalongScrollTimer = setTimeout(() => {
            this._readalongScrollTimer = null;
            this._endReadalongScroll();
        }, ms);
    }

    _endReadalongScroll() {
        if (this._readalongScrollTimer) {
            clearTimeout(this._readalongScrollTimer);
            this._readalongScrollTimer = null;
        }
        if (!this._readalongScrollMarked) return;
        this._readalongScrollMarked = false;
        const ui = window.uiModule;
        try { ui?.endProgrammaticScroll?.(200); } catch (_) {}
    }

    _scrollReadalongWord(word) {
        const el = word && word.el;
        if (!el || !el.isConnected) return;
        if (typeof el.getClientRects === 'function' && !el.getClientRects().length) return;

        // A new turn means the user is looking at the conversation tail, not
        // the message being read. Let the new-message auto-scroll own the
        // viewport instead of yanking it back up to the spoken word.
        if (this._readalongHasNewerMessage()) return;

        // Respect the existing follow-scroll contract: if the user scrolled up,
        // leave their viewport alone until they return to the bottom. Hidden
        // tabs are also skipped so returning to the page does not jump.
        const ui = window.uiModule;
        if (ui && typeof ui.getAutoScroll === 'function' && !ui.getAutoScroll()) return;
        if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;

        const scroller = el.closest('.chat-history') || document.getElementById('chat-history');
        if (!scroller || !scroller.clientHeight) return;

        const wordRect = el.getBoundingClientRect();
        const scrollRect = scroller.getBoundingClientRect();
        const pad = 12;
        if (wordRect.top >= scrollRect.top + pad && wordRect.bottom <= scrollRect.bottom - pad) return;

        const maxScroll = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
        const target = Math.max(
            0,
            Math.min(
                maxScroll,
                scroller.scrollTop + (wordRect.top - scrollRect.top) - scroller.clientHeight * 0.38
            )
        );
        if (Math.abs(target - scroller.scrollTop) < 1) return;

        this._markReadalongScroll();
        if (this._prefersReducedMotion() || typeof scroller.scrollTo !== 'function') {
            scroller.scrollTop = target;
        } else {
            scroller.scrollTo({ top: target, behavior: 'smooth' });
        }
    }

    _readalongHasNewerMessage() {
        const message = this._readalongMessage;
        if (!message || !message.isConnected) return false;
        const msg = message.closest ? (message.closest('.msg') || message) : message;
        const next = msg && msg.nextElementSibling;
        return !!(next && next.classList && next.classList.contains('msg'));
    }

    isReadingPositionVisible() {
        const scroller = document.getElementById('chat-history') || document.querySelector('.chat-history');
        if (!scroller || !scroller.clientHeight) return false;
        const active = this._readalongWords?.[this._readalongIndex]?.el;
        const el = active && active.isConnected
            ? active
            : (this._readalongBody && this._readalongBody.isConnected
                ? this._readalongBody
                : (this._readalongMessage && this._readalongMessage.isConnected ? this._readalongMessage : null));
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        const view = scroller.getBoundingClientRect();
        const pad = 8;
        return rect.bottom > view.top + pad && rect.top < view.bottom - pad;
    }

    _stopReadalong() {
        this._readalongActive = false;
        this._readalongAudio = null;
        this._stopReadalongTimer();
        this._unbindReadalongAudio();
        this._endReadalongScroll();

        const spans = this._readalongWordSpans || [];
        const parents = new Set();
        for (const span of spans) {
            const parent = span && span.parentNode;
            if (!parent || !parent.isConnected) continue;
            parents.add(parent);
            parent.replaceChild(document.createTextNode(span.textContent || ''), span);
        }
        for (const parent of parents) {
            try { parent.normalize(); } catch (_) {}
        }

        this._readalongWordSpans = [];
        this._readalongWords = [];
        this._readalongIndex = -1;
        this._readalongTotalWeight = 1;
        this._readalongMessage = null;
        this._readalongBody = null;
        this._readalongProvider = '';
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

            const audio = new Audio(audioUrl);
            this.currentAudio = audio;
            audio.addEventListener('ended', () => {
                if (this.currentAudio === audio) this._stopReadalong();
            });
            audio.addEventListener('error', () => {
                if (this.currentAudio === audio) this._stopReadalong();
            });
            audio.addEventListener('pause', () => {
                if (this.currentAudio === audio) this._stopReadalong();
            });
            await audio.play();
            if (this.currentAudio !== audio) return;
            this.isPlaying = true;
            this._startReadalong(plainText, 'audio', audio);
            // Note: onended should be set by the caller (addAITTSButton)
            // to reset button state when audio finishes

        } catch (error) {
            this._stopReadalong();
            console.error('Failed to play audio:', error);
            throw error;
        }
    }

    _playBrowser(plainText, messageElement) {
        return new Promise((resolve, reject) => {
            const utterance = new SpeechSynthesisUtterance(plainText);
            const voice = this._findBrowserVoice();
            if (voice) utterance.voice = voice;
            utterance.rate = this.playbackSpeed;

            this._browserUtterance = utterance;
            this._startReadalong(plainText, 'browser', null, messageElement);
            utterance.onboundary = (event) => {
                if (this._browserUtterance !== utterance) return;
                if (event && typeof event.charIndex === 'number' && (!event.name || event.name === 'word')) {
                    this._setReadalongCharIndex(event.charIndex);
                }
            };
            utterance.onend = () => {
                if (this._browserUtterance === utterance) {
                    this._browserUtterance = null;
                    this._stopReadalong();
                    this._paused = false;
                    this.isPlaying = false;
                    this._syncPauseButton();
                }
                resolve();
            };
            utterance.onerror = (e) => {
                if (this._browserUtterance === utterance) {
                    this._browserUtterance = null;
                    this._stopReadalong();
                    this._paused = false;
                    this.isPlaying = false;
                    this._syncPauseButton();
                }
                reject(new Error('Browser TTS error: ' + e.error));
            };

            try {
                window.speechSynthesis.speak(utterance);
            } catch (error) {
                if (this._browserUtterance === utterance) {
                    this._browserUtterance = null;
                    this._stopReadalong();
                    this.isPlaying = false;
                    this._syncPauseButton();
                }
                reject(error);
                return;
            }
            this.isPlaying = true;
            this._syncPauseButton();
        });
    }

    stop() {
        this._paused = false;
        this._stopReadalong();

        // Cancel streaming TTS
        this._streamActive = false;
        if (this._streamDebounceTimer) {
            clearTimeout(this._streamDebounceTimer);
            this._streamDebounceTimer = null;
        }
        this._streamSentencesSent = 0;

        this._teardownAnalyser();

        // Clear the entire queue and reset all queued buttons
        for (const item of this._queue) {
            if (item.resetFn) item.resetFn();
        }
        this._queue = [];
        this._processing = false;

        if (this.useBrowserTTS) {
            this._browserUtterance = null;
            window.speechSynthesis.cancel();
            this.isPlaying = false;
        }
        if (this.currentAudio) {
            this.currentAudio.pause();
            this.currentAudio.currentTime = 0;
            this.currentAudio = null;
            this.isPlaying = false;
        }
        this._syncPauseButton();
    }

    pause() {
        if (!this.isPlaying) return false;
        if (this._paused) return this.resume();

        this._paused = true;
        if (this.currentAudio && !this.currentAudio.paused) {
            this.currentAudio.pause();
        } else if (this.useBrowserTTS && window.speechSynthesis) {
            window.speechSynthesis.pause();
        }
        this._syncPauseButton();
        return true;
    }

    resume() {
        if (!this._paused) return false;
        this._paused = false;

        if (this.currentAudio) {
            const playing = this.currentAudio.play();
            if (playing && typeof playing.catch === 'function') playing.catch(() => {});
        } else if (this.useBrowserTTS && window.speechSynthesis) {
            window.speechSynthesis.resume();
        }
        this._syncPauseButton();
        return true;
    }

    _syncPauseButton() {
        const btn = document.getElementById('scroll-bottom-btn');
        if (!btn) return;

        if (this.isPlaying) {
            const paused = this._paused;
            btn.dataset.ttsPause = paused ? 'paused' : 'playing';
            btn.classList.add('show', 'tts-pause');
            btn.classList.remove('slide-out');
            btn.innerHTML = paused
                ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>'
                : '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>';
            btn.title = paused ? 'Resume reading' : 'Pause reading';
            btn.setAttribute('aria-label', btn.title);
        } else {
            delete btn.dataset.ttsPause;
            btn.classList.remove('tts-pause');
            btn.innerHTML = '<span class="scroll-nav-caret" aria-hidden="true">▾</span>';
            btn.title = 'Scroll to bottom';
            btn.setAttribute('aria-label', 'Scroll to bottom');
        }
        try { window.dispatchEvent(new CustomEvent('odysseus-tts-state-change')); } catch (_) {}
    }

    _prefetchQueueItem(item) {
        if (!item || this.useBrowserTTS) return;
        const plainText = this.extractPlainText(item.text);
        if (!plainText) return;
        const cacheKey = this.getCacheKey(plainText);
        if (this.cache.has(cacheKey) || this._synthInflight.has(cacheKey)) return;
        this.synthesize(item.text).catch(() => {});
    }

    /**
     * Enqueue a message for auto-play. Plays sequentially — each message
     * finishes before the next starts. Stopping any message clears the queue.
     */
    enqueue(text, button, resetFn, messageElement) {
        this._queue.push({ text, button, resetFn, messageElement });
        if (!this._processing) {
            this._processQueue();
        } else if (this._queue.length > 1) {
            this._prefetchQueueItem(this._queue[1]);
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
        this._paused = false;
        this._syncPauseButton();
    }

    async _playQueueItem(item) {
        const { text } = item;
        const button = item.button;
        const ICON_LOADING = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="9" stroke-dasharray="42" stroke-dashoffset="12" stroke-linecap="round"><animateTransform attributeName="transform" type="rotate" from="0 12 12" to="360 12 12" dur="0.8s" repeatCount="indefinite"/></circle></svg>';
        var ICON_STOP = '<svg class="tts-wave" width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none" aria-hidden="true"><rect class="tts-wave-bar" x="3" y="9" width="3" height="6" rx="1.5"/><rect class="tts-wave-bar" x="8" y="5" width="3" height="14" rx="1.5"/><rect class="tts-wave-bar" x="13" y="2" width="3" height="20" rx="1.5"/><rect class="tts-wave-bar" x="18" y="7" width="3" height="10" rx="1.5"/></svg>';

        let loadingSpinner = null;
        try {
            loadingSpinner = spinnerModule.createWhirlpool(14);
            button.replaceChildren(loadingSpinner.element);
        } catch (_) {
            loadingSpinner = null;
            button.innerHTML = ICON_LOADING;
        }
        button.classList.add('loading');
        button.style.color = '#ccc';
        button.title = 'Loading...';

        try {
            if (!this._processing) return;

            const audioUrl = await this.synthesize(text);

            if (!this._processing) return;

            // Warm the next sentence while this one is still playing. The
            // in-flight map in synthesize() prevents duplicate requests.
            if (this._queue.length > 1) this._prefetchQueueItem(this._queue[1]);

            // Streaming TTS can swap a hidden placeholder for the real button
            // while synthesis is in flight; always drive the current one.
            const activeButton = item.button || button;
            if (loadingSpinner) {
                try { loadingSpinner.destroy(); } catch (_) {}
                loadingSpinner = null;
            }
            activeButton.innerHTML = ICON_STOP;
            activeButton.classList.remove('loading');
            activeButton.classList.add('playing');
            activeButton.style.color = 'var(--brand-color, var(--red))';
            activeButton.title = 'Stop';

            const messageElement = item.messageElement
                || (activeButton.closest ? activeButton.closest('.msg') : null);
            const plainText = this.extractPlainText(text);

            if (this.useBrowserTTS) {
                await this._playBrowser(plainText, messageElement);
            } else {
                if (this.currentAudio) {
                    this.currentAudio.pause();
                    this.currentAudio = null;
                }
                this._teardownAnalyser();

                await new Promise((resolve, reject) => {
                    const audio = new Audio(audioUrl);
                    if (this._provider === 'local' && this.playbackSpeed !== 1) {
                        audio.playbackRate = this.playbackSpeed;
                    }
                    this.currentAudio = audio;
                    audio.onended = () => {
                        if (this.currentAudio === audio) this._stopReadalong();
                        this._teardownAnalyser(audio);
                        this._paused = false;
                        this.isPlaying = false;
                        if (this.currentAudio === audio) this.currentAudio = null;
                        this._syncPauseButton();
                        resolve();
                    };
                    audio.onerror = (e) => {
                        if (this.currentAudio === audio) this._stopReadalong();
                        this._teardownAnalyser(audio);
                        this._paused = false;
                        this.isPlaying = false;
                        if (this.currentAudio === audio) this.currentAudio = null;
                        this._syncPauseButton();
                        reject(new Error('Audio playback error'));
                    };
                    audio.onpause = () => {
                        if (this._paused) return;
                        if (this.currentAudio === audio) this._stopReadalong();
                        this._teardownAnalyser(audio);
                        if (this.currentAudio !== audio) {
                            resolve();
                        }
                    };
                    audio.play().then(() => {
                        if (!this._processing || this.currentAudio !== audio) return;
                        this.isPlaying = true;
                        this._syncPauseButton();
                        this._startAnalyser(audio, item.button || button);
                        this._startReadalong(plainText, 'audio', audio, messageElement);
                    }).catch((err) => {
                        this._stopReadalong();
                        this._teardownAnalyser(audio);
                        reject(err);
                    });
                });
            }
        } finally {
            if (loadingSpinner) {
                try { loadingSpinner.destroy(); } catch (_) {}
                loadingSpinner = null;
            }
            this._stopReadalong();
            if (item.resetFn) item.resetFn();
        }
    }

    // ── Streaming TTS (sentence-by-sentence) ──

    streamingStart(messageElement) {
        this._streamSentencesSent = 0;
        this._streamActive = true;
        this._streamButton = null;
        this._streamResetFn = null;
        this._streamMessageElement = messageElement || null;
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
            this.enqueue(sentence, btn, resetFn, this._streamMessageElement);
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
        const messageElement = button && button.closest ? button.closest('.msg') : null;
        if (messageElement) this._streamMessageElement = messageElement;
        for (var i = 0; i < this._queue.length; i++) {
            if (this._queue[i].button && this._queue[i].button.classList.contains('streaming-placeholder')) {
                this._queue[i].button = button;
                this._queue[i].resetFn = resetFn;
                if (messageElement) this._queue[i].messageElement = messageElement;
            }
        }
        // If a hidden placeholder is already playing, move the analyser over.
        if (this._analyser && this._analyserButton &&
            this._analyserButton.classList.contains('streaming-placeholder')) {
            this._bindAnalyserButton(button);
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
            this.enqueue(remaining, btn, resetFn, this._streamMessageElement);
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
    var ICON_STOP = '<svg class="tts-wave" width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none" aria-hidden="true"><rect class="tts-wave-bar" x="3" y="9" width="3" height="6" rx="1.5"/><rect class="tts-wave-bar" x="8" y="5" width="3" height="14" rx="1.5"/><rect class="tts-wave-bar" x="13" y="2" width="3" height="20" rx="1.5"/><rect class="tts-wave-bar" x="18" y="7" width="3" height="10" rx="1.5"/></svg>';
    var ICON_LOADING = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="9" stroke-dasharray="42" stroke-dashoffset="12" stroke-linecap="round"><animateTransform attributeName="transform" type="rotate" from="0 12 12" to="360 12 12" dur="0.8s" repeatCount="indefinite"/></circle></svg>';

    const playButton = document.createElement('button');
    playButton.className = 'ai-tts-button';
    playButton.type = 'button';
    playButton.title = 'Read aloud';
    playButton.innerHTML = ICON_PLAY;
    playButton.style.cssText = 'background:none;border:none;color:#6b7280;cursor:pointer;padding:2px 6px;border-radius:4px;transition:color .15s;line-height:1;display:inline-flex;align-items:center;';

    playButton.addEventListener('mouseenter', () => { if (!playButton.classList.contains('playing') && !playButton.classList.contains('loading')) playButton.style.color = '#ccc'; });
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

        mgr.enqueue(text, playButton, resetButton, messageElement);
    });

    const moreButton = actions.querySelector('.msg-more-btn');
    if (moreButton) actions.insertBefore(playButton, moreButton);
    else actions.appendChild(playButton);
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
