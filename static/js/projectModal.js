/**
 * projectModal.js — Project detail modal
 *
 * Opens when the user double-clicks a project in the sidebar or calls
 * openProjectModal(projectId). Lets the user edit name/description/instructions,
 * manage pinned documents, trigger memory synthesis, and view memories.
 */

const API_BASE = window.location.origin;

let _currentProjectId = null;
let _modalEl = null;

function _closeModal() {
  if (_modalEl) {
    _modalEl.remove();
    _modalEl = null;
    _currentProjectId = null;
  }
}

function _buildModal() {
  const overlay = document.createElement('div');
  overlay.id = 'project-modal-overlay';
  overlay.style.cssText = [
    'position:fixed', 'inset:0', 'z-index:1200',
    'background:rgba(0,0,0,0.5)',
    'display:flex', 'align-items:center', 'justify-content:center',
    'padding:16px',
  ].join(';');

  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) _closeModal();
  });

  const panel = document.createElement('div');
  panel.style.cssText = [
    'background:var(--bg-primary,#1e1e1e)',
    'color:var(--text-primary,#e0e0e0)',
    'border-radius:10px',
    'width:min(600px,100%)',
    'max-height:85vh',
    'overflow-y:auto',
    'padding:20px',
    'box-shadow:0 8px 32px rgba(0,0,0,0.4)',
    'position:relative',
  ].join(';');

  // Close button
  const closeBtn = document.createElement('button');
  closeBtn.textContent = '×';
  closeBtn.style.cssText = 'position:absolute;top:10px;right:14px;font-size:20px;background:none;border:none;cursor:pointer;color:var(--text-secondary);';
  closeBtn.addEventListener('click', _closeModal);
  panel.appendChild(closeBtn);

  // Title
  const titleEl = document.createElement('h3');
  titleEl.id = 'pm-title';
  titleEl.style.cssText = 'margin:0 0 16px 0;font-size:16px;';
  panel.appendChild(titleEl);

  function _row(labelText, inputEl) {
    const row = document.createElement('div');
    row.style.cssText = 'margin-bottom:12px;';
    const lbl = document.createElement('label');
    lbl.textContent = labelText;
    lbl.style.cssText = 'display:block;font-size:12px;color:var(--text-secondary);margin-bottom:4px;';
    row.appendChild(lbl);
    row.appendChild(inputEl);
    return row;
  }

  const _inputStyle = 'width:100%;box-sizing:border-box;background:var(--bg-secondary,#2a2a2a);color:var(--text-primary);border:1px solid var(--border,#444);border-radius:6px;padding:6px 8px;font-size:13px;';

  const nameInput = document.createElement('input');
  nameInput.id = 'pm-name';
  nameInput.type = 'text';
  nameInput.style.cssText = _inputStyle;
  panel.appendChild(_row('Name', nameInput));

  const descInput = document.createElement('input');
  descInput.id = 'pm-desc';
  descInput.type = 'text';
  descInput.style.cssText = _inputStyle;
  panel.appendChild(_row('Description', descInput));

  const instrInput = document.createElement('textarea');
  instrInput.id = 'pm-instructions';
  instrInput.rows = 5;
  instrInput.placeholder = 'Custom instructions for every session in this project (markdown supported)';
  instrInput.style.cssText = _inputStyle + 'resize:vertical;font-family:inherit;';
  panel.appendChild(_row('Instructions (injected into every session)', instrInput));

  // Save button
  const saveBtn = document.createElement('button');
  saveBtn.textContent = 'Save changes';
  saveBtn.style.cssText = 'background:var(--accent-primary,#5b8abf);color:#fff;border:none;border-radius:6px;padding:7px 16px;cursor:pointer;font-size:13px;margin-bottom:20px;';
  saveBtn.addEventListener('click', async () => {
    const fd = new FormData();
    fd.append('name', nameInput.value.trim());
    fd.append('description', descInput.value.trim());
    fd.append('instructions', instrInput.value);
    const res = await fetch(`${API_BASE}/api/projects/${_currentProjectId}`, { method: 'PATCH', body: fd });
    if (res.ok) {
      const updated = await res.json();
      titleEl.textContent = updated.name;
      if (window.sessionsModule && window.sessionsModule.loadProjects) {
        window.sessionsModule.loadProjects().catch(() => {});
      }
      _showStatus(statusEl, 'Saved');
    } else {
      _showStatus(statusEl, 'Save failed');
    }
  });
  panel.appendChild(saveBtn);

  const statusEl = document.createElement('span');
  statusEl.style.cssText = 'font-size:12px;color:var(--accent-primary);margin-left:8px;';
  panel.appendChild(statusEl);

  // Pinned documents section
  const docSection = document.createElement('div');
  docSection.style.cssText = 'margin-top:8px;border-top:1px solid var(--border,#444);padding-top:12px;';
  const docTitle = document.createElement('div');
  docTitle.style.cssText = 'font-weight:bold;font-size:13px;margin-bottom:8px;display:flex;align-items:center;gap:8px;';
  docTitle.textContent = 'Pinned Documents';

  const pinBtn = document.createElement('button');
  pinBtn.textContent = '+ Pin by ID';
  pinBtn.style.cssText = 'background:none;border:1px solid var(--border,#444);border-radius:4px;color:var(--text-secondary);cursor:pointer;font-size:11px;padding:2px 6px;';
  pinBtn.addEventListener('click', async () => {
    const docId = window.prompt('Enter document ID to pin:');
    if (!docId || !docId.trim()) return;
    const res = await fetch(`${API_BASE}/api/projects/${_currentProjectId}/documents/${docId.trim()}`, { method: 'POST' });
    if (res.ok) {
      _showStatus(statusEl, 'Document pinned');
      await _refreshDocList(docList);
    } else {
      _showStatus(statusEl, 'Failed to pin document');
    }
  });
  docTitle.appendChild(pinBtn);
  docSection.appendChild(docTitle);

  const docList = document.createElement('div');
  docList.id = 'pm-doc-list';
  docSection.appendChild(docList);
  panel.appendChild(docSection);

  // Synthesize memories section
  const synthSection = document.createElement('div');
  synthSection.style.cssText = 'margin-top:12px;border-top:1px solid var(--border,#444);padding-top:12px;';
  const synthTitle = document.createElement('div');
  synthTitle.style.cssText = 'font-weight:bold;font-size:13px;margin-bottom:8px;';
  synthTitle.textContent = 'Synthesized Memory';

  const synthBtn = document.createElement('button');
  synthBtn.textContent = 'Synthesize from sessions';
  synthBtn.style.cssText = 'background:var(--accent-primary,#5b8abf);color:#fff;border:none;border-radius:6px;padding:6px 14px;cursor:pointer;font-size:12px;';
  synthBtn.addEventListener('click', async () => {
    synthBtn.disabled = true;
    synthBtn.textContent = 'Synthesizing...';
    try {
      const res = await fetch(`${API_BASE}/api/projects/${_currentProjectId}/synthesize`, { method: 'POST' });
      if (res.ok) {
        _showStatus(statusEl, 'Memory synthesized');
        await _refreshMemories(memList);
      } else {
        const err = await res.json().catch(() => ({}));
        _showStatus(statusEl, err.detail || 'Synthesis failed');
      }
    } finally {
      synthBtn.disabled = false;
      synthBtn.textContent = 'Synthesize from sessions';
    }
  });

  synthSection.appendChild(synthTitle);
  synthSection.appendChild(synthBtn);

  const memList = document.createElement('div');
  memList.id = 'pm-mem-list';
  memList.style.cssText = 'margin-top:10px;';
  synthSection.appendChild(memList);
  panel.appendChild(synthSection);

  overlay.appendChild(panel);
  return { overlay, nameInput, descInput, instrInput, titleEl, docList, memList, statusEl };
}

