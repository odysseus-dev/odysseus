import test from 'node:test';
import { runBehaviorSuite } from './helpers/runBehaviorSuite.mjs';

test('documentFlows extracted behavior', () => {
  runBehaviorSuite(new URL('./documentFlows.behavior.mjs', import.meta.url));
});
