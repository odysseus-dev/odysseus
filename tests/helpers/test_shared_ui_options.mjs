import assert from 'node:assert/strict';
import test from 'node:test';
import { stepWrappedOption } from '../../frontend/src/shared/ui/optionNavigation.js';

test('wrapped option navigation handles empty, unselected, single and boundary states', () => {
  assert.equal(stepWrappedOption(-1, 0, 1), -1);
  assert.equal(stepWrappedOption(-1, 4, 1), 0);
  assert.equal(stepWrappedOption(-1, 4, -1), 3);
  assert.equal(stepWrappedOption(0, 1, -1), 0);
  assert.equal(stepWrappedOption(0, 4, -1), 3);
  assert.equal(stepWrappedOption(3, 4, 1), 0);
  assert.equal(stepWrappedOption(2, 4, -1), 1);
});