function _showStatus(el, msg) {
  el.textContent = msg;
  setTimeout(() => { el.textContent = ''; }, 3000);
}

async function _refreshDocList(docList) {
  docList.innerHTML = '';
  try {
    const res = await fetch(`${API_BASE}/api/projects/${_currentProjectId}/documents`);
    if (!res.ok) return;
    const docs = await res.json();
    if (!docs.length) {
      docList.textContent = 'No pinned documents.';
      docList.style.color = 'var(--text-secondary)';
      docList.style.fontSize = '12px';
      return;
    }
    docs.forEach(d => {
      const row = document.createElement('div');
      row.style.cssText = 'display:flex;align-items:center;gap:6px;padding:4px 0;font-size:12px;';
      const name = document.createElement('span');
      name.textContent = d.title || d.id;
      name.style.flex = '1';
      const unpinBtn = document.createElement('button');
      unpinBtn.textContent = 'Unpin';
      unpinBtn.style.cssText = 'background:none;border:1px solid var(--border,#444);border-radius:4px;color:var(--text-secondary);cursor:pointer;font-size:10px;padding:1px 6px;';
      unpinBtn.addEventListener('click', async () => {
        await fetch(`${API_BASE}/api/projects/${_currentProjectId}/documents/${d.id}`, { method: 'DELETE' });
        await _refreshDocList(docList);
      });
      row.appendChild(name);
      row.appendChild(unpinBtn);
      docList.appendChild(row);
    });
  } catch (e) {
    docList.textContent = 'Failed to load documents.';
  }
}

