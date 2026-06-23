// static/js/projects.js
// Projects tab: list, create, select, sub-tab nav, brain hover, settings.

(function () {
    const API_BASE = '/api/projects';
    const PROJECTS_TAB_ID = 'projects-tab';
    const MAIN_PANEL_ID = 'projects-main-panel';

    function el(tag, props = {}, ...children) {
        const e = document.createElement(tag);
        for (const [k, v] of Object.entries(props)) {
            if (k === 'class') e.className = v;
            else if (k === 'onclick') e.addEventListener('click', v);
            else if (k === 'dataset') Object.assign(e.dataset, v);
            else e.setAttribute(k, v);
        }
        for (const c of children) {
            if (c == null) continue;
            e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
        }
        return e;
    }

    function owner() {
        // Mirror backend: prefer current user state; fall back to header for tests.
        return window.currentUser || '';
    }

    async function fetchJson(method, path, body) {
        const opts = { method, headers: { 'Content-Type': 'application/json' } };
        if (owner()) opts.headers['X-Owner'] = owner();
        if (body !== undefined) opts.body = JSON.stringify(body);
        const res = await fetch(API_BASE + path, opts);
        if (!res.ok) throw new Error(`${method} ${path}: ${res.status}`);
        return res.json();
    }

    function show() {
        const section = document.getElementById('projects-section');
        if (section) section.style.display = '';
        const panel = document.getElementById(MAIN_PANEL_ID);
        if (panel) panel.style.display = '';
    }

    async function renderProjectList() {
        // Lightweight sidebar list — kept minimal. Click a project to select.
        const list = await fetchJson('GET', '');
        const sidebar = document.getElementById('projects-list');
        if (!sidebar) return;
        sidebar.innerHTML = '';
        if (list.length === 0) {
            sidebar.style.display = 'none';
            return;
        }
        sidebar.style.display = '';
        for (const p of list) {
            const item = el('div', {
                class: 'list-item project-item',
                dataset: { projectId: p.id },
                onclick: () => selectProject(p.id),
            },
                el('span', { class: 'grow' }, p.icon || '📁 ', p.name),
            );
            sidebar.appendChild(item);
        }
    }

    async function selectProject(pid) {
        try {
            const proj = await fetchJson('GET', `/${pid}`);
            // Highlight the selected item in the sidebar.
            const list = document.getElementById('projects-list');
            if (list) {
                for (const item of list.querySelectorAll('.project-item')) {
                    item.classList.toggle('selected', item.dataset.projectId === pid);
                }
            }
            // Open the project header overlay so the user can see what they picked.
            window.dispatchEvent(new CustomEvent('project-selected', { detail: proj }));
            window.currentProjectId = pid;
        } catch (e) {
            console.error('selectProject failed:', e);
        }
    }

    function showCreateDialog() {
        const existing = document.getElementById('project-create-modal');
        if (existing) existing.remove();

        const modal = el('div', {
            id: 'project-create-modal',
            class: 'modal-backdrop',
            style: 'position:fixed; inset:0; background:rgba(0,0,0,0.5); display:flex; align-items:center; justify-content:center; z-index:9999;',
        },
            el('div', {
                style: 'background:var(--panel, #222); border:1px solid var(--border, #444); border-radius:8px; padding:24px; min-width:380px; max-width:520px;',
            },
                el('h3', { style: 'margin:0 0 12px 0;' }, 'New project'),
                el('label', { style: 'display:block; font-size:0.85em; opacity:0.8; margin:8px 0 4px;' }, 'Name'),
                el('input', {
                    id: 'project-name-input', type: 'text', maxlength: '64',
                    style: 'width:100%; box-sizing:border-box; padding:8px; background:var(--bg, #111); color:var(--fg, #eee); border:1px solid var(--border, #444); border-radius:4px;',
                }),
                el('label', { style: 'display:block; font-size:0.85em; opacity:0.8; margin:12px 0 4px;' }, 'Icon (emoji, optional)'),
                el('input', {
                    id: 'project-icon-input', type: 'text', maxlength: '16',
                    style: 'width:100%; box-sizing:border-box; padding:8px; background:var(--bg, #111); color:var(--fg, #eee); border:1px solid var(--border, #444); border-radius:4px;',
                }),
                el('label', { style: 'display:block; font-size:0.85em; opacity:0.8; margin:12px 0 4px;' }, 'Description (optional)'),
                el('input', {
                    id: 'project-desc-input', type: 'text', maxlength: '256',
                    style: 'width:100%; box-sizing:border-box; padding:8px; background:var(--bg, #111); color:var(--fg, #eee); border:1px solid var(--border, #444); border-radius:4px;',
                }),
                el('label', { style: 'display:block; font-size:0.85em; opacity:0.8; margin:12px 0 4px;' }, 'Memory mode'),
                el('select', {
                    id: 'project-mode-input',
                    style: 'width:100%; box-sizing:border-box; padding:8px; background:var(--bg, #111); color:var(--fg, #eee); border:1px solid var(--border, #444); border-radius:4px;',
                },
                    el('option', { value: 'isolated' }, 'Isolated — private memory'),
                    el('option', { value: 'shared' }, 'Shared — share with main brain'),
                    el('option', { value: 'inherit' }, 'Inherit — snapshot of main brain'),
                ),
                el('div', { id: 'project-create-error', style: 'color:var(--red, #e06c75); font-size:0.85em; margin-top:8px; min-height:1.2em;' }),
                el('div', { style: 'display:flex; gap:8px; justify-content:flex-end; margin-top:16px;' },
                    el('button', {
                        type: 'button',
                        style: 'padding:6px 14px; background:transparent; color:var(--fg, #eee); border:1px solid var(--border, #444); border-radius:4px; cursor:pointer;',
                        onclick: () => modal.remove(),
                    }, 'Cancel'),
                    el('button', {
                        type: 'button', id: 'project-create-submit',
                        style: 'padding:6px 14px; background:var(--red, #e06c75); color:#fff; border:none; border-radius:4px; cursor:pointer;',
                        onclick: () => submitCreate(modal),
                    }, 'Create'),
                ),
            ),
        );
        document.body.appendChild(modal);
        // Submit on Enter, dismiss on Escape, click outside.
        const nameInput = modal.querySelector('#project-name-input');
        nameInput.focus();
        nameInput.addEventListener('keydown', (ev) => {
            if (ev.key === 'Enter') submitCreate(modal);
            if (ev.key === 'Escape') modal.remove();
        });
        modal.addEventListener('click', (ev) => { if (ev.target === modal) modal.remove(); });
    }

    async function submitCreate(modal) {
        const errEl = modal.querySelector('#project-create-error');
        errEl.textContent = '';
        const name = modal.querySelector('#project-name-input').value.trim();
        const icon = modal.querySelector('#project-icon-input').value.trim() || null;
        const description = modal.querySelector('#project-desc-input').value.trim() || null;
        const memory_mode = modal.querySelector('#project-mode-input').value;
        if (!name) { errEl.textContent = 'Name is required.'; return; }
        try {
            await fetchJson('POST', '', { name, icon, description, memory_mode });
            modal.remove();
            await renderProjectList();
        } catch (e) {
            errEl.textContent = `Create failed: ${e.message}`;
        }
    }

    // Wire the "+" button next to the Projects section title.
    // Module scripts are deferred so DOMContentLoaded may have already
    // fired before this script runs — attach directly instead of waiting
    // for DOMContentLoaded.
    const newBtn = document.getElementById('projects-new-btn');
    if (newBtn) {
        newBtn.addEventListener('click', (ev) => {
            ev.stopPropagation();
            showCreateDialog();
        });
    }
    const sectionTitle = document.getElementById('projects-section-title');
    if (sectionTitle) {
        sectionTitle.addEventListener('click', () => { renderProjectList(); });
    }

    function explainerFor(proj) {
        if (proj.memory_mode === 'shared') {
            return 'Memory is shared with the main brain — open the brain hover to view or edit.';
        }
        if (proj.memory_mode === 'inherit' && proj.snapshot_meta) {
            try {
                const m = JSON.parse(proj.snapshot_meta);
                return `Snapshot taken with ${m.count} facts (main had ${m.source_count}).`;
            } catch (_) { return 'Snapshot taken from main brain.'; }
        }
        return 'Memory is private and starts empty.';
    }

    async function renderSettings(proj) {
        const body = document.getElementById('projects-settings-body');
        if (!body) return;
        body.innerHTML = '';
        body.appendChild(el('p', {}, `Memory mode: ${proj.memory_mode} — ${explainerFor(proj)}`));
        body.appendChild(el('label', {}, 'Custom prompt (≤4000)'));
        const promptArea = el('textarea', { maxlength: '4000', id: 'proj-prompt' });
        promptArea.value = proj.custom_prompt || '';
        body.appendChild(promptArea);

        body.appendChild(el('label', {}, 'Custom instructions (≤2000)'));
        const instrArea = el('textarea', { maxlength: '2000', id: 'proj-instr' });
        instrArea.value = proj.custom_instructions || '';
        body.appendChild(instrArea);

        const saveBtn = el('button', {
            onclick: async () => {
                await fetchJson('PUT', `/${proj.id}/settings`, {
                    custom_prompt: promptArea.value || null,
                    custom_instructions: instrArea.value || null,
                });
                // Toast hook (left as a no-op stub for the unit test).
                if (window.showToast) window.showToast('Project settings saved.');
            },
        }, 'Save');
        body.appendChild(saveBtn);
    }

    function modeBadge(mode) {
        return ({ shared: 'Shared', inherit: 'Inherit', isolated: 'Isolated' })[mode] || 'Unknown';
    }

    async function openBrainHover(proj) {
        // For Shared mode, the brain hover opens the MAIN brain (single
        // source of truth — spec §6). The /api/projects/{pid}/memory
        // endpoints return 409 for Shared, so we redirect to the global
        // hover without a project-scoped fetch.
        if (proj.memory_mode === 'shared') {
            window.dispatchEvent(new CustomEvent('open-main-brain-hover'));
            return;
        }
        // Inherit / Isolated: open a project-scoped hover that lists the
        // project's memory.json entries via GET /api/projects/{pid}/memory.
        const memories = await fetchJson('GET', `/${proj.id}/memory`);
        const overlay = el('div', { class: 'brain-hover', id: `brain-hover-${proj.id}` },
            el('div', { class: 'brain-badge' }, `Memory · ${modeBadge(proj.memory_mode)}`),
            el('ul', { class: 'brain-list' },
                ...memories.map(m => el('li', {}, m.text)),
            ),
            el('div', { class: 'brain-actions' },
                el('button', { onclick: () => exportMemory(proj.id) }, 'Export'),
                el('label', { class: 'import-btn' }, 'Import',
                    el('input', {
                        type: 'file', accept: 'application/json', style: 'display:none',
                        onchange: (ev) => importMemory(proj.id, ev.target.files[0]),
                    }),
                ),
            ),
        );
        document.body.appendChild(overlay);
    }

    async function exportMemory(pid) {
        const data = await fetchJson('GET', `/${pid}/memory/export`);
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = el('a', { href: url, download: `${pid}-memory.json` });
        a.click();
        URL.revokeObjectURL(url);
    }

    async function importMemory(pid, file) {
        if (!file) return;
        const text = await file.text();
        const bundle = JSON.parse(text);
        // Preview first (no confirm), then send `?confirm=1` after user OKs.
        const preview = await fetchJson('POST', `/${pid}/memory/import?confirm=0`, bundle);
        if (!window.confirm(`Import ${preview.incoming_count} memories? This overwrites existing.`)) return;
        await fetchJson('POST', `/${pid}/memory/import?confirm=1`, bundle);
        await openBrainHover({ id: pid, memory_mode: 'inherit' });
    }

    window.projectsModule = {
        show, renderProjectList, selectProject, createProject, deleteProject,
        renderSettings, explainerFor,
        openBrainHover, exportMemory, importMemory, modeBadge,
    };

    // Bootstrap: probe GET /api/projects to detect the feature flag and
    // reveal the tab if the route is reachable. When FEATURES.projects_enabled
    // is false the route returns 404 and the tab stays hidden (its
    // inline display:none keeps the sidebar clean until opt-in).
    (async () => {
        try {
            const res = await fetch(API_BASE, {
                method: 'GET',
                headers: owner() ? { 'X-Owner': owner() } : {},
            });
            if (res.ok) show();
        } catch (_) {
            // Network error or auth failure — leave the tab hidden.
        }
    })();
})();
