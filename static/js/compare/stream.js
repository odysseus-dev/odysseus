// compare/stream.js — SSE streaming to panes
import state from './state.js';
import { addFinishBadge } from './vote.js';
import { getModelCost, safeDisplayImageSrc } from '../chatRenderer.js';
import markdownModule from '../markdown.js';
import spinnerModule from '../spinner.js';
import uiModule from '../ui.js';
import presetsModule from '../presets.js';

var escapeHtml = uiModule.esc;

const WAVE_FRAMES = ['▁▂▃', '▂▃▄', '▃▄▅', '▄▅▆', '▅▆▇', '▆▅▄', '▅▄▃', '▄▃▂'];

function _safeHttpHref(raw) {
  try {
    const parsed = new URL(String(raw || '').trim(), window.location.origin);
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
      return parsed.href;
    }
  } catch (_) {}
  return '';
}

// ── Lazy-registered functions from compare.js (avoids circular deps) ──
let _rerollPane = null;
let _autoPreviewHtml = null;

/** Register external functions that live in compare.js. */
function registerStreamActions({ rerollPane, autoPreviewHtml }) {
  _rerollPane = rerollPane;
  _autoPreviewHtml = autoPreviewHtml;
}

/** Format milliseconds as human-readable duration (e.g. "120ms", "1.23s", "4.5s"). */
function _formatMs(ms) {
  if (ms < 1000) return Math.round(ms) + 'ms';
  if (ms < 10000) return (ms / 1000).toFixed(2) + 's';
  return (ms / 1000).toFixed(1) + 's';
}

/** Build a DOM container of search-result cards from a search response. Returns an HTMLElement. */
function _renderSearchResults(data) {
  const container = document.createElement('div');
  container.className = 'compare-search-results';
  (data.results || []).forEach(r => {
    const card = document.createElement('div');
    card.className = 'compare-search-result';
    const titleLink = document.createElement('a');
    const safeUrl = _safeHttpHref(r.url);
    if (safeUrl) {
      titleLink.href = safeUrl;
      titleLink.target = '_blank';
      titleLink.rel = 'noopener noreferrer';
    }
    titleLink.className = 'search-result-title';
    titleLink.textContent = r.title || 'Untitled';
    card.appendChild(titleLink);
    if (r.snippet) {
      const s = document.createElement('div');
      s.className = 'search-result-snippet';
      s.textContent = r.snippet;
      card.appendChild(s);
    }
    if (r.url) {
      const u = document.createElement('div');
      u.className = 'search-result-url';
      u.textContent = r.url;
      card.appendChild(u);
    }
    container.appendChild(card);
  });
  return container;
}

