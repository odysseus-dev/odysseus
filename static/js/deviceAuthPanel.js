// Shared "enter this code" panel for OAuth device-code sign-in: a waiting
// spinner, the user code with a Copy button, and an Authorize link button.
// Styled by the adm-copilot-* rules; used by the provider (Copilot / ChatGPT)
// sign-in in admin.js and the Microsoft mail sign-in in settings.js.

function _esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

async function _copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (e) {}
  // navigator.clipboard is unavailable in non-secure contexts (HTTP
  // self-host over a LAN IP), so fall back to execCommand('copy').
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.cssText = 'position:fixed;top:0;left:0;width:1px;height:1px;padding:0;border:0;opacity:0;font-size:16px;';
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  try { ta.setSelectionRange(0, text.length); } catch (e) {}
  let ok = false;
  try { ok = document.execCommand('copy'); } catch (e) {}
  ta.remove();
  return ok;
}

export function renderDeviceAuthWaitPanel(container, { userCode, authUrl, authLabel, waitLabel }) {
  if (!container) return;
  container.className = '';
  container.innerHTML =
    '<div class="adm-copilot-panel">' +
      '<div class="adm-copilot-wait"><span class="admin-spinner"></span>' +
        '<span>' + _esc(waitLabel) + '</span></div>' +
      '<div class="adm-copilot-coderow">' +
        '<span class="adm-copilot-code-label">Code</span>' +
        '<code class="adm-copilot-code">' + _esc(userCode) + '</code>' +
        '<button type="button" class="admin-btn-sm adm-device-auth-copy">Copy</button>' +
      '</div>' +
      '<a class="admin-btn-add adm-copilot-auth" href="' + _esc(authUrl || '') + '" target="_blank" rel="noopener">' + _esc(authLabel) + ' ↗</a>' +
    '</div>';
  const copyBtn = container.querySelector('.adm-device-auth-copy');
  if (copyBtn) copyBtn.addEventListener('click', async () => {
    const ok = await _copyText(userCode || '');
    copyBtn.textContent = ok ? 'Copied' : 'Failed';
    setTimeout(() => { copyBtn.textContent = 'Copy'; }, 1500);
  });
}
