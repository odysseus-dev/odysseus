// frontend/src/features/documents/ui/pdf/annotations.js
/**
 * Form-backed document detection and markdown PDF annotation codec.
 */

export function _isFormBackedDoc(docOrContent) {
  const c = typeof docOrContent === 'string'
    ? docOrContent
    : (docOrContent?.content || '');
  return /<!--\s*pdf_form_source\s+upload_id="[^"]+"/.test(c)
    || /<!--\s*pdf_source\s+upload_id="[^"]+"/.test(c);
}

export function _annotationRegexGlobal() {
  return /^[ \t]*-\s+(.*?)\s*<!--\s*annotation\s+id=([\w-]+)\s+page=(\d+)\s+x=([\d.]+)\s+y=([\d.]+)\s+w=([\d.]+)\s+h=([\d.]+)(?:\s+kind=(\w+))?(?:\s+lh=([\d.]+))?(?:\s+fs=([\d.]+))?\s*-->[ \t]*$/gm;
}

export function _escapeAnnotationValue(s) {
  return String(s == null ? '' : s).replace(/\\/g, '\\\\').replace(/\n/g, '\\n');
}

export function _unescapeAnnotationValue(s) {
  return String(s || '').replace(/\\(.)/g, (m, c) => (c === 'n' ? '\n' : c === '\\' ? '\\' : m));
}

export function _parseAnnotations(md) {
  const out = [];
  const re = _annotationRegexGlobal();
  let m;
  while ((m = re.exec(md || '')) !== null) {
    const rawVal = m[1] === '_(empty)_' ? '' : _unescapeAnnotationValue(m[1]);
    out.push({
      value: rawVal,
      id: m[2],
      page: parseInt(m[3], 10),
      x: parseFloat(m[4]),
      y: parseFloat(m[5]),
      w: parseFloat(m[6]),
      h: parseFloat(m[7]),
      kind: m[8] || 'text',
      lineHeight: m[9] ? parseFloat(m[9]) : 1.3,
      fontSize: m[10] ? parseFloat(m[10]) : 11,
    });
  }
  return out;
}

export function _annotationLine(a) {
  const kind = a.kind || 'text';
  const lh = (a.lineHeight && Number.isFinite(a.lineHeight)) ? a.lineHeight : 1.3;
  const fs = (a.fontSize && Number.isFinite(a.fontSize)) ? a.fontSize : 11;
  const escaped = a.value === '' || a.value == null ? '_(empty)_' : _escapeAnnotationValue(a.value);
  return `- ${escaped} <!-- annotation id=${a.id} page=${a.page} x=${a.x.toFixed(2)} y=${a.y.toFixed(2)} w=${a.w.toFixed(2)} h=${a.h.toFixed(2)} kind=${kind} lh=${lh.toFixed(2)} fs=${fs.toFixed(1)} -->`;
}

export function _writeAnnotations(md, annotations) {
  let out = (md || '').replace(_annotationRegexGlobal(), '');
  out = out.replace(/\n##\s+Annotations\s*\r?\n+/g, '\n');
  out = out.replace(/\n{3,}/g, '\n\n');
  if (!annotations || !annotations.length) return out;
  if (!out.endsWith('\n')) out += '\n';
  out += '\n## Annotations\n\n';
  for (const a of annotations) out += _annotationLine(a) + '\n';
  return out;
}

export function _newAnnotationId() {
  return 'ann-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 7);
}

export function _encodeFieldName(name) {
  let out = '';
  for (const ch of name || '') {
    if (/[A-Za-z0-9_.\-]/.test(ch)) {
      out += ch;
    } else {
      const enc = new TextEncoder().encode(ch);
      for (const b of enc) out += '%' + b.toString(16).toUpperCase().padStart(2, '0');
    }
  }
  return out;
}
