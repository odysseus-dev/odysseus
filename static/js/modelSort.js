// Shared alphabetical sorting for model pickers and dropdowns.

function _sortText(value) {
  return String(value || '').split('/').pop().trim() || String(value || '');
}

function _compareText(a, b) {
  return _sortText(a).localeCompare(_sortText(b), undefined, {
    numeric: true,
    sensitivity: 'base',
  }) || String(a || '').localeCompare(String(b || ''), undefined, {
    numeric: true,
    sensitivity: 'base',
  });
}

export function sortModelIds(models) {
  return (models || []).slice().sort(_compareText);
}

export function compareModelObjects(a, b) {
  const aLabel = a && (a.display || a.displayName || a.name || a.mid || a.id || a.model);
  const bLabel = b && (b.display || b.displayName || b.name || b.mid || b.id || b.model);
  return _compareText(aLabel, bLabel);
}

export function sortModelObjects(models) {
  return (models || []).slice().sort(compareModelObjects);
}
