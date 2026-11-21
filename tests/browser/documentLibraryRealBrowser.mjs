/**
 * Real browser validation of Odysseus Document Library in headless Chromium via Playwright.
 *
 * Verifies:
 * 1. Document Library opens correctly.
 * 2. Plain-text import works.
 * 3. Spreadsheet import works, including multiple worksheets (SheetJS parsing).
 * 4. DOCX import works (backend import-docx multipart endpoint).
 * 5. Existing PDF import delegation works (backend import-pdf multipart endpoint).
 * 6. Imported documents appear in the library grid.
 * 7. Existing document selection and interactions still work.
 * 8. No unresolved module imports or browser console errors occur.
 */

import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const ROOT = path.resolve(import.meta.dirname, '../..');
const CHROME_PATH = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || '/usr/bin/google-chrome';

// In-memory documents store for mock API
const documents = [];
let nextId = 1;

function makeMockDoc(title, language, content, extra = {}) {
  const now = new Date().toISOString();
  return {
    id: `doc-${nextId++}`,
    title,
    language: language || '',
    current_content: content,
    created_at: now,
    updated_at: now,
    session_id: null,
    session_name: null,
    ...extra
  };
}

// Start mock server serving static files and minimal document APIs
function startServer() {
  const server = http.createServer(async (req, res) => {
    console.log("SERVER REQ:", req.method, req.url);
    const parsedUrl = new URL(req.url, 'http://127.0.0.1');
    const pathname = parsedUrl.pathname;

    // API endpoints
    if (pathname === '/api/documents/library') {
      console.log("SERVER: /api/documents/library called, count:", documents.length);
      const languages = {};
      for (const d of documents) {
        const lang = d.language || 'text';
        languages[lang] = (languages[lang] || 0) + 1;
      }
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({
        documents: [...documents],
        total: documents.length,
        languages: languages,
        session_count: 0
      }));
      return;
    }

    if (pathname === '/api/document' && req.method === 'POST') {
      let body = '';
      for await (const chunk of req) body += chunk;
      const data = JSON.parse(body);
      const doc = makeMockDoc(data.title, data.language, data.content);
      documents.push(doc);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ id: doc.id, title: doc.title, success: true }));
      return;
    }

    if (pathname === '/api/documents/import-pdf' && req.method === 'POST') {
      // Drain multipart body
      for await (const _ of req) {}
      const doc = makeMockDoc('contract.pdf', 'pdf', '', { doc_kind: 'pdf' });
      documents.push(doc);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ id: doc.id, doc_id: doc.id, success: true }));
      return;
    }

    if (pathname === '/api/tasks/notification-logs') {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end("{}");
      return;
    }

    if (pathname === '/api/documents/import-docx' && req.method === 'POST') {
      // Drain multipart body
      for await (const _ of req) {}
      const doc = makeMockDoc('project-proposal.docx', 'docx', 'Converted docx text', { doc_kind: 'docx' });
      documents.push(doc);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ id: doc.id, doc_id: doc.id, success: true }));
      return;
    }

    // Static file serving from repo root
    
    if (pathname.startsWith('/api/')) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end('{}');
      return;
    }
    let filePath = path.join(ROOT, pathname);
    console.log("SERVE FILE:", filePath, fs.existsSync(filePath));

    if (pathname === '/' || pathname === '/index.html') {
      // Deliver minimal test harness host
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(`<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Document Library Test Host</title>
  <link rel="stylesheet" href="/static/css/00-tokens.css">
  <link rel="stylesheet" href="/static/css/05-documents.css">
<link rel="icon" href="data:,">
</head>
<body>
  <div id="app">
    <button id="tool-doclib-btn" type="button">Doclib</button>
  <div id="toast" class="toast"></div>
  </div>
</body>
</html>`);
      return;
    }

    if (fs.existsSync(filePath) && fs.statSync(filePath).isFile()) {
      const ext = path.extname(filePath);
      const mimeTypes = {
        '.js': 'application/javascript',
        '.mjs': 'application/javascript',
        '.css': 'text/css',
        '.html': 'text/html',
        '.json': 'application/json',
        '.svg': 'image/svg+xml',
        '.png': 'image/png'
      };
      res.writeHead(200, { 'Content-Type': mimeTypes[ext] || 'application/octet-stream' });
      res.end(fs.readFileSync(filePath));
      return;
    }

    res.writeHead(404, { 'Content-Type': 'text/plain' });
    res.end('Not found: ' + pathname);
  });

  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      resolve({ server, origin: `http://127.0.0.1:${port}` });
    });
  });
}

