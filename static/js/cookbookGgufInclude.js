// GGUF quant include globs for Cookbook downloads (#7294).
//
// A bare `*Q6_K*` also matches sibling quants whose name starts with the
// same text (`Q6_K_L`, `Q4_K` -> `Q4_K_M`/`Q4_K_S`, `Q8_0` -> `Q8_0_L`), so
// one download pulled several multi-GB files. The quant must be followed
// by `.` (single file, `-Q6_K.gguf`) or `-` (split file,
// `-Q6_K-00001-of-00002.gguf`), never by `_`.

const QUANT_END = '[-.]';

// Include glob for a `repo:QUANT` tag.
export function quantTagInclude(quant) {
  return `*${quant}${QUANT_END}*`;
}

// Include glob for a quant picked from the repo file list. `prefix` is the
// folder the quant's files live in ('' for the repo root).
export function quantFileInclude(quant, prefix = '') {
  return `${prefix}*${quant}${QUANT_END}*gguf`;
}

// fnmatch-style glob -> RegExp (what `hf download --include` and
// `snapshot_download(allow_patterns=...)` use): `*` and `?` also cross `/`.
// Case-insensitive, and anchored at any `/` so a repo-relative glob still
// matches a path with extra leading folders.
export function includeToRegExp(glob) {
  let re = '';
  const s = String(glob || '');
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (c === '*') re += '.*';
    else if (c === '?') re += '.';
    else if (c === '[') {
      const end = s.indexOf(']', i + 1);
      if (end === -1) { re += '\\['; continue; }
      re += `[${s.slice(i + 1, end).replace(/[\\\]^]/g, '\\$&')}]`;
      i = end;
    } else re += c.replace(/[.+^${}()|\\\/]/g, '\\$&');
  }
  return new RegExp(`(?:^|/)${re}$`, 'i');
}

// Human text for an include glob (task names): drop the wildcards and the
// quant-end class.
export function includeDisplayText(glob) {
  return String(glob || '').split(QUANT_END).join('.').replace(/\*/g, '').replace(/\.{2,}/g, '.');
}
