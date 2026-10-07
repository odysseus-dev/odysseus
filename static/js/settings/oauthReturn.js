// Return path from an OAuth redirect.
//
// Email, Microsoft calendar and Microsoft tasks all send the browser back to
// the app root with the same shaped result parameters under different prefixes.
// This module owns the one-time navigation back to Settings and renders enough
// provider detail for a user or operator to act on failures.

const OAUTH_PROVIDER_META = {
  google: { label: 'Google' },
  microsoft: { label: 'Microsoft' },
};

// What each failure means and what to do about it. Keyed by Odysseus's own
// error code, then by the provider's code / AADSTS number, which is far more
// specific when the provider supplied one.
const OAUTH_ERROR_GUIDANCE = {
  missing_refresh_token: 'The provider did not return a refresh token, so the mailbox would stop working within the hour. Check that offline_access is granted on the app registration, then connect again.',
  identity_verification_failed: 'You signed in as a different mailbox than this account is configured for. Sign in with the address in the account\'s Email field, or correct that field first.',
  token_exchange_failed: 'The provider rejected the token exchange. The client secret is usually wrong or expired, or the redirect URI does not match the one registered on the app.',
  invalid_state: 'The sign-in could not be matched to the request that started it. Start the connect again from Settings.',
  missing_code: 'The provider returned no authorization code. Start the connect again from Settings.',
  account_not_found: 'The email account this connect belonged to no longer exists.',
  ownership_error: 'That email account belongs to another user.',
  no_refresh_token: 'Microsoft returned no refresh token, so the calendar would stop syncing within the hour. Check that offline_access is granted on the app registration, then connect again.',
  microsoft_error: 'Microsoft refused the sign-in before issuing a code.',
};

const OAUTH_PROVIDER_CODE_GUIDANCE = {
  access_denied: 'Sign-in or consent was declined. On a work or school tenant this usually means the app still needs administrator approval.',
  consent_required: 'The tenant requires administrator consent for this app. In Entra, open the app registration, go to API permissions and use "Grant admin consent".',
  interaction_required: 'Microsoft needs an interactive sign-in for this account. Try again in a normal browser window.',
  invalid_scope: 'A requested permission is not registered on the app. Mail needs the Exchange delegated permissions (IMAP.AccessAsUser.All, SMTP.Send); calendar sync needs Microsoft Graph Calendars.ReadWrite.',
  invalid_client: 'The client id or client secret does not match the registered app.',
  AADSTS65001: 'Nobody has consented to this app for the tenant yet. In Entra, open the app registration, go to API permissions and use "Grant admin consent".',
  AADSTS90094: 'This app needs administrator approval before it can be used. Ask a tenant administrator to grant consent.',
  AADSTS7000215: 'The client secret is wrong. Generate a new one in Certificates & secrets and copy its Value, not its Secret ID.',
  AADSTS700016: 'The application was not found in this tenant. Check MICROSOFT_OAUTH_CLIENT_ID and MICROSOFT_OAUTH_TENANT_ID.',
  AADSTS50011: 'The redirect URI does not match the one registered on the app. It must match exactly, including scheme and port.',
  AADSTS50194: 'The app registration is single-tenant, so the shared /common sign-in endpoint is refused. Set MICROSOFT_OAUTH_TENANT_ID to the Directory (tenant) ID from the app registration Overview, then restart. Only a tenant id or verified domain works here — organizations is for multi-tenant apps.',
  AADSTS500113: 'The app registration has no redirect URI. Add a Web platform with the callback URL.',
  AADSTS50020: 'This account cannot sign in to the app. Check the Supported account types on the app registration.',
};

const OAUTH_REDIRECT_FLOWS = [
  { prefix: 'email_oauth', subject: 'email' },
  { prefix: 'calendar_oauth', subject: 'calendar sync' },
  { prefix: 'tasks_oauth', subject: 'task sync' },
];

/**
 * @param {object} options
 * @param {(tab: string) => void} options.openSettings  the Settings open() entry point
 */
