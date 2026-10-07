import test from 'node:test';
import assert from 'node:assert/strict';
import {readEmailReplyResponse} from '../static/js/emailReplyStream.js';

const response = frames => new Response(frames.map(f => `data: ${JSON.stringify(f)}\n\n`).join(''),
  {headers: {'content-type': 'text/event-stream'}});

test('streams body then requires successful terminal result', async () => {
  const seen = [];
  const result = await readEmailReplyResponse(response([
    {type:'reply', text:'Hi'}, {type:'reply', text:'Hi Jonathan'},
    {type:'result', success:true, reply:'Hi Jonathan'},
  ]), text => seen.push(text));
  assert.deepEqual(seen, ['Hi', 'Hi Jonathan']);
  assert.equal(result.success, true);
});

test('editing the draft stops streaming insertion', async () => {
  await assert.rejects(readEmailReplyResponse(response([{type:'reply', text:'Hi'}]), () => false), /edited or closed/);
});

test('disconnect is not a completed draft', async () => {
  await assert.rejects(readEmailReplyResponse(response([{type:'reply', text:'Hi'}]), () => {}), /before completion/);
});
