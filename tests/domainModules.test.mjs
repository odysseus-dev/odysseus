import test from 'node:test';
import assert from 'node:assert/strict';

import {
  AUTO_DETECT_DELAY,
  AUTO_DETECT_MIN_CHARS,
  AUTO_DETECT_MIN_RELEVANCE,
  AUTO_DETECT_SAMPLE_SIZE,
  HLJS_TO_DROPDOWN,
  _DOC_SAVE_STATE,
  _buildDiffSummary,
  _esc,
  _escHtml,
  _hasRichHtmlElement,
  _hasViewToggle,
  _htmlToPlainText,
  _isDocxLang,
  _isMarkdownImageFile,
  _isRenderLang,
  _isRichTextLang,
  _looksLikeMarkdown,
  _markdownImageAlt,
} from '../frontend/src/features/documents/domain/documentKinds.js';

import {
  _ATTACH_RE,
  _accountCanSend,
  _buildEmailContent,
  _buildEmailContentFromFields,
  _decodeBase64EmailWrapper,
  _emailQuoteMarkerMatch,
  _looksLikeWrappedEmailContent,
  _parseEmailHeader,
} from '../frontend/src/features/documents/domain/emailMessage.js';

import {
  _RICH_BLOCK_INPUT_RULES,
  _RICH_COLOR_PALETTE,
  _RICH_FONT_SIZE_PX,
  _RICH_SLASH_COMMANDS,
  _isRichInlineCodeMarker,
  _normalizeRichLinkUrl,
} from '../frontend/src/features/documents/domain/richTextRules.js';

// ============================================================================
// 1. domain/documentKinds.js
// ============================================================================

test('documentKinds constants define expected values and mappings', () => {
  assert.equal(AUTO_DETECT_DELAY, 500);
  assert.equal(AUTO_DETECT_MIN_CHARS, 30);
  assert.equal(AUTO_DETECT_MIN_RELEVANCE, 8);
  assert.equal(AUTO_DETECT_SAMPLE_SIZE, 2000);

  assert.equal(HLJS_TO_DROPDOWN.python, 'python');
  assert.equal(HLJS_TO_DROPDOWN.javascript, 'javascript');
  assert.equal(HLJS_TO_DROPDOWN.typescript, 'typescript');
  assert.equal(HLJS_TO_DROPDOWN.xml, 'html');
  assert.equal(HLJS_TO_DROPDOWN.html, 'html');
  assert.equal(HLJS_TO_DROPDOWN.css, 'css');
  assert.equal(HLJS_TO_DROPDOWN.markdown, 'markdown');
  assert.equal(HLJS_TO_DROPDOWN.json, 'json');
  assert.equal(HLJS_TO_DROPDOWN.yaml, 'yaml');
  assert.equal(HLJS_TO_DROPDOWN.bash, 'bash');
  assert.equal(HLJS_TO_DROPDOWN.shell, 'bash');
  assert.equal(HLJS_TO_DROPDOWN.sql, 'sql');
  assert.equal(HLJS_TO_DROPDOWN.rust, 'rust');
  assert.equal(HLJS_TO_DROPDOWN.go, 'go');
  assert.equal(HLJS_TO_DROPDOWN.java, 'java');
  assert.equal(HLJS_TO_DROPDOWN.c, 'c');
  assert.equal(HLJS_TO_DROPDOWN.cpp, 'cpp');
  assert.equal(HLJS_TO_DROPDOWN.csv, 'csv');

  assert.deepEqual(_DOC_SAVE_STATE.dirty, { label: 'Unsaved', title: 'Changes are waiting to be saved' });
  assert.deepEqual(_DOC_SAVE_STATE.saving, { label: 'Saving', title: 'Saving document' });
  assert.deepEqual(_DOC_SAVE_STATE.saved, { label: 'Saved', title: 'All changes saved' });
  assert.deepEqual(_DOC_SAVE_STATE.error, { label: 'Save failed', title: 'Document could not be saved' });
});

test('escaping functions escape HTML entities and handle null/edge inputs', () => {
  assert.equal(_escHtml(null), '');
  assert.equal(_escHtml(undefined), '');
  assert.equal(_escHtml(''), '');
  assert.equal(_escHtml(0), '0');
  assert.equal(_escHtml(42), '42');
  assert.equal(_escHtml(false), 'false');
  assert.equal(_escHtml('Hello World'), 'Hello World');
  assert.equal(_escHtml('&<>"\''), '&amp;&lt;&gt;&quot;&#39;');
  assert.equal(
    _escHtml('<script>alert("x & y \'z\'")</script>'),
    '&lt;script&gt;alert(&quot;x &amp; y &#39;z&#39;&quot;)&lt;/script&gt;'
  );

  // _esc delegates to _escHtml
  assert.equal(_esc(null), '');
  assert.equal(_esc(undefined), '');
  assert.equal(_esc(''), '');
  assert.equal(_esc('<b>bold & "quotes"</b>'), '&lt;b&gt;bold &amp; &quot;quotes&quot;&lt;/b&gt;');
});

