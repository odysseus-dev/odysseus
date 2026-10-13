// JS twin of tests/helpers/document_source.py: the document editor's whole
// implementation set, so tests keep finding code as it moves out of the
// static/js/document.js entry into static/js/document/.
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const JS = join(HERE, '..', '..', 'static', 'js');
const ENTRY = join(JS, 'document.js');
const IMPL_DIR = join(JS, 'document');

function walk(dir) {
  return readdirSync(dir).sort().flatMap(name => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return walk(path);
    return name.endsWith('.js') ? [path] : [];
  });
}

/** Every file holding document-editor implementation, entry first. */
export function documentSourcePaths() {
  if (!existsSync(ENTRY)) {
    throw new Error(`document editor entry point is missing: ${ENTRY}`);
  }
  return [ENTRY, ...(existsSync(IMPL_DIR) ? walk(IMPL_DIR).sort() : [])];
}

/** The whole implementation set as one string, entry first. */
export function documentSource() {
  return documentSourcePaths().map(path => readFileSync(path, 'utf8')).join('\n');
}
