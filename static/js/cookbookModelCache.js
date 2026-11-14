// Shared model-cache invalidation contract.
//
// A completed Hugging Face download must make the new model appear in Launch
// without a manual refresh. One event connects the task poller (which notices
// completion) to the two cached-model views: the Launch list's scan cache and
// the Download/HW-fit "downloaded" markers. Keeping the event name and the
// target-matching rules here means the two views cannot drift apart.

export const MODEL_DOWNLOAD_COMPLETED_EVENT = 'cookbook:model-download-completed';

function _targetHost(value) {
  return String(value || '').trim();
}

export function isModelDownloadTask(task) {
  if (!task || task.type !== 'download' || task.payload?._dep) return false;
  const repoId = String(task.payload?.repo_id || task.payload?.repo || '').trim();
  return !!repoId && !repoId.startsWith('pip-');
}

export function modelDownloadCompletedDetail(task) {
  return {
    repoId: String(task?.payload?.repo_id || task?.payload?.repo || '').trim(),
    host: _targetHost(task?.remoteHost || task?.payload?.remote_host),
    serverKey: String(task?.remoteServerKey || task?.payload?.remote_server_key || '').trim(),
  };
}

export function cachedModelScanHost(signature) {
  const value = String(signature || '');
  if (!value || value === 'local') return '';
  return _targetHost(new URLSearchParams(value.replace(/^\?/, '')).get('host'));
}

export function invalidateCachedModelScans(entries, host) {
  const targetHost = _targetHost(host);
  const next = { ...(entries && typeof entries === 'object' ? entries : {}) };
  for (const signature of Object.keys(next)) {
    if (cachedModelScanHost(signature) === targetHost) delete next[signature];
  }
  return next;
}

export function modelDownloadMatchesTarget(detail, target) {
  if (_targetHost(detail?.host) !== _targetHost(target?.host)) return false;
  const eventKey = String(detail?.serverKey || '').trim();
  const targetKey = String(target?.serverKey || '').trim();
  return !eventKey || !targetKey || targetKey === 'local' || eventKey === targetKey;
}