test('language classification predicates handle variants, case-insensitivity, and edge values', () => {
  // _isDocxLang
  assert.equal(_isDocxLang('docx'), true);
  assert.equal(_isDocxLang('DOCX'), true);
  assert.equal(_isDocxLang('Docx'), true);
  assert.equal(_isDocxLang('doc'), false);
  assert.equal(_isDocxLang('pdf'), false);
  assert.equal(_isDocxLang(''), false);
  assert.equal(_isDocxLang(null), false);
  assert.equal(_isDocxLang(undefined), false);

  // _isRenderLang
  for (const lang of ['html', 'svg', 'xml', 'HTML', 'Svg', 'XML']) {
    assert.equal(_isRenderLang(lang), true, `Expected ${lang} to be render lang`);
  }
  for (const notRender of ['markdown', 'json', 'docx', 'richtext', '', null, undefined]) {
    assert.equal(_isRenderLang(notRender), false, `Expected ${notRender} not to be render lang`);
  }

  // _isRichTextLang
  assert.equal(_isRichTextLang('richtext'), true);
  assert.equal(_isRichTextLang('rich-text'), true);
  assert.equal(_isRichTextLang('RICHTEXT'), true);
  assert.equal(_isRichTextLang('Rich-Text'), true);
  assert.equal(_isRichTextLang('markdown'), false);
  assert.equal(_isRichTextLang('html'), false);
  assert.equal(_isRichTextLang(''), false);
  assert.equal(_isRichTextLang(null), false);
  assert.equal(_isRichTextLang(undefined), false);

  // _hasViewToggle
  const toggleLangs = [
    'csv', 'python', 'javascript', 'typescript', 'bash', 'sh', 'shell',
    'php', 'ruby', 'sql', 'java', 'go', 'rust',
    'c', 'cpp', 'c++', 'csharp', 'c#',
    'yaml', 'json', 'css',
    'ini', 'toml',
    'html', 'svg', 'xml', // render langs
    'docx',               // docx lang
    'richtext', 'rich-text', // rich text langs
  ];
  for (const lang of toggleLangs) {
    assert.equal(_hasViewToggle(lang), true, `Expected toggle for ${lang}`);
    assert.equal(_hasViewToggle(lang.toUpperCase()), true, `Expected toggle for uppercase ${lang}`);
  }
  for (const noToggle of ['plaintext', 'text', 'unknown', 'diff', '', null, undefined]) {
    assert.equal(_hasViewToggle(noToggle), false, `Expected no toggle for ${noToggle}`);
  }
});

test('markdown image file recognition and alt text generation', () => {
  // _isMarkdownImageFile
  assert.equal(_isMarkdownImageFile(null), false);
  assert.equal(_isMarkdownImageFile(undefined), false);
  assert.equal(_isMarkdownImageFile({}), false);
  assert.equal(_isMarkdownImageFile({ type: 'image/png' }), true);
  assert.equal(_isMarkdownImageFile({ type: 'IMAGE/JPEG' }), true);
  assert.equal(_isMarkdownImageFile({ type: 'image/webp' }), true);

  const extensions = ['avif', 'bmp', 'gif', 'jpg', 'jpeg', 'png', 'svg', 'webp'];
  for (const ext of extensions) {
    assert.equal(_isMarkdownImageFile({ name: `photo.${ext}` }), true);
    assert.equal(_isMarkdownImageFile({ name: `photo.${ext.toUpperCase()}` }), true);
  }
  assert.equal(_isMarkdownImageFile({ name: 'document.pdf' }), false);
  assert.equal(_isMarkdownImageFile({ name: 'archive.tar.gz' }), false);
  assert.equal(_isMarkdownImageFile({ name: 'script.js' }), false);

  // _markdownImageAlt
  assert.equal(_markdownImageAlt(null), 'image');
  assert.equal(_markdownImageAlt(undefined), 'image');
  assert.equal(_markdownImageAlt(''), 'image');
  assert.equal(_markdownImageAlt('.png'), 'image');
  assert.equal(_markdownImageAlt('diagram.png'), 'diagram');
  assert.equal(_markdownImageAlt('quarterly report graph.svg'), 'quarterly report graph');
  assert.equal(_markdownImageAlt('[bracketed]\nname\rhere.jpg'), 'bracketed name here');
  assert.equal(_markdownImageAlt('multiple   spaces   test.png'), 'multiple spaces test');
});

test('_looksLikeMarkdown detects markdown elements with score threshold', () => {
  // Headings score 3
  assert.equal(_looksLikeMarkdown('# Title\nSome text'), true);
  assert.equal(_looksLikeMarkdown('## Heading 2\nBody text'), true);
  assert.equal(_looksLikeMarkdown('### Heading 3'), true);

  // Links (2) + bold (1) on different lines = 3
  assert.equal(_looksLikeMarkdown('Check [this link](https://example.com)\nfor **details**.'), true);
  // Same line: link matches first, bold is in else branch, score = 2
  assert.equal(_looksLikeMarkdown('Check [this link](https://example.com) for **details**.'), false);

  // Fenced code block (2) + list item (1) = 3
  assert.equal(_looksLikeMarkdown('```js\nconsole.log(1);\n```\n- Note'), true);

  // Plain prose without markdown syntax
  assert.equal(_looksLikeMarkdown('This is plain text with no special markdown symbols.'), false);
  assert.equal(_looksLikeMarkdown('Just an ordinary email paragraph.\nWith two lines.'), false);

  // Single list item score = 1 (< 3)
  assert.equal(_looksLikeMarkdown('- only one list item'), false);

  // Multiple list items score = 3
  assert.equal(_looksLikeMarkdown('- item 1\n- item 2\n- item 3'), true);

  // Blockquote (1) + ordered list (1) = 2 (< 3)
  assert.equal(_looksLikeMarkdown('> quote\n1. first'), false);

  // Blockquote (1) + ordered list (1) + hr (1) = 3
  assert.equal(_looksLikeMarkdown('> quote\n1. first\n---'), true);
});

