import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { chromium } from 'playwright';

test('Chat Photo Editor Action Integrity and Turn Scoping', async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const serverResponses = new Map();
    const recordedUploadRequests = [];

    await page.route('http://photo-test.local/**', async (route) => {
      const url = new URL(route.request().url());
      const path = url.pathname;

      if (path === '/') {
        return route.fulfill({
          contentType: 'text/html',
          body: `<!DOCTYPE html>
          <html>
            <body>
              <div id="toast"></div>
              <div id="chat-history"></div>
            </body>
          </html>`,
        });
      }

      if (path.startsWith('/api/upload/')) {
        const id = path.replace('/api/upload/', '');
        const method = route.request().method();
        recordedUploadRequests.push({ id, method });
        const status = serverResponses.get(id) || 200;
        if (status === 200) {
          return route.fulfill({
            status: 200,
            headers: {
              'Content-Type': 'image/png',
              'Content-Length': '16',
              'X-Content-Type-Options': 'nosniff',
            },
            body: method === 'HEAD' ? Buffer.alloc(0) : Buffer.from('fake-image-bytes'),
          });
        }
        return route.fulfill({
          status,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ detail: 'Upload error or forbidden' }),
        });
      }

      try {
        const source = await readFile(new URL('..' + path, import.meta.url), 'utf8');
        return route.fulfill({ contentType: 'text/javascript', body: source });
      } catch (err) {
        return route.abort();
      }
    });

    await page.goto('http://photo-test.local/');

    const results = await page.evaluate(async () => {
      const box = document.getElementById('chat-history');

      // Import chat.js to test hooks
      await import('/static/js/chat.js');
      const hooks = window.__chatPhotoEditorTestHooks;
      if (!hooks) throw new Error('Test hooks not exposed on window.__chatPhotoEditorTestHooks');

      const { _isAuthorizedImageUrl, _findOriginatingUserMessage, _attachedImageForTurn, renderPhotoEditorApplyButton } = hooks;

      // --- Scenario 1: Authorization checks ---
      const authValidUpload = _isAuthorizedImageUrl('/api/upload/img_123');
      const authValidGallery = _isAuthorizedImageUrl('/api/gallery/img_123');
      const authBlob = _isAuthorizedImageUrl('blob:http://photo-test.local/550e8400-e29b-41d4-a716-446655440000');
      const authCrossOriginBlob = _isAuthorizedImageUrl('blob:https://evil.com/550e8400-e29b-41d4-a716-446655440000');
      const authMalformedBlob = _isAuthorizedImageUrl('blob:javascript:alert(1)');
      const pixel = document.createElement('canvas'); pixel.width = pixel.height = 1;
      const authDataPng = _isAuthorizedImageUrl(pixel.toDataURL('image/png'));
      const authDataJpeg = _isAuthorizedImageUrl(pixel.toDataURL('image/jpeg'));
      const authDataWebp = _isAuthorizedImageUrl(pixel.toDataURL('image/webp'));
      const authDataGif = _isAuthorizedImageUrl('data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7');
      const authDataSvg = _isAuthorizedImageUrl('data:image/svg+xml;base64,PHN2Zz4=');
      const authDataHtml = _isAuthorizedImageUrl('data:text/html;base64,PGgxPlRlc3Q=');
      const authDataMalformed = _isAuthorizedImageUrl('data:image/png;base64,invalid!@#$');
      const authDataOversized = _isAuthorizedImageUrl('data:image/png;base64,' + 'A'.repeat(11 * 1024 * 1024));
      const authMaliciousDomain = _isAuthorizedImageUrl('https://evil.com/api/upload/img_123');
      const authSchemeRelative = _isAuthorizedImageUrl('//evil.com/api/upload/img_123');
      const authJavascript = _isAuthorizedImageUrl('javascript:alert(1)');
      const authDirectoryTraversal = _isAuthorizedImageUrl('/api/upload/../../../etc/passwd');
      const authEncodedTraversal = _isAuthorizedImageUrl('/api/upload/%2e%2e%2f');

      // --- Scenario 2: Multi-turn isolation ---
      // Build Turn 1: User attaches img_turn1
      const user1 = document.createElement('div');
      user1.className = 'msg msg-user';
      user1.innerHTML = '<div class="body">Turn 1 text</div><div class="attach-image-preview" data-file-id="img_turn1"><img src="/api/upload/img_turn1" /></div>';
      box.appendChild(user1);

      const ai1 = document.createElement('div');
      ai1.className = 'msg msg-ai round-holder';
      ai1.innerHTML = '<div class="body">Turn 1 AI reply</div>';
      box.appendChild(ai1);

      // Build Turn 2: User attaches NO photo (just text)
      const user2 = document.createElement('div');
      user2.className = 'msg msg-user';
      user2.innerHTML = '<div class="body">Turn 2 text without photo</div>';
      box.appendChild(user2);

      const ai2 = document.createElement('div');
      ai2.className = 'msg msg-ai round-holder';
      ai2.innerHTML = '<div class="body">Turn 2 AI reply</div>';
      box.appendChild(ai2);

      // Build Turn 3: User attaches img_turn3
      const user3 = document.createElement('div');
      user3.className = 'msg msg-user';
      user3.innerHTML = '<div class="body">Turn 3 text</div><div class="attach-card" data-file-id="img_turn3"><img src="/api/upload/img_turn3" /></div>';
      box.appendChild(user3);

      const ai3 = document.createElement('div');
      ai3.className = 'msg msg-ai round-holder';
      ai3.innerHTML = '<div class="body">Turn 3 AI reply</div>';
      box.appendChild(ai3);

      // Resolve images for each turn
      const turn1Resolved = _attachedImageForTurn(ai1, { kind: 'open_and_apply', prompt: 'edit turn 1' });
      const turn2Resolved = _attachedImageForTurn(ai2, { kind: 'open_and_apply', prompt: 'edit turn 2' });
      const turn3Resolved = _attachedImageForTurn(ai3, { kind: 'open_and_apply', prompt: 'edit turn 3' });

      // Action with explicit imageId should honor explicit id
      const explicitActionResolved = _attachedImageForTurn(ai2, { kind: 'open_and_apply', imageId: 'explicit_999' });
      const filenameResolved = _attachedImageForTurn(ai2, { kind: 'open_and_apply', imageId: 'explicit_999.png' });
      const invalidExplicitResolved = _attachedImageForTurn(ai1, { kind: 'open_and_apply', imageUrl: 'data:image/png;base64,=' });

      // Action on container with no user message above
      const detachedAi = document.createElement('div');
      detachedAi.className = 'msg msg-ai';
      const detachedResolved = _attachedImageForTurn(detachedAi, { kind: 'open_and_apply' });

      return {
        authValidUpload,
        authValidGallery,
        authBlob,
        authCrossOriginBlob,
        authMalformedBlob,
        authDataPng,
        authDataJpeg,
        authDataWebp,
        authDataGif,
        authDataSvg,
        authDataHtml,
        authDataMalformed,
        authDataOversized,
        authMaliciousDomain,
        authSchemeRelative,
        authJavascript,
        authDirectoryTraversal,
        authEncodedTraversal,
        turn1Resolved,
        turn2Resolved,
        turn3Resolved,
        explicitActionResolved,
        filenameResolved,
        invalidExplicitResolved,
        detachedResolved,
      };
    });

    // Verify Scenario 1: Authorization
    assert.equal(results.authValidUpload, true, 'Valid same-origin upload is authorized');
    assert.equal(results.authValidGallery, true, 'Valid same-origin gallery is authorized');
    assert.equal(results.authBlob, false, 'Same-origin alone does not authorize a blob URL');
    assert.equal(results.authCrossOriginBlob, false, 'Cross-origin blob URL is rejected');
    assert.equal(results.authMalformedBlob, false, 'Malformed blob URL is rejected');
    assert.equal(results.authDataPng, true, 'PNG data URL is authorized');
    assert.equal(results.authDataJpeg, true, 'JPEG data URL is authorized');
    assert.equal(results.authDataWebp, true, 'WebP data URL is authorized');
    assert.equal(results.authDataGif, true, 'GIF data URL is authorized');
    assert.equal(results.authDataSvg, false, 'SVG data URL is rejected');
    assert.equal(results.authDataHtml, false, 'HTML data URL is rejected');
    assert.equal(results.authDataMalformed, false, 'Malformed data URL is rejected');
    assert.equal(results.authDataOversized, false, 'Oversized data URL > 10MB is rejected');
    assert.equal(results.authMaliciousDomain, false, 'External malicious domain rejected');
    assert.equal(results.authSchemeRelative, false, 'Scheme-relative URL rejected');
    assert.equal(results.authJavascript, false, 'Javascript scheme rejected');
    assert.equal(results.authDirectoryTraversal, false, 'Directory traversal in path rejected');
    assert.equal(results.authEncodedTraversal, false, 'Encoded directory traversal in path rejected');

    // Verify Scenario 2: Multi-turn isolation
    assert.equal(results.turn1Resolved, '/api/upload/img_turn1', 'Turn 1 AI correctly resolves Turn 1 user image');
    assert.equal(results.turn2Resolved, null, 'Turn 2 AI resolves NULL because Turn 2 had no attached image');
    assert.equal(results.turn3Resolved, '/api/upload/img_turn3', 'Turn 3 AI correctly resolves Turn 3 user image');
    assert.equal(results.explicitActionResolved, '/api/upload/explicit_999', 'Explicit action imageId honored');
    assert.equal(results.filenameResolved, '/api/upload/explicit_999.png', 'Canonical filenames keep their extensions');
    assert.equal(results.invalidExplicitResolved, null, 'Invalid explicit data never falls back to a different image');
    assert.equal(results.detachedResolved, null, 'Detached AI container without user message resolves to null');

    // --- Scenario 3: Button click lifecycle (deleted upload, rapid click, open apply) ---
    const clickResults = await page.evaluate(async () => {
      const hooks = window.__chatPhotoEditorTestHooks;
      const { renderPhotoEditorApplyButton } = hooks;

      let editorCalls = [];
      window.gePhotoEditorApply = async (opts) => {
        editorCalls.push(opts);
        await new Promise((r) => setTimeout(r, 50));
      };

      let errorsShown = [];
      const fakeUi = {
        showError(msg) { errorsShown.push(msg); },
        showToast(msg) {},
      };

      // Case A: Missing attachment on Turn 2
      const ai2 = document.querySelectorAll('.msg-ai')[1];
      renderPhotoEditorApplyButton(ai2, { kind: 'open_and_apply', prompt: 'test turn 2' });
      const btn2 = ai2.querySelector('.chat-open-in-editor-btn');
      btn2.click();
      await new Promise((r) => setTimeout(r, 20));
      const btn2DisabledAfterMissing = btn2.disabled;

      // Case B: Deleted upload (server returns 404)
      const userDel = document.createElement('div');
      userDel.className = 'msg msg-user';
      userDel.innerHTML = '<div class="attach-image-preview" data-file-id="img_deleted"><img src="/api/upload/img_deleted" /></div>';
      document.getElementById('chat-history').appendChild(userDel);

      const aiDel = document.createElement('div');
      aiDel.className = 'msg msg-ai round-holder';
      document.getElementById('chat-history').appendChild(aiDel);

      renderPhotoEditorApplyButton(aiDel, { kind: 'open_and_apply', prompt: 'test deleted' });
      const btnDel = aiDel.querySelector('.chat-open-in-editor-btn');

      return {
        hasBtn2: !!btn2,
        btn2DisabledAfterMissing,
      };
    });

    assert.equal(clickResults.hasBtn2, true);
    assert.equal(clickResults.btn2DisabledAfterMissing, false, 'Button re-enabled after missing attachment');

    // Set server status 404 for img_deleted
    serverResponses.set('img_deleted', 404);

    const deletedResults = await page.evaluate(async () => {
      const aiDel = document.querySelectorAll('.msg-ai')[3];
      const btnDel = aiDel.querySelector('.chat-open-in-editor-btn');
      btnDel.click();
      await new Promise((r) => setTimeout(r, 50));
      return {
        disabled: btnDel.disabled,
      };
    });

    assert.equal(deletedResults.disabled, false, 'Button re-enabled when server returns 404');

    // Case C: Valid attachment on Turn 1 click launches editor
    const validResults = await page.evaluate(async () => {
      let editorCalls = [];
      window.gePhotoEditorApply = async (opts) => {
        editorCalls.push(opts);
      };

      const ai1 = document.querySelectorAll('.msg-ai')[0];
      const { renderPhotoEditorApplyButton } = window.__chatPhotoEditorTestHooks;
      renderPhotoEditorApplyButton(ai1, { kind: 'open_and_apply', prompt: 'make b&w' });
      const btn1 = ai1.querySelector('.chat-open-in-editor-btn');

      // Rapid clicks: trigger multiple synchronous clicks
      btn1.click();
      btn1.click();
      btn1.click();

      await new Promise((r) => setTimeout(r, 50));

      return {
        editorCallsCount: editorCalls.length,
        editorCall: editorCalls[0],
      };
    });

    assert.equal(validResults.editorCallsCount, 1, 'Rapid clicks deduplicated, editor called exactly once');
    assert.equal(validResults.editorCall.imageUrl, '/api/upload/img_turn1', 'Editor opened with Turn 1 image');
    assert.equal(validResults.editorCall.prompt, 'make b&w');

    // Case D: Verify HEAD probe was used for verification
    const headProbeObserved = recordedUploadRequests.some((r) => r.id === 'img_turn1' && r.method === 'HEAD');
    assert.equal(headProbeObserved, true, 'Chat-to-editor preflight executed an authorized HEAD probe');

    // Case E: Unauthorized upload returns 403, surfaces error and re-enables button
    serverResponses.set('img_forbidden', 403);
    const forbiddenResults = await page.evaluate(async () => {
      const userForbidden = document.createElement('div');
      userForbidden.className = 'msg msg-user';
      userForbidden.innerHTML = '<div class="attach-image-preview" data-file-id="img_forbidden"><img src="/api/upload/img_forbidden" /></div>';
      document.getElementById('chat-history').appendChild(userForbidden);

      const aiForbidden = document.createElement('div');
      aiForbidden.className = 'msg msg-ai round-holder';
      document.getElementById('chat-history').appendChild(aiForbidden);

      const { renderPhotoEditorApplyButton } = window.__chatPhotoEditorTestHooks;
      renderPhotoEditorApplyButton(aiForbidden, { kind: 'open_and_apply', prompt: 'forbidden test' });
      const btnForb = aiForbidden.querySelector('.chat-open-in-editor-btn');
      btnForb.click();
      await new Promise((r) => setTimeout(r, 50));

      return {
        disabled: btnForb.disabled,
      };
    });

    assert.equal(forbiddenResults.disabled, false, 'Button re-enabled when server returns 403');
  } finally {
    await browser.close();
  }
});

