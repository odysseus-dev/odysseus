(() => {
  const $ = (id) => document.getElementById(id);
  const audio = $('player');
  const el = {
    banner: $('banner'), nowTitle: $('now-title'), nowMeta: $('now-meta'),
    seek: $('seek'), tNow: $('t-now'), tTotal: $('t-total'),
    prev: $('prev'), playpause: $('playpause'), next: $('next'), volume: $('volume'),
    fav: $('fav'), min: $('min'), queue: $('queue'), queueEmpty: $('queue-empty'),
    favorites: $('favorites'), favEmpty: $('fav-empty'),
    form: $('search-form'), input: $('search-input'), counts: $('search-counts'),
    state: $('search-state'), results: $('results'),
  };

  const MSG_EXPIRED = 'Acceso no disponible';
  const MSG_NO_MUSIC = 'Tu invitación no incluye música.';

  class AccessError extends Error {
    constructor(kind) { super(kind); this.kind = kind; }
  }

  let ticket = '';
  let renewTimer = null;
  let queue = [];
  let current = -1;
  let accessOk = false;

  // search_all devuelve quality como texto (contrato antiguo) o como objeto con quality_label.
  const qualityText = (q) => (typeof q === 'string' ? q : (q && q.quality_label) || '');
  const fmt = (sec) => {
    if (!isFinite(sec) || sec < 0) sec = 0;
    return Math.floor(sec / 60) + ':' + String(Math.floor(sec % 60)).padStart(2, '0');
  };
  const fmtDur = (sec) => (sec >= 1 ? fmt(sec) : '');

  // ---- Persistencia (localStorage). Cada bloque en su clave; todo en try/catch. ----
  const PREFS_KEY = 'odysseus.music.prefs';
  const SESSION_KEY = 'odysseus.music.session';
  const LADDER = [64, 96, 128, 192, 320];
  const DEFAULT_PREFS = { quality: 'auto', preload: 'auto', rememberPos: true, volume: 1, compact: false, favorites: [] };
  let prefs = { ...DEFAULT_PREFS };
  try { prefs = { ...DEFAULT_PREFS, ...JSON.parse(localStorage.getItem(PREFS_KEY) || '{}') }; } catch (_) { /* sin persistencia */ }
  // Valores antiguos (0/5/15) se traducen: solo existen auto (inmediata) y metadata (bajo demanda).
  if (!['auto', 'metadata'].includes(prefs.preload)) prefs.preload = prefs.preload === '0' ? 'metadata' : 'auto';
  if (!Array.isArray(prefs.favorites)) prefs.favorites = [];

  function savePrefs() {
    try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch (_) { /* sin persistencia */ }
  }

  function saveSession() {
    try {
      const item = queue[current];
      localStorage.setItem(SESSION_KEY, JSON.stringify({
        queue, current, pos: item ? audio.currentTime : 0, ts: Date.now(),
      }));
    } catch (_) { /* sin persistencia */ }
  }

  function loadSession() {
    try {
      const s = JSON.parse(localStorage.getItem(SESSION_KEY) || 'null');
      if (!s || !Array.isArray(s.queue)) return null;
      return s;
    } catch (_) { return null; }
  }

  // ---- Calidad: Auto (escalera), fija, o FLAC ----
  const isAuto = () => prefs.quality === 'auto';
  const isFlac = () => prefs.quality === 'flac';
  let autoRung = 2;
  let autoLocked = false;
  let consecutiveDown = 0;
  let waitTimes = [];
  let lastWait = 0;
  let lastStep = 0;
  let reloadTimes = [];
  (function initialRung() {
    const dl = navigator.connection && navigator.connection.downlink;
    if (!dl) return;
    let best = 0;
    LADDER.forEach((br, i) => { if (br <= dl * 1000 * 0.6) best = i; });
    autoRung = best;
  })();

  const effectiveBr = () => (isAuto() ? LADDER[autoRung] : Number(prefs.quality));
  const effectiveQualityText = () => {
    if (isFlac()) return 'FLAC';
    return isAuto() ? 'Auto · Opus ' + effectiveBr() + 'k' : 'Opus ' + effectiveBr() + 'k';
  };
  const streamUrl = (rel) => {
    let url = '/api/music/stream?path=' + encodeURIComponent(rel) + '&ticket=' + encodeURIComponent(ticket);
    if (!isFlac()) url += '&fmt=opus&br=' + effectiveBr();
    return url;
  };

  // Recarga el stream de la pista actual en la posición exacta (presupuesto de 2 recargas/min).
  function reloadAt(pos, forced = false) {
    if (current < 0 || !queue[current]) return false;
    const now = Date.now();
    reloadTimes = reloadTimes.filter((t) => now - t < 60000);
    if (!forced && reloadTimes.length >= 2) return false;
    reloadTimes.push(now);
    const wasPlaying = !audio.paused;
    audio.addEventListener('loadedmetadata', function restorePos() {
      audio.removeEventListener('loadedmetadata', restorePos);
      audio.currentTime = pos;
      if (wasPlaying) audio.play().catch(() => {});
    });
    audio.src = streamUrl(queue[current].path);
    renderQuality();
    return true;
  }

  function renderQuality() {
    const label = $('quality-label');
    const note = $('quality-note');
    if (label) label.textContent = effectiveQualityText();
    if (note) note.hidden = isAuto();
  }

  function stepDown() {
    if (autoRung === 0) { autoLocked = true; return; }
    autoRung -= 1;
    consecutiveDown += 1;
    if (consecutiveDown >= 3) autoLocked = true; // tres bajadas sin mejorar: se queda abajo y deja de intentarlo
    lastStep = Date.now();
    reloadAt(audio.currentTime);
  }

  audio.addEventListener('waiting', () => {
    if (!isAuto() || autoLocked || current < 0) return;
    const now = Date.now();
    lastWait = now;
    waitTimes = waitTimes.filter((t) => now - t < 20000);
    waitTimes.push(now);
    if (waitTimes.length >= 2) { waitTimes = []; stepDown(); }
  });

  function maybeStepUp() {
    if (!isAuto() || autoLocked || autoRung >= LADDER.length - 1 || audio.paused) return;
    const now = Date.now();
    if (now - lastWait < 90000 || now - lastStep < 90000) return;
    const buffered = audio.buffered.length ? audio.buffered.end(audio.buffered.length - 1) - audio.currentTime : 0;
    if (buffered < 8) return;
    autoRung += 1;
    consecutiveDown = 0;
    lastStep = now;
    reloadAt(audio.currentTime);
  }

  // ---- Acceso y ticket ----
  function showBanner(msg) {
    el.banner.textContent = msg;
    el.banner.hidden = !msg;
  }

  async function getTicket() {
    const r = await fetch('/api/music/remote-ticket', { method: 'POST', credentials: 'same-origin' });
    if (r.status === 401) throw new AccessError('expired');
    if (r.status === 403) throw new AccessError('no-music');
    if (!r.ok) throw new Error('ticket ' + r.status);
    return r.json();
  }

  function scheduleRenew(expiresAt) {
    clearTimeout(renewTimer);
    const delay = Math.max(30, expiresAt - Date.now() / 1000 - 60) * 1000;
    renewTimer = setTimeout(renewTicket, delay);
  }

  // El ticket dura 10 min. Al renovarlo se vuelve a fijar el src conservando posición y estado.
  async function renewTicket() {
    try {
      const t = await getTicket();
      ticket = t.ticket;
      accessOk = true;
      showBanner('');
      if (current >= 0 && audio.getAttribute('src')) {
        const pos = audio.currentTime;
        const wasPlaying = !audio.paused;
        audio.src = streamUrl(queue[current].path);
        audio.currentTime = pos;
        if (wasPlaying) audio.play().catch(() => {});
      }
      scheduleRenew(t.expires_at);
    } catch (e) {
      handleAccessError(e);
      if (!(e instanceof AccessError)) renewTimer = setTimeout(renewTicket, 15000);
    }
  }

  function handleAccessError(e) {
    if (e instanceof AccessError) {
      accessOk = false;
      showBanner(e.kind === 'no-music' ? MSG_NO_MUSIC : MSG_EXPIRED);
      el.form.querySelector('button').disabled = true;
      el.input.disabled = true;
    } else {
      showBanner('Sin conexión con Odysseus. Reintentando…');
    }
  }

  // ---- Panel de opciones ----
  const prefQuality = $('pref-quality');
  const prefPreload = $('pref-preload');
  const prefRemember = $('pref-remember');
  const prefPanel = $('prefs');
  audio.preload = prefs.preload;
  audio.volume = Number(prefs.volume) || 1;
  el.volume.value = audio.volume;
  prefQuality.value = prefs.quality;
  prefPreload.value = prefs.preload;
  prefRemember.checked = !!prefs.rememberPos;
  prefQuality.addEventListener('change', () => {
    prefs.quality = prefQuality.value;
    if (isAuto()) { autoLocked = false; consecutiveDown = 0; }
    savePrefs();
    renderQuality();
    if (current >= 0 && audio.getAttribute('src')) reloadAt(audio.currentTime, true);
  });
  prefPreload.addEventListener('change', () => {
    prefs.preload = prefPreload.value;
    audio.preload = prefs.preload;
    savePrefs();
  });
  prefRemember.addEventListener('change', () => { prefs.rememberPos = prefRemember.checked; savePrefs(); });
  $('gear').addEventListener('click', () => { prefPanel.hidden = !prefPanel.hidden; });
  $('prefs-close').addEventListener('click', () => { prefPanel.hidden = true; });
  renderQuality();

  // ---- Compacto (minimizar): barra con título y transporte ----
  function applyCompact() {
    const card = document.querySelector('.card.now');
    card.classList.toggle('compact', !!prefs.compact);
    el.min.setAttribute('aria-pressed', String(!!prefs.compact));
    el.min.textContent = prefs.compact ? '▴' : '▾';
    el.min.setAttribute('aria-label', prefs.compact ? 'Expandir reproductor' : 'Minimizar reproductor');
  }
  el.min.addEventListener('click', () => {
    prefs.compact = !prefs.compact;
    savePrefs();
    applyCompact();
  });
  applyCompact();

  // ---- Favoritos: clave = path (local) o usuario+fichero (Soulseek) ----
  const favKey = (item) => (item.path ? 'L:' + item.path : 'R:' + item.username + '\n' + item.remote_filename);
  const isFav = (item) => prefs.favorites.some((f) => f.key === favKey(item));

  function toggleFav(item) {
    const key = favKey(item);
    if (prefs.favorites.some((f) => f.key === key)) {
      prefs.favorites = prefs.favorites.filter((f) => f.key !== key);
    } else {
      prefs.favorites.push({
        key, path: item.path || null, username: item.username || null, remote_filename: item.remote_filename || null,
        title: item.title || '', artist: item.artist || '', size_bytes: item.size_bytes || 0,
      });
    }
    savePrefs();
    renderFavorites();
    renderNow();
    renderResultFavs();
  }

  function renderFavorites() {
    el.favorites.replaceChildren();
    el.favEmpty.hidden = prefs.favorites.length > 0;
    prefs.favorites.forEach((f) => {
      const li = document.createElement('li');
      const info = document.createElement('div');
      info.className = 'info';
      const name = document.createElement('div');
      name.className = 'name';
      name.textContent = f.title || f.remote_filename || 'Sin título';
      const sub = document.createElement('div');
      sub.className = 'sub';
      sub.textContent = [f.artist, f.path ? 'Biblioteca' : 'Soulseek (pendiente de descarga)'].filter(Boolean).join(' · ');
      info.append(name, sub);
      li.append(info);
      if (f.path) {
        const play = document.createElement('button');
        play.type = 'button';
        play.className = 'act primary';
        play.textContent = '▶';
        play.setAttribute('aria-label', 'Reproducir favorita');
        play.addEventListener('click', () => playItem(f));
        const add = document.createElement('button');
        add.type = 'button';
        add.className = 'act';
        add.textContent = '+ Cola';
        add.addEventListener('click', () => addItem(f));
        li.append(play, add);
      }
      const rm = document.createElement('button');
      rm.type = 'button';
      rm.className = 'act danger';
      rm.textContent = '✕';
      rm.setAttribute('aria-label', 'Quitar de favoritos');
      rm.addEventListener('click', () => toggleFav(f));
      li.append(rm);
      el.favorites.append(li);
    });
  }

  // Botón ♡ de la pista actual.
  function renderFavButton() {
    const item = queue[current];
    el.fav.disabled = !item;
    const on = !!item && isFav(item);
    el.fav.textContent = on ? '♥' : '♡';
    el.fav.setAttribute('aria-pressed', String(on));
    el.fav.setAttribute('aria-label', on ? 'Quitar de favoritos' : 'Añadir a favoritos');
  }
  el.fav.addEventListener('click', () => { if (queue[current]) toggleFav(queue[current]); });

  // ---- Cola y reproducción ----
  function renderQueue() {
    el.queue.replaceChildren();
    el.queueEmpty.hidden = queue.length > 0;
    queue.forEach((item, i) => {
      const li = document.createElement('li');
      if (i === current) li.className = 'current';
      const info = document.createElement('div');
      info.className = 'info';
      const name = document.createElement('div');
      name.className = 'name';
      name.textContent = item.title || 'Sin título';
      const sub = document.createElement('div');
      sub.className = 'sub';
      sub.textContent = [item.artist, qualityText(item.quality), fmtDur(item.duration_sec)].filter(Boolean).join(' · ');
      info.append(name, sub);
      const play = document.createElement('button');
      play.type = 'button';
      play.className = 'act';
      play.textContent = i === current && !audio.paused ? 'Sonando' : 'Reproducir';
      play.addEventListener('click', () => playIndex(i));
      const rm = document.createElement('button');
      rm.type = 'button';
      rm.className = 'act';
      rm.textContent = 'Quitar';
      rm.setAttribute('aria-label', 'Quitar de la cola');
      rm.addEventListener('click', () => removeIndex(i));
      li.append(info, play, rm);
      el.queue.append(li);
    });
  }

  // Estado de la pista actual, para la tarjeta y el botón de reproducir.
  function renderNow() {
    const item = queue[current];
    el.nowTitle.textContent = item ? (item.title || 'Sin título') : 'Nada reproduciéndose';
    el.nowMeta.textContent = item ? (item.artist || '') : 'Busca una canción para empezar.';
    el.prev.disabled = current < 0;
    el.next.disabled = current < 0 && queue.length === 0;
    el.seek.disabled = !item;
    const playing = !!item && !audio.paused;
    el.playpause.textContent = playing ? '❚❚' : '▶';
    el.playpause.setAttribute('aria-label', playing ? 'Pausa' : 'Reproducir');
    renderFavButton();
    renderQuality();
  }

  function renderAll() {
    renderQueue();
    renderNow();
    renderFavorites();
    renderResultFavs();
  }

  // ---- Historial: se envía al cambiar de pista, al pausar y al terminar ----
  let playedSecs = 0;
  let lastT = 0;
  let reportedPath = null;
  let lastSave = 0;

  function flushPlay(completed) {
    const item = reportedPath && queue.find((q) => q.path === reportedPath);
    if (item && playedSecs > 0) {
      fetch('/api/music/plays', {
        method: 'POST',
        credentials: 'same-origin',
        keepalive: true,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: item.path, artist: item.artist || null, title: item.title || null, secs_played: playedSecs, completed }),
      }).catch(() => {});
    }
    playedSecs = 0;
    reportedPath = null;
  }

  function startReporting() {
    if (current >= 0 && !reportedPath) {
      reportedPath = queue[current].path;
      lastT = audio.currentTime;
    }
  }

  // ---- Canales 24/7: el servidor dice qué pista suena ahora y en qué segundo ----
  let channelName = null;

  function baseName(rel) {
    return rel.replace(/\\/g, '/').split('/').pop().replace(/\.[^.]+$/, '');
  }

  // El canal añade su pista a la cola (no la sustituye); la cola se conserva.
  async function playChannel(name) {
    channelName = name;
    markChannel(name);
    try {
      const r = await fetch('/api/music/channel/' + encodeURIComponent(name), { credentials: 'same-origin' });
      if (r.status === 401 || r.status === 403) throw new AccessError(r.status === 403 ? 'no-music' : 'expired');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const info = await r.json();
      const stem = baseName(info.track);
      const parts = stem.split(' - ');
      const item = { path: info.track, title: parts.length > 1 ? parts.slice(1).join(' - ') : stem, artist: parts.length > 1 ? parts[0] : '', quality: '', duration_sec: info.duration_sec };
      let i = queue.findIndex((q) => q.path === item.path);
      if (i < 0) { queue.push(item); i = queue.length - 1; }
      playIndex(i, info.offset_sec);
    } catch (e) {
      channelName = null;
      markChannel(null);
      if (e instanceof AccessError) handleAccessError(e);
      else el.nowMeta.textContent = 'No se pudo cargar el canal.';
    }
  }

  function markChannel(name) {
    document.querySelectorAll('#chips .chip').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.channel === name)));
  }

  async function loadChannels() {
    try {
      const r = await fetch('/api/music/channels', { credentials: 'same-origin' });
      if (!r.ok) return;
      const { channels } = await r.json();
      const chips = $('chips');
      channels.forEach((c) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'chip';
        b.dataset.channel = c.name;
        b.setAttribute('aria-pressed', 'false');
        b.textContent = c.label;
        const n = document.createElement('small');
        n.textContent = c.tracks;
        b.append(n);
        b.addEventListener('click', () => playChannel(c.name));
        chips.append(b);
      });
      if (channels.length) $('channels').hidden = false;
    } catch (_) { /* sin canales: la sección sigue oculta */ }
  }

  // Reproduce la posición i de la cola. offsetSec (opcional) fija el punto de inicio (canales).
  function playIndex(i, offsetSec = null) {
    if (i < 0 || i >= queue.length) return;
    if (!accessOk) { showBanner(MSG_EXPIRED); return; }
    flushPlay(false);
    current = i;
    audio.src = streamUrl(queue[i].path);
    let resume = offsetSec;
    if (resume == null && prefs.rememberPos && prefs.last && prefs.last.path === queue[i].path && prefs.last.pos > 5) {
      resume = prefs.last.pos;
    }
    if (resume != null) {
      audio.addEventListener('loadedmetadata', function restoreStart() {
        audio.removeEventListener('loadedmetadata', restoreStart);
        audio.currentTime = resume;
      });
    }
    audio.play().catch(() => { renderNow(); showBanner('Toca ▶ para reproducir.'); });
    renderAll();
    saveSession();
  }

  function playItem(item) {
    let i = queue.findIndex((q) => q.path === item.path);
    if (i < 0) { queue.push(item); i = queue.length - 1; }
    channelName = null;
    markChannel(null);
    playIndex(i);
  }

  function addItem(item) {
    if (!queue.some((q) => q.path === item.path)) queue.push(item);
    renderAll();
    saveSession();
  }

  // Quitar una pista: si es la actual, se para; la cola restante se conserva.
  function removeIndex(i) {
    if (i === current) {
      audio.pause();
      audio.removeAttribute('src');
      audio.load();
      current = -1;
    } else if (i < current) {
      current -= 1;
    }
    queue.splice(i, 1);
    renderAll();
    saveSession();
  }

  // Un único handler de pausa/reproducción. El estado visible sale de audio.paused tras cada acción,
  // no de eventos de media (en Android los eventos llegan tarde o no llegan si el play fue rechazado).
  function togglePlay() {
    if (current < 0) { if (queue.length) playIndex(0); return; }
    if (audio.paused) {
      audio.play().then(() => renderNow()).catch(() => {
        renderNow();
        showBanner('El navegador bloqueó la reproducción. Toca ▶ de nuevo.');
      });
    } else {
      audio.pause();
      renderNow();
    }
    renderNow();
  }

  // Siguiente: la cola manda; si no hay más, se añaden relacionadas a la pista actual (radio corta).
  async function nextTrack() {
    if (current + 1 < queue.length) { playIndex(current + 1); return; }
    const seed = queue[current];
    if (!seed || !seed.path) return;
    try {
      const r = await fetch('/api/music/related?path=' + encodeURIComponent(seed.path) + '&limit=10', { credentials: 'same-origin' });
      if (r.status === 401 || r.status === 403) throw new AccessError(r.status === 403 ? 'no-music' : 'expired');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const { items } = await r.json();
      const fresh = items.filter((it) => !queue.some((q) => q.path === it.path));
      if (!fresh.length) { el.nowMeta.textContent = 'No hay más pistas relacionadas.'; return; }
      fresh.forEach((it) => queue.push({ path: it.path, title: it.title, artist: it.artist, quality: '', duration_sec: null }));
      playIndex(current + 1);
    } catch (e) {
      if (e instanceof AccessError) handleAccessError(e);
      else el.nowMeta.textContent = 'No se pudo buscar la siguiente pista.';
    }
  }

  // ---- Radio y descubrimiento (sin polling: una petición por clic) ----
  async function radioFromCurrent() {
    const item = queue[current];
    if (!item) return;
    el.nowMeta.textContent = 'Cargando radio…';
    try {
      const r = await fetch('/api/music/radio?path=' + encodeURIComponent(item.path) + '&limit=30', { credentials: 'same-origin' });
      if (r.status === 401 || r.status === 403) throw new AccessError(r.status === 403 ? 'no-music' : 'expired');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      data.items.forEach((it) => {
        if (!queue.some((q) => q.path === it.path)) {
          queue.push({ path: it.path, title: it.title, artist: it.artist, quality: '', duration_sec: null });
        }
      });
      renderAll();
      saveSession();
      renderNow();
    } catch (e) {
      if (e instanceof AccessError) handleAccessError(e);
      else el.nowMeta.textContent = 'No se pudo cargar la radio.';
    }
  }

  async function showDiscovery() {
    el.results.replaceChildren();
    el.counts.textContent = '';
    setState('Buscando algo nuevo…');
    try {
      const r = await fetch('/api/music/discovery?limit=30', { credentials: 'same-origin' });
      if (r.status === 401 || r.status === 403) throw new AccessError(r.status === 403 ? 'no-music' : 'expired');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      setState(data.items.length ? '' : 'No hay nada nuevo que proponer todavía.');
      el.results.replaceChildren(...data.items.map((it) => resultRow({
        title: it.title, artist: it.artist, path: it.path, source: 'local', quality: '', duration_sec: null,
      })));
    } catch (e) {
      if (e instanceof AccessError) handleAccessError(e);
      else setState('No se pudo cargar el descubrimiento.');
    }
  }

  $('radio').addEventListener('click', radioFromCurrent);
  $('discover').addEventListener('click', showDiscovery);

  el.playpause.addEventListener('click', togglePlay);
  el.next.addEventListener('click', nextTrack);
  el.prev.addEventListener('click', () => {
    if (audio.currentTime > 3) audio.currentTime = 0;
    else if (current > 0) playIndex(current - 1);
  });
  el.seek.addEventListener('input', () => { audio.currentTime = Number(el.seek.value); });
  el.volume.addEventListener('input', () => { audio.volume = Number(el.volume.value); prefs.volume = audio.volume; savePrefs(); });
  loadChannels();

  audio.addEventListener('loadedmetadata', () => {
    el.seek.max = isFinite(audio.duration) ? audio.duration : 0;
    el.tTotal.textContent = fmt(audio.duration);
    if (queue[current] && !queue[current].duration_sec && isFinite(audio.duration)) {
      queue[current].duration_sec = audio.duration;
      renderQueue();
    }
  });
  audio.addEventListener('timeupdate', () => {
    maybeStepUp();
    if (prefs.rememberPos && queue[current] && audio.currentTime > 5) {
      prefs.last = { path: queue[current].path, pos: audio.currentTime };
    }
    if (reportedPath && !audio.paused) {
      const dt = audio.currentTime - lastT;
      if (dt > 0 && dt < 2) playedSecs += dt; // saltos (seek) no cuentan
      lastT = audio.currentTime;
    }
    // Posición y cola: como mucho cada 5 s durante la reproducción.
    if (Date.now() - lastSave > 5000) { lastSave = Date.now(); saveSession(); savePrefs(); }
    el.seek.value = audio.currentTime;
    const _d = audio.duration || 0;
    el.seek.style.setProperty('--p', (_d ? (audio.currentTime / _d) * 100 : 0) + '%');
    el.tNow.textContent = fmt(audio.currentTime);
  });
  audio.addEventListener('play', () => { startReporting(); renderNow(); renderQueue(); });
  audio.addEventListener('pause', () => { flushPlay(false); savePrefs(); saveSession(); renderNow(); renderQueue(); });
  audio.addEventListener('ended', () => {
    flushPlay(true);
    if (channelName && current + 1 >= queue.length) { playChannel(channelName); return; }
    if (current + 1 < queue.length) playIndex(current + 1);
    else nextTrack();
  });
  audio.addEventListener('error', () => {
    if (current >= 0 && audio.getAttribute('src')) {
      el.nowMeta.textContent = 'No se pudo reproducir esta pista.';
    }
  });

  // ---- Búsqueda: una petición por envío, sin sondeo ----
  function setState(msg) {
    el.state.textContent = msg;
  }

  // Descargas por clave usuario+fichero. El servidor avisa por SSE al terminar.
  const downloads = new Map();
  const dlKey = (username, file) => username + '\n' + file;

  function paintDownload(btn, state) {
    if (state === 'downloading') { btn.disabled = true; btn.textContent = 'Descargando…'; }
    else if (state === 'done') { btn.disabled = true; btn.textContent = 'Lista en biblioteca'; }
    else if (state === 'failed') { btn.disabled = false; btn.textContent = 'Falló · reintentar'; }
    else { btn.disabled = false; btn.textContent = '⤓ Descargar'; }
  }

  function resultRow(item) {
    const li = document.createElement('li');
    li._item = item;
    const info = document.createElement('div');
    info.className = 'info';
    const name = document.createElement('div');
    name.className = 'name';
    name.textContent = item.title || item.remote_filename || 'Sin título';
    const sub = document.createElement('div');
    sub.className = 'sub';
    const badge = document.createElement('span');
    badge.className = 'badge' + (item.source === 'slskd' ? ' remote' : '');
    badge.textContent = item.source === 'local' ? 'Biblioteca' : 'Soulseek';
    sub.append(badge, [item.artist, qualityText(item.quality), fmtDur(item.duration_sec)].filter(Boolean).join(' · '));
    info.append(name, sub);
    li.append(info);

    const fav = document.createElement('button');
    fav.type = 'button';
    fav.className = 'act fav-row';
    fav.dataset.fav = favKey(item);
    fav.addEventListener('click', () => toggleFav(item));
    paintFavRow(fav, item);

    if (item.source === 'local' && item.path) {
      const play = document.createElement('button');
      play.type = 'button';
      play.className = 'act primary';
      play.textContent = '▶';
      play.setAttribute('aria-label', 'Reproducir ' + (item.title || ''));
      play.addEventListener('click', () => playItem(item));
      const add = document.createElement('button');
      add.type = 'button';
      add.className = 'act';
      add.textContent = '+ Cola';
      add.addEventListener('click', () => addItem(item));
      li.append(play, add, fav);
    } else if (item.username && item.remote_filename) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'act';
      btn.textContent = '⤓ Descargar';
      btn.dataset.dl = dlKey(item.username, item.remote_filename);
      paintDownload(btn, downloads.get(btn.dataset.dl) || 'idle');
      btn.addEventListener('click', () => downloadRemote(item, btn));
      li.append(btn, fav);
    }
    return li;
  }

  function paintFavRow(btn, item) {
    const on = isFav(item);
    btn.textContent = on ? '♥' : '♡';
    btn.setAttribute('aria-pressed', String(on));
    btn.setAttribute('aria-label', on ? 'Quitar de favoritos' : 'Añadir a favoritos');
  }

  // Tras cambiar favoritos, repinta los ♡ de los resultados visibles.
  function renderResultFavs() {
    el.results.querySelectorAll('button.fav-row').forEach((btn) => {
      const li = btn.closest('li');
      const item = li && li._item;
      if (item) paintFavRow(btn, item);
    });
  }

  async function downloadRemote(item, btn) {
    const key = dlKey(item.username, item.remote_filename);
    btn.disabled = true;
    btn.textContent = 'Encolando…';
    try {
      const r = await fetch('/api/music/download/slskd', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: item.username, remote_filename: item.remote_filename, size_bytes: item.size_bytes || 0 }),
      });
      if (r.status === 401 || r.status === 403) throw new AccessError(r.status === 403 ? 'no-music' : 'expired');
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body.detail || 'HTTP ' + r.status);
      }
      downloads.set(key, 'downloading');
      paintDownload(btn, 'downloading');
    } catch (e) {
      if (e instanceof AccessError) handleAccessError(e);
      paintDownload(btn, 'failed');
      btn.title = e.message;
      setState('No se pudo iniciar la descarga: ' + e.message);
    }
  }

  // SSE: solo eventos de descarga; el resto del canal (estado del reproductor) se ignora.
  let es = null;
  function connectEvents() {
    if (es) es.close();
    es = new EventSource('/api/music/events?ticket=' + encodeURIComponent(ticket));
    es.onmessage = (ev) => {
      let d;
      try { d = JSON.parse(ev.data); } catch (_) { return; }
      if (d.type !== 'download') return;
      const key = dlKey(d.username, d.file);
      const state = d.state === 'done' ? 'done' : 'failed';
      downloads.set(key, state);
      el.results.querySelectorAll('button[data-dl]').forEach((btn) => {
        if (btn.dataset.dl === key) paintDownload(btn, state);
      });
    };
    es.onerror = () => {
      es.close();
      es = null;
      setTimeout(async () => {
        try { const t = await getTicket(); ticket = t.ticket; scheduleRenew(t.expires_at); } catch (_) { /* handleAccessError en la próxima acción */ }
        connectEvents();
      }, 5000);
    };
  }

  async function runSearch(q) {
    el.results.replaceChildren();
    el.counts.textContent = '';
    setState('Buscando…');
    try {
      const r = await fetch('/api/music/search_all?q=' + encodeURIComponent(q) + '&limit=20', { credentials: 'same-origin' });
      if (r.status === 401) throw new AccessError('expired');
      if (r.status === 403) throw new AccessError('no-music');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      const c = data.counts || {};
      el.counts.textContent = (c.local || 0) + ' en biblioteca · ' + (c.remote || 0) + ' en Soulseek · ' + (c.duplicates_removed || 0) + ' duplicados ocultos';
      if (!data.results || data.results.length === 0) {
        setState('No encontramos nada para “' + q + '”. Prueba con otras palabras.');
        return;
      }
      setState('');
      el.results.replaceChildren(...data.results.map(resultRow));
    } catch (e) {
      if (e instanceof AccessError) handleAccessError(e);
      else setState('No se pudo buscar. Revisa tu conexión e inténtalo de nuevo.');
    }
  }

  el.form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    const q = el.input.value.trim();
    if (q && accessOk) runSearch(q);
  });

  // Arranque: ticket, luego restaurar la sesión guardada (cola, pista y posición), sin autoplay.
  (async () => {
    const saved = loadSession();
    if (saved && saved.queue.length) {
      queue = saved.queue.filter((q) => q && q.path);
      if (typeof saved.current === 'number' && saved.current >= 0 && saved.current < queue.length) {
        current = saved.current;
        audio.src = '';
        const pos = Number(saved.pos) || 0;
        renderAll();
        try {
          const t = await getTicket();
          ticket = t.ticket;
          accessOk = true;
          scheduleRenew(t.expires_at);
          audio.src = streamUrl(queue[current].path);
          audio.addEventListener('loadedmetadata', function restoreSession() {
            audio.removeEventListener('loadedmetadata', restoreSession);
            audio.currentTime = pos;
          });
          connectEvents();
          renderAll();
          return;
        } catch (e) {
          handleAccessError(e);
          renderAll();
          return;
        }
      }
    }
    try {
      const t = await getTicket();
      ticket = t.ticket;
      accessOk = true;
      scheduleRenew(t.expires_at);
      connectEvents();
    } catch (e) {
      handleAccessError(e);
    }
    renderAll();
  })();
})();