async function runValidation() {
  const { server, origin } = await startServer();
  console.log(`Test server running at ${origin}`);

  const browser = await chromium.launch({
    executablePath: CHROME_PATH,
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
  });

  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });

  const consoleErrors = [];
  const pageErrors = [];
  page.on('console', msg => {
    if (msg.type() === 'error') {
      console.log("CONSOLE ERROR TEXT:", msg.text(), "LOCATION:", msg.location());
      // Ignore expected 404s for favicon / non-critical css
      if (msg.text().includes("favicon")) return;
      if (msg.text().includes("12-documents.css")) return;
      consoleErrors.push(msg.text());
    }
  });
  page.on('pageerror', err => {
    pageErrors.push(err.message || String(err));
  });

  try {
    await page.goto(origin + '/');

    // 1. Initialize and open Document Library
    console.log('1. Initializing and opening Document Library...');
    await page.evaluate(async () => {
      const docLib = await import('/static/js/documentLibrary.js');
      window.docLib = docLib;
      docLib.initLibrary({
        apiBase: '',
        esc: s => String(s || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
        getDocs: () => new Map(),
        isOpen: () => false,
        createDocument: () => {},
        newDocument: () => {},
        loadDocument: () => {},
        prepareDocumentOpen: () => {},
        switchToDoc: () => {},
        openPanel: () => {},
        addDocToTabs: () => {},
        syncDocIndicator: () => {},
      });
      docLib.openLibrary();
    });

    // Check modal exists in DOM
    const modal = page.locator('#doclib-modal');
    await modal.waitFor({ state: 'visible', timeout: 5000 });
    assert.equal(await page.evaluate(() => window.docLib.isLibraryOpen()), true, 'Library should report open');
    console.log('✓ Document Library opened successfully');

    // 2. Plain-text import
    console.log('2. Testing plain-text file import...');
    await page.evaluate(async () => {
      const input = document.getElementById('doclib-file-input');
      const file = new File(['Hello plain text content'], 'test-plain.txt', { type: 'text/plain' });
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });

    // Wait for document to appear in documents array
    await page.waitForFunction(() => {
      const card = document.querySelector('.doclib-card[data-doc-id]');
      return card !== null;
    }, { timeout: 5000 });

    const plainCard = page.locator('.doclib-card:has-text("test-plain")');
    assert.ok(await plainCard.count() >= 1, 'Plain text document card should be rendered');
    console.log('✓ Plain-text import verified');

    // 3. Multi-sheet Spreadsheet import (XLSX)
    console.log('3. Testing spreadsheet (multi-sheet XLSX) import...');
    // Create a real multi-sheet xlsx arraybuffer using window.XLSX after lazy-loading
    await page.evaluate(async () => {
      const { ensureXLSX } = await import('/static/js/document/ui/fileProcessing.js');
      await ensureXLSX();
      const ws1 = window.XLSX.utils.aoa_to_sheet([['col1', 'col2'], ['A1', 'B1']]);
      const ws2 = window.XLSX.utils.aoa_to_sheet([['colX', 'colY'], ['X1', 'Y1']]);
      const wb = window.XLSX.utils.book_new();
      window.XLSX.utils.book_append_sheet(wb, ws1, 'Quarter1');
      window.XLSX.utils.book_append_sheet(wb, ws2, 'Quarter2');
      const wbout = window.XLSX.write(wb, { bookType: 'xlsx', type: 'array' });
      const file = new File([wbout], 'financials.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
      const input = document.getElementById('doclib-file-input');
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });

    await page.waitForFunction(() => {
      const cards = document.querySelectorAll('.doclib-card');
      const texts = Array.from(cards).map(c => c.textContent);
      return texts.some(t => t.includes('Quarter1')) && texts.some(t => t.includes('Quarter2'));
    }, { timeout: 5000 });

    console.log('✓ Multi-sheet spreadsheet import verified (both sheets created)');

    // 4. DOCX import delegation
    console.log('4. Testing DOCX import delegation...');
    await page.evaluate(async () => {
      const input = document.getElementById('doclib-file-input');
      const file = new File(['fake-docx-bytes'], 'project-proposal.docx', { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' });
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });

    await page.waitForFunction(() => {
      const cards = document.querySelectorAll('.doclib-card');
      return Array.from(cards).some(c => c.textContent.includes('project-proposal'));
    }, { timeout: 5000 });
    console.log('✓ DOCX import delegation verified');

    // 5. PDF import delegation
    console.log('5. Testing PDF import delegation...');
    await page.evaluate(async () => {
      const input = document.getElementById('doclib-file-input');
      const file = new File(['%PDF-1.4 mock bytes'], 'contract.pdf', { type: 'application/pdf' });
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });

    await page.waitForFunction(() => {
      const cards = document.querySelectorAll('.doclib-card');
      return Array.from(cards).some(c => c.textContent.includes('contract'));
    }, { timeout: 5000 });
    console.log('✓ PDF import delegation verified');

    // 6. Selection and interactions in Document Library
    console.log('6. Testing document selection and interactions...');
    const selectBtn = page.locator('#doclib-select-btn');
    await selectBtn.click();
    assert.equal(await page.evaluate(() => !document.getElementById('doclib-bulk-bar')?.classList.contains('hidden')), true, 'Bulk action bar should not have hidden class');

    // Select first card
    const firstCheckbox = page.locator('.memory-select-cb').first();
    await firstCheckbox.check();

    const selectedCount = page.locator('#doclib-selected-count');
    assert.equal(await selectedCount.textContent(), '1 Selected');
    console.log('✓ Document selection interaction verified');

    // 7. Verify close library
    console.log('7. Testing library close...');
    await page.evaluate(() => window.docLib.closeLibrary());
    await page.waitForTimeout(300);
    assert.equal(await page.evaluate(() => window.docLib.isLibraryOpen()), false, 'Library should report closed');
    assert.equal(await page.locator('#doclib-modal').count(), 0, 'Modal should be removed from DOM');
    console.log('✓ Document Library close verified');

    // 8. Console errors check
    assert.equal(consoleErrors.length, 0, `Browser console errors occurred:\n${consoleErrors.join('\n')}`);
    assert.equal(pageErrors.length, 0, `Page errors occurred:\n${pageErrors.join('\n')}`);
    console.log('✓ No unresolved module imports or browser console errors occurred');

    console.log('\n========================================');
    console.log('ALL REAL BROWSER VALIDATIONS PASSED');
    console.log('========================================');
  } finally {
    await browser.close();
    server.close();
  }
}

runValidation().catch(err => {
  console.error('Browser validation failed:', err);
  process.exit(1);
});
