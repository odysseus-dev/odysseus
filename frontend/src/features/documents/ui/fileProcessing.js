// frontend/src/features/documents/ui/fileProcessing.js
/**
 * Document file-processing helpers for the Document Library import flow.
 *
 * Responsibilities:
 *   - Lazy-loading third-party parsing dependencies (SheetJS, Mammoth).
 *   - Reading local file contents (plain text, DOCX, spreadsheet).
 *   - Converting Mammoth HTML output to clean Markdown.
 *   - Mapping file extensions to Odysseus document language identifiers.
 *
 * Document Library workflow note:
 *   The primary Document Library import flow delegates DOCX file processing to the
 *   backend `/api/documents/import-docx` endpoint and splits multi-sheet spreadsheets
 *   into individual document entries. The client-side DOCX and spreadsheet parsing
 *   branches in `readFileContent()`, as well as `ensureMammoth()` and `htmlToMarkdown()`,
 *   provide standalone/fallback capability or future direct client-side processing.
 *
 * This module has no dependency on Document Library UI state, modal lifecycle,
 * toast notifications, or application-wide state. Callers supply file objects;
 * this module returns content strings or throws on failure.
 */

// ── Dependency loading ────────────────────────────────────────────────────────

let _xlsxReady = null;

/**
 * Ensure the SheetJS library is available on `window.XLSX`.
 * Returns the same promise for concurrent callers (no duplicate script tags).
 * Rejects if the script fails to load.
 *
 * @returns {Promise<void>}
 */
export function ensureXLSX() {
  if (_xlsxReady) return _xlsxReady;
  if (typeof window !== 'undefined' && window.XLSX) {
    return (_xlsxReady = Promise.resolve());
  }
  _xlsxReady = new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = '/static/lib/xlsx.full.min.js';
    s.onload = resolve;
    s.onerror = () => reject(new Error('Failed to load XLSX library'));
    document.head.appendChild(s);
  });
  return _xlsxReady;
}

let _mammothReady = null;

/**
 * Ensure the Mammoth DOCX-to-HTML library is available on `window.mammoth`.
 * Returns the same promise for concurrent callers (no duplicate script tags).
 * Rejects if the script fails to load.
 *
 * Provides standalone/fallback client-side DOCX parsing capability when backend
 * delegation is unavailable or direct browser processing is desired.
 *
 * @returns {Promise<void>}
 */
export function ensureMammoth() {
  if (_mammothReady) return _mammothReady;
  if (typeof window !== 'undefined' && window.mammoth) {
    return (_mammothReady = Promise.resolve());
  }
  _mammothReady = new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = '/static/lib/mammoth.browser.min.js';
    s.onload = resolve;
    s.onerror = () => reject(new Error('Failed to load DOCX library'));
    document.head.appendChild(s);
  });
  return _mammothReady;
}

// ── HTML-to-Markdown conversion ───────────────────────────────────────────────

/**
 * Convert an HTML string (as produced by Mammoth) to clean Markdown.
 * Uses DOMParser so HTML entities and structure are interpreted correctly.
 * Skips embedded base64 images, which produce unreadable blobs.
 *
 * @param {string} html
 * @returns {string}
 */
export function htmlToMarkdown(html) {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  let md = '';

  function walk(node) {
    if (node.nodeType === 3) { md += node.textContent; return; }
    if (node.nodeType !== 1) return;
    const tag = node.tagName.toLowerCase();
    if (tag === 'h1') { md += '\n# '; walkChildren(node); md += '\n'; }
    else if (tag === 'h2') { md += '\n## '; walkChildren(node); md += '\n'; }
    else if (tag === 'h3') { md += '\n### '; walkChildren(node); md += '\n'; }
    else if (tag === 'h4') { md += '\n#### '; walkChildren(node); md += '\n'; }
    else if (tag === 'strong' || tag === 'b') { md += '**'; walkChildren(node); md += '**'; }
    else if (tag === 'em' || tag === 'i') { md += '*'; walkChildren(node); md += '*'; }
    else if (tag === 'a') { md += '['; walkChildren(node); md += `](${node.href || ''})`; }
    else if (tag === 'br') { md += '\n'; }
    else if (tag === 'p') { md += '\n'; walkChildren(node); md += '\n'; }
    else if (tag === 'ul' || tag === 'ol') { md += '\n'; walkChildren(node); }
    else if (tag === 'li') {
      const parent = node.parentElement?.tagName?.toLowerCase();
      if (parent === 'ol') {
        const idx = Array.from(node.parentElement.children).indexOf(node) + 1;
        md += `${idx}. `;
      } else { md += '- '; }
      walkChildren(node);
      md += '\n';
    }
    else if (tag === 'table') { md += '\n'; convertTable(node); md += '\n'; }
    else if (tag === 'img') {
      // Skip embedded base64 images — they produce huge unreadable blobs
      const src = node.src || '';
      if (!src.startsWith('data:')) {
        md += `![${node.alt || ''}](${src})`;
      } else if (node.alt) {
        md += `*[image: ${node.alt}]*`;
      }
    }
    else { walkChildren(node); }
  }

  function walkChildren(node) { for (const child of node.childNodes) walk(child); }

  function convertTable(table) {
    const rows = table.querySelectorAll('tr');
    rows.forEach((tr, i) => {
      const cells = tr.querySelectorAll('th, td');
      md += '| ' + Array.from(cells).map(c => c.textContent.trim()).join(' | ') + ' |\n';
      if (i === 0) md += '| ' + Array.from(cells).map(() => '---').join(' | ') + ' |\n';
    });
  }

  walkChildren(doc.body);
  return md.replace(/\n{3,}/g, '\n\n').trim();
}

