// Loads the pure-logic pieces of the real memoryGraph.js under Node, mirroring
// the vm.createContext() sandbox pattern used by
// tests/markdown_codefence_placeholder_regression.mjs: read the production
// source, string-shim its sibling imports out, strip `export` keywords, and
// expose the internal functions under test via `this.__name = name`.
//
// Only functions that don't touch Cytoscape/the DOM beyond getComputedStyle
// are exposed here — anything that calls `_cy.*` or builds live modal DOM
// stays covered by manual browser verification instead (see docs/progress.md).
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const SOURCE_PATH = path.join(REPO, 'static', 'js', 'memoryGraph.js');

// Fake theme: distinct colors per CSS custom property so category->color
// mapping is deterministic and each category is provably distinct.
const FAKE_CSS_VARS = {
  '--fg': '#fg-color',
  '--bg': '#bg-color',
  '--border': '#border-color',
  '--accent': '#accent-color',
  '--red': '#red-color',
  '--hl-keyword': '#identity-color',
  '--warn': '#preference-color',
  '--color-accent': '#contact-color',
  '--color-brand-blue': '#project-color',
  '--accent-warm': '#goal-color',
  '--green': '#task-color',
  '--color-muted-alt': '#fallback-color',
};

export function loadMemoryGraph() {
  let src = fs.readFileSync(SOURCE_PATH, 'utf8');

  src = src.replace(/^import uiModule from '\.\/ui\.js';$/m, "const uiModule = { esc: (s) => String(s) };");
  src = src.replace(/^import spinnerModule from '\.\/spinner\.js';$/m, "const spinnerModule = {};");
  src = src.replace(/^import \* as Modals from '\.\/modalManager\.js';$/m, "const Modals = {};");
  src = src.replace(/^import \{ makeWindowDraggable \} from '\.\/windowDrag\.js';$/m, "function makeWindowDraggable() {}");
  src = src.replace(/^export function /gm, 'function ');
  src = src.replace(/^export const /gm, 'const ');
  src = src.replace(/^const memoryGraphModule[\s\S]*?^export default memoryGraphModule;$/m, '');

  src += `
this.__nodeSize = _nodeSize;
this.__categoryColor = _categoryColor;
this.__toElements = _toElements;
this.__buildQuery = _buildQuery;
this.__componentNodeIds = _componentNodeIds;
this.__DEMO_GRAPH = DEMO_GRAPH;
this.__KNOWN_CATEGORIES = KNOWN_CATEGORIES;
`;

  const sandbox = {
    console,
    URLSearchParams,
    getComputedStyle() {
      return { getPropertyValue: (name) => FAKE_CSS_VARS[name] || '' };
    },
    document: { documentElement: {} },
    window: { location: { origin: 'http://localhost' } },
  };
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: SOURCE_PATH });
  return sandbox;
}
