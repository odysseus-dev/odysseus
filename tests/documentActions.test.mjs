import test from 'node:test';
import { runBehaviorSuite } from './helpers/runBehaviorSuite.mjs';

test('documentActions extracted behavior', () => {
  runBehaviorSuite(new URL('./documentActions.behavior.mjs', import.meta.url));
});