// ── File reading ──────────────────────────────────────────────────────────────

/**
 * Read a local File object and return its content as a string.
 *
 * Workflow note:
 * The primary Document Library import workflow delegates DOCX files to the backend
 * `/api/documents/import-docx` endpoint and splits multi-sheet spreadsheets into
 * separate individual documents. The spreadsheet and DOCX branches below provide
 * standalone/fallback capability for single-string file reading or direct client-side
 * import handling:
 * - `.xlsx` / `.xls` / `.ods`: loaded with SheetJS; each sheet is serialized
 *   as CSV. Multi-sheet workbooks prepend a `# Sheet: <name>` heading before
 *   each sheet's CSV block, joined with blank lines.
 * - `.docx`: converted to HTML via Mammoth, then to Markdown via htmlToMarkdown.
 * - Everything else: read as UTF-8 plain text via FileReader.
 *
 * Throws if a required library fails to load or the file cannot be read.
 *
 * @param {File} file
 * @returns {Promise<string>}
 */
export async function readFileContent(file) {
  const name = file.name.toLowerCase();
  const isSpreadsheet = name.endsWith('.xlsx') || name.endsWith('.xls') || name.endsWith('.ods');
  const isDocx = name.endsWith('.docx');

  if (isSpreadsheet) {
    await ensureXLSX();
    const buf = await file.arrayBuffer();
    const wb = window.XLSX.read(buf, { type: 'array' });
    // Convert each sheet to CSV, join with a header per sheet
    const parts = [];
    for (const sheetName of wb.SheetNames) {
      if (wb.SheetNames.length > 1) parts.push(`# Sheet: ${sheetName}`);
      parts.push(window.XLSX.utils.sheet_to_csv(wb.Sheets[sheetName]));
    }
    return parts.join('\n\n');
  }

  if (isDocx) {
    await ensureMammoth();
    const buf = await file.arrayBuffer();
    const result = await window.mammoth.convertToHtml({ arrayBuffer: buf });
    return htmlToMarkdown(result.value);
  }

  // Plain text
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsText(file);
  });
}

// ── Extension and language mapping ────────────────────────────────────────────

/**
 * Common source code and text file extension mappings shared across device
 * and document library import workflows.
 */
export const COMMON_EXT_TO_LANG = Object.freeze({
  '.py': 'python', '.js': 'javascript', '.ts': 'typescript',
  '.html': 'html', '.htm': 'html', '.css': 'css', '.md': 'markdown',
  '.json': 'json', '.yml': 'yaml', '.yaml': 'yaml', '.sh': 'bash',
  '.bash': 'bash', '.sql': 'sql', '.rs': 'rust', '.go': 'go',
  '.java': 'java', '.c': 'c', '.cpp': 'cpp', '.h': 'c', '.hpp': 'cpp',
  '.rb': 'ruby', '.php': 'php', '.xml': 'xml',
  '.toml': 'toml', '.ini': 'ini', '.txt': '', '.log': '',
  '.csv': 'csv', '.tsv': 'csv', '.jsx': 'javascript', '.tsx': 'typescript',
});

/**
 * File extension to language mapping for direct device import (importFromDevice.js).
 * Preserves the exact original classification policy of importFromDevice.js.
 */
export const DEVICE_IMPORT_EXT_TO_LANG = COMMON_EXT_TO_LANG;

/**
 * File extension to Odysseus document language identifier for the Document Library.
 * Extends COMMON_EXT_TO_LANG with additional Document-Library-specific web/config/office mappings.
 * Extensions not present in this map are treated as plain text (language = null).
 */
export const EXT_TO_LANG = Object.freeze({
  ...COMMON_EXT_TO_LANG,
  '.cfg': 'ini', '.conf': 'ini', '.env': '',
  '.vue': 'html', '.svelte': 'html',
  '.scss': 'css', '.sass': 'css', '.less': 'css',
  '.xlsx': 'csv', '.xls': 'csv', '.ods': 'csv',
  '.docx': 'docx', '.doc': 'markdown',
});

/**
 * File extensions treated as spreadsheets (require SheetJS for parsing).
 */
export const SPREADSHEET_EXTENSIONS = Object.freeze(['.xlsx', '.xls', '.ods']);
