/** Availability is separate from execution and answer correctness. */
export function capabilityAvailable(contract, capability, expectedTools = []) {
  const offered = (contract.offered || []).map(name => String(name).replace(/^mcp__email__/, ''));
  return capability === null
    || (contract.active_capabilities || contract.capabilities || []).includes(capability)
    || (contract.routing_experiment === 'recent_model_choice'
        && expectedTools.some(tool => offered.includes(tool)));
}

/** Compare source steps in memory; callers retain booleans, never private text. */
export function skillDetailEvidence(output, answer) {
  let text = String(output || '');
  for (let i = 0; i < 3; i++) {
    try {
      const parsed = JSON.parse(text);
      const inner = parsed.stdout ?? parsed.results ?? parsed.response;
      if (typeof inner !== 'string') break;
      text = inner;
    } catch { break; }
  }
  const normalize = value => value.replace(/[`*_]/g, '').replace(/\s+/g, ' ').trim().toLowerCase();
  const steps = [];
  let selected = false;
  for (const line of text.split('\n')) {
    const heading = line.match(/^#{1,6}\s+(.+)/);
    if (heading) { selected = /^(?:procedure|verification)$/i.test(heading[1].trim()); continue; }
    if (selected && line.trim()) steps.push(normalize(line.replace(/^\s*(?:\d+[.)]|[-*])\s+/, '')));
  }
  return {steps: steps.length, covered: steps.length > 0 && steps.every(step => normalize(answer).includes(step))};
}
