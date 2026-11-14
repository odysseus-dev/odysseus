// Effective temperature shown by the chat and compare settings popups.

export function effectiveTemperature(data) {
  const d = data || {};
  if (d.temperature_override !== null && d.temperature_override !== undefined) {
    return d.temperature_override;
  }
  if (d.effective_temperature !== null && d.effective_temperature !== undefined) {
    return d.effective_temperature;
  }
  return 1;
}

export function temperatureHint(data) {
  return data && data.temperature_source === 'model' ? 'From model settings' : '';
}

export async function loadSessionTemperature(fetchImpl, apiBase, sessionId) {
  if (!sessionId) return null;
  const url = apiBase + '/api/session/' + encodeURIComponent(sessionId) + '/context';
  const res = await fetchImpl(url, { credentials: 'same-origin' });
  if (!res || !res.ok) return null;
  const data = await res.json();
  return {
    value: effectiveTemperature(data),
    hint: temperatureHint(data),
    source: data.temperature_source || '',
    override: data.temperature_override,
  };
}
