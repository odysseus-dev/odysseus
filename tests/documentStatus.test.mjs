import test from 'node:test';
import { runBehaviorSuite } from './helpers/runBehaviorSuite.mjs';

test('documentStatus extracted behavior', () => {
  runBehaviorSuite(new URL('./documentStatus.behavior.mjs', import.meta.url));
});
