/** Note colors, custom background selection and form color controls. */
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

  function _bgImageUrl(c) { return _isBgImage(c) ? c.slice(3) : ''; }

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
    if (!_isBgImage(c)) return '';
    const url = _bgImageUrl(c);
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

  function _wireFormColors(form, setColor) {
    // Color dots : apply to entire form immediately
    const _applyFormColor = (newColor) => {
      const currentColor = newColor || '';
      setColor(currentColor);
      form.dataset.noteColor = currentColor;
      const isBg = _isBgImage(currentColor);
      COLORS.forEach(c => { if (c.value && c.value !== 'custom') form.classList.remove('note-color-' + c.value); });
      if (currentColor && !isBg) form.classList.add('note-color-' + currentColor);
      if (isBg) form.setAttribute('style', _customColorStyle(currentColor));
      else form.removeAttribute('style');
      form.querySelectorAll('.note-color-dot').forEach(d => {
        d.classList.toggle('active', _dotIsActive(d.dataset.color, currentColor));
        d.style.background = _dotBg(d.dataset.color, currentColor);
      });
      form.dispatchEvent(new Event('change', { bubbles: true }));
    };
    form.querySelectorAll('.note-color-dot').forEach(dot => {
      dot.addEventListener('click', () => {
        if (dot.dataset.color === 'custom') {
          _pickCustomBgImage().then(url => { if (url) _applyFormColor('bg:' + url); });
          return;
        }
        _applyFormColor(dot.dataset.color);
      });
    });


  }

  return { COLORS, COLOR_HEX, _isBgImage, _bgImageUrl, _dotBg, _dotIsActive, _customColorStyle, _pickCustomBgImage, _wireFormColors };
}