test('editor actions validate raster bytes, dimensions, decode and reject all unverified blobs before launch', async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const apiRequests = [];
    await page.route('http://raster.local/**', async route => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/') return route.fulfill({ contentType: 'text/html', body: '<div id="chat-history"></div>' });
      if (path === '/api/gallery/owned') { apiRequests.push(path); return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ id: 'owned', url: '/api/generated-image/owned.png' }) }); }
      if (path === '/api/gallery/foreign') { apiRequests.push(path); return route.fulfill({ status: 404, body: '' }); }
      if (path.startsWith('/api/upload/')) { apiRequests.push(path); return route.fulfill({ status: 200, headers: { 'Content-Type': 'image/png' }, body: '' }); }
      if (path.startsWith('/api/')) return route.fulfill({ contentType: 'application/json', body: '{}' });
      try { return route.fulfill({ contentType: 'text/javascript', body: await readFile(new URL('..' + path, import.meta.url), 'utf8') }); }
      catch { return route.fulfill({ status: 404, body: '' }); }
    });
    await page.goto('http://raster.local/');
    const results = await page.evaluate(async () => {
      await import('/static/js/chat.js');
      const { renderPhotoEditorApplyButton } = window.__chatPhotoEditorTestHooks;
      const canvas = document.createElement('canvas'); canvas.width = canvas.height = 2;
      canvas.getContext('2d').fillRect(0, 0, 2, 2);
      const png = canvas.toDataURL('image/png');
      const bytes = Uint8Array.from(atob(png.split(',')[1]), ch => ch.charCodeAt(0));
      const encode = bytes => { let text = ''; for (const byte of bytes) text += String.fromCharCode(byte); return 'data:image/png;base64,' + btoa(text); };
      const headerDimensions = (w, h) => { const b = bytes.slice(); const v = new DataView(b.buffer); v.setUint32(16, w); v.setUint32(20, h); return encode(b); };
      const invalidDecode = bytes.slice(0, 33); // valid signature/dimensions but no pixel data
      const ownBlob = URL.createObjectURL(new Blob([bytes], { type: 'image/png' }));
      const revoked = URL.createObjectURL(new Blob([bytes], { type: 'image/png' })); URL.revokeObjectURL(revoked);
      // This action intentionally has no blob registration API. Even application-created
      // object URLs must use their authorized server attachment or raster data source.
      const cases = [
        ['upload', '/api/upload/owned', true], ['gallery', '/api/gallery/owned', true],
        ['png', png, true], ['jpeg', canvas.toDataURL('image/jpeg'), true], ['webp', canvas.toDataURL('image/webp'), true],
        ['gif', 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7', true],
        ['padding-one', 'data:image/png;base64,=', false], ['padding-length', 'data:image/png;base64,A', false],
        ['padding-middle', 'data:image/png;base64,AA=AA', false], ['padding-excess', 'data:image/png;base64,AAAA====', false], ['foreign-gallery', '/api/gallery/foreign', false],
        ['pad-bits', 'data:image/png;base64,AB==', false], ['bytes', 'data:image/png;base64,AAAA', false],
        ['mismatch', png.replace('image/png', 'image/jpeg'), false], ['truncated-image', encode(invalidDecode), false],
        ['svg', 'data:image/svg+xml;base64,PHN2Zy8+', false], ['html', 'data:text/html;base64,PGgxLz4=', false],
        ['svg-disguised', 'data:image/png;base64,PHN2Zy8+', false],
        ['foreign-blob', 'blob:https://foreign.invalid/deadbeef', false], ['arbitrary-blob', 'blob:http://raster.local/deadbeef', false],
        ['application-created-blob', ownBlob, false], ['revoked-blob', revoked, false],
        ['oversized-url', 'data:image/png;base64,' + 'A'.repeat(10 * 1024 * 1024), false],
        ['oversized-decoded-bytes', 'data:image/png;base64,' + 'A'.repeat(4 * Math.ceil((7 * 1024 * 1024 + 3) / 3)), false],
        ['dimension', headerDimensions(8193, 1), false], ['pixel-budget', headerDimensions(8192, 8192), false],
        ['scheme-relative', '//raster.local/api/upload/owned', false], ['encoded-traversal', '/api/upload/%2e%2e/owned', false],
      ];
      const oldDecode = window.createImageBitmap; let decodes = 0;
      window.createImageBitmap = (...args) => { decodes++; return oldDecode(...args); };
      let launches = []; window.gePhotoEditorApply = async opts => launches.push(opts.imageUrl);
      const output = [];
      for (const [name, url, accepted] of cases) {
        const host = document.createElement('div'); document.body.append(host);
        renderPhotoEditorApplyButton(host, { kind: 'open_and_apply', imageUrl: url, prompt: 'synthetic edit' });
        const btn = host.querySelector('button'); const before = launches.length; const decodeBefore = decodes;
        btn.click(); btn.click(); btn.click();
        while (btn.disabled) await new Promise(resolve => setTimeout(resolve, 5));
        output.push({ name, accepted, launches: launches.length - before, decodes: decodes - decodeBefore }); host.remove();
      }
      // Cache reuse across repeated activation of the same complete inline image.
      const host = document.createElement('div'); document.body.append(host);
      renderPhotoEditorApplyButton(host, { kind: 'open_and_apply', imageUrl: png, prompt: 'repeat' });
      const btn = host.querySelector('button'); const before = decodes;
      for (let i = 0; i < 3; i++) { btn.click(); while (btn.disabled) await new Promise(resolve => setTimeout(resolve, 5)); }
      const cachedDecodes = decodes - before;
      URL.revokeObjectURL(ownBlob);
      return { cases: output, cachedDecodes, launches };
    });
    assert.deepEqual(results.cases.filter(item => item.launches !== (item.accepted ? 1 : 0)).map(item => item.name), [], 'every raster authorization case must match');
    for (const name of ['dimension', 'pixel-budget', 'oversized-url', 'oversized-decoded-bytes', 'bytes', 'mismatch']) {
      assert.equal(results.cases.find(item => item.name === name).decodes, 0, name + ' rejected before browser allocation');
    }
    assert.equal(results.cases.find(item => item.name === 'truncated-image').decodes, 1, 'actual decoder rejects incomplete image');
    assert.equal(results.cachedDecodes, 1, 'same image only decoded once for repeated activation');
    assert.deepEqual(apiRequests, ['/api/upload/owned', '/api/gallery/owned', '/api/gallery/foreign']);
    assert.ok(results.launches.includes('http://raster.local/api/generated-image/owned.png'));
  } finally { await browser.close(); }
});
