import test from 'node:test';
import { runBehaviorSuite } from './helpers/runBehaviorSuite.mjs';

test('header overflow extracted behavior', () => {
  runBehaviorSuite(new URL('./documentOverflow.behavior.mjs', import.meta.url));
});