test('_buildDiffSummary formats line changes and limits display to top changes', () => {
  assert.equal(_buildDiffSummary('', ''), '');
  assert.equal(_buildDiffSummary(null, null), '');
  assert.equal(_buildDiffSummary('same\nlines', 'same\nlines'), '');

  // Line addition
  const addSummary = _buildDiffSummary('line 1\n', 'line 1\nline 2\n');
  assert.ok(addSummary.includes('<span class="diff-add">line 2</span>'));

  // Line removal
  const delSummary = _buildDiffSummary('line 1\nline 2\n', 'line 1\n');
  assert.ok(delSummary.includes('<span class="diff-del">line 2</span>'));

  // HTML escaping in diff
  const xssSummary = _buildDiffSummary('old <script>', 'new <b>tag</b>');
  assert.ok(xssSummary.includes('&lt;script&gt;'));
  assert.ok(xssSummary.includes('&lt;b&gt;tag&lt;/b&gt;'));
  assert.ok(!xssSummary.includes('<script>'));

  // Truncation when more than 2 additions or deletions occur
  const oldMany = 'r1\nr2\nr3\nr4\nr5';
  const newMany = 'a1\na2\na3\na4\na5';
  const manySummary = _buildDiffSummary(oldMany, newMany);
  assert.ok(manySummary.includes('+6 more changes'));
});

test('_htmlToPlainText and _hasRichHtmlElement convert untrusted HTML safely without DOMParser', () => {
  // Plain text and literal angle brackets not starting HTML tags
  assert.equal(_htmlToPlainText('Hello world'), 'Hello world');
  assert.equal(_htmlToPlainText('5 < 10 and 10 > 5'), '5 < 10 and 10 > 5');

  // Unexpected input types and empty values
  assert.equal(_htmlToPlainText(null), '');
  assert.equal(_htmlToPlainText(undefined), '');
  assert.equal(_htmlToPlainText(''), '');
  assert.equal(_htmlToPlainText(0), '0');
  assert.equal(_htmlToPlainText(false), 'false');
  assert.equal(_htmlToPlainText({ toString: () => '<p>hi</p>' }), '');
  assert.equal(_htmlToPlainText(['<a>x</a>']), '');

  // Legitimate HTML and entity decoding without double-unescape
  assert.equal(_htmlToPlainText('<p>Hello <b>world</b> &amp; friends</p>'), 'Hello world & friends');
  assert.equal(_htmlToPlainText('&lt;script&gt;alert(1)&lt;/script&gt;'), '<script>alert(1)</script>');
  assert.equal(_htmlToPlainText('&amp;lt;script&amp;gt;'), '&lt;script&gt;');
  assert.equal(_htmlToPlainText('&#60;b&#x3E;bold&#x26;#x27;'), '<b>bold&#x27;');
  assert.equal(_htmlToPlainText('&#0;&#x110000;ok'), '\ufffd\ufffdok');

  // Script/style/noscript contents and quoted attribute '>' characters
  assert.equal(_htmlToPlainText('<script>alert("<div>xss</div>")</script>After'), 'After');
  assert.equal(_htmlToPlainText('<style>p > a { color: red; }</style>Visible'), 'Visible');
  assert.equal(_htmlToPlainText('<noscript><img src=x></noscript>Shown'), 'Shown');
  assert.equal(_htmlToPlainText('<img alt="a > b" src=x onerror="javascript:alert(1)">'), '');
  assert.equal(_htmlToPlainText('<scr<script>ipt>alert(1)</script>Tail'), 'ipt>alert(1)Tail');
  assert.equal(_htmlToPlainText('<div attr="unclosed'), '<div attr="unclosed');
  assert.equal(_htmlToPlainText('<!-- <img src=x> -->Clean'), 'Clean');
  assert.equal(
    _htmlToPlainText('Hi Bob,\n\nI think a<b holds.\n\nThanks'),
    'Hi Bob,\n\nI think a<b holds.\n\nThanks'
  );
  assert.equal(_htmlToPlainText('<p>R&eacute;union &euro;5</p>'), 'Réunion €5');
  assert.equal(_htmlToPlainText('&Lt; &Gt; &Not; &unknown;'), '&Lt; &Gt; &Not; &unknown;', 'unknown case-sensitive names stay intact without a browser decoder');
  assert.equal(_htmlToPlainText('<p>Paragraph 1</p><p>Paragraph 2</p>'), 'Paragraph 1\n\nParagraph 2');
  assert.equal(_htmlToPlainText('Line 1<br>Line 2<br><br>Line 3'), 'Line 1\nLine 2\n\nLine 3');
  assert.equal(_htmlToPlainText('<div>Item 1</div><div>Item 2</div>'), 'Item 1\nItem 2');
  assert.equal(_htmlToPlainText('<p>Outer <b>bold <i>italic</i></b> tail</p>'), 'Outer bold italic tail');

  // Rich element detection ignores comments and escaped entities
  for (const rich of [
    '<img src="x">',
    '<VIDEO src="x"></VIDEO>',
    '<audio controls></audio>',
    '<iframe srcdoc="<p>nested</p>"></iframe>',
    '<table><tr><td>Cell</td></tr></table>',
    '<img alt="a > b" src="x">',
  ]) {
    assert.equal(_hasRichHtmlElement(rich), true, `Expected rich element in: ${rich}`);
  }
  for (const plain of [
    '',
    null,
    '<p>Plain text</p>',
    '<!-- <img src="x"> -->',
    '&lt;img src="x"&gt;',
    '[AI reply draft will appear here]',
  ]) {
    assert.equal(_hasRichHtmlElement(plain), false, `Did not expect rich element in: ${String(plain)}`);
  }
});

