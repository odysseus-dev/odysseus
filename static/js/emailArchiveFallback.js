/**
 * Account-scoped orchestration for the email Archive-folder fallback.
 *
 * Keep this module DOM-free so account-switch races can be exercised directly
 * under Node. Callers own their UI, HTTP, and folder-refresh implementations.
 */

const _archiveSetupPromptsByAccount = new Map();

function _accountId(value) {
  return String(value || '');
}

export function captureArchiveAction(uid, accountId, sourceFolder) {
  return Object.freeze({
    uid: String(uid ?? ''),
    accountId: _accountId(accountId),
    sourceFolder: String(sourceFolder || 'INBOX'),
  });
}

export function isArchiveActionCurrent(action, activeAccountId) {
  return Boolean(action) && action.accountId === _accountId(activeAccountId);
}

function _staleResult() {
  return { success: false, stale: true };
}

async function _ensureArchiveFolder(action, suggestedFolder, deps) {
  const accountKey = action.accountId;
  let prompt = _archiveSetupPromptsByAccount.get(accountKey);
  let ownsPrompt = false;
  if (!prompt) {
    ownsPrompt = true;
    prompt = (async () => {
      const folderName = String(suggestedFolder || 'Archive').trim() || 'Archive';
      const confirmed = await deps.confirmCreate(folderName, action);
      if (!confirmed) return { ready: false, canceled: true };
      if (!isArchiveActionCurrent(action, deps.getActiveAccountId())) {
        return { ready: false, stale: true };
      }

      let created;
      try {
        created = await deps.createFolder(folderName, action);
      } catch (error) {
        return {
          ready: false,
          error: error?.message || 'Failed to create Archive folder',
        };
      }
      if (!created || created.success === false) {
        return {
          ready: false,
          error: created?.error || 'Failed to create Archive folder',
        };
      }
      return { ready: true, data: created };
    })();
    _archiveSetupPromptsByAccount.set(accountKey, prompt);
  }

  try {
    const result = await prompt;
    return ownsPrompt ? { ...result, ownsPrompt: true } : result;
  } finally {
    if (_archiveSetupPromptsByAccount.get(accountKey) === prompt) {
      _archiveSetupPromptsByAccount.delete(accountKey);
    }
  }
}

export async function runArchiveFallback(action, deps) {
  const isCurrent = () => isArchiveActionCurrent(action, deps.getActiveAccountId());
  if (!isCurrent()) return _staleResult();

  const first = await deps.archiveOnce(action);
  if (!isCurrent()) return _staleResult();
  if (first.success) return first;
  if (!first.needs_archive_folder) return first;

  const setup = await _ensureArchiveFolder(
    action,
    first.suggested_folder || 'Archive',
    deps,
  );
  if (setup.stale) return _staleResult();
  if (setup.canceled) return { success: false, canceled: true };
  if (!setup.ready) return { success: false, error: setup.error };
  if (!isCurrent()) return _staleResult();

  await deps.refreshFolders(action, setup.data);
  if (!isCurrent()) return _staleResult();
  if (setup.ownsPrompt && typeof deps.onFolderReady === 'function') {
    await deps.onFolderReady(setup.data, action);
  }
  if (!isCurrent()) return _staleResult();

  const retry = await deps.archiveOnce(action);
  if (!isCurrent()) return _staleResult();
  return retry;
}
