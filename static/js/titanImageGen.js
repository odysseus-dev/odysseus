/**
 * Titan image generation UX — poll scheduler phase, chat lock, Stop (pipeline step 5).
 */
(function () {
  const API_BASE = window.API_BASE || '';
  let _pollTimer = null;
  let _stopBtn = null;

  async function fetchImageStatus(signal) {
    if (window.titanSchedulerStatus) {
      const st = await window.titanSchedulerStatus.fetchStatus(signal);
      return window.titanSchedulerStatus.imagePhasePayload(st);
    }
    try {
      const r = await fetch(`${API_BASE}/api/titan/hub/image-status`, {
        credentials: 'same-origin',
        signal,
      });
      if (!r.ok) return { phase: 'idle', progress_step: 0, progress_total: 0 };
      return await r.json();
    } catch (_) {
      return { phase: 'idle', progress_step: 0, progress_total: 0 };
    }
  }

  function isImageGenerationActive(phase) {
    return phase === 'generating' || phase === 'swapping' || phase === 'restoring_llm';
  }

  function progressMessage(st) {
    const phase = st?.phase || 'idle';
    if (phase === 'swapping') return 'Preparing image model…';
    if (phase === 'restoring_llm') return 'Restoring chat model…';
    if (phase === 'generating') {
      const total = Number(st.progress_total) || 0;
      const step = Number(st.progress_step) || 0;
      if (total > 0 && step > 0) {
        const pct = Math.min(99, Math.round((step / total) * 100));
        return `Generating image… ${pct}%`;
      }
      return 'Generating image…';
    }
    if (phase === 'error') return 'Image generation failed';
    return null;
  }

  function ensureStopButton() {
    if (_stopBtn) return _stopBtn;
    _stopBtn = document.createElement('button');
    _stopBtn.type = 'button';
    _stopBtn.className = 'titan-image-stop-btn';
    _stopBtn.textContent = 'Stop generace';
    _stopBtn.addEventListener('click', async () => {
      _stopBtn.disabled = true;
      await stopImageGeneration();
      _stopBtn.disabled = false;
      stopGenerationWatch();
    });
    document.body.appendChild(_stopBtn);
    return _stopBtn;
  }

  function setChatLock(locked) {
    const bar = document.querySelector('.chat-input-bar');
    if (bar) bar.classList.toggle('titan-image-locked', !!locked);
    const msg = document.getElementById('message');
    if (msg) msg.disabled = !!locked;
  }

  function showStopButton(show) {
    const btn = ensureStopButton();
    btn.classList.toggle('visible', !!show);
  }

  async function stopImageGeneration() {
    const r = await fetch(`${API_BASE}/api/titan/hub/image-stop`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    return r.ok;
  }

  function startGenerationWatch(onProgress) {
    stopGenerationWatch();
    setChatLock(true);
    showStopButton(true);
    const tick = async () => {
      const st = await fetchImageStatus();
      const phase = st?.phase || 'idle';
      const msg = progressMessage(st);
      if (typeof onProgress === 'function' && msg) onProgress(msg);
      if (!isImageGenerationActive(phase)) {
        if (phase === 'error' && typeof onProgress === 'function') {
          onProgress('Generace selhala');
        }
        stopGenerationWatch();
      }
    };
    tick();
    _pollTimer = setInterval(tick, 1500);
  }

  function stopGenerationWatch() {
    if (_pollTimer) {
      clearInterval(_pollTimer);
      _pollTimer = null;
    }
    setChatLock(false);
    showStopButton(false);
  }

  window.__titanImageGen = {
    fetchImageStatus,
    isImageGenerationActive,
    progressMessage,
    stopImageGeneration,
    startGenerationWatch,
    stopGenerationWatch,
    setChatLock,
  };
})();