// ============================================================================
// 2. domain/emailMessage.js
// ============================================================================

test('_ATTACH_RE matches multilingual attachment keywords', () => {
  // English
  assert.match('Please see attached file', _ATTACH_RE);
  assert.match('Find attached the report', _ATTACH_RE);
  assert.match('PFA the invoice', _ATTACH_RE);
  assert.match('Document is enclosed for review', _ATTACH_RE);
  assert.match('I am enclosing the receipt', _ATTACH_RE);
  assert.match('See attachment below', _ATTACH_RE);
  assert.ok(_ATTACH_RE.test('Please see attached report.'));
  assert.ok(_ATTACH_RE.test('Find the attachment below.'));
  assert.ok(_ATTACH_RE.test('PFA the requested document'));
  assert.ok(_ATTACH_RE.test('Enclosed you will find the signed agreement.'));
  assert.ok(_ATTACH_RE.test('Attaching the updated spreadsheet now.'));

  // French
  assert.match('Veuillez trouver ci-joint le document', _ATTACH_RE);
  assert.match('Le fichier en pièce jointe', _ATTACH_RE);
  assert.match('Le fichier en piece jointe', _ATTACH_RE);
  assert.match('Document ajoute au message', _ATTACH_RE);
  assert.match('Voir le fichier joint', _ATTACH_RE);
  assert.ok(_ATTACH_RE.test('Veuillez trouver le fichier joint.'));
  assert.ok(_ATTACH_RE.test('Document ci-joint pour information.'));
  assert.ok(_ATTACH_RE.test('En pièce jointe le devis.'));

  // German
  assert.match('Anbei übersende ich den Bericht', _ATTACH_RE);
  assert.match('Die Rechnung finden Sie im Anhang', _ATTACH_RE);
  assert.match('Das Formular ist beigefügt', _ATTACH_RE);
  assert.match('Das Formular ist beigefugt', _ATTACH_RE);
  assert.ok(_ATTACH_RE.test('Anbei erhalten Sie die Rechnung.'));
  assert.ok(_ATTACH_RE.test('Im Anhang finden Sie die Unterlagen.'));

  // Multilingual boundary semantics
  assert.ok(_ATTACH_RE.test('file添付data'));

  // Non-matches and word boundaries
  assert.doesNotMatch('This is a normal message without keywords', _ATTACH_RE);
  assert.doesNotMatch('Just discussing attachments_fake in code', _ATTACH_RE);
  assert.ok(!_ATTACH_RE.test('unattached unrelated string'));
  assert.ok(!_ATTACH_RE.test('catastrophe or jointure unrelated'));
  assert.ok(!_ATTACH_RE.test(''));
  assert.ok(!_ATTACH_RE.test('General greeting with no mentions'));
});

test('_accountCanSend checks SMTP credentials and OAuth providers', () => {
  assert.equal(_accountCanSend(null), false);
  assert.equal(_accountCanSend(undefined), false);
  assert.equal(_accountCanSend({}), false);

  // Missing host or user
  assert.equal(_accountCanSend({ smtp_host: 'smtp.example.com' }), false);
  assert.equal(_accountCanSend({ smtp_user: 'user@example.com' }), false);
  assert.equal(_accountCanSend({ smtp_host: 'smtp.example.com', has_smtp_password: true }), false);
  assert.equal(_accountCanSend({ smtp_user: 'user@example.com', has_smtp_password: true }), false);
  assert.equal(_accountCanSend({ smtp_host: 'smtp.example.com', smtp_user: 'user@example.com' }), false);
  assert.equal(_accountCanSend({ smtp_host: 'smtp.example.com', smtp_user: 'user@example.com', has_smtp_password: false }), false);
  assert.equal(_accountCanSend({ smtp_host: '', smtp_user: 'user@example.com', has_smtp_password: true }), false);

  // Valid SMTP with password
  assert.equal(_accountCanSend({
    smtp_host: 'smtp.example.com',
    smtp_user: 'user@example.com',
    has_smtp_password: true,
  }), true);

  // Valid SMTP with OAuth provider
  assert.equal(_accountCanSend({
    smtp_host: 'smtp.gmail.com',
    smtp_user: 'user@gmail.com',
    oauth_provider: 'google',
  }), true);

  // Missing password and oauth provider
  assert.equal(_accountCanSend({
    smtp_host: 'smtp.example.com',
    smtp_user: 'user@example.com',
    has_smtp_password: false,
    oauth_provider: null,
  }), false);
});

