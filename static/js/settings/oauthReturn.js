// Return path from the Google OAuth2 redirect.
//
// The provider sends the browser back to the app root with a result in the
// query string, so something has to notice on load and put the user back where
// they were — the Integrations panel — with the outcome visible. That is
// navigation, not an Integrations concern, and it runs once per page load
// rather than once per panel open.

/**
 * @param {object} options
 * @param {(tab: string) => void} options.openSettings  the Settings open() entry point
 */
export function handleSettingsOauthReturn({ openSettings } = {}) {
  const sp = new URLSearchParams(window.location.search);
  if (!sp.has('email_oauth_success') && !sp.has('email_oauth_error')) return;
  // Strip params from URL without a page reload.
  const clean = window.location.pathname + window.location.hash;
  window.history.replaceState(null, '', clean);
  const success = sp.has('email_oauth_success');
  const errMsg = sp.get('email_oauth_error') || '';

  // The caller passes its own open() in, so this never has to wait for a
  // window-level alias to exist.
  function _showResult() {
    openSettings('integrations');
    // Brief toast-style banner.
    const banner = document.createElement('div');
    banner.textContent = success
      ? 'Google account connected — email is ready'
      : `Google OAuth failed: ${errMsg || 'unknown error'}`;
    Object.assign(banner.style, {
      position: 'fixed', bottom: '24px', left: '50%', transform: 'translateX(-50%)',
      background: success ? 'var(--accent, #50fa7b)' : 'var(--red, #ff5555)',
      color: '#000', padding: '8px 18px', borderRadius: '6px', fontSize: '12px',
      fontWeight: '600', zIndex: '99999', pointerEvents: 'none',
      boxShadow: '0 2px 12px rgba(0,0,0,0.3)',
    });
    document.body.appendChild(banner);
    setTimeout(() => banner.remove(), 4000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _showResult, { once: true });
  } else {
    _showResult();
  }
}
