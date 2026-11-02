import { readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const STATIC = join(HERE, '..', '..', 'static');
const INDEX = join(STATIC, 'index.html');

const LINK = /<link\b[^>]*\brel\s*=\s*["']stylesheet["'][^>]*\bhref\s*=\s*["']\/static\/([^"'?]+)([^"']*)["']/gi;

async function entries() {
  const html = await readFile(INDEX, 'utf8');
  const out = [];

  for (const match of html.matchAll(LINK)) {
    const rel = match[1];
    const query = match[2];

    if (rel.startsWith('lib/')) continue;

    out.push({
      path: join(STATIC, rel),
      url: `/static/${rel}${query}`,
    });
  }

  if (!out.length) {
    throw new Error(`no app stylesheet <link> tags found in ${INDEX}`);
  }

  for (const entry of out) {
    try {
      await readFile(entry.path);
    } catch {
      throw new Error(
        `index.html links a stylesheet that does not exist: ${entry.path}`,
      );
    }
  }

  return out;
}

export async function stylesheetPaths() {
  return (await entries()).map(entry => entry.path);
}

export async function stylesheetUrls() {
  return (await entries()).map(entry => entry.url);
}

export async function stylesheetLinkTags() {
  return (await stylesheetUrls())
    .map(url => `<link rel="stylesheet" href="${url}">`)
    .join('');
}

export async function appCss() {
  const paths = await stylesheetPaths();
  const parts = [];

  for (const path of paths) {
    parts.push(await readFile(path, 'utf8'));
  }

  return parts.join('\n');
}