test('_looksLikeWrappedEmailContent checks header separator and RFC822-style headers', () => {
  assert.equal(_looksLikeWrappedEmailContent(''), false);
  assert.equal(_looksLikeWrappedEmailContent(null), false);
  assert.equal(_looksLikeWrappedEmailContent(undefined), false);
  assert.equal(_looksLikeWrappedEmailContent('Just some plain text'), false);
  assert.equal(_looksLikeWrappedEmailContent('Just body text without separator'), false);
  assert.equal(_looksLikeWrappedEmailContent('Header: value\nNo separator here'), false);
  assert.equal(_looksLikeWrappedEmailContent('No recognized headers\n---\nBody here'), false);

  const validWrapped = 'To: user@example.com\nSubject: Hello\n---\nBody of the email';
  assert.equal(_looksLikeWrappedEmailContent(validWrapped), true);

  const crlfWrapped = 'To: user@example.com\r\nSubject: Hello\r\n---\r\nBody of the email';
  assert.equal(_looksLikeWrappedEmailContent(crlfWrapped), true);

  const inReplyTo = 'In-Reply-To: <123@example.com>\n---\nReply body';
  assert.equal(_looksLikeWrappedEmailContent(inReplyTo), true);

  const missingSeparator = 'To: user@example.com\nSubject: Hello\nBody without separator';
  assert.equal(_looksLikeWrappedEmailContent(missingSeparator), false);

  const separatorWithoutHeaders = 'Some notes\n---\nMore notes';
  assert.equal(_looksLikeWrappedEmailContent(separatorWithoutHeaders), false);
});

test('_decodeBase64EmailWrapper decodes base64-wrapped email content', () => {
  assert.equal(_decodeBase64EmailWrapper(''), null);
  assert.equal(_decodeBase64EmailWrapper(null), null);
  assert.equal(_decodeBase64EmailWrapper(undefined), null);
  assert.equal(_decodeBase64EmailWrapper('short'), null);
  assert.equal(_decodeBase64EmailWrapper('badlengthwith25characters!'), null);
  assert.equal(_decodeBase64EmailWrapper('invalid!chars!not!base64!'), null);
  assert.equal(_decodeBase64EmailWrapper('AAAAAAAAAAAAAAAAAAAAAAAA!@#$'), null);

  // Encode a valid wrapped email to base64
  const plainEmail = 'To: alice@example.com\nSubject: Test Email\n---\nHello Alice,\nHow are you?';
  const encoded = Buffer.from(plainEmail, 'utf8').toString('base64');

  const decoded = _decodeBase64EmailWrapper(encoded);
  assert.equal(decoded, plainEmail);

  // Base64 with surrounding or inner whitespace
  const withWhitespace = `  ${encoded.slice(0, 10)}\n  ${encoded.slice(10)}  \n`;
  const decodedWhitespace = _decodeBase64EmailWrapper(withWhitespace);
  assert.equal(decodedWhitespace, plainEmail);

  // Encoded content that is NOT a wrapped email
  const notEmail = Buffer.from('Just a generic text document without headers or separator', 'utf8').toString('base64');
  assert.equal(_decodeBase64EmailWrapper(notEmail), null);
});

test('_buildEmailContent constructs standard email content format', () => {
  const content = _buildEmailContent(
    'bob@example.com',
    'Meeting Agenda',
    '<msg-123@example.com>',
    '<msg-ref@example.com>',
    'Here is the agenda.',
    '42',
    'INBOX',
    'carol@example.com',
    'secret@example.com'
  );

  const expected = [
    'To: bob@example.com',
    'Cc: carol@example.com',
    'Bcc: secret@example.com',
    'Subject: Meeting Agenda',
    'In-Reply-To: <msg-123@example.com>',
    'References: <msg-ref@example.com>',
    'X-Source-UID: 42',
    'X-Source-Folder: INBOX',
    '---',
    'Here is the agenda.',
  ].join('\n');

  assert.equal(content, expected);

  // Minimal content with omitted optional fields
  const minimal = _buildEmailContent('dan@example.com', 'Quick Question', null, null, 'Can you talk?', null, null, null, null);
  const expectedMinimal = [
    'To: dan@example.com',
    'Subject: Quick Question',
    '---',
    'Can you talk?',
  ].join('\n');
  assert.equal(minimal, expectedMinimal);
});

test('_buildEmailContentFromFields builds content from fields object and attachments', () => {
  const fields = {
    to: 'dev@example.com',
    cc: 'team@example.com',
    bcc: 'lead@example.com',
    subject: 'Sprint Plan',
    inReplyTo: '<parent-1@example.com>',
    references: '<ref-1@example.com>',
    sourceUid: '100',
    sourceFolder: 'Archive',
    forwardAttachments: true,
    attachments: [
      { index: 0, filename: 'spec.pdf', size: 1024 },
      { index: 1, filename: 'notes.txt', size: 512 },
    ],
  };

  const body = 'Please find the sprint plan details attached.';
  const output = _buildEmailContentFromFields(fields, body);

  const expected = [
    'To: dev@example.com',
    'Cc: team@example.com',
    'Bcc: lead@example.com',
    'Subject: Sprint Plan',
    'In-Reply-To: <parent-1@example.com>',
    'References: <ref-1@example.com>',
    'X-Source-UID: 100',
    'X-Source-Folder: Archive',
    'X-Forward-Attachments: 1',
    'X-Attachments: 0:spec.pdf:1024|1:notes.txt:512',
    '---',
    'Please find the sprint plan details attached.',
  ].join('\n');

  assert.equal(output, expected);

  // Empty fields and empty body
  assert.equal(_buildEmailContentFromFields(null, null), 'To: \nSubject: \n---\n');
});

