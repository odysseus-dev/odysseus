/**
 * "Show external images in notes": a per-user preference, off by default.
 *
 * Off, an image from another site renders as a link and is never fetched.
 * On, it renders as an image in the note window, the cards and a note
 * someone shared with you, which tells that site your IP address. The
 * setting is the viewer's, never the note owner's: a collaborator who keeps
 * it off never fetches an image the owner embedded. It only changes how a
 * note is shown; what is saved is the same either way.
 */
export const EXTERNAL_IMAGES_PREF = 'notes_show_external_images';

export function createExternalImagesSetting({ origin, request, onChange = () => {} }) {
  let enabled = false;
  const url = `${origin}/api/prefs/${EXTERNAL_IMAGES_PREF}`;
  const inputs = new Set();

  function apply(value) {
    const next = value === true;
    inputs.forEach(input => { input.checked = next; });
    if (next === enabled) return;
    enabled = next;
    onChange(enabled);
  }

  /** Reads the stored value. Anything but an explicit `true` is off. */
  async function load() {
    try {
      const response = await request(url, { credentials: 'same-origin' });
      if (response.ok) apply((await response.json())?.value);
    } catch {}
    return enabled;
  }

  async function save(value) {
    const response = await request(url, {
      method: 'PUT',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: value === true }),
    });
    if (!response.ok) throw new Error('Failed to save preference');
    apply(value === true);
  }

  /** Keeps a checkbox in step with the setting, and saves it when toggled. */
  function bind(input, { showError = () => {}, showToast = () => {} } = {}) {
    if (!input || inputs.has(input)) return;
    inputs.add(input);
    input.checked = enabled;
    input.addEventListener('change', async () => {
      const wanted = input.checked;
      try {
        await save(wanted);
        showToast(wanted ? 'External images shown in notes' : 'External images shown as links');
      } catch {
        input.checked = enabled;
        showError('Failed to save preference');
      }
    });
  }

  return { enabled: () => enabled, load, save, bind };
}
