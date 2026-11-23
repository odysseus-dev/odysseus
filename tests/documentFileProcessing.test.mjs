// tests/documentFileProcessing.test.mjs
/**
 * Regression tests for the document file-processing module.
 *
 * These tests run in Node.js via node:test with --experimental-vm-modules.
 * They cover:
 *   - htmlToMarkdown conversion (all element types)
 *   - EXT_TO_LANG and SPREADSHEET_EXTENSIONS mappings
 *   - ensureXLSX / ensureMammoth lazy-loading behavior
 *   - readFileContent for plain-text, spreadsheet (mocked XLSX), and DOCX (mocked Mammoth)
 *   - Concurrent dependency-loading (no duplicate script injection)
 *   - Failed dependency loads
 */

import { describe, it, before } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { SourceTextModule, createContext } from 'node:vm';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');
const SOURCE = path.join(ROOT, 'frontend', 'src', 'features', 'documents', 'ui', 'fileProcessing.js');
const sourceText = fs.readFileSync(SOURCE, 'utf8');

// ── Module loader using createContext for globals injection ───────────────────

/**
 * Load the fileProcessing module in a fresh VM context with the given globals.
 * Each call gets an isolated module instance with its own module-level state
 * (so _xlsxReady and _mammothReady start as null for each test that needs them).
 *
 * @param {object} globals - Values to inject as globals (window, document, DOMParser, FileReader)
 * @returns {Promise<object>} module namespace
 */