test('_parseEmailHeader parses headers, folded lines, and attachment metadata', () => {
  // Empty or invalid inputs
  assert.deepEqual(_parseEmailHeader(''), {
    to: '', cc: '', bcc: '', subject: '', inReplyTo: '', references: '', sourceUid: '', sourceFolder: '', forwardAttachments: false, attachments: [], body: ''
  });
  assert.deepEqual(_parseEmailHeader(null), {
    to: '', cc: '', bcc: '', subject: '', inReplyTo: '', references: '', sourceUid: '', sourceFolder: '', forwardAttachments: false, attachments: [], body: ''
  });

  // Content without '---' separator returns original content as body
  const noSeparator = 'Some body without any header delimiter';
  assert.equal(_parseEmailHeader(noSeparator).body, noSeparator);
  assert.equal(_parseEmailHeader(noSeparator).to, '');

  // Full header with folded line and attachments
  const content = [
    'To: alice@example.com',
    'Cc: bob@example.com,',
    '    carol@example.com', // folded header line
    'Bcc: audit@example.com',
    'Subject: Multi-line Folded',
    ' Subject Line',
    'In-Reply-To: <parent@example.com>',
    'References: <root@example.com>',
    'X-Source-UID: 999',
    'X-Source-Folder: Sent',
    'X-Forward-Attachments: 1',
    'X-Attachments: 0:photo.jpg:2048|1:doc.docx:4096',
    '---',
    'Line 1 of body',
    '---',
    'Line 2 of body (retained separator in body)',
  ].join('\n');

  const parsed = _parseEmailHeader(content);
  assert.equal(parsed.to, 'alice@example.com');
  assert.equal(parsed.cc, 'bob@example.com, carol@example.com');
  assert.equal(parsed.bcc, 'audit@example.com');
  assert.equal(parsed.subject, 'Multi-line Folded Subject Line');
  assert.equal(parsed.inReplyTo, '<parent@example.com>');
  assert.equal(parsed.references, '<root@example.com>');
  assert.equal(parsed.sourceUid, '999');
  assert.equal(parsed.sourceFolder, 'Sent');
  assert.equal(parsed.forwardAttachments, true);
  assert.deepEqual(parsed.attachments, [
    { index: 0, filename: 'photo.jpg', size: 2048 },
    { index: 1, filename: 'doc.docx', size: 4096 },
  ]);
  assert.equal(parsed.body, 'Line 1 of body\n---\nLine 2 of body (retained separator in body)');
});

test('_emailQuoteMarkerMatch recognizes previous message quote dividers', () => {
  // Plain text divider
  const plain = 'Some text\n----- Previous message -----\nQuoted text';
  const m1 = _emailQuoteMarkerMatch(plain);
  assert.ok(m1);
  assert.match(m1[0], /Previous message/i);

  // HTML paragraph wrapped divider
  const html = '<div>Hello</div><p>----- Previous message -----</p><blockquote>Quote</blockquote>';
  const m2 = _emailQuoteMarkerMatch(html);
  assert.ok(m2);
  assert.match(m2[0], /Previous message/i);

  // Longer dashes
  const longDashes = '-------- Previous message --------';
  assert.ok(_emailQuoteMarkerMatch(longDashes));

  // Non-matching
  assert.equal(_emailQuoteMarkerMatch('Normal conversation without divider'), null);
  assert.equal(_emailQuoteMarkerMatch('Regular paragraph text'), null);
  assert.equal(_emailQuoteMarkerMatch(null), null);
  assert.equal(_emailQuoteMarkerMatch(undefined), null);
  assert.equal(_emailQuoteMarkerMatch(''), null);
});

// ============================================================================
// 3. domain/richTextRules.js
// ============================================================================

test('_RICH_SLASH_COMMANDS contains valid command specifications', () => {
  assert.ok(Array.isArray(_RICH_SLASH_COMMANDS));
  assert.equal(_RICH_SLASH_COMMANDS.length, 16);

  const actions = _RICH_SLASH_COMMANDS.map(c => c.action);
  assert.deepEqual(actions, [
    'paragraph', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'ul', 'ol', 'check', 'quote', 'codeblock', 'hr',
    'pagebreak', 'table:insert:3:3', 'image'
  ]);
  assert.ok(actions.includes('paragraph'));
  assert.ok(actions.includes('h1'));
  assert.ok(actions.includes('h2'));
  assert.ok(actions.includes('h3'));
  assert.ok(actions.includes('ul'));
  assert.ok(actions.includes('ol'));
  assert.ok(actions.includes('check'));
  assert.ok(actions.includes('quote'));
  assert.ok(actions.includes('codeblock'));
  assert.ok(actions.includes('hr'));
  assert.ok(actions.includes('pagebreak'));
  assert.ok(actions.includes('table:insert:3:3'));
  assert.ok(actions.includes('image'));

  for (const cmd of _RICH_SLASH_COMMANDS) {
    assert.ok(cmd.action, 'Command missing action');
    assert.ok(cmd.label, 'Command missing label');
    assert.ok(cmd.icon, 'Command missing icon');
    assert.ok(cmd.keywords, 'Command missing keywords');
    assert.equal(typeof cmd.action, 'string');
    assert.equal(typeof cmd.label, 'string');
    assert.equal(typeof cmd.icon, 'string');
    assert.equal(typeof cmd.keywords, 'string');
  }
});

