// Notifies other modules (the admin panel, the model picker) that the set of
// configured integrations changed.

export function notifyIntegrationsChanged() {
  try {
    window.dispatchEvent(new CustomEvent('odysseus-integrations-changed'));
  } catch (_) {}
}
