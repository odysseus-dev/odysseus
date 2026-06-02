// Mobile App settings tab — fetches a pairing code from the companion bridge
// and renders a scannable QR + the host/port/token for manual entry.
//
// The endpoint (/api/companion/pair?format=json) is admin-only and mints a
// fresh chat-scoped token each call, invalidating the auth cache so it works
// immediately. We render the server-built QR data-URI; no client QR lib needed.

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

async function generatePairingCode(btn, out) {
  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = 'Generating…';
  out.innerHTML = '';
  try {
    const res = await fetch('/api/companion/pair?format=json', {
      headers: { Accept: 'application/json' },
      credentials: 'same-origin',
    });
    if (res.status === 403) throw new Error('Admin only — log in as an admin to pair a device.');
    if (!res.ok) throw new Error('Server responded ' + res.status + '.');
    const d = await res.json();

    const qr = d.qr
      ? `<img src="${esc(d.qr)}" alt="Pairing QR" width="240" height="240" style="border-radius:10px;background:#fff;padding:8px">`
      : `<p style="opacity:0.7">QR unavailable — enter the details manually.</p>`;
    const others = (d.hosts || []).slice(1).join(', ') || '—';

    out.innerHTML = `
      <div style="display:flex;flex-direction:column;align-items:center;gap:14px;margin-bottom:14px">
        ${qr}
        <div style="width:100%;max-width:360px;text-align:left;font-size:13px;line-height:1.9">
          <div><strong>Host</strong><span style="float:right"><code>${esc(d.host)}</code></span></div>
          <div><strong>Port</strong><span style="float:right"><code>${esc(d.port)}</code></span></div>
          <div><strong>Token</strong><span style="float:right"><code style="word-break:break-all">${esc(d.token)}</code></span></div>
          <div style="opacity:0.6"><strong>Other addresses</strong><span style="float:right">${esc(others)}</span></div>
        </div>
        <div style="font-size:11px;opacity:0.6">Scan from the Odysseus Mobile app, or type the host / port / token in.</div>
      </div>`;
    btn.textContent = 'Generate a new code';
  } catch (e) {
    out.innerHTML = `<p style="color:var(--accent-error,#e0685e);font-size:13px">${esc(e.message || 'Could not generate a pairing code.')}</p>`;
    btn.textContent = original;
  } finally {
    btn.disabled = false;
  }
}

function init() {
  const btn = document.getElementById('companion-pair-btn');
  const out = document.getElementById('companion-pair-out');
  if (!btn || !out || btn.dataset.wired) return;
  btn.dataset.wired = '1';
  btn.addEventListener('click', () => generatePairingCode(btn, out));
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
