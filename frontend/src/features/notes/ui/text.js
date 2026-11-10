/** Escaping, safe image sources and link rendering. */
export function createText({
  uiModule
}) {
  function _esc(s) { return uiModule.esc ? uiModule.esc(s || '') : (s || '').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

  function _attrEsc(s) {
    return String(s || '')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/`/g, '&#96;');
  }

  function _safeImgSrc(s) {
    const v = (s || '').trim();
    if (!v) return '';
    if (v.startsWith('/') || v.startsWith('./') || v.startsWith('../')) return v;
    if (/^https?:\/\//i.test(v) || /^data:image\/(?:png|jpe?g|gif|webp);base64,/i.test(v)) return v;
    return '';
  }

  function _linkify(s) {
    const escaped = _esc(s);
    const urlRe = /\b((?:https?:\/\/|www\.)[^\s<>"']+[^\s<>"'.,;:!?\]])/g;
    return escaped.replace(urlRe, (m) => {
      let url = m;
      // Trim a trailing ')' that doesn't have a matching '(' inside the URL
      if (url.endsWith(')') && (url.match(/\(/g) || []).length < (url.match(/\)/g) || []).length) {
        url = url.slice(0, -1);
      }
      const href = url.startsWith('www.') ? `https://${url}` : url;
      return `<a href="${_attrEsc(href)}" class="note-link" target="_blank" rel="noopener noreferrer">${url}</a>` + (url !== m ? m.slice(url.length) : '');
    });
  }

  return { _esc, _attrEsc, _safeImgSrc, _linkify };
}
