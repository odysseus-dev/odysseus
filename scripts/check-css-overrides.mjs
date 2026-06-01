#!/usr/bin/env node
/*
 * check-css-overrides.mjs — make desktop/mobile paired CSS rules discoverable.
 *
 * `static/style.css` is one big consolidated stylesheet, and a lot of "the CSS
 * did not move" bugs come from a selector that is styled at the top level and
 * then quietly re-styled again inside a responsive `@media` block. When you
 * tweak the desktop rule and nothing changes on a phone, it's almost always a
 * mobile override you forgot existed.
 *
 * This tool parses the stylesheet (comments and strings stripped, braces
 * balanced — no regex-guessing) and reports, for every selector, where it is
 * defined at the base layer and which `@media` breakpoints override it. It also
 * flags breakpoints written inconsistently (e.g. `max-width: 768px` vs
 * `max-width:768px`), which silently split one logical breakpoint into two.
 *
 * Usage:
 *   node scripts/check-css-overrides.mjs            # human-readable report
 *   node scripts/check-css-overrides.mjs --json     # machine-readable JSON
 *   node scripts/check-css-overrides.mjs --check     # exit 1 on breakpoint drift (CI)
 *   node scripts/check-css-overrides.mjs path/to.css # analyze a specific file
 *
 * Exports `analyzeCss(cssText)` for tests; no third-party dependencies.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';

/**
 * Replace comment bodies and string literals with spaces, preserving overall
 * length and every newline so reported line numbers stay accurate. Strings and
 * comments are the two places a stray `{`, `}`, `;` or `,` can appear without
 * being structural, so neutralizing them lets the parser stay brace-simple.
 */
function blankCommentsAndStrings(src) {
  let out = '';
  let i = 0;
  const n = src.length;
  while (i < n) {
    const c = src[i];
    const c2 = src[i + 1];
    if (c === '/' && c2 === '*') {
      out += '  ';
      i += 2;
      while (i < n && !(src[i] === '*' && src[i + 1] === '/')) {
        out += src[i] === '\n' ? '\n' : ' ';
        i += 1;
      }
      if (i < n) {
        out += '  ';
        i += 2;
      }
      continue;
    }
    if (c === '"' || c === "'") {
      const quote = c;
      out += ' ';
      i += 1;
      while (i < n && src[i] !== quote) {
        if (src[i] === '\\') {
          out += ' ';
          out += src[i + 1] === '\n' ? '\n' : ' ';
          i += 2;
          continue;
        }
        out += src[i] === '\n' ? '\n' : ' ';
        i += 1;
      }
      if (i < n) {
        out += ' ';
        i += 1;
      }
      continue;
    }
    out += c;
    i += 1;
  }
  return out;
}

/** Split a selector list on top-level commas (ignoring commas inside () or []). */
function splitSelectors(prelude) {
  const out = [];
  let depth = 0;
  let cur = '';
  for (const ch of prelude) {
    if (ch === '(' || ch === '[') depth += 1;
    else if (ch === ')' || ch === ']') depth = Math.max(0, depth - 1);
    if (ch === ',' && depth === 0) {
      out.push(cur);
      cur = '';
    } else {
      cur += ch;
    }
  }
  out.push(cur);
  return out
    .map((s) => s.trim().replace(/\s+/g, ' '))
    .filter(Boolean);
}

const NORMALIZE = (q) => q.replace(/\s+/g, '').toLowerCase();
// At-rules whose inner blocks are NOT selectors (keyframe stops, font
// descriptors, etc.) — never record their contents as overridable selectors.
const SUPPRESS_ATRULES = new Set([
  'keyframes',
  'font-face',
  'page',
  'property',
  'counter-style',
  'font-feature-values',
  'viewport',
]);

/**
 * Parse a stylesheet into selector → {base lines, per-breakpoint override lines}
 * plus the raw list of `@media` blocks encountered.
 */
