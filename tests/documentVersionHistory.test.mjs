import test from 'node:test';
import { runBehaviorSuite } from './helpers/runBehaviorSuite.mjs';

test('documentVersionHistory extracted behavior', () => {
  runBehaviorSuite(new URL('./documentVersionHistory.behavior.mjs', import.meta.url));
});
