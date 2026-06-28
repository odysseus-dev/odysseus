// Helpers for workspace deep links. Kept separate from workspace.js so the URL
// behavior can be tested without loading the full browser UI module graph.

export function parseWorkspaceUrl(locationLike) {
  try {
    const loc = locationLike || window.location;
    const params = new URLSearchParams(loc.search || '');
    if (!params.has('workspace')) return { found: false, path: '', cleanUrl: '' };
    const path = String(params.get('workspace') || '').trim();
    params.delete('workspace');
    const query = params.toString();
    const cleanUrl = `${loc.pathname || '/'}${query ? `?${query}` : ''}${loc.hash || ''}`;
    return { found: true, path, cleanUrl };
  } catch (e) {
    return { found: false, path: '', cleanUrl: '' };
  }
}
