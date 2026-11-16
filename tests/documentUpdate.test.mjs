import test from 'node:test';
import { runBehaviorSuite } from './helpers/runBehaviorSuite.mjs';

test('documentUpdate extracted behavior', () => {
  runBehaviorSuite(new URL('./documentUpdate.behavior.mjs', import.meta.url));
});
