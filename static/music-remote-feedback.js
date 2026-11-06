(() => {
  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.className = 'fb-toggle';
  toggle.textContent = '💬';
  toggle.setAttribute('aria-label', 'Enviar comentario');

  const panel = document.createElement('section');
  panel.className = 'fb-panel';
  panel.hidden = true;
  panel.setAttribute('aria-label', 'Comentarios');

  const head = document.createElement('div');
  head.className = 'fb-head';
  const title = document.createElement('h2');
  title.textContent = 'Comentario';
  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'fb-close';
  close.textContent = '✕';
  close.setAttribute('aria-label', 'Cerrar comentarios');
  head.append(title, close);
  const area = document.createElement('textarea');
  area.maxLength = 2000;
  area.placeholder = '¿Qué no funciona o qué echas de menos?';
  const row = document.createElement('div');
  row.className = 'fb-row';
  const status = document.createElement('span');
  status.className = 'fb-status';
  const send = document.createElement('button');
  send.type = 'button';
  send.textContent = 'Enviar';
  row.append(status, send);
  panel.append(head, area, row);
  document.body.append(toggle, panel);

  // Cierre: X, Escape, o tocar fuera del panel (el botón 💬 alterna).
  const setOpen = (open) => { panel.hidden = !open; if (open) area.focus(); };
  toggle.addEventListener('click', () => setOpen(panel.hidden));
  close.addEventListener('click', () => setOpen(false));
  document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape' && !panel.hidden) setOpen(false); });
  document.addEventListener('pointerdown', (ev) => {
    if (!panel.hidden && !panel.contains(ev.target) && !toggle.contains(ev.target)) setOpen(false);
  });

  function say(msg, cls) {
    status.textContent = msg;
    status.className = 'fb-status' + (cls ? ' ' + cls : '');
  }

  send.addEventListener('click', async () => {
    const text = area.value.trim();
    if (!text) { say('Escribe algo antes de enviar.', 'err'); return; }
    send.disabled = true;
    say('Enviando…');
    try {
      const r = await fetch('/api/feedback', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, page: location.pathname }),
      });
      if (r.status === 429) throw new Error('Has enviado muchos comentarios esta hora.');
      if (r.status === 401) throw new Error('Tu sesión caducó.');
      if (!r.ok) throw new Error('No se pudo enviar (' + r.status + ').');
      area.value = '';
      say('Enviado. Gracias.', 'ok');
    } catch (e) {
      say(e.message, 'err');
    } finally {
      send.disabled = false;
    }
  });
})();
