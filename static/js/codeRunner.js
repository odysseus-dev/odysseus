// static/js/codeRunner.js

import * as uiModule from './ui.js';

/**
 * In-browser code runner for Python (Pyodide), JavaScript, and HTML
 */

let pyodideInstance = null;
let pyodideLoading = false;
const pyodideQueue = [];

/**
 * Get or create an output panel below the <pre> element
 */
function getOrCreatePanel(pre) {
  let panel = pre.nextElementSibling;
  if (panel && panel.classList.contains('code-runner-output')) {
    panel.innerHTML = '';
    panel.style.display = 'block';
    return panel;
  }
  panel = document.createElement('div');
  panel.className = 'code-runner-output';
  pre.parentNode.insertBefore(panel, pre.nextSibling);
  return panel;
}

/**
 * Show a loading message in the panel
 */
function showLoading(panel, msg) {
  panel.innerHTML = `<div class="code-runner-loading">${msg}</div>`;
}

/**
 * Show output text in the panel
 */
function showOutput(panel, text, isError) {
  const el = document.createElement('pre');
  el.className = isError ? 'code-runner-pre code-runner-error' : 'code-runner-pre';
  el.textContent = text;
  panel.innerHTML = '';
  panel.appendChild(el);
  // Copy button — visible labeled pill at the top-right of the panel
  // itself (no separate footer / divider, no tiny icon corner).
  if (text) {
    const cbtn = document.createElement('button');
    cbtn.type = 'button';
    cbtn.className = 'code-runner-copy-inline';
    cbtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:4px;"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>Copy';
    cbtn.addEventListener('click', (e) => {
      e.stopPropagation();
      e.preventDefault();
      let ok = false;
      try {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.cssText = 'position:fixed;left:0;top:0;width:1px;height:1px;opacity:0;';
        document.body.appendChild(ta);
        ta.focus();
        ta.select();
        ta.setSelectionRange(0, text.length);
        ok = document.execCommand && document.execCommand('copy');
        ta.remove();
      } catch (_) {}
      if (!ok && navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(() => {
          if (uiModule.showToast) uiModule.showToast('Copied');
          cbtn.textContent = 'Copied!';
          setTimeout(() => { cbtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:4px;"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>Copy'; }, 1500);
        }).catch(() => { if (uiModule.showToast) uiModule.showToast('Copy failed'); });
        return;
      }
      if (uiModule.showToast) uiModule.showToast(ok ? 'Copied' : 'Copy failed');
      const orig = cbtn.innerHTML;
      cbtn.textContent = ok ? 'Copied!' : 'Copy failed';
      setTimeout(() => { cbtn.innerHTML = orig; }, 1500);
    });
    // Button lives directly in the panel — no wrapping bar. The panel is
    // position:relative so the button can sit absolute-top-right of it.
    panel.appendChild(cbtn);
  }
  if (isError) {
    setTimeout(() => { if (panel) panel.style.display = 'none'; }, 7000);
  }
}

/**
 * Legacy absolute-positioned copy button — replaced by the inline bar in
 * showOutput. Kept here as no-op so any earlier callers don't crash.
 */
function addCopyBtn_unused(panel, text) {
  if (!text) return;
  const btn = document.createElement('button');
  btn.type = 'button';  // Default <button> type is 'submit' — explicit "button" avoids any accidental form submission.
  btn.className = 'code-runner-copy';
  btn.title = 'Copy output';
  btn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
  btn.addEventListener('click', async (e) => {
    e.stopPropagation();
    e.preventDefault();
    // Synchronous copy via a hidden textarea + execCommand — this is the
    // single most reliable path across browsers / non-secure contexts /
    // mobile Firefox. Run BEFORE any async navigator.clipboard attempt so
    // the user-gesture context is preserved.
    let ok = false;
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.cssText = 'position:fixed;left:0;top:0;width:1px;height:1px;opacity:0;';
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      ta.setSelectionRange(0, text.length);
      ok = document.execCommand && document.execCommand('copy');
      ta.remove();
    } catch (_) {}
    // As a backup, also try the modern clipboard API (won't hurt if the
    // legacy path already copied).
    if (!ok && navigator.clipboard && window.isSecureContext) {
      try { await navigator.clipboard.writeText(text); ok = true; } catch (_) {}
    }
    if (uiModule && uiModule.showToast) {
      uiModule.showToast(ok ? 'Copied' : 'Copy failed');
    }
    const _orig = btn.innerHTML;
    btn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
    btn.classList.add('copied');
    setTimeout(() => { btn.innerHTML = _orig; btn.classList.remove('copied'); }, 1500);
  });
  panel.prepend(btn);
}

/**
 * Add a collapse/close button to the panel.
 * Disabled \u2014 the run-output panel is now closed via the unified Code\u2194Run
 * toggle in the editor footer, so a separate X was redundant + cluttered.
 */
function addCloseBtn(_panel) { /* no-op */ }

/**
 * Lazy-load Pyodide from CDN
 */
function loadPyodide() {
  if (pyodideInstance) return Promise.resolve(pyodideInstance);
  if (pyodideLoading) {
    return new Promise((resolve, reject) => {
      pyodideQueue.push({ resolve, reject });
    });
  }
  pyodideLoading = true;

  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = 'https://cdn.jsdelivr.net/pyodide/v0.27.5/full/pyodide.js';
    script.onload = () => {
      window.loadPyodide({ indexURL: 'https://cdn.jsdelivr.net/pyodide/v0.27.5/full/' })
        .then(py => {
          pyodideInstance = py;
          pyodideLoading = false;
          pyodideQueue.forEach(q => q.resolve(py));
          pyodideQueue.length = 0;
          resolve(py);
        })
        .catch(err => {
          pyodideLoading = false;
          pyodideQueue.forEach(q => q.reject(err));
          pyodideQueue.length = 0;
          reject(err);
        });
    };
    script.onerror = () => {
      pyodideLoading = false;
      const err = new Error('Failed to load Pyodide');
      pyodideQueue.forEach(q => q.reject(err));
      pyodideQueue.length = 0;
      reject(err);
    };
    document.head.appendChild(script);
  });
}

