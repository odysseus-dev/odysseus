// Reminder delivery settings (browser, ntfy, email) and their persistence.
// Extracted from settings.js; the modal element and the manage-settings
// predicate are passed in instead of being read from coordinator state.

import { byId as el } from './dom.js';
import { postSettings as _postSettings } from './api.js';
import { esc } from './html.js';
import uiModule from '../ui.js?v=20260916largetoolscroll1';

export async function initReminderSettings(modalEl, { canManage, restricted = () => false }) {
  const root = el('settings-modal');
  const panel = root && root.querySelector('[data-settings-panel="reminders"]');
  if (!panel) return;
  // Set again once every control below is wired; until then a click is not saved.
  delete panel.dataset.ready;

  // Public URL field (used for deep-links in outgoing alert emails)
  const pubUrlIn = el('set-app-public-url');
  const pubUrlMsg = el('set-app-public-url-msg');
  if (pubUrlIn) {
    try {
      const r = await fetch('/api/auth/settings', { credentials: 'same-origin' });
      const s = await r.json();
      pubUrlIn.value = s.app_public_url || '';
    } catch (_) {}
    let pubDebounce;
    pubUrlIn.addEventListener('input', () => {
      clearTimeout(pubDebounce);
      pubDebounce = setTimeout(async () => {
        try {
          const val = pubUrlIn.value.trim().replace(/\/+$/, '');
          await _postSettings({ app_public_url: val });
          if (pubUrlMsg) {
            pubUrlMsg.textContent = val ? 'Saved' : 'Cleared (deep-links disabled)';
            pubUrlMsg.style.color = 'var(--green,#50fa7b)';
            setTimeout(() => { pubUrlMsg.textContent = ''; }, 2000);
          }
        } catch (_) {
          if (pubUrlMsg) { pubUrlMsg.textContent = 'Save failed'; pubUrlMsg.style.color = 'var(--red)'; }
        }
      }, 600);
    });
  }

  const channelSel = el('set-reminder-channel');
  const emailOpt = el('set-reminder-channel-email-opt');
  const ntfyOpt = el('set-reminder-channel-ntfy-opt');
  const webhookOpt = el('set-reminder-channel-webhook-opt');
  const hint = el('set-reminder-channel-hint');
  const llmToggle = el('set-reminder-llm-toggle');
  // "Integrations" link in the channel-hint copy. Jumps to the
  // Integrations tab so the user can configure the underlying accounts
  // (email, ntfy server) the channel dropdown depends on. Idempotent.
  const openIntgBtn = el('set-reminders-open-integrations');
  if (openIntgBtn && !openIntgBtn.dataset.wired) {
    openIntgBtn.dataset.wired = '1';
    openIntgBtn.addEventListener('click', (e) => {
      e.preventDefault();
      const target = modalEl?.querySelector('[data-settings-tab="integrations"]');
      if (target) target.click();
    });
  }
  if (!channelSel || !llmToggle) return;

  // Detect configured email accounts. The legacy single-account
  // `/api/email/config` endpoint was a no-op stub for most installs;
  // the real per-account list lives at `/api/email/accounts` and is
  // what the Integrations panel manages. Treat the email channel as
  // configured if there's at least one account with SMTP set.
  let emailAccounts = [];
  const smtpAccountReady = (account) => !!(
    account.smtp_host
    && account.smtp_user
    && (account.has_smtp_password || account.oauth_provider === 'google')
  );
  try {
    const res = await fetch('/api/email/accounts', { credentials: 'same-origin' });
    if (res.ok) {
      const d = await res.json();
      emailAccounts = (d.accounts || []).filter(smtpAccountReady);
    }
  } catch (_) {}
  let smtpConfigured = emailAccounts.length > 0;

  if (!smtpConfigured && emailOpt) {
    emailOpt.disabled = true;
    emailOpt.textContent = 'Email (add an account in Integrations)';
  }

  // Detect whether ntfy integration exists: try admin endpoint, fall back to
  // checking if an ntfy integration was saved in settings (non-admin users).
  // A caller already known not to manage settings skips the admin request.
  let ntfyConfigured = false;
  try {
    const isRestricted = typeof restricted === 'function' ? await restricted() : false;
    let res = null;
    if (!isRestricted) {
      res = await fetch('/api/auth/integrations', { credentials: 'same-origin' });
    }
    if (res && res.ok) {
      const data = await res.json();
      ntfyConfigured = (data.integrations || []).some(
        i => (i.preset === 'ntfy' || (i.name || '').toLowerCase() === 'ntfy') && i.enabled !== false && i.base_url
      );
    } else if (res && !res.ok && res.status !== 401 && res.status !== 403) {
      console.warn('[settings] unexpected response from /api/auth/integrations: HTTP ' + res.status);
    }
  } catch (_) {}
  // If admin check failed, check if ntfy was previously selected (trust the saved setting)
  if (!ntfyConfigured) {
    try {
      const res = await fetch('/api/auth/settings', { credentials: 'same-origin' });
      const s = await res.json();
      if (s.reminder_channel === 'ntfy') ntfyConfigured = true;
    } catch (_) {}
  }

  if (!ntfyConfigured && ntfyOpt) {
    ntfyOpt.disabled = true;
    ntfyOpt.textContent = 'ntfy (add in Integrations first)';
  }

  // Webhook: available whenever at least one integration with a base_url exists.
  // The user picks which integration to target and supplies a payload template.
  let allIntegrations = [];
  let webhookConfigured = false;
  try {
    const isRestricted = typeof restricted === 'function' ? await restricted() : false;
    let res = null;
    if (!isRestricted) {
      res = await fetch('/api/auth/integrations', { credentials: 'same-origin' });
    }
    if (res && res.ok) {
      const data = await res.json();
      allIntegrations = (data.integrations || []).filter(i => i.base_url && i.enabled !== false);
      webhookConfigured = allIntegrations.length > 0;
    } else if (res && !res.ok && res.status !== 401 && res.status !== 403) {
      console.warn('[settings] unexpected response from /api/auth/integrations: HTTP ' + res.status);
    }
  } catch (_) {}
  if (!webhookConfigured && webhookOpt) {
    webhookOpt.disabled = true;
    webhookOpt.textContent = 'Webhook (add an Integration first)';
  }

  const emailFromRow = el('set-reminder-email-from-row');
  const emailAcctSel = el('set-reminder-email-account');
  const emailToRow = el('set-reminder-email-to-row');
  const emailToIn = el('set-reminder-email-to');
  const ntfyTopicRow = el('set-reminder-ntfy-topic-row');
  const ntfyTopicIn = el('set-reminder-ntfy-topic');
  const webhookIntgRow = el('set-reminder-webhook-intg-row');
  const webhookIntgSel = el('set-reminder-webhook-intg');
  const webhookTemplateRow = el('set-reminder-webhook-template-row');
  const webhookTemplateIn = el('set-reminder-webhook-template');

  function populateReminderEmailAccounts(selectedId = '') {
    if (!emailAcctSel) return;
    emailAcctSel.innerHTML = emailAccounts.map(a =>
      `<option value="${a.id}">${esc(a.name || a.from_address || a.imap_user || 'Unnamed')}${a.is_default ? ' (default)' : ''}</option>`
    ).join('');
    const fallback = (emailAccounts.find(a => a.is_default) || emailAccounts[0] || {}).id || '';
    emailAcctSel.value = (selectedId && emailAccounts.some(a => a.id === selectedId)) ? selectedId : fallback;
  }

  function populateWebhookIntegrations(selectedId = '') {
    if (!webhookIntgSel) return;
    webhookIntgSel.innerHTML = allIntegrations.length
      ? allIntegrations.map(i => `<option value="${esc(i.id)}">${esc(i.name || i.id)}</option>`).join('')
      : '<option value="">No integrations configured</option>';
    if (selectedId && allIntegrations.some(i => i.id === selectedId)) webhookIntgSel.value = selectedId;
  }

  function applyReminderChannelAvailability() {
    if (emailOpt) {
      emailOpt.disabled = !smtpConfigured;
      emailOpt.textContent = smtpConfigured ? 'Email' : 'Email (add an account in Integrations)';
    }
    if (ntfyOpt) {
      ntfyOpt.disabled = !ntfyConfigured;
      ntfyOpt.textContent = ntfyConfigured ? 'ntfy' : 'ntfy (add in Integrations first)';
    }
    if (webhookOpt) {
      webhookOpt.disabled = !webhookConfigured;
      webhookOpt.textContent = webhookConfigured ? 'Webhook' : 'Webhook (add an Integration first)';
    }
  }

  async function refreshReminderChannelAvailability() {
    const currentChannel = channelSel.value || 'browser';
    const currentEmailAccount = emailAcctSel?.value || '';
    const currentWebhookIntg = webhookIntgSel?.value || '';
    try {
      const res = await fetch('/api/email/accounts', { credentials: 'same-origin' });
      if (res.ok) {
        const d = await res.json();
        emailAccounts = (d.accounts || []).filter(smtpAccountReady);
      }
    } catch (_) {}
    smtpConfigured = emailAccounts.length > 0;

    ntfyConfigured = false;
    try {
      const res = await fetch('/api/auth/integrations', { credentials: 'same-origin' });
      if (res.ok) {
        const data = await res.json();
        ntfyConfigured = (data.integrations || []).some(
          i => (i.preset === 'ntfy' || (i.name || '').toLowerCase() === 'ntfy') && i.enabled !== false && i.base_url
        );
        allIntegrations = (data.integrations || []).filter(i => i.base_url && i.enabled !== false);
        webhookConfigured = allIntegrations.length > 0;
      }
    } catch (_) {}
    if (!ntfyConfigured) {
      try {
        const res = await fetch('/api/auth/settings', { credentials: 'same-origin' });
        const s = await res.json();
        if (s.reminder_channel === 'ntfy') ntfyConfigured = true;
      } catch (_) {}
    }

    applyReminderChannelAvailability();
    populateReminderEmailAccounts(currentEmailAccount);
    populateWebhookIntegrations(currentWebhookIntg);
    if (currentChannel === 'email' && !smtpConfigured) channelSel.value = 'browser';
    else if (currentChannel === 'ntfy' && !ntfyConfigured) channelSel.value = 'browser';
    else if (currentChannel === 'webhook' && !webhookConfigured) channelSel.value = 'browser';
    else channelSel.value = currentChannel;
    if (hint) hint.textContent = CHANNEL_HINTS[channelSel.value] || '';
    syncChannelRows();
  }

  // Populate the "Send from" picker with all configured email accounts.
  populateReminderEmailAccounts();

  function syncChannelRows() {
    const isEmail = channelSel.value === 'email';
    const isWebhook = channelSel.value === 'webhook';
    if (emailFromRow) emailFromRow.style.display = (isEmail && emailAccounts.length > 1) ? 'flex' : 'none';
    if (emailToRow) emailToRow.style.display = isEmail ? 'flex' : 'none';
    if (ntfyTopicRow) ntfyTopicRow.style.display = channelSel.value === 'ntfy' ? 'flex' : 'none';
    if (webhookIntgRow) webhookIntgRow.style.display = isWebhook ? 'flex' : 'none';
    if (webhookTemplateRow) webhookTemplateRow.style.display = isWebhook ? 'flex' : 'none';
  }

  // Browser notifications fire on EVERY reminder (see
  // routes/note_routes.py — the in-app notif is always queued
  // regardless of channel). The hint should make that clear so
  // users don't think they have to choose between channels.
  const CHANNEL_HINTS = {
    browser: 'Reminders appear as browser notifications inside Odysseus.',
    email: 'Reminders are emailed and shown as a browser notification.',
    ntfy: 'Reminders are pushed via ntfy AND shown as a browser notification.',
    webhook: 'Reminders are POSTed to the selected integration AND shown as a browser notification. Use {{title}} and {{message}} in the payload template.',
  };

  applyReminderChannelAvailability();
  if (!channelSel.dataset.integrationRefreshWired) {
    channelSel.dataset.integrationRefreshWired = '1';
    window.addEventListener('odysseus-integrations-changed', () => {
      refreshReminderChannelAvailability().catch(e => console.warn('Failed to refresh reminder channels', e));
    });
  }

  // Default payload templates for known presets — auto-filled when the user
  // picks a matching integration so they don't have to write JSON from scratch.
  // Defined here (before the load block) so both the load path and the change
  // handler can reference it.
  const WEBHOOK_PRESET_TEMPLATES = {
    discord_webhook: '{"embeds": [{"title": "{{title}}", "description": "{{message}}", "color": 5793266}]}',
  };

  try {
    const res = await fetch('/api/auth/settings', { credentials: 'same-origin' });
    const s = await res.json();
    let savedChannel = s.reminder_channel || 'browser';
    if (savedChannel === 'email' && !smtpConfigured) savedChannel = 'browser';
    if (savedChannel === 'ntfy' && !ntfyConfigured) savedChannel = 'browser';
    if (savedChannel === 'webhook' && !webhookConfigured) savedChannel = 'browser';
    channelSel.value = savedChannel;
    llmToggle.checked = !!s.reminder_llm_synthesis;
    // Persona dropdown — populate from built-in PROMPT_TEMPLATES (characters)
    // plus any custom character preset. Selected value persists to
    // reminder_llm_persona (backend hook lives in src/notes.py once
    // /api/notes/fire-reminder lands).
    const personaSel = el('set-reminder-llm-persona');
    if (personaSel) {
      try {
        const presetsMod = await import('../presets.js?v=20260908personaname1');
        const tpl = presetsMod.PROMPT_TEMPLATES || [];
        const chars = tpl.filter(t => t.isCharacter);
        for (const c of chars) {
          const opt = document.createElement('option');
          opt.value = c.id;
          opt.textContent = c.name;
          personaSel.appendChild(opt);
        }
        // Custom character (single-slot preset)
        try {
          const all = (presetsMod.getAllPresets && presetsMod.getAllPresets()) || {};
          if (all.custom && all.custom.character_name) {
            const opt = document.createElement('option');
            opt.value = 'custom';
            opt.textContent = all.custom.character_name + ' (custom)';
            personaSel.appendChild(opt);
          }
        } catch (_) {}
      } catch (_) {}
      personaSel.value = s.reminder_llm_persona || '';
      personaSel.addEventListener('change', () => {
        save({ reminder_llm_persona: personaSel.value });
      });
    }
    if (emailToIn) emailToIn.value = s.reminder_email_to || '';
    if (ntfyTopicIn) ntfyTopicIn.value = s.reminder_ntfy_topic || 'Reminders';
    populateWebhookIntegrations(s.reminder_webhook_integration_id || '');
    if (webhookTemplateIn) {
      webhookTemplateIn.value = s.reminder_webhook_payload_template || '';
      // If an integration is already selected but no template was ever saved,
      // auto-fill with the preset default so the first test works out of the box.
      if (!webhookTemplateIn.value && webhookIntgSel?.value) {
        const intg = allIntegrations.find(i => i.id === webhookIntgSel.value);
        const tpl = WEBHOOK_PRESET_TEMPLATES[intg?.preset] || '';
        if (tpl) { webhookTemplateIn.value = tpl; save({ reminder_webhook_payload_template: tpl }); }
      }
    }
    // Restore the previously-picked email account (if any), otherwise
    // default to the account flagged is_default in the integrations
    // list. Falls through to the first option if neither exists.
    if (emailAcctSel) {
      const savedId = s.reminder_email_account_id;
      populateReminderEmailAccounts(savedId || '');
      // Opening Settings may adopt the default account, but only for someone
      // who can change this server-wide setting; anyone else would trigger a
      // refused write (and an error toast) just by looking at the panel.
      if (canManage() && emailAcctSel.value && emailAcctSel.value !== (savedId || '')) {
        save({ reminder_email_account_id: emailAcctSel.value || null });
      }
    }
    if (hint) hint.textContent = CHANNEL_HINTS[channelSel.value] || '';
    syncChannelRows();
  } catch (e) { console.warn('Failed to load reminder settings', e); }

  async function save(patch) {
    try {
      await _postSettings(patch);
    } catch (e) {
      console.warn('Failed to save reminder settings', e);
      // postSettings already reports a refused (403) write.
      if (e && e.status !== 403 && uiModule && uiModule.showError) {
        uiModule.showError('Could not save reminder settings. Reopen Settings to see what is stored.');
      }
    }
  }

  channelSel.addEventListener('change', () => {
    if (hint) hint.textContent = CHANNEL_HINTS[channelSel.value] || '';
    syncChannelRows();
    save({ reminder_channel: channelSel.value });
    // Email reminder bell visibility tracks this — broadcast so the
    // email library can re-evaluate without waiting for a re-open.
    try { window.dispatchEvent(new CustomEvent('odysseus-reminder-channel-changed', { detail: { channel: channelSel.value } })); } catch (_) {}
  });
  if (emailToIn) {
    let emailDebounce;
    emailToIn.addEventListener('input', () => {
      clearTimeout(emailDebounce);
      emailDebounce = setTimeout(() => save({ reminder_email_to: emailToIn.value.trim() }), 600);
    });
  }
  if (emailAcctSel) {
    emailAcctSel.addEventListener('change', () => {
      save({ reminder_email_account_id: emailAcctSel.value || null });
    });
  }
  if (ntfyTopicIn) {
    let topicDebounce;
    ntfyTopicIn.addEventListener('input', () => {
      clearTimeout(topicDebounce);
      topicDebounce = setTimeout(() => save({ reminder_ntfy_topic: ntfyTopicIn.value.trim() || 'reminders' }), 600);
    });
  }
  if (webhookIntgSel) {
    webhookIntgSel.addEventListener('change', () => {
      save({ reminder_webhook_integration_id: webhookIntgSel.value || '' });
      // If the template is empty and we recognise the integration's preset,
      // pre-fill with a sensible default so users can test immediately.
      if (webhookTemplateIn && !webhookTemplateIn.value.trim()) {
        const intg = allIntegrations.find(i => i.id === webhookIntgSel.value);
        const tpl = WEBHOOK_PRESET_TEMPLATES[intg?.preset] || '';
        if (tpl) {
          webhookTemplateIn.value = tpl;
          save({ reminder_webhook_payload_template: tpl });
        }
      }
    });
  }
  if (webhookTemplateIn) {
    let templateDebounce;
    webhookTemplateIn.addEventListener('input', () => {
      clearTimeout(templateDebounce);
      templateDebounce = setTimeout(() => save({ reminder_webhook_payload_template: webhookTemplateIn.value.trim() }), 600);
    });
  }
  // Dim the whole AI Synthesis card when off (matches Vision/Utility/etc.).
  function syncSynthesisDim() {
    const card = llmToggle.closest('.admin-card');
    if (card) card.style.opacity = llmToggle.checked ? '' : '0.45';
  }
  syncSynthesisDim();
  llmToggle.addEventListener('change', () => {
    syncSynthesisDim();
    save({ reminder_llm_synthesis: llmToggle.checked });
  });

  // Test button
  const testBtn = el('set-reminder-test-btn');
  const testMsg = el('set-reminder-test-msg');
  if (testBtn) {
    testBtn.addEventListener('click', async () => {
      testBtn.disabled = true;
      if (testMsg) { testMsg.textContent = 'Sending'; testMsg.style.color = 'var(--fg)'; }
      // Whirlpool loader right next to the "Sending" text while it sends.
      let _testSpin = null;
      try {
        const _sp = (await import('../spinner.js')).default;
        _testSpin = _sp.createWhirlpool(14);
        _testSpin.element.style.cssText = 'width:14px;height:14px;margin:0 0 0 7px;display:inline-block;vertical-align:middle;';
        (testMsg || testBtn).insertAdjacentElement('afterend', _testSpin.element);
      } catch (_) {}
      const _stopTestSpin = () => { try { _testSpin && _testSpin.stop(); _testSpin && _testSpin.element.remove(); } catch (_) {} };
      try {
        // Persona picker is in a different scope (Reminders init), look it up
        // by id so we can pass whatever is currently selected on screen.
        const personaSel = el('set-reminder-llm-persona');
        const res = await fetch('/api/notes/fire-reminder', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            note_id: 'test-' + Date.now(),
            title: 'Test Reminder',
            body: 'This is a test reminder to verify your settings are working.',
            channel: channelSel.value,
            // Mirror the in-UI AI Synthesis toggle + persona so the test never
            // races a pending save and lets the user preview changes before
            // hitting Save.
            llm_synthesis: !!(llmToggle && llmToggle.checked),
            llm_persona: (personaSel && personaSel.value) || '',
            ...(channelSel.value === 'webhook' ? {
              webhook_integration_id: webhookIntgSel?.value || '',
              webhook_payload_template: webhookTemplateIn?.value.trim() || '',
            } : {}),
          }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Server error');
        if (channelSel.value === 'email' && !data.email_sent) {
          throw new Error(data.email_error || 'Email reminder was not sent');
        }
        if (channelSel.value === 'ntfy' && !data.ntfy_sent) {
          throw new Error(data.ntfy_error || 'ntfy reminder was not sent');
        }
        if (channelSel.value === 'webhook' && !data.webhook_sent) {
          const activeChannel = data.channel ? ` (server used channel: "${data.channel}")` : '';
          throw new Error((data.webhook_error || 'Webhook reminder was not sent') + activeChannel);
        }
        let status = 'Delivered via ' + channelSel.value;
        if (data.synthesis) status += ' (AI: "' + data.synthesis.slice(0, 60) + '...")';
        if (data.email_sent) status += ' — email sent';
        if (data.ntfy_sent) status += ' — ntfy sent';
        if (data.webhook_sent) status += ' — webhook sent';
        if (testMsg) { testMsg.textContent = status; testMsg.style.color = 'var(--green, #50fa7b)'; }
        // Also fire a browser notification so user can see it
        if ('Notification' in window && Notification.permission === 'granted') {
          try {
            new Notification('Test Reminder', {
              body: data.synthesis || 'This is a test reminder.',
              tag: 'reminder-test',
              icon: '/static/favicon.ico',
            });
          } catch {}
        }
      } catch (e) {
        if (testMsg) { testMsg.textContent = 'Failed: ' + e.message; testMsg.style.color = 'var(--red)'; }
      } finally {
        _stopTestSpin();
        testBtn.disabled = false;
      }
    });
  }
  panel.dataset.ready = 'true';
}
