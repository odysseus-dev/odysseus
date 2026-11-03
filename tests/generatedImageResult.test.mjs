import test from 'node:test';
import assert from 'node:assert/strict';
import { generatedImageResult } from '../static/js/generatedImageResult.js';

test('restores successful legacy images from saved tool output', () => {
  const image = { image_url: '/api/generated-image/test.png', image_model: 'openai/gpt-5-image' };
  assert.deepEqual(generatedImageResult({ tool: 'generate_image', exit_code: 0, output: JSON.stringify(image) }), image);
  assert.equal(generatedImageResult({ tool: 'generate_image', error: true, output: JSON.stringify(image) }), null);
  assert.equal(generatedImageResult({ tool: 'web_fetch', output: JSON.stringify(image) }), null);
  assert.equal(generatedImageResult({ tool: 'generate_image', output: 'invalid' }), null);
});

test('uses persisted image metadata for new turns', () => {
  const event = { tool: 'generate_image', image_url: '/api/generated-image/test.png', exit_code: 0 };
  assert.equal(generatedImageResult(event), event);
});
