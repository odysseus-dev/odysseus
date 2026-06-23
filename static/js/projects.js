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
        const tab = document.getElementById(PROJECTS_TAB_ID);
        if (tab) tab.style.display = '';
        const panel = document.getElementById(MAIN_PANEL_ID);
        if (panel) panel.style.display = '';
    }

    async function renderProjectList() {
        const list = await fetchJson('GET', '');
        const sidebar = document.getElementById('projects-sidebar');
        if (!sidebar) return;
        sidebar.innerHTML = '';
        for (const p of list) {
            const item = el('div', {
                class: 'project-item',
                dataset: { projectId: p.id },
                onclick: () => selectProject(p.id),
            }, el('span', { class: 'icon' }, p.icon || '📁'),
               el('span', { class: 'name' }, p.name));
            sidebar.appendChild(item);
        }
    }

    async function selectProject(pid) {
        const proj = await fetchJson('GET', `/${pid}`);
        const header = document.getElementById('projects-header');
        if (!header) return;
        header.innerHTML = '';
        header.appendChild(el('span', { class: 'icon' }, proj.icon || '📁'));
        header.appendChild(el('span', { class: 'name' }, proj.name));
        // Sub-tab nav handled in T28.
    }

    async function createProject(body) {
        const proj = await fetchJson('POST', '', body);
        await renderProjectList();
        await selectProject(proj.id);
        return proj;
    }

    async function deleteProject(pid, name) {
        const res = await fetch(`${API_BASE}/${pid}`, {
            method: 'DELETE',
            headers: { 'X-Owner': owner(), 'X-Confirm-Name': name },
        });
        if (!res.ok) throw new Error(`delete: ${res.status}`);
        return res.json();
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
