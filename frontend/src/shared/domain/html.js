// Escape plain text before interpolating it into HTML text or a quoted attribute.
const ENTITIES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

export function escapeHtmlString(text) {
  return text.replace(/[&<>"']/g, char => ENTITIES[char]);
}

export function escapeHtml(value) {
  return escapeHtmlString(String(value ?? ''));
}
