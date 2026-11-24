// CardDAV contacts integration form and its contacts manager. Extracted from
// the unified integrations host; the host passes { formEl, renderList }.

import { byId as el } from '../../dom.js';
import { notifyIntegrationsChanged } from '../events.js';
import uiModule from '../../../ui.js?v=20260916largetoolscroll1';
import { esc } from '../../html.js';
import { downloadBlob } from '../../../shared/platform/downloads.js';

// ── CardDAV form + contacts manager ──
export async function showCardDavForm({ formEl, renderList, closeForm }) {
  formEl.innerHTML = `
      <div class="admin-card" style="margin-top:8px">
        <h2 style="font-size:13px;display:flex;align-items:center;gap:6px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color:var(--accent, var(--red));flex-shrink:0;"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>Contacts (CardDAV)</h2>
        <div class="settings-col">
          <div class="settings-row"><label class="settings-label" for="uf-carddav-url">URL</label><input id="uf-carddav-url" class="settings-input" placeholder="http://localhost:5232/user/contacts/" required aria-required="true"></div>
          <div class="settings-row"><label class="settings-label" for="uf-carddav-user">Username</label><input id="uf-carddav-user" class="settings-input"></div>
          <div class="settings-row"><label class="settings-label" for="uf-carddav-pass">Password</label><input id="uf-carddav-pass" class="settings-input" type="password"></div>
          <div class="settings-row" style="margin-top:10px;align-items:center;justify-content:flex-end;gap:6px;">
            <span id="uf-carddav-msg" role="status" aria-live="polite" style="font-size:11px;flex:1;margin-right:8px"></span>
            <button class="admin-btn-add" id="uf-carddav-save" style="display:inline-flex;align-items:center;gap:5px;background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));font-weight:600;">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>
              Save
            </button>
            <button class="admin-btn-add" id="uf-carddav-cancel" style="display:inline-flex;align-items:center;gap:5px;background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
              Cancel
            </button>
          </div>
        </div>
      </div>
      <div class="admin-card contacts-manager" style="margin-top:8px">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
          <h2 style="font-size:13px;margin:0;display:flex;align-items:center;gap:6px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color:var(--accent, var(--red));flex-shrink:0;"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>Contacts Import <span id="cm-count" style="opacity:0.5;font-weight:normal;font-size:11px;"></span></h2>
          <button class="admin-btn-sm" id="cm-import-btn" style="margin-left:auto;">Import</button>
          <button class="admin-btn-sm" id="cm-export-vcf-btn">Export .vcf</button>
          <button class="admin-btn-sm" id="cm-export-csv-btn">Export .csv</button>
          <button class="admin-btn-sm" id="cm-add-toggle">+ Add</button>
          <input type="file" id="cm-import-file" accept=".vcf,.csv,text/vcard,text/csv" multiple style="display:none">
        </div>
        <div id="cm-add-row" class="contacts-add-row" style="display:none;flex-direction:column;gap:4px;">
          <input id="cm-add-name" class="settings-input" placeholder="Name" aria-label="Contact name">
          <input id="cm-add-email" class="settings-input" placeholder="email@example.com" aria-label="Contact email">
          <input id="cm-add-phone" class="settings-input" placeholder="Phone (optional)" aria-label="Contact phone">
          <input id="cm-add-address" class="settings-input" placeholder="Address (optional)" aria-label="Contact address">
          <div style="display:flex;gap:6px;justify-content:flex-end;"><button class="admin-btn-sm" id="cm-add-save">Save</button></div>
        </div>
        <input type="text" id="cm-search" class="settings-input" placeholder="Search contacts (name, email, phone, address)" aria-label="Search contacts" style="margin-top:6px;">
        <div id="cm-list" class="contacts-list"><div style="opacity:0.4;font-size:11px;padding:8px 2px;">Loading…</div></div>
      </div>`;
  try {
    const r = await fetch('/api/contacts/config', { credentials: 'same-origin' }); const d = await r.json();
    el('uf-carddav-url').value = d.url || ''; el('uf-carddav-user').value = d.username || '';
    // Server masks the password as '***' when one is saved (or '' when
    // none). Surface that state via the input's placeholder so users
    // can tell their password is already on file without us echoing it.
    const passInput = el('uf-carddav-pass');
    if (passInput && d.password) passInput.placeholder = '(unchanged)';
  } catch (_) {}
  el('uf-carddav-cancel').addEventListener('click', () => {
    if (typeof closeForm === 'function') closeForm();
    else { formEl.style.display = 'none'; formEl.innerHTML = ''; }
  });
  el('uf-carddav-save').addEventListener('click', async () => {
    const body = { carddav_url: el('uf-carddav-url').value, carddav_username: el('uf-carddav-user').value };
    if (el('uf-carddav-pass').value) body.carddav_password = el('uf-carddav-pass').value;
    try {
      await fetch('/api/contacts/config', { method: 'PUT', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      el('uf-carddav-msg').textContent = 'Saved';
      el('uf-carddav-msg').style.color = 'var(--green, #50fa7b)';
      // Refresh both the sub-panel (contacts manager) AND the
      // outer integrations list so the CardDAV row appears
      // immediately instead of waiting for a page reload.
      await _renderContactsManager();
      await renderList();
      notifyIntegrationsChanged();
    } catch (_) {
      el('uf-carddav-msg').textContent = 'Failed';
      el('uf-carddav-msg').style.color = 'var(--red)';
    }
  });
  // Add-row toggle + save
  el('cm-add-toggle')?.addEventListener('click', () => {
    const row = el('cm-add-row');
    const open = row.style.display !== 'none';
    row.style.display = open ? 'none' : 'flex';
    if (!open) el('cm-add-name')?.focus();
  });
  el('cm-add-save')?.addEventListener('click', async () => {
    const name = el('cm-add-name').value.trim();
    const email = el('cm-add-email').value.trim();
    const phone = el('cm-add-phone')?.value.trim() || '';
    const address = el('cm-add-address')?.value.trim() || '';
    // Need at least a name or email; address-only entries without a
    // name aren't useful as a contact.
    if (!name && !email) { (name ? el('cm-add-email') : el('cm-add-name')).focus(); return; }
    try {
      await fetch('/api/contacts/add', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, email, phone, address }) });
    } catch (_) {}
    el('cm-add-name').value = '';
    el('cm-add-email').value = '';
    if (el('cm-add-phone')) el('cm-add-phone').value = '';
    if (el('cm-add-address')) el('cm-add-address').value = '';
    el('cm-add-row').style.display = 'none';
    await _renderContactsManager();
  });
  const _downloadContacts = async (format) => {
    const btn = el(format === 'csv' ? 'cm-export-csv-btn' : 'cm-export-vcf-btn');
    const orig = btn ? btn.textContent : '';
    if (btn) { btn.textContent = 'Exporting...'; btn.disabled = true; }
    try {
      const res = await fetch(`/api/contacts/export?format=${encodeURIComponent(format)}`, { credentials: 'same-origin' });
      if (!res.ok) throw new Error('Export failed');
      const blob = await res.blob();
      downloadBlob(blob, format === 'csv' ? 'odysseus-contacts.csv' : 'odysseus-contacts.vcf', 1000);
    } catch (_) {
      uiModule.showError ? uiModule.showError('Export failed') : alert('Export failed');
    } finally {
      if (btn) { btn.textContent = orig; btn.disabled = false; }
    }
  };
  el('cm-export-vcf-btn')?.addEventListener('click', () => _downloadContacts('vcf'));
  el('cm-export-csv-btn')?.addEventListener('click', () => _downloadContacts('csv'));

  // Import .vcf/.csv — read each selected file as text, concatenate by type,
  // then POST. Imported CardDAV contacts immediately feed email autocomplete
  // because compose searches /api/contacts/search.
  el('cm-import-btn')?.addEventListener('click', () => el('cm-import-file')?.click());
  el('cm-import-file')?.addEventListener('change', async (e) => {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    const btn = el('cm-import-btn');
    const orig = btn ? btn.textContent : '';
    if (btn) { btn.textContent = 'Importing…'; btn.disabled = true; }
    try {
      const texts = await Promise.all(files.map(f => f.text()));
      const vcfParts = [];
      const csvParts = [];
      texts.forEach((text, idx) => {
        const name = (files[idx]?.name || '').toLowerCase();
        if (name.endsWith('.csv') || !String(text || '').toUpperCase().includes('BEGIN:VCARD')) csvParts.push(text);
        else vcfParts.push(text);
      });
      let imported = 0, total = 0, failed = 0;
      const _postImport = async (body) => {
        const r = await fetch('/api/contacts/import', {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const d = await r.json();
        if (d.error) throw new Error(d.error);
        imported += Number(d.imported || 0);
        total += Number(d.total || 0);
        failed += Number(d.failed || 0);
      };
      if (vcfParts.length) await _postImport({ vcf: vcfParts.join('\n') });
      if (csvParts.length) await _postImport({ csv: csvParts.join('\n') });
      if (!vcfParts.length && !csvParts.length) throw new Error('No contact data found');
      const msg = `Imported ${imported}/${total}` + (failed ? ` (${failed} failed)` : '');
      uiModule.showToast ? uiModule.showToast(msg) : null;
    } catch (err) {
      uiModule.showError ? uiModule.showError(err?.message || 'Import failed') : alert(err?.message || 'Import failed');
    } finally {
      if (btn) { btn.textContent = orig; btn.disabled = false; }
      e.target.value = '';
      await _renderContactsManager();
    }
  });
  await _renderContactsManager();
}

// Render the contacts list inside the manager card with inline edit +
// delete. Each row: name + emails; pencil flips to editable inputs.
async function _renderContactsManager() {
  const list = el('cm-list');
  if (!list) return;
  let contacts = [];
  try {
    const r = await fetch('/api/contacts/list', { credentials: 'same-origin' });
    const d = await r.json();
    contacts = d.contacts || [];
  } catch (_) {
    list.innerHTML = '<div style="opacity:0.5;font-size:11px;padding:8px 2px;">Failed to load contacts (check CardDAV config above).</div>';
    return;
  }
  const cnt = el('cm-count');
  if (cnt) cnt.textContent = contacts.length ? `(${contacts.length})` : '';
  if (!contacts.length) {
    list.innerHTML = '<div style="opacity:0.4;font-size:11px;padding:8px 2px;">No contacts yet.</div>';
    return;
  }
  // Sort by name for a stable list.
  contacts.sort((a, b) => (a.name || '').localeCompare(b.name || ''));

  // Live filter — search across name/emails/phones/address.
  const searchInput = el('cm-search');
  const q = (searchInput?.value || '').trim().toLowerCase();
  const filtered = !q ? contacts : contacts.filter(c => {
    const hay = [
      c.name || '',
      (c.emails || []).join(' '),
      (c.phones || []).join(' '),
      c.address || '',
    ].join(' ').toLowerCase();
    return hay.includes(q);
  });
  if (cnt) cnt.textContent = contacts.length ? `(${filtered.length}/${contacts.length})` : '';

  if (!filtered.length) {
    list.innerHTML = `<div style="opacity:0.4;font-size:11px;padding:8px 2px;">${q ? 'No matches.' : 'No contacts yet.'}</div>`;
  } else {
    list.innerHTML = filtered.map(c => {
      const emails = (c.emails || []).join(', ');
      const phones = (c.phones || []).join(', ');
      const address = c.address || '';
      const sub = [emails, phones, address].filter(Boolean).join(' · ');
      return `<div class="contact-row" data-uid="${esc(c.uid)}">
          <div class="contact-row-view" style="display:flex;align-items:center;gap:8px;">
            <div style="flex:1;min-width:0;">
              <div class="contact-name" style="font-size:12px;font-weight:600;">${esc(c.name || '(no name)')}</div>
              <div class="contact-sub" style="font-size:10px;opacity:0.55;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(sub)}</div>
            </div>
            <button class="admin-btn-sm contact-edit" title="Edit" style="display:inline-flex;align-items:center;gap:4px;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 35%, var(--border));">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
              Edit
            </button>
            <button class="admin-btn-sm contact-del" title="Delete" style="opacity:0.85;display:inline-flex;align-items:center;gap:4px;">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
              Delete
            </button>
          </div>
          <div class="contact-row-edit" style="display:none;flex-direction:column;gap:4px;">
            <input class="settings-input contact-edit-name" value="${esc(c.name || '')}" placeholder="Name">
            <input class="settings-input contact-edit-emails" value="${esc(emails)}" placeholder="email1, email2">
            <input class="settings-input contact-edit-phones" value="${esc(phones)}" placeholder="phone1, phone2">
            <input class="settings-input contact-edit-address" value="${esc(address)}" placeholder="Address">
            <div style="display:flex;gap:6px;"><button class="admin-btn-sm contact-save">Save</button><button class="admin-btn-sm contact-cancel" style="opacity:0.7;">Cancel</button></div>
          </div>
        </div>`;
    }).join('');
  }

  // Wire the search input — debounced so we don't refetch on every key.
  if (searchInput && !searchInput._wired) {
    searchInput._wired = true;
    let _t;
    searchInput.addEventListener('input', () => {
      clearTimeout(_t);
      _t = setTimeout(() => _renderContactsManager(), 80);
    });
  }
  // Stash latest contacts so the search input doesn't have to refetch.
  list._lastContacts = contacts;
  // Wire each row's edit / delete / save / cancel.
  list.querySelectorAll('.contact-row').forEach(row => {
    const uid = row.dataset.uid;
    const view = row.querySelector('.contact-row-view');
    const editForm = row.querySelector('.contact-row-edit');
    row.querySelector('.contact-edit')?.addEventListener('click', () => {
      view.style.display = 'none';
      editForm.style.display = 'flex';
    });
    row.querySelector('.contact-cancel')?.addEventListener('click', () => {
      editForm.style.display = 'none';
      view.style.display = 'flex';
    });
    row.querySelector('.contact-save')?.addEventListener('click', async () => {
      const body = {
        name: row.querySelector('.contact-edit-name').value.trim(),
        emails: row.querySelector('.contact-edit-emails').value.split(',').map(s => s.trim()).filter(Boolean),
        phones: row.querySelector('.contact-edit-phones').value.split(',').map(s => s.trim()).filter(Boolean),
        address: row.querySelector('.contact-edit-address')?.value.trim() || '',
      };
      try {
        await fetch('/api/contacts/' + encodeURIComponent(uid), { method: 'PUT', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      } catch (_) {}
      await _renderContactsManager();
    });
    row.querySelector('.contact-del')?.addEventListener('click', async () => {
      const ok = uiModule.styledConfirm
        ? await uiModule.styledConfirm('Delete this contact?', { confirmText: 'Delete', danger: true })
        : window.confirm('Delete this contact?');
      if (!ok) return;
      try {
        await fetch('/api/contacts/' + encodeURIComponent(uid), { method: 'DELETE', credentials: 'same-origin' });
      } catch (_) {}
      await _renderContactsManager();
    });
  });
}