function parse(css) {
  const clean = blankCommentsAndStrings(css);
  const stack = []; // frames: {type:'media'|'group'|'suppress'|'decl', normKey?, rawQuery?}
  const mediaBlocks = [];
  const selectors = new Map(); // selector -> {base:[lines], media:Map<normKey,{rawForms:Map,lines:[]}>}

  let buf = '';
  let bufHasContent = false;
  let bufLine = 1;
  let line = 1;

  const nearestMedia = () => {
    for (let k = stack.length - 1; k >= 0; k -= 1) {
      if (stack[k].type === 'media') return stack[k];
    }
    return null;
  };
  const record = (sel, mediaFrame, atLine) => {
    let entry = selectors.get(sel);
    if (!entry) {
      entry = { base: [], media: new Map() };
      selectors.set(sel, entry);
    }
    if (!mediaFrame) {
      entry.base.push(atLine);
      return;
    }
    let bucket = entry.media.get(mediaFrame.normKey);
    if (!bucket) {
      bucket = { rawForms: new Map(), lines: [] };
      entry.media.set(mediaFrame.normKey, bucket);
    }
    bucket.rawForms.set(mediaFrame.rawQuery, (bucket.rawForms.get(mediaFrame.rawQuery) || 0) + 1);
    bucket.lines.push(atLine);
  };

  for (let i = 0; i < clean.length; i += 1) {
    const ch = clean[i];
    if (ch === '\n') {
      line += 1;
      buf += ch;
      continue;
    }
    if (ch === '{') {
      const prelude = buf.trim();
      buf = '';
      bufHasContent = false;
      if (prelude.startsWith('@')) {
        const m = /^@(?:-[a-z]+-)?([a-z-]+)/i.exec(prelude);
        const name = m ? m[1].toLowerCase() : '';
        if (name === 'media') {
          const rawQuery = prelude.slice(prelude.indexOf('media') + 'media'.length).trim().replace(/\s+/g, ' ');
          const frame = { type: 'media', normKey: NORMALIZE(rawQuery), rawQuery };
          mediaBlocks.push({ ...frame, line: bufLine });
          stack.push(frame);
        } else if (SUPPRESS_ATRULES.has(name)) {
          stack.push({ type: 'suppress' });
        } else {
          stack.push({ type: 'group' }); // @supports / @layer / @container — transparent to media
        }
      } else if (prelude) {
        if (!stack.some((f) => f.type === 'suppress')) {
          const mediaFrame = nearestMedia();
          for (const sel of splitSelectors(prelude)) record(sel, mediaFrame, bufLine);
        }
        stack.push({ type: 'decl' });
      } else {
        stack.push({ type: 'group' });
      }
      continue;
    }
    if (ch === '}') {
      stack.pop();
      buf = '';
      bufHasContent = false;
      continue;
    }
    if (ch === ';') {
      const top = stack[stack.length - 1];
      if (!top || top.type !== 'decl') {
        buf = '';
        bufHasContent = false;
      }
      continue;
    }
    if (ch !== ' ' && ch !== '\t' && ch !== '\r' && !bufHasContent) {
      bufHasContent = true;
      bufLine = line;
    }
    buf += ch;
  }

  return { mediaBlocks, selectors };
}

