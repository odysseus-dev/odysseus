export function syncWhisperIndicatorAccessibility(button, active, target = null) {
  if (!button) return;
  const name = target && target.name ? String(target.name) : '';
  button.setAttribute(
    'aria-label',
    active ? `Whisper to ${name || 'group participant'}` : 'Whisper mode active',
  );
}
