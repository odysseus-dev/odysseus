// Companion app pairing: Tools -> "Companion app" opens a QR (and manual
// details) so a phone running the Odysseus companion can pair. Mints a fresh
// chat-scoped token via POST /api/companion/pair (admin-only, cookie auth) and
// renders it as a self-contained overlay -- intentionally NOT wired into the
// draggable modal/tile manager, so it stays simple and can't break that system.

const BTN_ID = 'tool-companion-btn';
const OVERLAY_ID = 'companion-pair-overlay';

function close() {
  document.getElementById(OVERLAY_ID)?.remove();
  document.removeEventListener('keydown', onKey);
}

function onKey(e) {
  if (e.key === 'Escape') close();
}

function field(label, value) {
  const row = document.createElement('div');
  row.className = 'companion-field';
  const wrap = document.createElement('div');
  wrap.style.minWidth = '0';
  const lab = document.createElement('div');
  lab.className = 'companion-field-label';
  lab.textContent = label;
  const code = document.createElement('code');
  code.textContent = value;
  wrap.appendChild(lab);
  wrap.appendChild(code);
  const copy = document.createElement('button');
  copy.className = 'companion-copy';
  copy.type = 'button';
  copy.textContent = 'Copy';
  copy.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(value);
      copy.textContent = 'Copied';
      setTimeout(() => (copy.textContent = 'Copy'), 1500);
    } catch {
      /* clipboard blocked; user can select manually */
    }
  });
  row.appendChild(wrap);
  row.appendChild(copy);
  return row;
}

function render(body, data) {
  body.innerHTML = '';

  if (data.qr) {
    const img = document.createElement('img');
    img.className = 'companion-qr';
    img.alt = 'Pairing QR code';
    img.src = data.qr; // server-built data:image/png;base64 URI
    body.appendChild(img);
  }

  const hint = document.createElement('p');
  hint.className = 'companion-hint';
  hint.textContent = data.qr
    ? 'Scan this in the Odysseus app (Pair -> Scan pairing code), or enter the details below.'
    : 'Enter these in the Odysseus app to pair.';
  body.appendChild(hint);

  body.appendChild(field('Server', `${data.host}:${data.port}`));
  body.appendChild(field('Pairing token', data.token));

  const note = document.createElement('p');
  note.className = 'companion-note';
  note.textContent =
    'One-time code, shown once. Revoke it anytime under Settings -> API tokens. ' +
    'Phone and PC must reach each other (same network, or a private tunnel).';
  body.appendChild(note);
}

async function open() {
  close();
  const overlay = document.createElement('div');
  overlay.className = 'companion-overlay';
  overlay.id = OVERLAY_ID;
  overlay.innerHTML =
    '<div class="companion-card" role="dialog" aria-label="Companion app" aria-modal="true">' +
    '<div class="companion-head"><h4>Companion app</h4>' +
    '<button class="companion-close" type="button" aria-label="Close">&#x2716;</button></div>' +
    '<div class="companion-body"><p class="companion-hint">Generating pairing code...</p></div>' +
    '</div>';
  document.body.appendChild(overlay);
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) close();
  });
  overlay.querySelector('.companion-close').addEventListener('click', close);
  document.addEventListener('keydown', onKey);

  const body = overlay.querySelector('.companion-body');
  try {
    const resp = await fetch('/api/companion/pair?format=json', { method: 'POST' });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    render(body, await resp.json());
  } catch (e) {
    body.innerHTML =
      '<p class="companion-err">Could not generate a pairing code. ' +
      'Pairing is admin-only -- make sure you are signed in as an admin.</p>';
    console.warn('companion pair failed', e);
  }
}

const btn = document.getElementById(BTN_ID);
if (btn) btn.addEventListener('click', open);
