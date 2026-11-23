/** Note colors and custom background selection. */
export function createColors({
  api
}) {
  const COLORS = [
    { name: 'none',    value: '' },
    { name: 'red',     value: 'red' },
    { name: 'orange',  value: 'orange' },
    { name: 'yellow',  value: 'yellow' },
    { name: 'green',   value: 'green' },
    { name: 'blue',    value: 'blue' },
    { name: 'purple',  value: 'purple' },
    { name: 'custom',  value: 'custom' },  // sentinel : clicking opens native color picker
  ];

  const COLOR_HEX = {
    '':       'var(--border)',
    // Pale/pastel palette : matches the calendar event color picker.
    red:      '#f0b5ba',
    orange:   '#e8ccb2',
    yellow:   '#f2dfbd',
    green:    '#cce0bc',
    blue:     '#b0d7f7',
    purple:   '#e2bcee',
  };

  const _CUSTOM_GRADIENT = 'conic-gradient(from 0deg, #e06c75, #d19a66, #e5c07b, #98c379, #61afef, #c678dd, #e06c75)';

  function _isBgImage(c) { return typeof c === 'string' && c.startsWith('bg:'); }

  // A picture background is an upload on this site and nothing else: the
  // colour lands in a style on every card that shows the note, including a
  // collaborator's. An origin in front (older clients wrote one) is dropped.
  const BG_UPLOAD_RE = /^bg:(?:https?:\/\/[A-Za-z0-9.-]+(?::\d{1,5})?)?(\/api\/upload\/[0-9a-f]{32}(?:\.[a-z0-9]{1,16})?)$/i;
  function _bgImageUrl(c) { return (typeof c === 'string' && BG_UPLOAD_RE.exec(c.trim())?.[1]) || ''; }

  function _dotBg(value, noteColor) {
    if (value === 'custom') {
      const url = _bgImageUrl(noteColor);
      return url ? `center/cover no-repeat url('${url}')` : _CUSTOM_GRADIENT;
    }
    return COLOR_HEX[value];
  }

  function _dotIsActive(value, noteColor) {
    if (value === 'custom') return _isBgImage(noteColor);
    return value === (noteColor || '');
  }

  function _customColorStyle(c) {
    const url = _bgImageUrl(c);
    if (!url) return '';
    return `background-image: linear-gradient(color-mix(in srgb, var(--panel) 60%, transparent), color-mix(in srgb, var(--panel) 60%, transparent)), url('${url}'); background-size: cover; background-position: center; border-color: color-mix(in srgb, var(--fg) 25%, var(--border));`;
  }

  function _pickCustomBgImage() {
    return new Promise(resolve => {
      const input = document.createElement('input');
      input.type = 'file';
      input.accept = 'image/*';
      input.style.cssText = 'position:fixed; left:-9999px; top:-9999px;';
      document.body.appendChild(input);
      let done = false;
      const finish = (v) => { if (done) return; done = true; input.remove(); resolve(v); };
      input.addEventListener('change', async () => {
        const file = input.files?.[0];
        if (!file) return finish(null);
        const fd = new FormData();
        fd.append('files', file);
        try {
          const res = await api.upload(fd);
          const data = await res.json();
          const fileId = data.files?.[0]?.id;
          if (!fileId) throw new Error('Upload failed');
          finish(api.uploadUrl(fileId));
        } catch { finish(null); }
      });
      // Best-effort cleanup if user dismisses the dialog.
      setTimeout(() => { if (!done && !input.files?.length) finish(null); }, 30000);
      input.click();
    });
  }

  return { COLORS, COLOR_HEX, _isBgImage, _bgImageUrl, _dotBg, _dotIsActive, _customColorStyle, _pickCustomBgImage };
}