/**
 * Run Python code via Pyodide
 */
export async function runPython(code, panel) {
  showLoading(panel, 'Loading Python runtime (first time ~10 MB)...');

  let py;
  try {
    py = await loadPyodide();
  } catch (e) {
    showOutput(panel, 'Failed to load Python runtime: ' + e.message, true);
    addCloseBtn(panel);
    return;
  }

  showLoading(panel, 'Running...');

  const wrapper = `
import sys, io
_stdout = io.StringIO()
_stderr = io.StringIO()
sys.stdout = _stdout
sys.stderr = _stderr
try:
    exec(${JSON.stringify(code)})
except Exception as _e:
    _stderr.write(str(_e))
finally:
    sys.stdout = sys.__stdout__
    sys.stderr = sys.__stderr__
(_stdout.getvalue(), _stderr.getvalue())
`;

  try {
    const result = await Promise.race([
      py.runPythonAsync(wrapper),
      new Promise((_, reject) => setTimeout(() => reject(new Error('Execution timed out (10 s)')), 10000))
    ]);

    const stdout = result.toJs ? result.toJs()[0] : (result[0] || '');
    const stderr = result.toJs ? result.toJs()[1] : (result[1] || '');
    if (result.destroy) result.destroy();

    panel.innerHTML = '';
    if (stderr) {
      showOutput(panel, stderr, true);
    } else if (stdout) {
      showOutput(panel, stdout, false);
    } else {
      showOutput(panel, '(no output)', false);
    }
  } catch (e) {
    showOutput(panel, e.message, true);
  }
  addCloseBtn(panel);
}

/**
 * Run JavaScript code in a sandboxed iframe
 */
