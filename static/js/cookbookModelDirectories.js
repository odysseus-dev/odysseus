// Directory chips contain remove glyphs, and displayed Unix roots can lose the leading slash.
export function normalizeCookbookModelDir(value) {
  const dir = String(value || '').replace(/[\u2715\u2716]/g, '').trim();
  return /^(home|mnt|media|data|opt|srv|var)\//.test(dir) ? `/${dir}` : dir;
}
