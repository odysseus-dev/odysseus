import test from 'node:test';
import { runBehaviorSuite } from './helpers/runBehaviorSuite.mjs';

test('markdown images extracted behavior', () => {
  runBehaviorSuite(new URL('./markdownImages.behavior.mjs', import.meta.url));
});
