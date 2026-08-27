const ACTIVE_DEP_STATUSES = new Set(['queued', 'running']);

export function depHostKey(host) {
  const value = String(host || '').trim();
  return (!value || value === 'local' || value === 'localhost' || value === '127.0.0.1')
    ? 'local'
    : value;
}

export function depInstallKey(identity, host = '', port = '', envPath = '') {
  return [
    String(identity || '').trim(),
    depHostKey(host),
    String(port || '').trim(),
    String(envPath || '').trim(),
  ].join('\n');
}

function _targetParts(target = {}) {
  return {
    host: target.host || target.remote_host || '',
    port: target.port || target.ssh_port || '',
    envPath: target.envPath || target.env_path || target.venv || '',
  };
}

export function dependencyCandidateKeys(pipSpec, catalogName, target = {}) {
  const { host, port, envPath } = _targetParts(target);
  const identities = [pipSpec, catalogName]
    .map(value => String(value || '').trim())
    .filter((value, index, all) => value && all.indexOf(value) === index);
  const keys = identities.map(identity => depInstallKey(identity, host, port, envPath));
  // Older tasks did not persist their venv identity. Keep a host/port-only
  // alias so one such active task still blocks a duplicate after upgrade.
  if (envPath) identities.forEach(identity => keys.push(depInstallKey(identity, host, port, '')));
  return [...new Set(keys)];
}

function _legacyTaskIdentities(task) {
  const payload = task?.payload || {};
  const values = [payload._dep_pip_spec, payload._dep_catalog_name, payload.repo_id];
  const repoId = String(payload.repo_id || '').trim();
  const taskName = String(task?.name || '').trim();
  const command = String(payload._cmd || '').trim();

  if (/\bllama-cpp-python\b/i.test(`${repoId} ${taskName} ${command}`)) {
    values.push('llama_cpp', 'llama-cpp-python[server]');
  }
  const setupName = repoId.match(/^(.+?)\s+setup$/i)?.[1];
  if (setupName) values.push(setupName);
  const reinstallName = taskName.match(/^reinstall-(.+)$/i)?.[1];
  if (
    reinstallName
    && /(?:^|\s)(?:[^\s"']*[\\/])?python\d*(?:\.\d+)?(?:\.exe)?\s+-m\s+pip\s+install\b/i.test(command)
  ) {
    values.push(reinstallName);
  }
  return values
    .map(value => String(value || '').trim())
    .filter((value, index, all) => value && all.indexOf(value) === index);
}

export function taskDepInstallKeys(task) {
  if (!task) return [];
  const payload = task.payload || {};
  const identities = _legacyTaskIdentities(task);
  const legacyReinstall = identities.some(identity => (
    String(task.name || '').toLowerCase() === `reinstall-${identity.toLowerCase()}`
  ));
  if (!payload._dep && !payload._dep_key && !legacyReinstall) return [];

  const target = {
    host: task.remoteHost || payload.remote_host || '',
    port: task.sshPort || payload.ssh_port || '',
    envPath: payload.env_path || payload._envPath || '',
  };
  const keys = payload._dep_key ? [String(payload._dep_key)] : [];
  identities.forEach(identity => keys.push(depInstallKey(identity, target.host, target.port, target.envPath)));
  return [...new Set(keys.filter(Boolean))];
}

export function isActiveDepTask(task) {
  return ACTIVE_DEP_STATUSES.has(task?.status || '') && taskDepInstallKeys(task).length > 0;
}

export function findActiveDepTask(tasks, candidateKeys) {
  const wanted = new Set(candidateKeys || []);
  if (!wanted.size) return null;
  return (tasks || []).find(task => (
    isActiveDepTask(task) && taskDepInstallKeys(task).some(key => wanted.has(key))
  )) || null;
}

export function sameDependencyTarget(left = {}, right = {}) {
  const a = _targetParts(left);
  const b = _targetParts(right);
  return depHostKey(a.host) === depHostKey(b.host)
    && String(a.port || '') === String(b.port || '')
    && String(a.envPath || '') === String(b.envPath || '');
}
