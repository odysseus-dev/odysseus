import test from 'node:test';
import assert from 'node:assert/strict';
import { capabilityAvailable } from '../scripts/tool_followup_oracle.mjs';

test('model-choice follow-up can use an offered family without a matching classifier label', () => {
  const contract = { routing_experiment: 'recent_model_choice',
    active_capabilities: ['notes'], offered: ['manage_notes', 'manage_calendar'] };
  assert.equal(capabilityAvailable(contract, 'calendar', ['manage_calendar']), true);
});

test('an absent tool is not rescued by model-choice mode or empty expectations', () => {
  const contract = { routing_experiment: 'recent_model_choice',
    active_capabilities: ['notes'], offered: ['manage_notes'] };
  assert.equal(capabilityAvailable(contract, 'calendar', ['manage_calendar']), false);
  assert.equal(capabilityAvailable(contract, 'calendar'), false);
});

test('baseline still requires its classified capability', () => {
  const contract = { routing_experiment: 'baseline', active_capabilities: ['notes'],
    offered: ['manage_notes', 'manage_calendar'] };
  assert.equal(capabilityAvailable(contract, 'calendar', ['manage_calendar']), false);
  assert.equal(capabilityAvailable(contract, 'notes', ['manage_notes']), true);
});

test('email MCP names and unspecified-capability turns are supported', () => {
  assert.equal(capabilityAvailable({ routing_experiment: 'recent_model_choice',
    offered: ['mcp__email__list_emails'] }, 'email', ['list_emails']), true);
  assert.equal(capabilityAvailable({}, null), true);
});