/** Build the JSON-serializable analysis used by both the CLI and the tests. */
export function analyzeCss(css) {
  const { mediaBlocks, selectors } = parse(css);

  // Breakpoint catalog + inconsistent-spelling detection.
  const byNorm = new Map(); // normKey -> {forms:Map<rawQuery,count>, count}
  for (const b of mediaBlocks) {
    let e = byNorm.get(b.normKey);
    if (!e) {
      e = { forms: new Map(), count: 0 };
      byNorm.set(b.normKey, e);
    }
    e.count += 1;
    e.forms.set(b.rawQuery, (e.forms.get(b.rawQuery) || 0) + 1);
  }
  const canonical = (forms) =>
    [...forms.entries()].sort((a, b) => b[1] - a[1])[0][0];

  const breakpoints = [...byNorm.entries()]
    .map(([, e]) => ({ query: canonical(e.forms), count: e.count }))
    .sort((a, b) => b.count - a.count);

  const inconsistentBreakpoints = [...byNorm.entries()]
    .filter(([, e]) => e.forms.size > 1)
    .map(([normalized, e]) => ({
      normalized,
      forms: [...e.forms.entries()]
        .sort((a, b) => b[1] - a[1])
        .map(([form, count]) => ({ form, count })),
    }));

  const paired = [];
  const mobileOnly = [];
  for (const [selector, entry] of selectors) {
    if (entry.media.size === 0) continue; // base-only — nothing responsive to track
    const overrides = [...entry.media.entries()]
      .map(([, bucket]) => ({
        query: canonical(bucket.rawForms),
        lines: bucket.lines.slice().sort((a, b) => a - b),
      }))
      .sort((a, b) => a.lines[0] - b.lines[0]);
    if (entry.base.length > 0) {
      paired.push({ selector, baseLines: entry.base.slice().sort((a, b) => a - b), overrides });
    } else {
      mobileOnly.push({ selector, overrides });
    }
  }
  // Messiest first: most override breakpoints, then most override sites.
  const weight = (o) => o.overrides.length * 1000 + o.overrides.reduce((n, x) => n + x.lines.length, 0);
  paired.sort((a, b) => weight(b) - weight(a) || a.selector.localeCompare(b.selector));
  mobileOnly.sort((a, b) => a.selector.localeCompare(b.selector));

  const baseSelectors = [...selectors.values()].filter((e) => e.base.length > 0).length;

  return {
    stats: {
      mediaBlocks: mediaBlocks.length,
      breakpoints: breakpoints.length,
      baseSelectors,
      overriddenSelectors: paired.length,
      mobileOnlySelectors: mobileOnly.length,
      inconsistentBreakpoints: inconsistentBreakpoints.length,
    },
    breakpoints,
    inconsistentBreakpoints,
    paired,
    mobileOnly,
  };
}

function formatReport(a, file) {
  const L = [];
  L.push(`CSS responsive-override report — ${file}`);
  L.push('='.repeat(60));
  L.push(
    `${a.stats.mediaBlocks} @media blocks · ${a.stats.breakpoints} distinct breakpoints · ` +
      `${a.stats.baseSelectors} base selectors`
  );
  L.push(
    `${a.stats.overriddenSelectors} selectors overridden inside @media · ` +
      `${a.stats.mobileOnlySelectors} defined only inside @media`
  );
  L.push('');

  L.push('Breakpoints (by number of blocks):');
  for (const b of a.breakpoints) L.push(`  ${String(b.count).padStart(4)}  @media ${b.query}`);
  L.push('');

  if (a.inconsistentBreakpoints.length) {
    L.push('⚠ Inconsistent breakpoint spellings (one logical breakpoint, multiple forms):');
    for (const inc of a.inconsistentBreakpoints) {
      L.push(`  ${inc.normalized}`);
      for (const f of inc.forms) L.push(`      ${String(f.count).padStart(3)}×  @media ${f.form}`);
    }
    L.push('  → Normalize these so the breakpoint groups into one block.');
    L.push('');
  }

  L.push('Selectors styled at base AND overridden under @media:');
  L.push('(edit one of these at base and the override may mask your change)');
  for (const p of a.paired) {
    const bps = p.overrides.map((o) => `@media ${o.query} L${o.lines.join(',')}`).join('  ·  ');
    L.push(`  ${p.selector}`);
    L.push(`      base L${p.baseLines.join(',')}  →  ${bps}`);
  }
  if (!a.paired.length) L.push('  (none)');

  return L.join('\n');
}

function main(argv) {
  const args = argv.slice(2);
  const json = args.includes('--json');
  const check = args.includes('--check');
  const fileArg = args.find((x) => !x.startsWith('--'));
  const file = fileArg
    ? fileArg
    : fileURLToPath(new URL('../static/style.css', import.meta.url));

  let css;
  try {
    css = readFileSync(file, 'utf8');
  } catch (err) {
    process.stderr.write(`Could not read ${file}: ${err.message}\n`);
    return 2;
  }

  const analysis = analyzeCss(css);

  if (json) {
    process.stdout.write(`${JSON.stringify(analysis, null, 2)}\n`);
  } else {
    process.stdout.write(`${formatReport(analysis, file)}\n`);
  }

  if (check && analysis.inconsistentBreakpoints.length) {
    process.stderr.write(
      `\nFAIL: ${analysis.inconsistentBreakpoints.length} breakpoint(s) written inconsistently. ` +
        `Normalize the spellings above.\n`
    );
    return 1;
  }
  return 0;
}

if (import.meta.url === pathToFileURL(process.argv[1] || '').href) {
  process.exit(main(process.argv));
}
