// static/js/convert.js — Settings → Convert Files tab.
// Upload a file, pick a target format, download the converted result.
// Backed by /api/convert (see routes/convert_routes.py).

let initialized = false;

function el(id) { return document.getElementById(id); }

function setMsg(text, kind) {
  const m = el('convert-msg');
  if (!m) return;
  m.textContent = text || '';
  m.style.color = kind === 'error'
    ? 'var(--color-error)'
    : (kind === 'ok' ? 'var(--color-success, #4caf50)' : 'inherit');
}

async function refreshTargets(filename) {
  const sel = el('convert-target-select');
  const runBtn = el('convert-run-btn');
  if (!sel) return;
  sel.innerHTML = '<option value="">Loading…</option>';
  sel.disabled = true;
  if (runBtn) runBtn.disabled = true;
  try {
    const r = await fetch(`/api/convert/targets?filename=${encodeURIComponent(filename)}`, {
      credentials: 'same-origin',
    });
    const data = await r.json();
    const targets = (data && data.targets) || [];
    if (!targets.length) {
      sel.innerHTML = '<option value="">Unsupported file type</option>';
      sel.disabled = true;
      setMsg('This file type can\'t be converted.', 'error');
      return;
    }
    sel.innerHTML = targets
      .map(t => `<option value="${t}">${t.toUpperCase()}</option>`)
      .join('');
    sel.disabled = false;
    if (runBtn) runBtn.disabled = false;
    setMsg('');
  } catch (e) {
    sel.innerHTML = '<option value="">Error</option>';
    setMsg('Could not load target formats.', 'error');
  }
}

async function runConversion() {
  const fileInput = el('convert-file-input');
  const sel = el('convert-target-select');
  const runBtn = el('convert-run-btn');
  if (!fileInput || !sel || !fileInput.files || !fileInput.files[0]) {
    setMsg('Choose a file first.', 'error');
    return;
  }
  const file = fileInput.files[0];
  const target = sel.value;
  if (!target) { setMsg('Choose a target format.', 'error'); return; }

  const fd = new FormData();
  fd.append('file', file);
  fd.append('target', target);

  if (runBtn) runBtn.disabled = true;
  setMsg('Converting…');
  try {
    const r = await fetch('/api/convert', {
      method: 'POST',
      credentials: 'same-origin',
      body: fd,
    });
    if (!r.ok) {
      let detail = `Conversion failed (${r.status}).`;
      try { const j = await r.json(); if (j && j.detail) detail = j.detail; } catch (_) {}
      setMsg(detail, 'error');
      return;
    }
    const blob = await r.blob();
    const outName = r.headers.get('X-Output-Filename')
      || `${file.name.replace(/\.[^.]+$/, '')}.${target}`;
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = outName;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setMsg(`Downloaded ${outName}`, 'ok');
  } catch (e) {
    setMsg('Conversion failed — network error.', 'error');
  } finally {
    if (runBtn) runBtn.disabled = false;
  }
}

export function init() {
  if (initialized) return;
  const fileInput = el('convert-file-input');
  const runBtn = el('convert-run-btn');
  if (!fileInput || !runBtn) return; // modal not in DOM
  initialized = true;

  fileInput.addEventListener('change', () => {
    const f = fileInput.files && fileInput.files[0];
    if (f) refreshTargets(f.name);
    else setMsg('');
  });
  runBtn.addEventListener('click', runConversion);
}

// Called each time the modal opens: ensure listeners are wired and reset the
// form so a previous conversion's file/target/message doesn't linger.
export function open() {
  init();
  const fileInput = el('convert-file-input');
  const sel = el('convert-target-select');
  const runBtn = el('convert-run-btn');
  if (fileInput) fileInput.value = '';
  if (sel) {
    sel.innerHTML = '<option value="">Choose a file first…</option>';
    sel.disabled = true;
  }
  if (runBtn) runBtn.disabled = true;
  setMsg('');
}

const convertModule = { init, open };
export default convertModule;
