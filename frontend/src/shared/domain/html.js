// Escape plain text before interpolating it into HTML text or a quoted attribute.
const ENTITIES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

export function escapeHtmlString(text) {
  return text.replace(/[&<>"']/g, char => ENTITIES[char]);
}

export function escapeHtml(value) {
  return escapeHtmlString(String(value ?? ''));
}

// Text segments and double-quoted attributes can preserve their original serialization.
export function escapeHtmlText(text) {
  return text.replace(/[&<>]/g, char => ENTITIES[char]);
}

export function escapeHtmlDoubleQuoted(text) {
  return text.replace(/[&<>"]/g, char => ENTITIES[char]);
}