async function _refreshMemories(memList) {
  memList.innerHTML = '';
  try {
    const res = await fetch(`${API_BASE}/api/projects/${_currentProjectId}/memories`);
    if (!res.ok) return;
    const mems = await res.json();
    if (!mems.length) {
      memList.textContent = 'No memories yet. Use "Synthesize from sessions" to create one.';
      memList.style.cssText = 'color:var(--text-secondary);font-size:12px;margin-top:8px;';
      return;
    }
    mems.forEach(m => {
      const card = document.createElement('div');
      card.style.cssText = 'background:var(--bg-secondary,#2a2a2a);border-radius:6px;padding:10px;margin-top:8px;font-size:12px;';
      const meta = document.createElement('div');
      const dt = m.synthesized_at ? new Date(m.synthesized_at).toLocaleString() : '';
      meta.style.cssText = 'color:var(--text-secondary);font-size:10px;margin-bottom:6px;';
      meta.textContent = `${dt} · ${m.session_count} session(s)`;
      const content = document.createElement('div');
      content.style.whiteSpace = 'pre-wrap';
      content.textContent = m.content;
      card.appendChild(meta);
      card.appendChild(content);
      memList.appendChild(card);
    });
  } catch (e) {
    memList.textContent = 'Failed to load memories.';
  }
}

export async function openProjectModal(projectId) {
  if (_modalEl) _closeModal();
  _currentProjectId = projectId;

  const { overlay, nameInput, descInput, instrInput, titleEl, docList, memList } = _buildModal();
  _modalEl = overlay;
  document.body.appendChild(overlay);

  // Load project data
  try {
    const res = await fetch(`${API_BASE}/api/projects/${projectId}`);
    if (!res.ok) { _closeModal(); return; }
    const proj = await res.json();
    titleEl.textContent = proj.name;
    nameInput.value = proj.name || '';
    descInput.value = proj.description || '';
    instrInput.value = proj.instructions || '';
  } catch (e) {
    _closeModal();
    return;
  }

  await _refreshDocList(docList);
  await _refreshMemories(memList);
}

// Expose globally for sessions.js calls
window.projectModalModule = { openProjectModal };

export default { openProjectModal };
