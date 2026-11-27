// GGUF download filter helpers. Kept free of browser imports so node can test them.

// `hf --include` and snapshot_download(allow_patterns=...) match globs
// case-sensitively, but repos disagree on quant casing (unsloth ships
// "Q4_K_M", google ships "q4_0"), so letters match in either case.
export function caseInsensitiveGlob(text) {
  return String(text).replace(/[a-z]/gi, ch => `[${ch.toUpperCase()}${ch.toLowerCase()}]`);
}

export function ggufIncludePattern(model, source) {
  if (source?.file) return source.file;
  if (model?.quant) return `*${caseInsensitiveGlob(model.quant)}*`;
  return '*.gguf';
}

// Plain text of an include filter ("*[Qq]4_0*" -> "Q4_0") for labels and
// file matching that compare against it as a substring.
export function includeText(include) {
  return String(include || '').replace(/\[([A-Za-z])[A-Za-z]\]/g, '$1').replace(/\*/g, '');
}
