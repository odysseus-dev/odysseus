// static/js/system-update.js — admin "App updates" card (Settings → System).
//
// Talks to the admin-only /api/system/update/* endpoints. Deliberately
// self-contained: no imports and no edits to admin.js, so it lives entirely in
// files upstream doesn't have and can't conflict on a merge. The heavy lifting
// (conflict-safe git merge) is server-side in src/self_update.py.

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));

async function api(path, method = 'GET') {
  const res = await fetch('/api/system/update/' + path, {
    method,
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
  });
  let data = {};
  try { data = await res.json(); } catch (_) { /* non-JSON error body */ }
  return { ok: res.ok, status: res.status, data };
}

function setStatus(html) { const e = $('sysupd-status'); if (e) e.innerHTML = html; }
function show(id, html) { const e = $(id); if (!e) return; if (html !== undefined) e.innerHTML = html; e.classList.remove('hidden'); }
function hide(id) { const e = $(id); if (e) e.classList.add('hidden'); }
function hideCard() { const c = $('settings-system-update-card'); if (c) c.classList.add('hidden'); }

let _busy = false;

async function doCheck() {
  if (_busy) return;
  _busy = true;
  const checkBtn = $('sysupd-check-btn');
  if (checkBtn) checkBtn.disabled = true;
  hide('sysupd-apply-btn'); hide('sysupd-changelog'); hide('sysupd-conflicts'); hide('sysupd-result');
  setStatus('Checking the upstream project…');
  try {
    const { ok, data } = await api('check');
    if (data && data.supported === false) { hideCard(); return; }
    if (!ok) { setStatus('Could not check for updates. Are you signed in as admin?'); return; }
    if (data.status === 'error') { setStatus('Update check failed: ' + esc(data.detail || 'unknown error')); return; }
    if (data.status === 'up_to_date') {
      const ahead = data.ahead ? `, ${data.ahead} local commit(s) ahead` : '';
      setStatus(`<span class="sysupd-ok">&#10003; You're on the latest version.</span> (${esc(data.current)}${ahead})`);
      return;
    }
    if (data.status === 'update_available') {
      const reqNote = data.requirements_changed ? ' Dependencies changed — they will be reinstalled.' : '';
      setStatus(`<b>${data.behind}</b> new commit(s) available from upstream (${esc(data.current)} &rarr; ${esc(data.target)}).${reqNote}`);
      if (data.changelog && data.changelog.length) show('sysupd-changelog', data.changelog.map(esc).join('\n'));
      if (data.predicted_conflicts && data.predicted_conflicts.length) {
        show('sysupd-conflicts', '&#9888; These files would conflict and need a manual merge: <b>'
          + data.predicted_conflicts.map(esc).join(', ')
          + '</b>. Applying will safely abort and change nothing.');
      }
      show('sysupd-apply-btn');
      return;
    }
    setStatus('Unexpected response from the server.');
  } catch (e) {
    setStatus('Update check failed: ' + esc(e && e.message ? e.message : e));
  } finally {
    _busy = false;
    if (checkBtn) checkBtn.disabled = false;
  }
}

async function doApply() {
  if (_busy) return;
  if (!confirm(
    'Apply the upstream update now?\n\n'
    + 'This merges new community/owner commits on top of your local customizations. '
    + 'If it cannot merge cleanly it aborts and changes nothing.\n\n'
    + 'After a successful update, restart Odysseus to load the new code.'
  )) return;
  _busy = true;
  const applyBtn = $('sysupd-apply-btn');
  const checkBtn = $('sysupd-check-btn');
  if (applyBtn) applyBtn.disabled = true;
  if (checkBtn) checkBtn.disabled = true;
  hide('sysupd-result');
  setStatus('Applying update… (this can take a minute if dependencies changed)');
  try {
    const { ok, data } = await api('apply', 'POST');
    if (!data || (!ok && !data.status)) { setStatus('Update failed (server error).'); return; }
    switch (data.status) {
      case 'applied': {
        const deps = data.deps_reinstalled ? ' Dependencies were reinstalled.' : '';
        const n = data.commits ? data.commits.length : 0;
        setStatus(`<span class="sysupd-ok">&#10003; Updated ${esc(data.from)} &rarr; ${esc(data.to)}</span> — ${n} commit(s) applied.${deps}`);
        show('sysupd-result', '&#128260; <b>Restart Odysseus to apply.</b> Quit the launcher (its process holds the server) and reopen the app. Rollback point: <code>'
          + esc(data.backup_tag || '') + '</code>.');
        hide('sysupd-apply-btn');
        break;
      }
      case 'up_to_date':
        setStatus('<span class="sysupd-ok">&#10003; Already up to date.</span>');
        hide('sysupd-apply-btn');
        break;
      case 'conflict':
        setStatus('Update aborted to protect your changes.');
        show('sysupd-conflicts', '&#9888; Conflicts in <b>' + (data.conflicts || []).map(esc).join(', ')
          + '</b>. <b>Nothing was changed</b> — your customizations are intact. Resolve manually '
          + '(e.g. <code>git merge upstream/dev</code>). Backup tag: <code>' + esc(data.backup_tag || '') + '</code>.');
        break;
      case 'dirty':
        setStatus('Cannot update: the working tree has uncommitted changes. Commit or stash them first, then retry.');
        break;
      case 'unsupported':
        hideCard();
        break;
      default:
        setStatus('Update failed: ' + esc(data.detail || 'unknown error'));
    }
  } catch (e) {
    setStatus('Update failed: ' + esc(e && e.message ? e.message : e));
  } finally {
    _busy = false;
    if (applyBtn) applyBtn.disabled = false;
    if (checkBtn) checkBtn.disabled = false;
  }
}

function wire() {
  const checkBtn = $('sysupd-check-btn');
  if (!checkBtn) return; // card not in DOM
  checkBtn.addEventListener('click', doCheck);
  const applyBtn = $('sysupd-apply-btn');
  if (applyBtn) applyBtn.addEventListener('click', doApply);

  // Hide the whole card where self-update can't work (frozen build / Docker /
  // no upstream remote). Lazy probe on first System-tab open, so we don't fire
  // an admin-only request for non-admins on every page load.
  const sysTab = document.querySelector('[data-settings-tab="system"]');
  let probed = false;
  if (sysTab) sysTab.addEventListener('click', async () => {
    if (probed) return;
    probed = true;
    try { const { ok, data } = await api('available'); if (ok && data && data.supported === false) hideCard(); }
    catch (_) { /* ignore */ }
  });
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', wire);
else wire();
