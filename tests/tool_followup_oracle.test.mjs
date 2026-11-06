import test from 'node:test';
import assert from 'node:assert/strict';
import {skillDetailEvidence} from '../scripts/tool_followup_oracle.mjs';

test('full detail requires all procedure and verification evidence, not just title', () => {
  const source = '# Skill\n## Procedure\n1. Report violet-72.\n2. Read the marker.\n## Verification\n- Confirm violet-72.\n## Other\nNot a step.';
  assert.deepEqual(skillDetailEvidence(source, 'Skill'), {steps: 3, covered: false});
  assert.deepEqual(skillDetailEvidence(JSON.stringify({stdout: JSON.stringify({results: source})}),
    'Report **violet-72**. Read the marker. Confirm violet-72.'), {steps: 3, covered: true});
  assert.equal(skillDetailEvidence(source, 'Report violet-72. Confirm violet-72.').covered, false);
  assert.deepEqual(skillDetailEvidence('summary only', 'summary only'), {steps: 0, covered: false});
});
