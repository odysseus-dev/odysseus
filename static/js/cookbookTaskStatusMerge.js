// Pure helpers for merging /api/cookbook/tasks/status records into saved
// Cookbook task state. Kept browser-free so regressions can run under Node.

function _cleanString(value) {
  return String(value || '').trim();
}

export function normalizeLiveDiagnosis(diagnosis) {
  if (!diagnosis || typeof diagnosis !== 'object') return null;
  const message = _cleanString(diagnosis.message);
  const suggestion = _cleanString(diagnosis.suggestion);
  if (!message) return null;
  return {
    ...diagnosis,
    message,
    ...(suggestion ? { suggestion } : {}),
  };
}

function _nextStatus(task, live) {
  if (live.status === 'completed') return 'done';
  if (live.status === 'error') return 'error';
  if (live.status === 'stopped') return task.type === 'download' ? 'crashed' : 'stopped';
  if (live.status === 'ready') return 'ready';
  if (live.status === 'running') return 'running';
  return null;
}

export function liveTaskStatusUpdates(task, live) {
  if (!task || !live) return {};
  const updates = {};
  const nextStatus = _nextStatus(task, live);
  if (nextStatus && task.status !== nextStatus) updates.status = nextStatus;

  if (live.progress && live.progress !== task.progress) updates.progress = live.progress;

  if (live.output_tail) {
    const previous = String(task.output || '');
    const tail = String(live.output_tail || '');
    if (tail && !previous.endsWith(tail)) {
      updates.output = `${previous ? `${previous}\n` : ''}${tail}`.slice(-5000);
    }
  }

  const taskType = live.type || task.type;
  const diagnosis = normalizeLiveDiagnosis(live.diagnosis);
  if (taskType === 'serve' && diagnosis) {
    updates.diagnosis = diagnosis;
  } else if (task.diagnosis && (live.status === 'running' || live.status === 'ready' || live.status === 'completed')) {
    updates.diagnosis = null;
  }

  const cmd = _cleanString(live.cmd);
  if (taskType === 'serve' && cmd && !task.payload?._cmd) {
    updates.payload = { ...(task.payload || {}), _cmd: cmd };
  }

  return updates;
}

export function applyLiveTaskStatus(task, live) {
  const updates = liveTaskStatusUpdates(task, live);
  if (!Object.keys(updates).length) return false;
  Object.assign(task, updates);
  return true;
}
