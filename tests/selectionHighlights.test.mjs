import test from 'node:test';
import { runBehaviorSuite } from './helpers/runBehaviorSuite.mjs';

test('selection highlights extracted behavior', () => {
  runBehaviorSuite(new URL('./selectionHighlights.behavior.mjs', import.meta.url));
});
