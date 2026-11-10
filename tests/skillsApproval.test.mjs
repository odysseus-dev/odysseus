import { test } from 'node:test';
import assert from 'node:assert/strict';
import { skillsSummaryMetrics, filterSkillsByQuickFilter, skillNeedsReview } from '../static/js/skillsMetrics.js';

test('approval uses the configured threshold for every item, not array index', () => {
  const rows = ['one', 'two', 'three'].map(name => ({ name, status: 'published', audit_verdict: 'pass', confidence: 0.9, baseline_verdict: 'same' }));
  assert.equal(skillsSummaryMetrics(rows, 0.85).approved, 3);
  assert.equal(skillsSummaryMetrics(rows, 0.85).review, 0);
  assert.equal(filterSkillsByQuickFilter(rows, 'approved', 0.95).length, 0);
  assert.equal(filterSkillsByQuickFilter(rows, 'draft', 0.95).length, 3);
});

test('review badges agree with approval and keep unaudited skills queued', () => {
  assert.equal(skillNeedsReview({ status: 'draft' }), false);
  assert.equal(skillNeedsReview({ status: 'draft', audit_verdict: 'pass', confidence: 0.95 }), true);
  assert.equal(skillNeedsReview({ status: 'published', audit_verdict: 'pass', confidence: 0.95, necessity: { necessary: false } }), true);
  assert.equal(skillNeedsReview({ status: 'published', source: 'builtin' }), false);
});

test('built-ins and drafts have distinct filters and archived skills remain recoverable', () => {
  const rows = [{ name: 'shipped', source: 'builtin', status: 'published' }, { name: 'bad', status: 'binned' }];
  assert.deepEqual(filterSkillsByQuickFilter(rows, 'builtin').map(s => s.name), ['shipped']);
  assert.deepEqual(filterSkillsByQuickFilter(rows, 'draft').map(s => s.name), ['bad']);
});
