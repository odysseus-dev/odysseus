import test from 'node:test';
import { runBehaviorSuite } from './helpers/runBehaviorSuite.mjs';

test('document toolbar extracted behavior', () => {
  runBehaviorSuite(new URL('./documentToolbar.behavior.mjs', import.meta.url));
});