async function loadModule(globals = {}) {
  const ctx = createContext({
    window: globals.window || {},
    document: globals.document || {},
    DOMParser: globals.DOMParser || class { parseFromString() { return { body: { childNodes: [] } }; } },
    FileReader: globals.FileReader || class {},
    // Node builtins that browser globals implicitly use:
    setTimeout,
    clearTimeout,
    URL: globalThis.URL,
    Promise,
    Array,
    Object,
    Error,
    TextEncoder,
    console,
  });
  const url = pathToFileURL(SOURCE).href;
  const mod = new SourceTextModule(sourceText, { identifier: url, context: ctx });
  await mod.link(() => { throw new Error('No static imports expected in fileProcessing.js'); });
  await mod.evaluate();
  return mod.namespace;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeFile(name, content) {
  return {
    name,
    _content: content,
    arrayBuffer: async () => new TextEncoder().encode(content || '').buffer,
  };
}

/**
 * Build a minimal DOM stub that tracks script injection.
 * Returns the stub object plus trigger functions for simulating load/error.
 */
function makeScriptDom() {
  let _onload = null;
  let _onerror = null;
  let appendCount = 0;
  let lastSrc = '';

  const scriptEl = {
    get onload() { return _onload; },
    set onload(fn) { _onload = fn; },
    get onerror() { return _onerror; },
    set onerror(fn) { _onerror = fn; },
    get src() { return lastSrc; },
    set src(v) { lastSrc = v; },
  };

  const doc = {
    createElement(tag) {
      if (tag === 'script') {
        appendCount++;
        return scriptEl;
      }
      return {};
    },
    head: {
      appendChild() {},
    },
  };

  return {
    doc,
    scriptEl,
    getAppendCount() { return appendCount; },
    getLastSrc() { return lastSrc; },
    triggerLoad() { if (_onload) _onload(); },
    triggerError() { if (_onerror) _onerror(); },
  };
}

// ── DOMParser stub for htmlToMarkdown ─────────────────────────────────────────
// Since we're in a VM context, we can't use the real DOMParser.
// We provide a simple but correct HTML-to-DOM parser for the tags htmlToMarkdown handles.

function buildDomParserStub() {
  return class DOMParserStub {
    parseFromString(html) {
      const body = parseBodyFromHtml(html);
      return { body };
    }
  };
}

function parseBodyFromHtml(html) {
  const stripped = html
    .replace(/<html[^>]*>/gi, '')
    .replace(/<\/html>/gi, '')
    .replace(/<head[^>]*>[\s\S]*?<\/head>/gi, '')
    .replace(/<\/?body[^>]*>/gi, '');
  const nodes = parseNodes(stripped);
  return makeElement('body', {}, nodes);
}

function makeElement(tag, attrs, children) {
  const el = {
    nodeType: 1,
    tagName: tag.toUpperCase(),
    childNodes: children,
    href: attrs.href || '',
    src: attrs.src || '',
    alt: attrs.alt || '',
    get textContent() {
      return children.map(c => c.nodeType === 3 ? c.textContent : c.textContent).join('');
    },
    parentElement: null,
    querySelectorAll(sel) {
      if (sel === 'tr') return flatFind(children, 'tr');
      if (sel === 'th, td') return flatFind(children, 'th', 'td');
      return [];
    },
    get children() { return children.filter(c => c.nodeType === 1); },
  };
  for (const child of children) {
    if (child && child.nodeType === 1) child.parentElement = el;
  }
  return el;
}

function makeText(text) {
  return { nodeType: 3, textContent: decodeHtmlEntities(text) };
}

function flatFind(nodes, ...tags) {
  const result = [];
  for (const node of nodes) {
    if (!node || node.nodeType !== 1) continue;
    if (tags.includes(node.tagName.toLowerCase())) result.push(node);
    if (node.childNodes) result.push(...flatFind(node.childNodes, ...tags));
  }
  return result;
}

function decodeHtmlEntities(str) {
  const entities = {
    '&amp;': '&',
    '&lt;': '<',
    '&gt;': '>',
    '&quot;': '"',
    '&#39;': "'",
  };

  return str.replace(
    /&(?:amp|lt|gt|quot|#39);/g,
    entity => entities[entity],
  );
}

function parseAttrs(str) {
  const attrs = {};
  const re = /(\w[\w-]*)(?:=["']([^"']*)["'])?/g;
  let m;
  while ((m = re.exec(str)) !== null) {
    attrs[m[1]] = m[2] !== undefined ? m[2] : true;
  }
  return attrs;
}

const VOID_TAGS = new Set(['br', 'hr', 'img', 'input', 'link', 'meta']);

function parseNodes(html) {
  const nodes = [];
  let i = 0;
  while (i < html.length) {
    if (html[i] !== '<') {
      const next = html.indexOf('<', i);
      const text = next >= 0 ? html.slice(i, next) : html.slice(i);
      if (text) nodes.push(makeText(text));
      i = next >= 0 ? next : html.length;
      continue;
    }
    const tagEnd = html.indexOf('>', i);
    if (tagEnd === -1) break;
    const tagContent = html.slice(i + 1, tagEnd).trim();
    if (tagContent.startsWith('/') || tagContent.startsWith('!')) {
      i = tagEnd + 1;
      continue;
    }
    const isSelfClose = tagContent.endsWith('/');
    const rawTag = tagContent.replace(/\/$/, '').trim();
    const spaceIdx = rawTag.search(/\s/);
    const tagName = (spaceIdx >= 0 ? rawTag.slice(0, spaceIdx) : rawTag).toLowerCase();
    const attrStr = spaceIdx >= 0 ? rawTag.slice(spaceIdx + 1) : '';
    const attrs = parseAttrs(attrStr);
    i = tagEnd + 1;
    let children = [];
    if (!isSelfClose && !VOID_TAGS.has(tagName)) {
      const closeTag = `</${tagName}>`;
      const closeIdx = html.indexOf(closeTag, i);
      if (closeIdx >= 0) {
        children = parseNodes(html.slice(i, closeIdx));
        i = closeIdx + closeTag.length;
      }
    }
    nodes.push(makeElement(tagName, attrs, children));
  }
  return nodes;
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('htmlToMarkdown', () => {
  let api;
  before(async () => {
    api = await loadModule({ DOMParser: buildDomParserStub() });
  });

  it('converts paragraphs', () => {
    const result = api.htmlToMarkdown('<p>Hello world</p>');
    assert.ok(result.includes('Hello world'), `paragraph text missing: ${result}`);
  });

  it('converts h1 heading', () => {
    const result = api.htmlToMarkdown('<h1>Title</h1>');
    assert.ok(result.includes('# Title'), `h1 missing: ${result}`);
  });

  it('converts h2 heading', () => {
    const result = api.htmlToMarkdown('<h2>Sub</h2>');
    assert.ok(result.includes('## Sub'), `h2 missing: ${result}`);
  });

  it('converts h3 heading', () => {
    const result = api.htmlToMarkdown('<h3>Sub2</h3>');
    assert.ok(result.includes('### Sub2'), `h3 missing: ${result}`);
  });

  it('converts h4 heading', () => {
    const result = api.htmlToMarkdown('<h4>Sub3</h4>');
    assert.ok(result.includes('#### Sub3'), `h4 missing: ${result}`);
  });

  it('wraps strong/b in double asterisks', () => {
    const result = api.htmlToMarkdown('<p><strong>bold</strong></p>');
    assert.ok(result.includes('**bold**'), `bold missing: ${result}`);
  });

  it('wraps em/i in single asterisks', () => {
    const result = api.htmlToMarkdown('<p><em>italic</em></p>');
    assert.ok(result.includes('*italic*'), `italic missing: ${result}`);
  });

  it('converts unordered list items', () => {
    const result = api.htmlToMarkdown('<ul><li>Item A</li><li>Item B</li></ul>');
    assert.ok(result.includes('- Item A'), `list item A missing: ${result}`);
    assert.ok(result.includes('- Item B'), `list item B missing: ${result}`);
  });

  it('converts ordered list items with index', () => {
    const result = api.htmlToMarkdown('<ol><li>First</li><li>Second</li></ol>');
    assert.ok(result.includes('1. First'), `ordered item 1 missing: ${result}`);
    assert.ok(result.includes('2. Second'), `ordered item 2 missing: ${result}`);
  });

  it('skips base64 images and renders alt text as note', () => {
    const result = api.htmlToMarkdown('<img src="data:image/png;base64,abc" alt="chart">');
    assert.ok(!result.includes('data:'), `base64 src should be skipped: ${result}`);
    assert.ok(result.includes('[image: chart]'), `alt text missing: ${result}`);
  });

  it('skips base64 image with no alt silently', () => {
    const result = api.htmlToMarkdown('<img src="data:image/png;base64,abc" alt="">');
    assert.equal(result.trim(), '', `should be empty: ${result}`);
  });

  it('renders external images as markdown image syntax', () => {
    const result = api.htmlToMarkdown('<img src="http://example.com/img.png" alt="photo">');
    assert.ok(result.includes('![photo](http://example.com/img.png)'), `external image missing: ${result}`);
  });

  it('collapses three or more consecutive newlines to two', () => {
    const result = api.htmlToMarkdown('<p>A</p><p>B</p><p>C</p>');
    assert.ok(!/\n{3,}/.test(result), `triple newlines not collapsed: ${JSON.stringify(result)}`);
  });

  it('renders br as newline', () => {
    const result = api.htmlToMarkdown('<p>line1<br>line2</p>');
    assert.ok(result.includes('line1\nline2'), `br newline missing: ${result}`);
  });

  it('renders anchor link', () => {
    const result = api.htmlToMarkdown('<a href="https://example.com">link</a>');
    assert.ok(result.includes('[link](https://example.com)'), `link missing: ${result}`);
  });

  it('renders a simple table with header separator', () => {
    const result = api.htmlToMarkdown('<table><tr><th>Name</th><th>Age</th></tr><tr><td>Alice</td><td>30</td></tr></table>');
    assert.ok(result.includes('| Name | Age |'), `table header missing: ${result}`);
    assert.ok(result.includes('| --- | --- |'), `table separator missing: ${result}`);
    assert.ok(result.includes('| Alice | 30 |'), `table row missing: ${result}`);
  });

  it('trims and returns non-empty content', () => {
    const result = api.htmlToMarkdown('<p>  hello  </p>');
    assert.ok(result === result.trim(), `result should be trimmed: ${JSON.stringify(result)}`);
  });

  it('decodes nested HTML entities in text only once', () => {
    const result = api.htmlToMarkdown('<p>&amp;lt;script&amp;gt;</p>');
    assert.equal(result, '&lt;script&gt;');
  });
});

describe('EXT_TO_LANG and DEVICE_IMPORT_EXT_TO_LANG', () => {
  let api;
  before(async () => { api = await loadModule({}); });

  it('maps common source extensions in both mappings', () => {
    for (const mapping of [api.EXT_TO_LANG, api.DEVICE_IMPORT_EXT_TO_LANG]) {
      assert.equal(mapping['.py'], 'python');
      assert.equal(mapping['.js'], 'javascript');
      assert.equal(mapping['.ts'], 'typescript');
      assert.equal(mapping['.md'], 'markdown');
      assert.equal(mapping['.json'], 'json');
      assert.equal(mapping['.sql'], 'sql');
      assert.equal(mapping['.rs'], 'rust');
      assert.equal(mapping['.txt'], '');
      assert.equal(mapping['.log'], '');
    }
  });

  it('preserves importFromDevice unclassified extensions as undefined in DEVICE_IMPORT_EXT_TO_LANG', () => {
    // These extensions were never classified in importFromDevice.js and must remain unclassified (undefined)
    // so that importFromDevice treats them as language=null / plain text.
    const unclassifiedInDevice = [
      '.cfg', '.conf', '.env', '.vue', '.svelte', '.scss', '.sass', '.less', '.doc', '.docx', '.xlsx', '.xls', '.ods'
    ];
    for (const ext of unclassifiedInDevice) {
      assert.equal(api.DEVICE_IMPORT_EXT_TO_LANG[ext], undefined, `Expected ${ext} to be undefined in DEVICE_IMPORT_EXT_TO_LANG`);
    }
  });

  it('classifies web, config, and office extensions in EXT_TO_LANG for Document Library', () => {
    assert.equal(api.EXT_TO_LANG['.cfg'], 'ini');
    assert.equal(api.EXT_TO_LANG['.conf'], 'ini');
    assert.equal(api.EXT_TO_LANG['.env'], '');
    assert.equal(api.EXT_TO_LANG['.vue'], 'html');
    assert.equal(api.EXT_TO_LANG['.svelte'], 'html');
    assert.equal(api.EXT_TO_LANG['.scss'], 'css');
    assert.equal(api.EXT_TO_LANG['.sass'], 'css');
    assert.equal(api.EXT_TO_LANG['.less'], 'css');
    assert.equal(api.EXT_TO_LANG['.xlsx'], 'csv');
    assert.equal(api.EXT_TO_LANG['.xls'], 'csv');
    assert.equal(api.EXT_TO_LANG['.ods'], 'csv');
    assert.equal(api.EXT_TO_LANG['.docx'], 'docx');
    assert.equal(api.EXT_TO_LANG['.doc'], 'markdown');
  });

  it('both mappings are frozen', () => {
    assert.ok(Object.isFrozen(api.EXT_TO_LANG));
    assert.ok(Object.isFrozen(api.DEVICE_IMPORT_EXT_TO_LANG));
  });

  it('returns undefined for unknown extensions', () => {
    assert.equal(api.EXT_TO_LANG['.unknown'], undefined);
    assert.equal(api.EXT_TO_LANG['.xyz'], undefined);
    assert.equal(api.DEVICE_IMPORT_EXT_TO_LANG['.unknown'], undefined);
  });
});

describe('SPREADSHEET_EXTENSIONS', () => {
  let api;
  before(async () => { api = await loadModule({}); });

  it('contains xlsx, xls, ods', () => {
    assert.ok(api.SPREADSHEET_EXTENSIONS.includes('.xlsx'));
    assert.ok(api.SPREADSHEET_EXTENSIONS.includes('.xls'));
    assert.ok(api.SPREADSHEET_EXTENSIONS.includes('.ods'));
  });

  it('does not contain csv, docx, or pdf', () => {
    assert.ok(!api.SPREADSHEET_EXTENSIONS.includes('.csv'));
    assert.ok(!api.SPREADSHEET_EXTENSIONS.includes('.docx'));
    assert.ok(!api.SPREADSHEET_EXTENSIONS.includes('.pdf'));
  });

  it('is frozen', () => {
    assert.ok(Object.isFrozen(api.SPREADSHEET_EXTENSIONS));
  });
});

describe('ensureXLSX', () => {
  it('resolves immediately when window.XLSX is already set', async () => {
    const dom = makeScriptDom();
    const api = await loadModule({ window: { XLSX: {} }, document: dom.doc });
    await api.ensureXLSX();
    assert.equal(dom.getAppendCount(), 0, 'No script tag when XLSX preset');
  });

  it('injects a script and resolves on load', async () => {
    const dom = makeScriptDom();
    const api = await loadModule({ window: {}, document: dom.doc });
    const p = api.ensureXLSX();
    dom.triggerLoad();
    await p;
    assert.equal(dom.getAppendCount(), 1, 'One script tag injected');
    assert.ok(dom.getLastSrc().includes('xlsx'), `src should reference xlsx: ${dom.getLastSrc()}`);
  });

  it('rejects with correct message on script error', async () => {
    const dom = makeScriptDom();
    const api = await loadModule({ window: {}, document: dom.doc });
    const p = api.ensureXLSX();
    dom.triggerError();
    await assert.rejects(p, /Failed to load XLSX library/);
  });

  it('concurrent calls return the same promise (no duplicate injection)', async () => {
    const dom = makeScriptDom();
    const api = await loadModule({ window: {}, document: dom.doc });
    const p1 = api.ensureXLSX();
    const p2 = api.ensureXLSX();
    assert.equal(p1, p2, 'Concurrent calls must return the same promise');
    dom.triggerLoad();
    await Promise.all([p1, p2]);
    assert.equal(dom.getAppendCount(), 1, 'Only one script should be appended');
  });
});

describe('ensureMammoth', () => {
  it('resolves immediately when window.mammoth is already set', async () => {
    const dom = makeScriptDom();
    const api = await loadModule({ window: { mammoth: {} }, document: dom.doc });
    await api.ensureMammoth();
    assert.equal(dom.getAppendCount(), 0, 'No script tag when mammoth preset');
  });

  it('injects a script and resolves on load', async () => {
    const dom = makeScriptDom();
    const api = await loadModule({ window: {}, document: dom.doc });
    const p = api.ensureMammoth();
    dom.triggerLoad();
    await p;
    assert.ok(dom.getLastSrc().includes('mammoth'), `src should reference mammoth: ${dom.getLastSrc()}`);
  });

  it('rejects with correct message on script error', async () => {
    const dom = makeScriptDom();
    const api = await loadModule({ window: {}, document: dom.doc });
    const p = api.ensureMammoth();
    dom.triggerError();
    await assert.rejects(p, /Failed to load DOCX library/);
  });

  it('concurrent calls return the same promise', async () => {
    const dom = makeScriptDom();
    const api = await loadModule({ window: {}, document: dom.doc });
    const p1 = api.ensureMammoth();
    const p2 = api.ensureMammoth();
    assert.equal(p1, p2, 'Concurrent calls must return the same promise');
    dom.triggerLoad();
    await Promise.all([p1, p2]);
  });
});

describe('readFileContent', () => {
  it('reads plain text files via FileReader', async () => {
    const mockFileReader = class {
      readAsText(file) {
        this.result = file._content;
        Promise.resolve().then(() => { if (this.onload) this.onload(); });
      }
    };
    const api = await loadModule({ window: {}, FileReader: mockFileReader });
    const result = await api.readFileContent(makeFile('notes.txt', 'hello text'));
    assert.equal(result, 'hello text');
  });

  it('reads source code files as plain text', async () => {
    const mockFileReader = class {
      readAsText(file) {
        this.result = file._content;
        Promise.resolve().then(() => { if (this.onload) this.onload(); });
      }
    };
    const api = await loadModule({ window: {}, FileReader: mockFileReader });
    const result = await api.readFileContent(makeFile('script.py', 'print("hello")'));
    assert.equal(result, 'print("hello")');
  });

  it('reads XLSX with mocked window.XLSX', async () => {
    const mockXLSX = {
      read() {
        return {
          SheetNames: ['Sheet1'],
          Sheets: { Sheet1: { _csv: 'col1,col2\n1,2' } },
        };
      },
      utils: { sheet_to_csv(sheet) { return sheet._csv; } },
    };
    const api = await loadModule({ window: { XLSX: mockXLSX } });
    const result = await api.readFileContent(makeFile('data.xlsx', ''));
    assert.equal(result, 'col1,col2\n1,2');
  });

  it('prefixes multi-sheet XLSX with sheet name headers', async () => {
    const mockXLSX = {
      read() {
        return {
          SheetNames: ['Alpha', 'Beta'],
          Sheets: {
            Alpha: { _csv: 'a,b\n1,2' },
            Beta:  { _csv: 'x,y\n3,4' },
          },
        };
      },
      utils: { sheet_to_csv(sheet) { return sheet._csv; } },
    };
    const api = await loadModule({ window: { XLSX: mockXLSX } });
    const result = await api.readFileContent(makeFile('report.xlsx', ''));
    assert.ok(result.includes('# Sheet: Alpha'), `Alpha header missing: ${result}`);
    assert.ok(result.includes('a,b\n1,2'), `Alpha data missing: ${result}`);
    assert.ok(result.includes('# Sheet: Beta'), `Beta header missing: ${result}`);
    assert.ok(result.includes('x,y\n3,4'), `Beta data missing: ${result}`);
  });

  it('single-sheet XLSX has no sheet header', async () => {
    const mockXLSX = {
      read() {
        return { SheetNames: ['Data'], Sheets: { Data: { _csv: 'a,b' } } };
      },
      utils: { sheet_to_csv(sheet) { return sheet._csv; } },
    };
    const api = await loadModule({ window: { XLSX: mockXLSX } });
    const result = await api.readFileContent(makeFile('table.xls', ''));
    assert.equal(result, 'a,b', `single sheet should not have header: ${result}`);
  });

  it('reads DOCX via mocked mammoth and converts to markdown', async () => {
    const mockMammoth = {
      convertToHtml: async () => ({ value: '<h1>Title</h1><p>Body text.</p>' }),
    };
    const api = await loadModule({
      window: { mammoth: mockMammoth },
      DOMParser: buildDomParserStub(),
    });
    const result = await api.readFileContent(makeFile('document.docx', ''));
    assert.ok(result.includes('# Title'), `h1 missing: ${result}`);
    assert.ok(result.includes('Body text.'), `body text missing: ${result}`);
  });

  it('propagates FileReader errors for plain text', async () => {
    const mockFileReader = class {
      readAsText() {
        this.error = new Error('disk error');
        Promise.resolve().then(() => { if (this.onerror) this.onerror(); });
      }
    };
    const api = await loadModule({ window: {}, FileReader: mockFileReader });
    await assert.rejects(
      () => api.readFileContent(makeFile('broken.txt', '')),
      /disk error/,
    );
  });

  it('propagates XLSX library load failure', async () => {
    const dom = makeScriptDom();
    const api = await loadModule({ window: {}, document: dom.doc });
    const p = api.readFileContent(makeFile('data.xlsx', ''));
    dom.triggerError();
    await assert.rejects(p, /Failed to load XLSX library/);
  });

  it('propagates Mammoth library load failure for DOCX', async () => {
    const dom = makeScriptDom();
    const api = await loadModule({ window: {}, document: dom.doc });
    const p = api.readFileContent(makeFile('report.docx', ''));
    dom.triggerError();
    await assert.rejects(p, /Failed to load DOCX library/);
  });

  it('handles unsupported extension as plain text', async () => {
    const mockFileReader = class {
      readAsText(file) {
        this.result = file._content;
        Promise.resolve().then(() => { if (this.onload) this.onload(); });
      }
    };
    const api = await loadModule({ window: {}, FileReader: mockFileReader });
    const result = await api.readFileContent(makeFile('config.cfg', 'key=value'));
    assert.equal(result, 'key=value');
  });
});

describe('importFromDevice submission regression', () => {
  const importDevicePath = path.join(ROOT, 'frontend', 'src', 'features', 'documents', 'ui', 'importFromDevice.js');
  const importDeviceSource = fs.readFileSync(importDevicePath, 'utf8');

  async function loadImportFromDeviceModule(globals = {}) {
    const ctx = createContext({
      window: globals.window || {},
      document: globals.document || {},
      FileReader: globals.FileReader || class {},
      FormData: globals.FormData || globalThis.FormData,
      fetch: globals.fetch || (async () => ({ ok: true, json: async () => ({}) })),
      requestAnimationFrame: globals.requestAnimationFrame || (fn => setTimeout(fn, 0)),
      setTimeout,
      clearTimeout,
      URL: globalThis.URL,
      Promise,
      Array,
      Object,
      Error,
      TextEncoder,
      console,
    });

    const fpUrl = pathToFileURL(SOURCE).href;
    const fpMod = new SourceTextModule(sourceText, { identifier: fpUrl, context: ctx });
    await fpMod.link(() => { throw new Error('No static imports in fileProcessing.js'); });
    await fpMod.evaluate();

    const devUrl = pathToFileURL(importDevicePath).href;
    const devMod = new SourceTextModule(importDeviceSource, { identifier: devUrl, context: ctx });
    await devMod.link((specifier) => {
      if (specifier === './fileProcessing.js') {
        return fpMod;
      }
      throw new Error(`Unexpected import specifier: ${specifier}`);
    });
    await devMod.evaluate();
    return devMod.namespace;
  }

  function createDomEnvironment() {
    let createdInput = null;
    const doc = {
      body: {
        appendChild(child) {},
      },
      createElement(tag) {
        if (tag === 'input') {
          const listeners = {};
          const el = {
            type: '',
            style: {},
            files: [],
            value: '',
            remove() {},
            addEventListener(event, fn) {
              listeners[event] = fn;
            },
            click() {},
            async triggerChange() {
              if (listeners.change) {
                await listeners.change();
              }
            },
          };
          createdInput = el;
          return el;
        }
        return {};
      },
    };
    return {
      doc,
      getInput: () => createdInput,
    };
  }

  const MockFileReader = class {
    readAsText(file) {
      this.result = file._content !== undefined ? file._content : '';
      Promise.resolve().then(() => {
        if (this.onload) this.onload();
      });
    }
  };

  it('sends language: null to /api/document for unclassified extensions (.cfg, .conf, .env, .vue)', async () => {
    const capturedRequests = [];
    const mockFetch = async (url, opts = {}) => {
      if (opts.method === 'POST' && url.includes('/api/document')) {
        capturedRequests.push({ url, body: JSON.parse(opts.body) });
        return { ok: true, json: async () => ({ id: 'doc-cfg-1', doc_id: 'doc-cfg-1' }) };
      }
      if (url.includes('/api/document/')) {
        return { ok: true, json: async () => ({ id: 'doc-cfg-1', title: 'config', session_id: 'sess-1' }) };
      }
      return { ok: true, json: async () => ({}) };
    };

    const domEnv = createDomEnvironment();
    const mod = await loadImportFromDeviceModule({
      document: domEnv.doc,
      FileReader: MockFileReader,
      fetch: mockFetch,
    });

    mod.initImportFromDevice({
      getApiBase: () => '',
      getSessionModule: () => ({ getCurrentSessionId: () => 'sess-test' }),
    });

    // Test .cfg
    mod._importFromDevice();
    const inputEl = domEnv.getInput();
    assert.ok(inputEl, 'File input element should have been created');
    inputEl.files = [{ name: 'settings.cfg', _content: 'param=true' }];
    await inputEl.triggerChange();

    assert.equal(capturedRequests.length, 1);
    assert.equal(capturedRequests[0].body.title, 'settings');
    assert.equal(capturedRequests[0].body.language, null, 'Unclassified .cfg must send language: null');
    assert.equal(capturedRequests[0].body.content, 'param=true');
    assert.equal(capturedRequests[0].body.session_id, 'sess-test');

    // Test .conf and .env
    inputEl.files = [{ name: 'nginx.conf', _content: 'server {}' }];
    await inputEl.triggerChange();
    assert.equal(capturedRequests.length, 2);
    assert.equal(capturedRequests[1].body.language, null, 'Unclassified .conf must send language: null');

    inputEl.files = [{ name: 'vars.env', _content: 'FOO=bar' }];
    await inputEl.triggerChange();
    assert.equal(capturedRequests.length, 3);
    assert.equal(capturedRequests[2].body.language, null, 'Unclassified .env must send language: null');
  });

  it('sends mapped language for recognized extensions (.py -> python, .js -> javascript)', async () => {
    const capturedRequests = [];
    const mockFetch = async (url, opts = {}) => {
      if (opts.method === 'POST' && url.includes('/api/document')) {
        capturedRequests.push({ url, body: JSON.parse(opts.body) });
        return { ok: true, json: async () => ({ id: 'doc-py-1', doc_id: 'doc-py-1' }) };
      }
      if (url.includes('/api/document/')) {
        return { ok: true, json: async () => ({ id: 'doc-py-1', title: 'main', session_id: 'sess-1' }) };
      }
      return { ok: true, json: async () => ({}) };
    };

    const domEnv = createDomEnvironment();
    const mod = await loadImportFromDeviceModule({
      document: domEnv.doc,
      FileReader: MockFileReader,
      fetch: mockFetch,
    });

    mod.initImportFromDevice({
      getApiBase: () => '',
      getSessionModule: () => ({ getCurrentSessionId: () => 'sess-test' }),
    });

    mod._importFromDevice();
    const inputEl = domEnv.getInput();
    inputEl.files = [{ name: 'main.py', _content: 'print("hello")' }];
    await inputEl.triggerChange();

    assert.equal(capturedRequests.length, 1);
    assert.equal(capturedRequests[0].body.title, 'main');
    assert.equal(capturedRequests[0].body.language, 'python', 'Recognized .py must send language: python');
  });
});
