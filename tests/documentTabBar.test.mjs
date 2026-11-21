import test from 'node:test';
import { runBehaviorSuite } from './helpers/runBehaviorSuite.mjs';

test('document tab bar extracted behavior', () => {
  runBehaviorSuite(new URL('./documentTabBar.behavior.mjs', import.meta.url));
});
