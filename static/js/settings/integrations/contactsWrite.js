export async function _writeContacts(url, options, failureMessage) {
  let response;
  try { response = await fetch(url, options); }
  catch (_) { throw new Error(failureMessage); }
  const data = await response.json().catch(() => null);
  if (!response.ok || data?.success !== true) {
    const reason = [data?.error, data?.detail].find(value => typeof value === 'string' && value.trim());
    throw new Error(reason || failureMessage);
  }
  return data;
}