test('_RICH_BLOCK_INPUT_RULES maps markdown prefixes to editor actions', () => {
  assert.ok(_RICH_BLOCK_INPUT_RULES instanceof Map);
  assert.equal(_RICH_BLOCK_INPUT_RULES.size, 16);
  assert.deepEqual(_RICH_BLOCK_INPUT_RULES.get('#'), { action: 'h1' });
  assert.deepEqual(_RICH_BLOCK_INPUT_RULES.get('##'), { action: 'h2' });
  assert.deepEqual(_RICH_BLOCK_INPUT_RULES.get('###'), { action: 'h3' });
  assert.deepEqual(_RICH_BLOCK_INPUT_RULES.get('####'), { action: 'h4' });
  assert.deepEqual(_RICH_BLOCK_INPUT_RULES.get('#####'), { action: 'h5' });
  assert.deepEqual(_RICH_BLOCK_INPUT_RULES.get('######'), { action: 'h6' });
  assert.deepEqual(_RICH_BLOCK_INPUT_RULES.get('-'), { action: 'ul' });
  assert.deepEqual(_RICH_BLOCK_INPUT_RULES.get('*'), { action: 'ul' });
  assert.deepEqual(_RICH_BLOCK_INPUT_RULES.get('1.'), { action: 'ol' });
  assert.deepEqual(_RICH_BLOCK_INPUT_RULES.get('>'), { action: 'quote' });
  assert.deepEqual(_RICH_BLOCK_INPUT_RULES.get('```'), { action: 'codeblock' });
  assert.deepEqual(_RICH_BLOCK_INPUT_RULES.get('[]'), { action: 'check' });
  assert.deepEqual(_RICH_BLOCK_INPUT_RULES.get('- []'), { action: 'check' });
  assert.deepEqual(_RICH_BLOCK_INPUT_RULES.get('- [ ]'), { action: 'check' });
  assert.deepEqual(_RICH_BLOCK_INPUT_RULES.get('- [x]'), { action: 'check', checked: true });
  assert.deepEqual(_RICH_BLOCK_INPUT_RULES.get('- [X]'), { action: 'check', checked: true });
});

test('_isRichInlineCodeMarker detects inline code marker span elements', () => {
  assert.equal(_isRichInlineCodeMarker(null), false);
  assert.equal(_isRichInlineCodeMarker(undefined), false);
  assert.equal(_isRichInlineCodeMarker({}), false);
  assert.equal(_isRichInlineCodeMarker({ tagName: 'DIV' }), false);
  assert.equal(_isRichInlineCodeMarker({ tagName: 'SPAN' }), false);

  // SPAN with correct OdysseusInlineCode font family
  assert.equal(_isRichInlineCodeMarker({
    tagName: 'SPAN',
    style: { fontFamily: 'OdysseusInlineCode, monospace' },
  }), true);

  // SPAN with other font family
  assert.equal(_isRichInlineCodeMarker({
    tagName: 'SPAN',
    style: { fontFamily: 'Arial, sans-serif' },
  }), false);

  // SPAN without style property
  assert.equal(_isRichInlineCodeMarker({
    tagName: 'SPAN',
  }), false);
});

