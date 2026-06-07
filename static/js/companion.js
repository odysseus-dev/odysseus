// Companion app pairing: Tools -> "Companion app" opens a QR (and manual
// details) so a phone running the Odysseus companion can pair. Mints a fresh
// chat-scoped token via POST /api/companion/pair (admin-only, cookie auth) and
// renders it as a self-contained overlay -- intentionally NOT wired into the
// draggable modal/tile manager, so it stays simple and can't break that system.
//
// Two modes via a toggle: "This network" (LAN address) and "Anywhere" (a
// Tailscale/tunnel address). The Anywhere tab shows a risk walkthrough the user
// must read and Continue past before the remote QR is revealed.

const BTN_ID = 'tool-companion-btn';
const OVERLAY_ID = 'companion-pair-overlay';

let pairData = null; // last fetched pairing payload
let tab = 'local'; // 'local' | 'remote'
let remoteAck = false; // user clicked Continue on the risk screen

function close() {
  document.getElementById(OVERLAY_ID)?.remove();
  document.removeEventListener('keydown', onKey);
}

function onKey(e) {
  if (e.key === 'Escape') close();
}

function pickLocal() {
  const o = pairData && pairData.options;
  if (!o || !o.length) return null;
  return o.find((x) => x.kind === 'local') || o[0];
}

function pickRemote() {
  const o = pairData && pairData.options;
  if (!o) return null;
  return o.find((x) => x.kind === 'tailscale') || o.find((x) => x.kind === 'other') || null;
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

function renderOption(body, opt, hintText) {
  if (opt && opt.qr) {
    const img = document.createElement('img');
    img.className = 'companion-qr';
    img.alt = 'Pairing QR code';
    img.src = opt.qr;
    body.appendChild(img);
  }
  const hint = document.createElement('p');
  hint.className = 'companion-hint';
  hint.textContent = hintText;
  body.appendChild(hint);
  if (opt) {
    body.appendChild(field('Server', `${opt.host}:${opt.port}`));
  }
  body.appendChild(field('Pairing token', pairData.token));
  const note = document.createElement('p');
  note.className = 'companion-note';
  note.textContent =
    'One-time code, shown once. Revoke it anytime under Settings -> API tokens.';
  body.appendChild(note);
}

function renderRisk(body) {
  const h = document.createElement('div');
  h.className = 'companion-risk';
  h.innerHTML =
    '<p class="companion-risk-title">Before you pair for access anywhere</p>' +
    '<ul class="companion-risk-list">' +
    '<li>The pairing token grants chat and tool access to this PC (including files and the terminal when those are enabled). Treat it like a password.</li>' +
    '<li>Anyone who has the token <em>and</em> can reach your tunnel could control Odysseus. If a phone is lost, revoke the token under Settings -> API tokens.</li>' +
    '<li>Use a private tunnel such as Tailscale or Cloudflare Tunnel. Do NOT forward router ports or expose Odysseus directly to the internet.</li>' +
    '<li>Only pair devices you trust.</li>' +
    '</ul>';
  body.appendChild(h);
  const btn = document.createElement('button');
  btn.className = 'companion-continue';
  btn.type = 'button';
  btn.textContent = 'I understand - continue';
  btn.addEventListener('click', () => {
    remoteAck = true;
    paint(body.parentElement.querySelector('.companion-body') || body);
  });
  body.appendChild(btn);
}

function renderNoTunnel(body) {
  const d = document.createElement('div');
  d.className = 'companion-risk';
  d.innerHTML =
    '<p class="companion-risk-title">No tunnel address detected</p>' +
    '<p class="companion-hint" style="text-align:left">To reach Odysseus from any network:</p>' +
    '<ul class="companion-risk-list">' +
    '<li>Install <strong>Tailscale</strong> on this PC and your phone, signed into the same account (free for personal use; private and encrypted).</li>' +
    '<li>Reopen this dialog - your Tailscale address will appear here with its own QR.</li>' +
    '</ul>' +
    '<p class="companion-note">You can still pair manually in the app with your tunnel address and the token below.</p>';
  body.appendChild(d);
  body.appendChild(field('Pairing token', pairData.token));
}

function segButton(label, active, onClick) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'companion-seg-btn' + (active ? ' active' : '');
  b.textContent = label;
  b.addEventListener('click', onClick);
  return b;
}

function paint(body) {
  body.innerHTML = '';
  const seg = document.createElement('div');
  seg.className = 'companion-seg';
  seg.appendChild(
    segButton('This network', tab === 'local', () => {
      tab = 'local';
      paint(body);
    }),
  );
  seg.appendChild(
    segButton('Anywhere', tab === 'remote', () => {
      tab = 'remote';
      paint(body);
    }),
  );
  body.appendChild(seg);

  if (tab === 'local') {
    renderOption(
      body,
      pickLocal(),
      'Scan in the Odysseus app (Pair -> Scan pairing code), or enter the details below. Works on this Wi-Fi.',
    );
  } else if (!remoteAck) {
    renderRisk(body);
  } else {
    const remote = pickRemote();
    if (remote && remote.qr) {
      renderOption(body, remote, 'Use this from any network (it works at home too). Keep your tunnel running.');
    } else {
      renderNoTunnel(body);
    }
  }
}

async function open() {
  close();
  tab = 'local';
  remoteAck = false;
  pairData = null;
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
    pairData = await resp.json();
    paint(body);
  } catch (e) {
    body.innerHTML =
      '<p class="companion-err">Could not generate a pairing code. ' +
      'Pairing is admin-only -- make sure you are signed in as an admin.</p>';
    console.warn('companion pair failed', e);
  }
}

const btn = document.getElementById(BTN_ID);
if (btn) btn.addEventListener('click', open);