/** Run synthesis for a search pane — sends search results to an LLM for analysis. */
async function _runSynthForPane(modelToUse, synthPrompt, synthBody, spinner, hist) {
  // Create temp session for synthesis
  const fd = new FormData();
  fd.append('name', 'Synthesis');
  fd.append('endpoint_url', modelToUse.endpoint || '');
  fd.append('model', modelToUse.model || '');
  if (modelToUse.endpointId) {
    fd.append('endpoint_id', modelToUse.endpointId);
    fd.append('skip_validation', 'true');
  }

  try {
    const createRes = await fetch(`${state.API_BASE}/api/session`, { method: 'POST', body: fd });
    if (!createRes.ok) {
      const errData = await createRes.json().catch(() => ({}));
      throw new Error(errData.detail || 'Failed to create session');
    }
    const createData = await createRes.json();

    const synthAc = new AbortController();
    state._abortControllers.push(synthAc);
    const streamRes = await fetch(`${state.API_BASE}/api/chat_stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session: createData.id, message: synthPrompt }),
      signal: synthAc.signal,
    });

    if (spinner) spinner.stop();
    synthBody.innerHTML = '';
    const reader = streamRes.body.getReader();
    const decoder = new TextDecoder();
    let synthText = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const chunk = decoder.decode(value);
      const lines = chunk.split('\n');
      for (const line of lines) {
        if (line.startsWith('data: ') && line !== 'data: [DONE]') {
          try {
            const d = JSON.parse(line.slice(6));
            if (d.delta) {
              synthText += d.delta;
              if (markdownModule && synthText.trim()) {
                synthBody.innerHTML = markdownModule.processWithThinking(
                  markdownModule.squashOutsideCode(synthText)
                );
              } else {
                synthBody.textContent = synthText;
              }
              hist.scrollTop = hist.scrollHeight;
            }
          } catch (e) {}
        }
      }
    }

    // Final highlight
    if (window.hljs) synthBody.querySelectorAll('pre code:not(.hljs)').forEach(b => window.hljs.highlightElement(b));

    // Cleanup temp session
    fetch(`${state.API_BASE}/api/session/${createData.id}`, { method: 'DELETE' }).catch(() => {});
  } catch (e) {
    if (spinner) spinner.stop();
    synthBody.innerHTML = '<div style="color:var(--color-error);font-size:0.85em;">Synthesis failed: ' + escapeHtml(e.message) + '</div>';
  }
}

/** Stream an SSE response into a compare pane. Handles text, tool blocks, images, metrics. */
async function streamToPane(paneIdx, sessionId, message, aiMsgEl, opts) {
  opts = opts || {};
  const aiBody = aiMsgEl ? aiMsgEl.querySelector('.body') : null;
  const hist = aiMsgEl ? aiMsgEl.parentElement : null;
  if (!aiBody) return;

  const ac = new AbortController();
  state._abortControllers[paneIdx] = ac;

  // Show stop button for this pane
  const _paneEl = document.querySelector(`.compare-pane[data-pane="${paneIdx}"]`);
  if (_paneEl) {
    const _stopBtn = _paneEl.querySelector('.pane-stop-btn');
    if (_stopBtn) _stopBtn.style.display = '';
  }

  let accumulated = '';
  let metrics = null;
  let timedOut = false;
  let streamOk = false;
  let currentToolBlock = null;  // track active agent tool block
  // Idle timeout — abort only if no data is received for this many seconds.
  // Long generations (SVG, big code) are fine as long as the stream stays
  // active. opts.timeout may still tighten this for specific paths.
  const effectiveTimeout = opts.timeout || state._timeout;
  let timeoutId = setTimeout(() => { timedOut = true; ac.abort(); }, effectiveTimeout * 1000);
  const _resetIdleTimeout = () => {
    clearTimeout(timeoutId);
    timeoutId = setTimeout(() => { timedOut = true; ac.abort(); }, effectiveTimeout * 1000);
  };

  // Live timer
  const _timerStart = performance.now();
  let _ttft = 0; // time to first token
  let _timerDone = false;
  const _timerEl = document.getElementById('cmp-timer-' + paneIdx);
  let _rafId = 0;
  function _tickTimer() {
    if (_timerDone) return;
    const elapsed = performance.now() - _timerStart;
    if (_timerEl) _timerEl.textContent = _formatMs(elapsed);
    _rafId = requestAnimationFrame(_tickTimer);
  }
  _rafId = requestAnimationFrame(_tickTimer);

  // Throttled markdown render — re-rendering the entire growing buffer on
  // every token is O(n²) total work. Coalesce updates so we paint at most
  // every ~80ms. The final render still runs at end-of-stream for quality.
  let _renderPending = false;
  let _renderLastAt = 0;
  const _RENDER_THROTTLE_MS = 80;
  function _scheduleLiveRender(target) {
    if (_renderPending) return;
    const now = performance.now();
    const elapsed = now - _renderLastAt;
    const delay = elapsed >= _RENDER_THROTTLE_MS ? 0 : _RENDER_THROTTLE_MS - elapsed;
    _renderPending = true;
    setTimeout(() => {
      _renderPending = false;
      _renderLastAt = performance.now();
      if (markdownModule && accumulated.trim()) {
        target.innerHTML = markdownModule.processWithThinking(
          markdownModule.squashOutsideCode(accumulated)
        );
      } else {
        target.textContent = accumulated;
      }
      if (hist) hist.scrollTop = hist.scrollHeight;
    }, delay);
  }

  try {
    const fd = new FormData();
    fd.append('message', message);
    fd.append('session', sessionId);

    // Compare mode determines what tools/features are enabled
    const isAgent = state._compareMode === 'agent';
    const isResearch = state._compareMode === 'research';

    // Agent mode: enable all tools (web, bash, etc.)
    if (isAgent) {
      fd.append('mode', 'agent');
      fd.append('allow_web_search', 'true');
      fd.append('allow_bash', 'true');
    } else if (isResearch) {
      fd.append('use_research', 'true');
    } else {
      // Chat/Image: pure chat only — no tools, no search, no bash, no RAG.
      // Explicitly send mode='chat' so the backend's compare_mode strip
      // (chat_routes.py line 385) actually triggers — otherwise the form
      // field was missing and chat_mode defaulted to "", which meant
      // bash/python/web_search were never added to disabled_tools and
      // models would still attempt to run Python.
      fd.append('mode', 'chat');
      fd.append('use_rag', 'false');
    }
    const incognitoChk = document.getElementById('incognito-toggle');
    if (incognitoChk && incognitoChk.checked) {
      fd.append('incognito', 'true');
    }
    // Disable document tool and memory injection in compare mode
    fd.append('no_documents', 'true');
    fd.append('no_memory', 'true');
    // Tell backend this is compare mode — strip all non-toggled tools
    fd.append('compare_mode', 'true');
    // Forward preset if selected
    if (presetsModule && presetsModule.getSelectedPreset()) {
      fd.append('preset_id', presetsModule.getSelectedPreset());
    }

    const response = await fetch(`${state.API_BASE}/api/chat_stream`, {
      method: 'POST', body: fd, signal: ac.signal
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      _resetIdleTimeout();  // any chunk = stream is alive

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const data = line.slice(6);
        if (data === '[DONE]') break;
        try {
          const json = JSON.parse(data);
          if (json.type === 'metrics') {
            metrics = json.data;

          // ── Research progress (spinner updates) ──
          } else if (json.type === 'research_progress') {
            const rp = json.data;
            const spinner = aiMsgEl._spinner;
            if (spinner) {
              if (rp.phase === 'searching') {
                const q = rp.queries ? `${rp.queries} queries` : '';
                const s = rp.total_sources ? ` · ${rp.total_sources} sources` : '';
                spinner.updateMessage(`R${rp.round || '?'}: Searching${q ? ' (' + q + ')' : ''}${s}`);
              } else if (rp.phase === 'reading') {
                spinner.updateMessage(`R${rp.round || '?'}: Reading ${rp.new_sources || ''} pages`);
              } else if (rp.phase === 'analyzing') {
                spinner.updateMessage(`R${rp.round || '?'}: Analyzing ${rp.total_findings || 0} findings`);
              } else if (rp.phase === 'writing') {
                spinner.updateMessage(`Writing report · ${rp.total_sources || 0} sources`);
              } else if (rp.phase === 'error') {
                spinner.updateMessage(rp.message || 'Research error');
              }
            }

          // ── Research sources / Web sources (compact sources box) ──
          } else if (json.type === 'research_sources' || json.type === 'web_sources') {
            const sources = json.data || [];
            if (sources.length > 0) {
              const label = json.type === 'research_sources' ? 'Research' : 'Web';
              const box = document.createElement('div');
              box.className = 'compare-sources-box';
              box.innerHTML = '<span class="sources-label">' + sources.length + ' ' + label + ' sources</span>';
              box.title = sources.map(s => s.title || s.url).join('\n');
              // Replace spinner with sources + new spinner
              aiBody.innerHTML = '';
              aiBody.appendChild(box);
              if (spinnerModule) {
                const newSpinner = spinnerModule.create('Generating response...', 'right');
                aiBody.appendChild(newSpinner.createElement());
                newSpinner.start();
                aiMsgEl._spinner = newSpinner;
              }
            }

          // ── Tool start (bash, web search agent tool) ──
          } else if (json.type === 'tool_start') {
            // Finalize any accumulated text before the tool block
            if (accumulated.trim() && aiMsgEl._textEl) {
              if (markdownModule) {
                aiMsgEl._textEl.innerHTML = markdownModule.processWithThinking(
                  markdownModule.squashOutsideCode(accumulated));
                if (window.hljs) aiMsgEl._textEl.querySelectorAll('pre code:not(.hljs)').forEach(b => window.hljs.highlightElement(b));
              }
            }
            // Destroy spinner if still present
            if (aiMsgEl._spinner && aiMsgEl._spinner.element) {
              aiMsgEl._spinner.destroy();
              aiMsgEl._spinner = null;
              // Clean up spinner element but keep sources box + text
              const spinnerEl = aiBody.querySelector('.spinner-wrapper, .mini-spinner');
              if (spinnerEl) spinnerEl.remove();
            }
            const toolName = json.tool || 'tool';
            const cmd = json.command || '';
            // Image generation: show ASCII spinner instead of compact tool block
            if (toolName === 'generate_image' && spinnerModule) {
              aiBody.innerHTML = '';
              const imgSpinner = spinnerModule.create('Generating image...', 'right');
              aiBody.appendChild(imgSpinner.createElement());
              imgSpinner.start();
              aiMsgEl._imgSpinner = imgSpinner;
              currentToolBlock = null;
            } else {
              // Agent thread node — matches main chat style
              const _toolLabels = { bash: 'Terminal', python: 'Python', web_search: 'Web Search', read_file: 'Read File', write_file: 'Write File' };
              const toolLabel = _toolLabels[toolName.toLowerCase()] || toolName;
              const cmdHtml = cmd ? `<pre class="agent-thread-cmd">${escapeHtml(cmd)}</pre>` : '';
              const node = document.createElement('div');
              node.className = 'agent-thread-node running';
              node.innerHTML = `<div class="agent-thread-dot"></div><div class="agent-thread-header"><span class="agent-thread-icon">\u25B6</span><span class="agent-thread-tool">${escapeHtml(toolLabel)}</span><span class="agent-thread-wave">▁▂▃</span></div><div class="agent-thread-content">${cmdHtml}</div>`;
              node.querySelector('.agent-thread-header').addEventListener('click', () => node.classList.toggle('open'));
              // Animate wave
              const waveEl = node.querySelector('.agent-thread-wave');
              if (waveEl) {
                const waveFrames = WAVE_FRAMES;
                let waveIdx = 0;
                node._waveInterval = setInterval(() => { waveIdx = (waveIdx + 1) % waveFrames.length; waveEl.textContent = waveFrames[waveIdx]; }, 100);
              }
              aiBody.appendChild(node);
              currentToolBlock = node;
            }
            if (hist) hist.scrollTop = hist.scrollHeight;

          // ── Tool output (image or non-image) ──
          } else if (json.type === 'tool_output') {
            if (json.image_url) {
              // Stop image spinner and render generated image in pane
              if (aiMsgEl._imgSpinner) { aiMsgEl._imgSpinner.destroy(); aiMsgEl._imgSpinner = null; }
              const safeImageUrl = safeDisplayImageSrc(json.image_url);
              aiBody.innerHTML = '';
              if (!safeImageUrl) {
                aiBody.textContent = '[Image unavailable]';
              } else {
                const img = document.createElement('img');
                img.className = 'compare-gen-image';
                img.src = safeImageUrl;
                img.alt = json.image_prompt || '';
                img.title = json.image_prompt || '';
                img.addEventListener('click', () => window.open(safeImageUrl, '_blank', 'noopener,noreferrer'));
                aiBody.appendChild(img);
                if (json.image_prompt) {
                  const caption = document.createElement('div');
                  caption.style.cssText = 'font-size:0.82em;color:color-mix(in srgb, var(--fg) 55%, transparent);margin-top:6px;line-height:1.4;';
                  caption.textContent = json.image_prompt;
                  aiBody.appendChild(caption);
                }
                // Show model name below image (hidden in blind mode until vote)
                if (json.image_model && !state._blindMode) {
                  const modelLabel = document.createElement('div');
                  modelLabel.style.cssText = 'font-size:0.75em;color:color-mix(in srgb, var(--fg) 40%, transparent);margin-top:4px;';
                  modelLabel.textContent = json.image_model;
                  aiBody.appendChild(modelLabel);
                }
                aiMsgEl._imageData = { url: safeImageUrl, prompt: json.image_prompt, model: json.image_model, size: json.image_size, quality: json.image_quality };
              }
            } else if (currentToolBlock) {
              // Stop wave animation
              if (currentToolBlock._waveInterval) { clearInterval(currentToolBlock._waveInterval); currentToolBlock._waveInterval = null; }
              const ok = (json.exit_code === 0 || json.exit_code == null);
              const cmd = json.command || '';
              const _toolLabels2 = { bash: 'Terminal', python: 'Python', web_search: 'Web Search', read_file: 'Read File', write_file: 'Write File' };
              const tLabel = _toolLabels2[(json.tool || '').toLowerCase()] || json.tool || '';
              let outHtml = '';
              if (json.output && json.output.trim()) {
                outHtml = `<details class="agent-tool-output"><summary>Output</summary><pre>${escapeHtml(json.output)}</pre></details>`;
              }
              const cmdHtml = cmd ? `<pre class="agent-thread-cmd">${escapeHtml(cmd)}</pre>` : '';
              currentToolBlock.className = 'agent-thread-node' + (ok ? '' : ' error');
              currentToolBlock.innerHTML = `<div class="agent-thread-dot"></div><div class="agent-thread-header"><span class="agent-thread-icon">${ok ? '\u2713' : '\u2717'}</span><span class="agent-thread-tool">${escapeHtml(tLabel)}</span><span class="agent-thread-status">${ok ? 'done' : 'failed'}</span><span class="agent-thread-chevron">\u25B6</span></div><div class="agent-thread-content">${cmdHtml}${outHtml}</div>`;
              currentToolBlock.querySelector('.agent-thread-header').addEventListener('click', () => currentToolBlock.classList.toggle('open'));
              currentToolBlock = null;
              // Reset text element so next deltas create a fresh container
              aiMsgEl._textEl = null;
              accumulated = '';
            }
            if (hist) hist.scrollTop = hist.scrollHeight;
          } else if (json.delta) {
            // Skip text deltas if we already rendered an image
            if (aiMsgEl._imageData) continue;
            // Capture TTFT on very first text delta
            if (!accumulated && !_ttft) _ttft = performance.now() - _timerStart;
            // On first delta, destroy spinner and prepare text area
            if (!accumulated && aiMsgEl._spinner) {
              if (aiMsgEl._spinner.element) aiMsgEl._spinner.destroy();
              aiMsgEl._spinner = null;
              // Keep sources box if present, clear everything else
              const srcBox = aiBody.querySelector('.compare-sources-box');
              aiBody.innerHTML = '';
              if (srcBox) aiBody.appendChild(srcBox);
              // Add text container
              const textEl = document.createElement('div');
              textEl.className = 'compare-text-content';
              aiBody.appendChild(textEl);
              aiMsgEl._textEl = textEl;
            }
            // After a tool block, create a new text container for continuing text
            if (!accumulated && !aiMsgEl._textEl) {
              const textEl = document.createElement('div');
              textEl.className = 'compare-text-content';
              aiBody.appendChild(textEl);
              aiMsgEl._textEl = textEl;
            }
            accumulated += json.delta;
            const target = aiMsgEl._textEl || aiBody;
            _scheduleLiveRender(target);
          }
        } catch (e) { console.warn('Compare stream render error:', e); }
      }
    }

    streamOk = true;
    // Destroy any remaining spinner
    if (aiMsgEl._spinner && aiMsgEl._spinner.element) aiMsgEl._spinner.destroy();
    aiMsgEl._spinner = null;
    // Final render
    const finalTarget = aiMsgEl._textEl || aiBody;
    if (markdownModule && accumulated.trim()) {
      finalTarget.innerHTML = markdownModule.processWithThinking(
        markdownModule.squashOutsideCode(accumulated)
      );
    }
    if (window.hljs) {
      finalTarget.querySelectorAll('pre code:not(.hljs)').forEach(b => window.hljs.highlightElement(b));
    }

    // ── Show play button if response contains HTML ──
    if (_autoPreviewHtml) _autoPreviewHtml(paneIdx, accumulated);

    // Metrics footer
    if (aiMsgEl && aiMsgEl._imageData) {
      // Image-specific footer with actions + metrics
      const imgD = aiMsgEl._imageData;
      const footer = document.createElement('div');
      footer.className = 'msg-footer';

      // Action buttons (copy prompt + download)
      const actions = document.createElement('span');
      actions.className = 'msg-actions';

      const copyBtn = document.createElement('button');
      copyBtn.className = 'footer-copy-btn';
      copyBtn.type = 'button';
      copyBtn.title = 'Copy prompt';
      copyBtn.textContent = '\u2398';
      copyBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        const txt = imgD.prompt || '';
        if (navigator.clipboard) navigator.clipboard.writeText(txt).catch(() => {});
        else { const ta = document.createElement('textarea'); ta.value = txt; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove(); }
        copyBtn.textContent = '\u2713';
        setTimeout(() => { copyBtn.textContent = '\u2398'; }, 1500);
        if (uiModule) uiModule.showToast('Prompt copied!');
      });
      actions.appendChild(copyBtn);

      const dlBtn = document.createElement('button');
      dlBtn.className = 'footer-copy-btn';
      dlBtn.type = 'button';
      dlBtn.title = 'Download image';
      dlBtn.textContent = '\u2913';
      dlBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        try {
          const resp = await fetch(imgD.url);
          const blob = await resp.blob();
          const a = document.createElement('a');
          a.href = URL.createObjectURL(blob);
          a.download = (imgD.prompt || 'image').slice(0, 40).replace(/[^a-zA-Z0-9 ]/g, '') + '.png';
          document.body.appendChild(a);
          a.click();
          a.remove();
          URL.revokeObjectURL(a.href);
          dlBtn.textContent = '\u2713';
          setTimeout(() => { dlBtn.textContent = '\u2913'; }, 1500);
        } catch { dlBtn.textContent = '\u2717'; setTimeout(() => { dlBtn.textContent = '\u2913'; }, 1500); }
      });
      actions.appendChild(dlBtn);

      footer.appendChild(actions);

      // Metrics — hide in blind mode to avoid revealing model identity
      if (!state._blindMode) {
        const span = document.createElement('span');
        span.className = 'response-metrics';
        const parts = [];
        if (imgD.model) parts.push(imgD.model.split('/').pop());
        if (imgD.size) parts.push(imgD.size);
        if (imgD.quality) parts.push(imgD.quality);
        if (metrics && metrics.response_time) parts.push(metrics.response_time + 's');
        const costFn = window.chatModule && window.chatModule.getImageCost;
        if (costFn) {
          const cost = costFn(imgD.model, imgD.quality, imgD.size);
          if (cost !== null) parts.push('$' + (cost < 0.01 ? cost.toFixed(4) : cost.toFixed(3)));
        }
        span.textContent = parts.join(' \u00b7 ');
        footer.appendChild(span);
      }
      aiMsgEl.appendChild(footer);
    } else if (metrics && aiMsgEl) {
      const footer = document.createElement('div');
      footer.className = 'msg-footer';
      const span = document.createElement('span');
      span.className = 'response-metrics';
      const outputTokens = metrics.output_tokens;
      const responseTime = metrics.response_time ?? metrics.total_time;
      const explicitTps = metrics.tokens_per_second ?? metrics.gen_tps ?? metrics.tps;
      const numericOutput = Number(outputTokens);
      const numericTime = Number(responseTime);
      const numericTps = Number(explicitTps);
      const derivedTps = Number.isFinite(numericTps)
        ? numericTps
        : (Number.isFinite(numericOutput) && Number.isFinite(numericTime) && numericTime > 0)
          ? numericOutput / numericTime
          : null;
      const tpsLabel = derivedTps != null
        ? (derivedTps >= 100 ? String(Math.round(derivedTps)) : derivedTps.toFixed(2).replace(/\.?0+$/, ''))
        : null;
      const parts = [];
      if (outputTokens != null && outputTokens !== 'undefined') {
        parts.push(outputTokens + ' tokens');
      }
      if (tpsLabel != null) {
        parts.push(tpsLabel + ' tok/s');
      }
      if (responseTime != null && responseTime !== 'undefined' && parts.length === 0) {
        parts.push(responseTime + 's');
      }
      // Add per-request cost and cost per 1000
      const _model = metrics.model || (state._selectedModels[paneIdx] && state._selectedModels[paneIdx].model) || '';
      const _cost = getModelCost(_model, metrics.input_tokens || 0, metrics.output_tokens || 0);
      // Build the metrics span with optional cost and context
      span.textContent = parts.join(' | ');
      if (_cost !== null) {
        const _cost1k = _cost * 1000;
        const costSpan = document.createElement('span');
        costSpan.style.color = 'var(--color-success, #4caf50)';
        costSpan.title = 'Estimated cost per 1,000 responses like this one';
        costSpan.textContent = (span.textContent ? ' | ' : '') + '$' + (_cost1k < 1 ? _cost1k.toFixed(2) : _cost1k.toFixed(0)) + '/1k';
        span.appendChild(costSpan);
      }
      if (metrics.context_percent > 0) {
        const ctx = document.createElement('span');
        ctx.textContent = (span.textContent ? ' | ' : '') + metrics.context_percent + '% ctx';
        if (metrics.context_percent >= 85) ctx.style.color = 'var(--color-error)';
        else if (metrics.context_percent >= 70) ctx.style.color = '#ff9900';
        span.appendChild(ctx);
      }
      footer.appendChild(span);
      aiMsgEl.appendChild(footer);
    }
    if (hist) hist.scrollTop = hist.scrollHeight;

  } catch (error) {
    if (error.name === 'AbortError') {
      if (timedOut) {
        if (accumulated.trim()) {
          if (markdownModule) {
            aiBody.innerHTML = markdownModule.processWithThinking(
              markdownModule.squashOutsideCode(accumulated));
          }
        }
        const notice = document.createElement('div');
        notice.style.cssText = 'color:#ff9800;font-size:0.8em;margin-top:8px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;';
        const text = document.createElement('span');
        text.style.fontStyle = 'italic';
        text.textContent = 'Timed out after ' + effectiveTimeout + 's' + (accumulated.trim() ? ' \u2014 response may be incomplete' : '');
        notice.appendChild(text);
        const retryBtn = document.createElement('button');
        retryBtn.textContent = 'Retry +' + effectiveTimeout + 's';
        retryBtn.style.cssText = 'background:rgba(255,152,0,0.15);border:1px solid #ff9800;color:#ff9800;border-radius:4px;cursor:pointer;padding:2px 8px;font-size:0.9em;white-space:nowrap;transition:all 0.15s;';
        retryBtn.addEventListener('mouseenter', () => { retryBtn.style.background = 'rgba(255,152,0,0.3)'; });
        retryBtn.addEventListener('mouseleave', () => { retryBtn.style.background = 'rgba(255,152,0,0.15)'; });
        retryBtn.addEventListener('click', () => { if (_rerollPane) _rerollPane(paneIdx, effectiveTimeout * 2); });
        notice.appendChild(retryBtn);
        aiBody.appendChild(notice);
      } else {
        if (!accumulated.trim()) aiBody.innerHTML = '<div style="color:#f0ad4e;font-size:0.9em;">Cancelled.</div>';
      }
    } else {
      console.error('Compare stream error:', error);
      aiBody.innerHTML = '<span style="color:var(--color-error);">Error: ' + escapeHtml(error.message) + '</span>';
    }
  } finally {
    clearTimeout(timeoutId);
    _timerDone = true;
    cancelAnimationFrame(_rafId);
    // Show final time with TTFT
    const _totalMs = performance.now() - _timerStart;
    if (_timerEl) {
      // TTFT removed from the header per user request — just show total time.
      _timerEl.textContent = _formatMs(_totalMs);
    }
    state._abortControllers[paneIdx] = null;
    // Hide stop button, show response action buttons
    const _paneElFinal = document.querySelector(`.compare-pane[data-pane="${paneIdx}"]`);
    if (_paneElFinal) {
      const _stopBtnFinal = _paneElFinal.querySelector('.pane-stop-btn');
      if (_stopBtnFinal) _stopBtnFinal.style.display = 'none';
      if (accumulated.trim()) {
        _paneElFinal.querySelectorAll('.pane-needs-response').forEach(b => b.style.display = '');
      }
    }
    state._paneMetrics[paneIdx] = metrics;
    state._paneElapsed[paneIdx] = _totalMs;
    if (!opts.skipBadge) {
      if (streamOk) {
        state._finishOrder++;
        if (state._parallel) {
          // Parallel: all panes started at the same instant, so first
          // to finish is genuinely the fastest.
          if (state._finishOrder === 1) addFinishBadge(paneIdx);
        } else {
          // Sequential: panes run one after another, so "first to
          // finish" is meaningless (it's just whoever ran first).
          // Wait until all panes are done, then badge whichever had
          // the lowest measured per-pane elapsed time.
          const total = state._selectedModels.length;
          const finished = state._paneElapsed.filter(v => typeof v === 'number').length;
          if (finished >= total) {
            let winnerIdx = -1, winnerMs = Infinity;
            for (let i = 0; i < total; i++) {
              const v = state._paneElapsed[i];
              if (typeof v === 'number' && v < winnerMs) { winnerMs = v; winnerIdx = i; }
            }
            if (winnerIdx >= 0) addFinishBadge(winnerIdx);
          }
        }
      } else {
        // Timed out or errored — show failed badge
        const badge = document.getElementById('cmp-badge-' + paneIdx);
        if (badge) { badge.textContent = timedOut ? 'Timeout' : 'Failed'; badge.style.color = 'var(--color-error)'; }
      }
    }
    // Auto-grade against expected answer — stamps ✓ or ✗ on the pane header.
    if (streamOk && state._expectedAnswer) {
      _stampGradeBadge(paneIdx, accumulated, state._expectedAnswer);
    }
    // Show copy/reroll buttons now that response exists
    const paneEl = document.querySelector('.compare-pane:nth-child(' + (paneIdx + 1) + ')');
    if (paneEl) paneEl.querySelectorAll('.pane-needs-response').forEach(b => b.style.display = '');
  }
}

/**
 * Auto-grade a pane's response against the eval prompt's expected answer.
 * Heuristic: lowercased substring match, plus a number-extraction fallback
 * so "the answer is 882" matches expected "882".
 * Skips meta answers like "count the words yourself…".
 */
function _stampGradeBadge(paneIdx, response, expected) {
  const norm = (s) => String(s).toLowerCase().replace(/\s+/g, ' ').trim();
  const r = norm(response);
  const e = norm(expected);
  if (!r || !e) return;
  // Skip non-checkable instructions
  if (e.includes('yourself') || e.includes('verify') || e.length > 120) return;

  let pass = r.includes(e);
  if (!pass) {
    // Numeric fallback — find first number in expected, look for it standalone in response
    const m = expected.match(/-?\d[\d,]*(?:\.\d+)?/);
    if (m) {
      const n = m[0].replace(/,/g, '');
      const re = new RegExp('(?<![\\d.])' + n.replace('.', '\\.') + '(?![\\d.])');
      pass = re.test(response);
    }
  }

  const paneEl = document.querySelector(`.compare-pane[data-pane="${paneIdx}"]`);
  if (!paneEl) return;
  const header = paneEl.querySelector('.pane-header');
  if (!header) return;
  // Remove any prior grade badge (re-roll case)
  const prev = header.querySelector('.pane-grade-badge');
  if (prev) prev.remove();
  const badge = document.createElement('span');
  badge.className = 'pane-grade-badge ' + (pass ? 'pass' : 'fail');
  badge.title = pass ? 'Response contains the expected answer' : 'Expected answer not found in response';
  badge.textContent = pass ? '✓' : '✗';
  // Insert just before the finish badge if present, else after the title
  const finBadge = header.querySelector('.pane-finish-badge');
  if (finBadge) header.insertBefore(badge, finBadge);
  else header.appendChild(badge);
}

export { streamToPane, _renderSearchResults, _runSynthForPane, _formatMs, registerStreamActions };
