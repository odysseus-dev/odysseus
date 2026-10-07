import test from 'node:test';
import assert from 'node:assert/strict';
import { deliveryMessages, researchCardState } from '../static/js/backgroundToolJobs.js';

test('research cards distinguish live work, handoff, completion and no evidence', () => {
  assert.match(researchCardState({ status: 'running', rounds: 2, progress: { phase: 'reading', round: 1, total_sources: 3 } }).detail, /Round 1\/2 · 3 sources/);
  assert.equal(researchCardState({ status: 'ready' }).label, 'Preparing chat update');
  assert.equal(researchCardState({ status: 'delivered', source_count: 4 }).tone, 'done');
  assert.equal(researchCardState({ status: 'delivered', outcome: 'no_sources' }).label, 'No sources found');
  assert.equal(researchCardState({ status: 'ready', outcome: 'error' }).tone, 'error');
});

test('only new delivered messages append; history reload and repeat polls deduplicate', () => {
  const job = { status: 'delivered', message: { content: 'Found it', metadata: { _db_id: 'result-1' } } };
  assert.deepEqual(deliveryMessages([job, job, { status: 'running' }], []), [job.message]);
  assert.deepEqual(deliveryMessages([job], ['result-1']), []);
  assert.deepEqual(deliveryMessages([{ status: 'ready', message: job.message }], []), []);
});
