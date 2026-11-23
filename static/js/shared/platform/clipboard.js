// The callback selects and copies synchronously to keep the caller's user gesture.
export function withClipboardTextarea(text, cssText, use, { readOnly = false } = {}) {
  const field = document.createElement('textarea');
  field.value = text;
  if (readOnly) field.setAttribute('readonly', 'readonly');
  field.style.cssText = cssText;
  document.body.appendChild(field);
  try {
    return use(field);
  } finally {
    field.remove();
  }
}

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

  return withClipboardTextarea(value,
    'position:fixed;left:0;top:0;width:1px;height:1px;opacity:0;z-index:-1;',
    field => {
      field.focus();
      field.select();
      field.setSelectionRange(0, value.length);
      try {
        return document.execCommand('copy');
      } catch {
        return false;
      }
    },
    { readOnly: true },
  );
}
