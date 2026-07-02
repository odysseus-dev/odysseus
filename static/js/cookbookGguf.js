// ============================================
// COOKBOOK GGUF INCLUDE/QUANT HELPERS
// Leaf module (no browser deps) so the include-pattern logic is unit
// testable via node — see tests/test_cookbook_gguf_include_pattern_js.py.
// ============================================

// Strict token used to *extract* a display quant from a filename/include.
export const _QUANT_TOKEN_RE = /\b(?:UD-)?(?:IQ[1-8]_[A-Z0-9]+|Q[2-8]_K_[MLS]|Q[2-8]_[0-9A-Z]+|Q[2-8])\b/i;

// Permissive check: does this string *look like* a real GGUF quant token
// (optional UD- prefix, optional I, then Q + a 2-8 bit width)? Distinguishes
// an actual quant (Q4_0, Q4_K_M, IQ4_XS, UD-Q4_K_XL) from a display label
// (QAT-INT4, INT4), which is not part of any filename.
export const _LOOKS_LIKE_QUANT_RE = /^(?:UD-)?I?Q[2-8]/i;

export function _ggufIncludePattern(model, source) {
  if (source?.file) return source.file;
  // model.quant is only a usable --include token when it looks like a real
  // quant. Some catalog entries carry a display label instead (e.g.
  // "QAT-INT4"), which matches no filename and silently downloads 0 files.
  // (hf --include is case-sensitive, so a label wouldn't match anyway.)
  // Fall back to *.gguf then — these GGUF repos hold only 1-2 files.
  if (model?.quant && _LOOKS_LIKE_QUANT_RE.test(model.quant)) return `*${model.quant}*`;
  return '*.gguf';
}

export function _ggufDisplayPartFromInclude(include) {
  const clean = String(include || '').replace(/\*/g, '');
  const parts = clean.split('/').filter(Boolean);
  const file = parts[parts.length - 1] || clean;
  const dir = parts.length > 1 ? parts[parts.length - 2] : '';
  const quant = `${dir} ${file}`.match(_QUANT_TOKEN_RE);
  if (quant) return quant[0].toUpperCase().replace(/^UD-/, '');
  return file.replace(/\.gguf$/i, '').replace(/-\d{5}-of-\d{5}$/i, '');
}
