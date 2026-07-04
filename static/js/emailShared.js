const API_BASE = window.location.origin;

export function emailAccountQuery(prefix = '&') {
  const accountId = window.__odysseusActiveEmailAccount || '';
  if (!accountId) return '';
  const lead = prefix === '?' ? '?' : '&';
  return `${lead}account_id=${encodeURIComponent(accountId)}`;
}

export function emailApiUrl(path, params = {}) {
  const url = new URL(`${API_BASE}${path}`);
  const accountId = window.__odysseusActiveEmailAccount || '';
  if (accountId) url.searchParams.set('account_id', accountId);
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return;
    url.searchParams.set(key, String(value));
  });
  return url.toString();
}