export function runJavaScript(code, panel) {
  showLoading(panel, 'Running...');

  const iframe = document.createElement('iframe');
  iframe.style.display = 'none';
  iframe.sandbox = 'allow-scripts';
  document.body.appendChild(iframe);

  let settled = false;
  const cleanup = () => {
    if (iframe.parentNode) iframe.remove();
  };

  const failsafe = setTimeout(() => {
    if (!settled) {
      settled = true;
      showOutput(panel, 'Execution timed out (10 s)', true);
      addCloseBtn(panel);
      cleanup();
    }
  }, 15000);

  const onMessage = (e) => {
    if (e.source !== iframe.contentWindow) return;
    if (settled) return;
    settled = true;
    clearTimeout(failsafe);
    window.removeEventListener('message', onMessage);

    const data = e.data;
    panel.innerHTML = '';
    if (data.error) {
      showOutput(panel, data.error, true);
    } else if (data.logs && data.logs.length > 0) {
      showOutput(panel, data.logs.join('\n'), false);
    } else {
      showOutput(panel, '(no output)', false);
    }
    addCloseBtn(panel);
    cleanup();
  };

  window.addEventListener('message', onMessage);

  const wrappedCode = `
<!DOCTYPE html><html><body><script>
var _logs = [];
var _origLog = console.log;
console.log = function() { _logs.push([].map.call(arguments, function(a) { try { return typeof a === 'object' ? JSON.stringify(a) : String(a); } catch(e) { return String(a); } }).join(' ')); };
console.warn = function() { _logs.push('[warn] ' + [].map.call(arguments, String).join(' ')); };
console.error = function() { _logs.push('[error] ' + [].map.call(arguments, String).join(' ')); };
try {
  var _timer = setTimeout(function() { parent.postMessage({error:'Execution timed out (10 s)'},'*'); }, 10000);
  ${code.replace(/<\/script>/gi, '<\\/script>')}
  clearTimeout(_timer);
  parent.postMessage({logs: _logs}, '*');
} catch(e) {
  parent.postMessage({error: e.toString()}, '*');
}
<\/script></body></html>`;

  iframe.srcdoc = wrappedCode;
}

/**
 * Run code server-side via POST /api/shell/exec
 */
export async function runServer(code, panel, lang) {
  showLoading(panel, 'Running on server...');
  var command;
  if (lang === 'python' || lang === 'py') {
    command = 'python3 -c ' + JSON.stringify(code);
  } else {
    command = 'bash -c ' + JSON.stringify(code);
  }
  try {
    var res = await fetch('/api/shell/exec', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: command }),
    });
    var data = await res.json();
    panel.innerHTML = '';
    if (data.stderr && data.stderr.trim()) {
      showOutput(panel, data.stderr, true);
      if (data.stdout && data.stdout.trim()) {
        var stdoutEl = document.createElement('pre');
        stdoutEl.className = 'code-runner-pre';
        stdoutEl.textContent = data.stdout;
        panel.appendChild(stdoutEl);
      }
    } else if (data.stdout && data.stdout.trim()) {
      showOutput(panel, data.stdout, false);
    } else {
      showOutput(panel, '(no output)' + (data.exit_code ? ' — exit code ' + data.exit_code : ''), !data.exit_code ? false : true);
    }
    if (data.exit_code && data.exit_code !== 0) {
      var exitEl = document.createElement('div');
      exitEl.style.cssText = 'font-size:0.75rem;opacity:0.5;padding:2px 8px;';
      exitEl.textContent = 'Exit code: ' + data.exit_code;
      panel.appendChild(exitEl);
    }
  } catch (e) {
    showOutput(panel, 'Execution failed: ' + e.message, true);
  }
  addCloseBtn(panel);
}

/**
 * Run HTML code in its own popup window
 */
export function runHTML(code, panel) {
  panel.innerHTML = '';

  const win = window.open('', '_blank', 'width=800,height=600,menubar=no,toolbar=no,location=no,status=no');
  if (!win) {
    showOutput(panel, 'Popup blocked — please allow popups for this site.', true);
    addCloseBtn(panel);
    return;
  }
  try { win.opener = null; } catch (_) {}
  win.document.open();
  win.document.write(code);
  win.document.close();

  showOutput(panel, 'Opened in new window', false);
  addCloseBtn(panel);
}

/**
 * Main entry point — called when a Run button is clicked
 */
export function run(btn) {
  const code = btn.getAttribute('data-code');
  const lang = (btn.getAttribute('data-lang') || '').toLowerCase();
  if (!code) return;

  const pre = btn.closest('pre');
  if (!pre) return;

  const panel = getOrCreatePanel(pre);

  if (lang === 'bash' || lang === 'sh' || lang === 'shell' || lang === 'zsh') {
    runServer(code, panel, 'bash');
  } else if (lang === 'python' || lang === 'py') {
    runServer(code, panel, 'python');
  } else if (lang === 'javascript' || lang === 'js') {
    runJavaScript(code, panel);
  } else if (lang === 'html') {
    runHTML(code, panel);
  }
}

const codeRunnerModule = { run, runPython, runJavaScript, runHTML, runServer };
export default codeRunnerModule;