test('_normalizeRichLinkUrl normalizes links and rejects unsafe schemes', () => {
  assert.equal(_normalizeRichLinkUrl(''), '');
  assert.equal(_normalizeRichLinkUrl('   '), '');
  assert.equal(_normalizeRichLinkUrl(null), '');
  assert.equal(_normalizeRichLinkUrl(undefined), '');

  // In-page anchor and relative paths (including leading slash)
  assert.equal(_normalizeRichLinkUrl('#section'), '#section');
  assert.equal(_normalizeRichLinkUrl('#heading'), '#heading');
  assert.equal(_normalizeRichLinkUrl('/path/to/page'), '/path/to/page');
  assert.equal(_normalizeRichLinkUrl('/path/to/resource'), '/path/to/resource');
  assert.equal(_normalizeRichLinkUrl('./local/file'), './local/file');
  assert.equal(_normalizeRichLinkUrl('./sibling.html'), './sibling.html');
  assert.equal(_normalizeRichLinkUrl('../parent/file'), '../parent/file');
  assert.equal(_normalizeRichLinkUrl('../parent/page'), '../parent/page');
  assert.equal(_normalizeRichLinkUrl('//example.com/asset.js'), '//example.com/asset.js');
  assert.equal(_normalizeRichLinkUrl('//cdn.example.com/lib.js'), '//cdn.example.com/lib.js');

  // Bare domain prepended with https://
  assert.equal(_normalizeRichLinkUrl('example.com'), 'https://example.com');
  assert.equal(_normalizeRichLinkUrl('www.google.com/search?q=test'), 'https://www.google.com/search?q=test');

  // Standard safe protocols
  assert.equal(_normalizeRichLinkUrl('https://example.com'), 'https://example.com');
  assert.equal(_normalizeRichLinkUrl('https://secure.example.com'), 'https://secure.example.com');
  assert.equal(_normalizeRichLinkUrl('http://insecure.example.com'), 'http://insecure.example.com');
  assert.equal(_normalizeRichLinkUrl('mailto:user@example.com'), 'mailto:user@example.com');
  assert.equal(_normalizeRichLinkUrl('mailto:support@example.com'), 'mailto:support@example.com');
  assert.equal(_normalizeRichLinkUrl('tel:+1234567890'), 'tel:+1234567890');
  assert.equal(_normalizeRichLinkUrl('tel:+15551234567'), 'tel:+15551234567');
  assert.equal(_normalizeRichLinkUrl('mailto:a@b.com,c@d.com'), 'mailto:a@b.com,c@d.com');
  assert.equal(_normalizeRichLinkUrl('mailto:a@b.com,c@d.com?subject=Hello'), 'mailto:a@b.com,c@d.com?subject=Hello');
  assert.equal(_normalizeRichLinkUrl('tel:+1-555-1234;ext=12'), 'tel:+1-555-1234;ext=12');
  assert.equal(_normalizeRichLinkUrl('tel:+1-555-1234;phone-context=example.com'), 'tel:+1-555-1234;phone-context=example.com');

  // Unsafe / forbidden protocols rejected
  assert.equal(_normalizeRichLinkUrl('javascript:alert(1)'), '');
  assert.equal(_normalizeRichLinkUrl('javascript:alert(document.cookie)'), '');
  assert.equal(_normalizeRichLinkUrl('data:text/html,<script>alert(1)</script>'), '');
  assert.equal(_normalizeRichLinkUrl('file:///etc/passwd'), '');
  assert.equal(_normalizeRichLinkUrl('ftp://ftp.example.com'), '');
  assert.equal(_normalizeRichLinkUrl('vbscript:msgbox("hello")'), '');

  // Control chars, obfuscated schemes, empty hosts, and malformed mailto/tel rejected
  assert.equal(_normalizeRichLinkUrl('java\tscript:alert(1)'), '');
  assert.equal(_normalizeRichLinkUrl('java\nscript:alert(1)'), '');
  assert.equal(_normalizeRichLinkUrl('java script:alert(1)'), '');
  assert.equal(_normalizeRichLinkUrl('https://example.com/\0bad'), '');
  assert.equal(_normalizeRichLinkUrl('https://'), '');
  assert.equal(_normalizeRichLinkUrl('http://'), '');
  assert.equal(_normalizeRichLinkUrl('///evil'), '');
  assert.equal(_normalizeRichLinkUrl('mailto:'), '');
  assert.equal(_normalizeRichLinkUrl('mailto:not-an-email'), '');
  assert.equal(_normalizeRichLinkUrl('mailto:a@b.com,bad-email'), '');
  assert.equal(_normalizeRichLinkUrl('tel:'), '');
  assert.equal(_normalizeRichLinkUrl('tel:abc'), '');
  assert.equal(_normalizeRichLinkUrl('tel:+1-555-1234;ext=<script>'), '');
});

test('_RICH_FONT_SIZE_PX is frozen and defines levels 1 through 7', () => {
  assert.ok(Object.isFrozen(_RICH_FONT_SIZE_PX));
  assert.equal(_RICH_FONT_SIZE_PX[1], 10);
  assert.equal(_RICH_FONT_SIZE_PX[2], 13);
  assert.equal(_RICH_FONT_SIZE_PX[3], 16);
  assert.equal(_RICH_FONT_SIZE_PX[4], 18);
  assert.equal(_RICH_FONT_SIZE_PX[5], 24);
  assert.equal(_RICH_FONT_SIZE_PX[6], 32);
  assert.equal(_RICH_FONT_SIZE_PX[7], 48);
});

test('_RICH_COLOR_PALETTE is frozen and contains dark and light palettes', () => {
  assert.ok(Object.isFrozen(_RICH_COLOR_PALETTE));
  assert.ok(Object.isFrozen(_RICH_COLOR_PALETTE.dark));
  assert.ok(Object.isFrozen(_RICH_COLOR_PALETTE.light));

  assert.equal(_RICH_COLOR_PALETTE.dark.length, 7);
  assert.equal(_RICH_COLOR_PALETTE.light.length, 7);

  // Check all entries in dark palette
  assert.deepEqual(_RICH_COLOR_PALETTE.dark[0], ['#111827', 'Ink']);
  assert.deepEqual(_RICH_COLOR_PALETTE.dark[1], ['#7f1d1d', 'Crimson']);
  assert.deepEqual(_RICH_COLOR_PALETTE.dark[2], ['#7c2d12', 'Copper']);
  assert.deepEqual(_RICH_COLOR_PALETTE.dark[3], ['#713f12', 'Gold']);
  assert.deepEqual(_RICH_COLOR_PALETTE.dark[4], ['#14532d', 'Forest']);
  assert.deepEqual(_RICH_COLOR_PALETTE.dark[5], ['#0c4a6e', 'Ocean']);
  assert.deepEqual(_RICH_COLOR_PALETTE.dark[6], ['#4c1d95', 'Plum']);

  // Check all entries in light palette
  assert.deepEqual(_RICH_COLOR_PALETTE.light[0], ['#ffffff', 'White']);
  assert.deepEqual(_RICH_COLOR_PALETTE.light[1], ['#fecdd3', 'Rose']);
  assert.deepEqual(_RICH_COLOR_PALETTE.light[2], ['#fed7aa', 'Peach']);
  assert.deepEqual(_RICH_COLOR_PALETTE.light[3], ['#fef3c7', 'Lemon']);
  assert.deepEqual(_RICH_COLOR_PALETTE.light[4], ['#bbf7d0', 'Mint']);
  assert.deepEqual(_RICH_COLOR_PALETTE.light[5], ['#bae6fd', 'Sky']);
  assert.deepEqual(_RICH_COLOR_PALETTE.light[6], ['#ddd6fe', 'Lavender']);
});
