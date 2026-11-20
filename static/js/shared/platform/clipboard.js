/** Copy nonempty plain text, including on self-hosted HTTP connections. */
export async function copyText(text) {
  const value = String(text || '');
  if (!value) return false;

  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch {
      // Try the synchronous fallback when the browser denies a clipboard write.
    }
  }

  const field = document.createElement('textarea');
  field.value = value;
  field.setAttribute('readonly', 'readonly');
  field.style.cssText = 'position:fixed;left:0;top:0;width:1px;height:1px;opacity:0;z-index:-1;';
  document.body.appendChild(field);
  try {
    field.focus();
    field.select();
    field.setSelectionRange(0, value.length);
    try {
      return document.execCommand('copy');
    } catch {
      return false;
    }
  } finally {
    field.remove();
  }
}
