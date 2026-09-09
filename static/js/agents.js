export function canvasUrl(conversationId, base) {
  const root = String(base || '').replace(/\/$/, '');
  return `${root}/conversations/${encodeURIComponent(conversationId)}`;
}

export function renderAgentStatus(projection) {
  if (!projection) return 'unknown';
  if (projection.stale) return 'stale';
  if (projection.degraded) return 'degraded';
  if (projection.reconnecting) return 'reconnecting';
  if (projection.synchronizing) return 'synchronizing';
  return projection.status || 'unknown';
}

async function _json(path, options) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  return response.json();
}

export async function launchAgent(payload) {
  return _json('/api/agents/executions', { method: 'POST', body: JSON.stringify(payload) });
}

export async function messageAgent(executionId, text) {
  return _json(`/api/agents/executions/${encodeURIComponent(executionId)}/messages`, {
    method: 'POST',
    body: JSON.stringify({ text }),
  });
}

export async function approveAgent(executionId, body) {
  return _json(`/api/agents/executions/${encodeURIComponent(executionId)}/approve`, {
    method: 'POST',
    body: JSON.stringify(body || {}),
  });
}

export async function cancelAgent(executionId) {
  return _json(`/api/agents/executions/${encodeURIComponent(executionId)}/cancel`, { method: 'POST' });
}

export async function resumeAgent(executionId) {
  return _json(`/api/agents/executions/${encodeURIComponent(executionId)}/resume`, { method: 'POST' });
}

export function initAgentPlatform() {
  const root = document.getElementById('agent-platform-panel');
  if (!root) return;
  const status = root.querySelector('[data-agent-status]');
  const canvas = root.querySelector('[data-agent-canvas]');
  if (status) status.textContent = 'idle';
  if (canvas) {
    canvas.textContent = 'Open in Agent Canvas';
    canvas.setAttribute('rel', 'noopener noreferrer');
    canvas.setAttribute('target', '_blank');
  }
  root.querySelector('[data-agent-launch]')?.addEventListener('click', async () => {
    const created = await launchAgent({
      request_id: `ui-${Date.now()}`,
      archetype: 'chat',
      payload: { text: root.querySelector('[data-agent-input]')?.value || '' },
    });
    if (status) status.textContent = renderAgentStatus(created);
    if (canvas && created.canvas_url) canvas.setAttribute('href', created.canvas_url);
  });
  root.querySelector('[data-agent-cancel]')?.addEventListener('click', async () => {
    const executionId = root.dataset.executionId;
    if (executionId) await cancelAgent(executionId);
  });
  root.querySelector('[data-agent-resume]')?.addEventListener('click', async () => {
    const executionId = root.dataset.executionId;
    if (executionId) await resumeAgent(executionId);
  });
  root.querySelector('[data-agent-approve]')?.addEventListener('click', async () => {
    const executionId = root.dataset.executionId;
    if (executionId) await approveAgent(executionId, {});
  });
}
