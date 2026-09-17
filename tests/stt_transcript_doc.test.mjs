// Tests for the raw-transcript document helpers
// (see static/js/sttTranscriptDoc.js).
//
// DOM-free pure functions: combined-document assembly must preserve queue
// order and verbatim text, skipping failed (empty) recordings.
import assert from 'node:assert/strict';
import test from 'node:test';

import {
  rawDocTitle,
  buildCombinedMarkdown,
} from '../static/js/sttTranscriptDoc.js';

test('rawDocTitle maps stem to <stem>_raw.md', () => {
  assert.equal(rawDocTitle('meeting.m4a'), 'meeting_raw.md');
  assert.equal(rawDocTitle('recording1.wav'), 'recording1_raw.md');
  assert.equal(rawDocTitle('a/b/c.mp3'), 'c_raw.md');
});

test('combined document preserves queue order verbatim', () => {
  const out = buildCombinedMarkdown([
    { name: 'recording1.wav', text: 'prvý text' },
    { name: 'recording2.wav', text: 'druhý text' },
    { name: 'recording3.wav', text: 'tretí text' },
  ]);
  assert.equal(out, [
    '# Audio Transcripts',
    '',
    '## recording1',
    '',
    'prvý text',
    '',
    '---',
    '',
    '## recording2',
    '',
    'druhý text',
    '',
    '---',
    '',
    '## recording3',
    '',
    'tretí text',
  ].join('\n'));
});

test('combined document skips failed recordings, keeps original filenames', () => {
  const out = buildCombinedMarkdown([
    { name: 'a.wav', text: 'ok-a' },
    { name: 'b.wav', text: '' }, // failed: no transcript
    { name: 'c.wav', text: 'ok-c' },
  ]);
  assert.match(out, /## a\n\nok-a/);
  assert.doesNotMatch(out, /## b/);
  assert.match(out, /## c\n\nok-c/);
  // Order: a before c.
  assert.ok(out.indexOf('## a') < out.indexOf('## c'));
});

test('combined document does not rewrite text', () => {
  const raw = '  MiXeD   CaSe,   punctuation!!!  ';
  const out = buildCombinedMarkdown([{ name: 'x.mp3', text: raw }]);
  assert.ok(out.includes(raw));
});


