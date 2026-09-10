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
  const root = document.getElementById('agent-chat-controls');
  if (!root) return;
  const canvas = root.querySelector('[data-agent-canvas]');
  const approve = root.querySelector('[data-agent-approve]');
  const cancel = root.querySelector('[data-agent-cancel]');
  const resume = root.querySelector('[data-agent-resume]');
  if (canvas) {
    canvas.textContent = 'Open in Agent Canvas';
    canvas.setAttribute('rel', 'noopener noreferrer');
    canvas.setAttribute('target', '_blank');
  }
  window.__odysseusBindAgentExecution = function bind(execution) {
    if (!execution) return;
    if (execution.execution_id) root.dataset.executionId = execution.execution_id;
    if (canvas && execution.conversation_id) {
      canvas.hidden = false;
      canvas.setAttribute(
        'href',
        execution.canvas_url || canvasUrl(execution.conversation_id, window.OPENHANDS_CANVAS_URL || ''),
      );
    }
    if (execution.pending_confirmation) {
      if (approve) approve.hidden = false;
    }
    if (cancel) cancel.hidden = !execution.execution_id;
    if (resume) resume.hidden = execution.status !== 'paused';
  };
  approve?.addEventListener('click', async () => {
    const id = root.dataset.executionId;
    if (id) await approveAgent(id, {});
  });
  cancel?.addEventListener('click', async () => {
    const id = root.dataset.executionId;
    if (id) await cancelAgent(id);
  });
  resume?.addEventListener('click', async () => {
    const id = root.dataset.executionId;
    if (id) await resumeAgent(id);
  });
}
