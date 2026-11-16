import test from 'node:test';
import { runBehaviorSuite } from './helpers/runBehaviorSuite.mjs';

test('documentLiveStream extracted behavior', () => {
  runBehaviorSuite(new URL('./documentLiveStream.behavior.mjs', import.meta.url));
});