export function handleSettingsOauthReturn({ openSettings } = {}) {
  const sp = new URLSearchParams(window.location.search);
  const flow = OAUTH_REDIRECT_FLOWS.find(
    ({ prefix }) => sp.has(`${prefix}_success`) || sp.has(`${prefix}_error`),
  );
  if (!flow) return;

  // Strip params from URL without a page reload.
  const clean = window.location.pathname + window.location.hash;
  window.history.replaceState(null, '', clean);

  const success = sp.has(`${flow.prefix}_success`);
  const reason = sp.get(`${flow.prefix}_error`) || '';
  const provider = sp.get(`${flow.prefix}_provider`) || '';
  const providerCode = sp.get(`${flow.prefix}_code`) || '';
  const aadsts = sp.get(`${flow.prefix}_aadsts`) || '';
  const providerName = (OAUTH_PROVIDER_META[provider] || {}).label || 'OAuth';

  // The caller passes its own open() in, so this never has to wait for a
  // window-level alias to exist.
  function _showResult() {
    openSettings('integrations');
    if (success) {
      const banner = document.createElement('div');
      banner.textContent = `${providerName} account connected — ${flow.subject} is ready`;
      Object.assign(banner.style, {
        position: 'fixed', bottom: '24px', left: '50%', transform: 'translateX(-50%)',
        background: 'var(--accent, #50fa7b)', color: '#000', padding: '8px 18px',
        borderRadius: '6px', fontSize: '12px', fontWeight: '600', zIndex: '99999',
        pointerEvents: 'none', boxShadow: '0 2px 12px rgba(0,0,0,0.3)',
      });
      document.body.appendChild(banner);
      setTimeout(() => banner.remove(), 4000);
      return;
    }

    // Failures stay visible because their codes often need to be copied into
    // configuration work or a support thread.
    const guidance = OAUTH_PROVIDER_CODE_GUIDANCE[aadsts]
      || OAUTH_PROVIDER_CODE_GUIDANCE[providerCode]
      || OAUTH_ERROR_GUIDANCE[reason]
      || 'Check the Odysseus server log for the provider error code.';
    const codes = [providerCode, aadsts].filter(Boolean).join(' · ');

    const panel = document.createElement('div');
    Object.assign(panel.style, {
      position: 'fixed', bottom: '24px', left: '50%', transform: 'translateX(-50%)',
      width: 'min(520px, calc(100vw - 32px))', background: 'var(--card, #1a1a1a)',
      color: 'var(--fg)', border: '1px solid var(--border)',
      borderLeft: '3px solid var(--red, #ff5555)', borderRadius: '6px',
      padding: '12px 14px', fontSize: '12px', lineHeight: '1.5', zIndex: '99999',
      boxShadow: '0 4px 18px rgba(0,0,0,0.4)',
    });

    const title = document.createElement('div');
    title.textContent = `${providerName} connection failed`;
    Object.assign(title.style, { fontWeight: '600', marginBottom: '4px' });

    const body = document.createElement('div');
    body.textContent = guidance;
    Object.assign(body.style, { opacity: '0.85', marginBottom: codes ? '8px' : '4px' });
    panel.appendChild(title);
    panel.appendChild(body);

    if (codes) {
      const codeRow = document.createElement('div');
      codeRow.textContent = codes;
      Object.assign(codeRow.style, {
        fontFamily: 'inherit', opacity: '0.7', userSelect: 'all',
        padding: '4px 6px', border: '1px solid var(--border)', borderRadius: '4px',
        marginBottom: '8px', wordBreak: 'break-all',
      });
      panel.appendChild(codeRow);
    }

    const actions = document.createElement('div');
    Object.assign(actions.style, { display: 'flex', gap: '6px', alignItems: 'center' });

    if (codes) {
      const copy = document.createElement('button');
      copy.type = 'button';
      copy.className = 'admin-btn-add';
      copy.style.fontSize = '11px';
      copy.textContent = 'Copy code';
      copy.addEventListener('click', async () => {
        try {
          await navigator.clipboard.writeText(`${reason} ${codes}`.trim());
          copy.textContent = 'Copied';
        } catch {
          copy.textContent = 'Copy failed';
        }
      });
      actions.appendChild(copy);
    }

    const dismiss = document.createElement('button');
    dismiss.type = 'button';
    dismiss.className = 'admin-btn-add';
    Object.assign(dismiss.style, { fontSize: '11px', opacity: '0.7', marginLeft: 'auto' });
    dismiss.textContent = 'Dismiss';
    dismiss.addEventListener('click', () => panel.remove());
    actions.appendChild(dismiss);

    panel.appendChild(actions);
    document.body.appendChild(panel);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _showResult, { once: true });
  } else {
    _showResult();
  }
}
