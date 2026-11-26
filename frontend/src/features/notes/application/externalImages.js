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

  // Saves go out one at a time, in the order they were asked for: two quick
  // toggles can never have the first one's answer arrive last and undo an
  // opt-out. Only the latest choice is applied, so a quick on then off never
  // turns images on, even for the moment the "off" is still being saved.
  let queue = Promise.resolve();
  let latest = 0;
  // What the server last confirmed, for a failed latest save to fall back to.
  let stored = null;

  /** Resolves true when this save was the latest choice and was applied. */
  function save(value) {
    const wanted = value === true;
    const token = ++latest;
    const run = queue.then(async () => {
      let response;
      try {
        response = await request(url, {
          method: 'PUT',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ value: wanted }),
        });
      } catch (error) {
        if (token === latest && stored !== null) apply(stored);
        throw error;
      }
      if (!response.ok) {
        if (token === latest && stored !== null) apply(stored);
        throw new Error('Failed to save preference');
      }
      stored = wanted;
      if (token !== latest) return false;
      apply(wanted);
      return true;
    });
    queue = run.catch(() => {});
    return run;
  }

  /** Keeps a checkbox in step with the setting, and saves it when toggled. */
  function bind(input, { showError = () => {}, showToast = () => {} } = {}) {
    if (!input || inputs.has(input)) return;
    inputs.add(input);
    input.checked = enabled;
    input.addEventListener('change', async () => {
      const wanted = input.checked;
      try {
        if (await save(wanted)) showToast(wanted ? 'External images shown in notes' : 'External images shown as links');
      } catch {
        input.checked = enabled;
        showError('Failed to save preference');
      }
    });
  }

  return { enabled: () => enabled, load, save, bind };
}
