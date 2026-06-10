// Frog-coder pet 🐸 — sits in the corner with a laptop and mirrors the agent:
// types while tools run, gets a thought cloud while the model thinks, and
// beams at you when the work is done. Click the frog to minimize it.
// Driven by tiny guarded hooks in chat.js: frogPet.onStream / mood changes
// piggyback on updateSubmitButton('streaming'|'idle').
(function () {
  'use strict';

  const STORE_KEY = 'odysseus-frog-pet'; // '0' = minimized
  const WORK_EMOJI = ['\u{1F4BB}', '⌨️', '✨', '\u{1F527}', '\u{1F4DD}', '\u{1F9D0}'];
  const TOOL_EMOJI = {
    bash: '⌨️', sh: '⌨️', shell: '⌨️',
    python: '\u{1F40D}',
    write_file: '\u{1F4DD}', edit_file: '\u{1F4DD}', create_document: '\u{1F4DD}',
    edit_document: '\u{1F4DD}', update_document: '\u{1F4DD}',
    read_file: '\u{1F4D6}', grep: '\u{1F50D}', glob: '\u{1F50D}', ls: '\u{1F50D}',
    web_search: '\u{1F310}', web_fetch: '\u{1F310}',
    generate_image: '\u{1F3A8}',
  };

  let root = null;
  let mood = 'idle';
  let lastEmote = 0;
  let happyTimer = null;
  let workEmoteTimer = null;

  const FROG_SVG = `
<svg class="frog-svg" viewBox="0 0 140 120" aria-hidden="true">
  <ellipse cx="28" cy="109" rx="16" ry="7" fill="#4e9a51"/>
  <ellipse cx="112" cy="109" rx="16" ry="7" fill="#4e9a51"/>
  <path d="M22 100 Q16 52 70 50 Q124 52 118 100 Q118 113 70 113 Q22 113 22 100 Z" fill="#66bb6a"/>
  <ellipse cx="70" cy="94" rx="31" ry="22" fill="#c8e6c9"/>
  <circle cx="48" cy="46" r="17" fill="#66bb6a"/>
  <circle cx="92" cy="46" r="17" fill="#66bb6a"/>
  <circle cx="48" cy="44" r="12" fill="#fff"/>
  <circle cx="92" cy="44" r="12" fill="#fff"/>
  <g class="frog-pupils">
    <circle cx="48" cy="46" r="5.5" fill="#263238"/>
    <circle cx="92" cy="46" r="5.5" fill="#263238"/>
    <circle cx="50" cy="44" r="1.8" fill="#fff"/>
    <circle cx="94" cy="44" r="1.8" fill="#fff"/>
  </g>
  <g class="frog-lids">
    <circle cx="48" cy="44" r="12.5" fill="#66bb6a"/>
    <circle cx="92" cy="44" r="12.5" fill="#66bb6a"/>
  </g>
  <g class="frog-cheeks">
    <ellipse cx="34" cy="62" rx="6" ry="3.5" fill="#f48fb1"/>
    <ellipse cx="106" cy="62" rx="6" ry="3.5" fill="#f48fb1"/>
  </g>
  <path class="frog-mouth frog-mouth-idle" d="M58 65 Q70 73 82 65" stroke="#33691e" stroke-width="2.5" fill="none" stroke-linecap="round"/>
  <path class="frog-mouth frog-mouth-focus" d="M62 67 Q70 69 78 67" stroke="#33691e" stroke-width="2.5" fill="none" stroke-linecap="round"/>
  <g class="frog-mouth frog-mouth-happy">
    <path d="M54 63 Q70 84 86 63 Z" fill="#5d3a32"/>
    <path d="M62 71 Q70 78 78 71 Q70 82 62 71 Z" fill="#ef9a9a"/>
  </g>
  <g class="frog-laptop">
    <rect class="frog-lap-screen" x="42" y="74" width="56" height="27" rx="4" fill="#37474f" stroke="#263238" stroke-width="1.5"/>
    <circle class="frog-lap-logo" cx="70" cy="87" r="4.5" fill="#9ccc65"/>
    <rect x="38" y="100" width="64" height="7" rx="3" fill="#455a64"/>
  </g>
  <ellipse class="frog-hand frog-hand-l" cx="50" cy="101" rx="7" ry="5" fill="#66bb6a"/>
  <ellipse class="frog-hand frog-hand-r" cx="90" cy="101" rx="7" ry="5" fill="#66bb6a"/>
</svg>`;

  const CLOUD_HTML = `
<div class="frog-cloud" aria-hidden="true">
  <svg viewBox="0 0 24 24" class="frog-spiral">
    <path d="M12 12 a1.2 1.2 0 0 1 1.2 1.2 a2.4 2.4 0 0 1 -2.4 2.4 a4 4 0 0 1 -4 -4 a5.8 5.8 0 0 1 5.8 -5.8 a7.6 7.6 0 0 1 7.6 7.6 a9.4 9.4 0 0 1 -3.4 7.2"
          fill="none" stroke="#90a4ae" stroke-width="1.8" stroke-linecap="round"/>
  </svg>
  <span class="frog-cloud-dot frog-cloud-d1"></span>
  <span class="frog-cloud-dot frog-cloud-d2"></span>
</div>`;

  function build() {
    if (root || !document.body) return;
    root = document.createElement('div');
    root.id = 'frog-pet';
    root.className = 'frog-idle';
    root.title = 'Ribbit. (click to minimize)';
    root.innerHTML = CLOUD_HTML + FROG_SVG +
      '<div class="frog-emotes" aria-hidden="true"></div>' +
      '<button class="frog-restore" title="Bring the frog back" aria-label="Show frog pet">\u{1F438}</button>';
    document.body.appendChild(root);

    if (localStorage.getItem(STORE_KEY) === '0') root.classList.add('frog-min');

    root.querySelector('.frog-svg').addEventListener('click', () => {
      root.classList.add('frog-min');
      localStorage.setItem(STORE_KEY, '0');
    });
    root.querySelector('.frog-restore').addEventListener('click', () => {
      root.classList.remove('frog-min');
      localStorage.setItem(STORE_KEY, '1');
      emote('\u{1F438}', true);
    });
  }

  function setMood(m) {
    if (!root || mood === m) return;
    mood = m;
    root.classList.remove('frog-idle', 'frog-thinking', 'frog-typing', 'frog-happy');
    root.classList.add('frog-' + m);
    clearInterval(workEmoteTimer);
    workEmoteTimer = null;
    if (m === 'typing') {
      // Occasional little work emoji while hacking away.
      workEmoteTimer = setInterval(() => {
        emote(WORK_EMOJI[Math.floor(Math.random() * WORK_EMOJI.length)]);
      }, 4200);
    }
  }

  function emote(ch, force) {
    if (!root || root.classList.contains('frog-min')) return;
    const now = Date.now();
    if (!force && now - lastEmote < 1500) return;
    lastEmote = now;
    const layer = root.querySelector('.frog-emotes');
    if (!layer) return;
    const el = document.createElement('span');
    el.className = 'frog-emote';
    el.textContent = ch;
    el.style.left = (18 + Math.random() * 56) + 'px';
    layer.appendChild(el);
    el.addEventListener('animationend', () => el.remove(), { once: true });
    setTimeout(() => el.remove(), 2500); // belt-and-braces cleanup
  }

  window.frogPet = {
    // A new request went out — frog waits for the model, pondering.
    onStart() {
      build();
      clearTimeout(happyTimer);
      setMood('thinking');
    },
    // One hook per SSE event from chat.js; `thinking` is chat.js's live flag.
    onStream(json, thinking) {
      if (!root) build();
      clearTimeout(happyTimer);
      if (json.type === 'tool_start') {
        setMood('typing');
        const t = String(json.tool || '').toLowerCase();
        emote(TOOL_EMOJI[t] || '\u{1F527}');
      } else if (json.thinking || thinking) {
        setMood('thinking');
      } else if (json.delta || json.type === 'doc_stream_delta') {
        setMood('typing');
      }
    },
    // Stream settled — beam at the viewer, then settle back to idle.
    onDone() {
      if (!root || mood === 'idle') return; // page-load/idle resets shouldn't celebrate
      setMood('happy');
      emote('\u{1F389}', true);
      clearTimeout(happyTimer);
      happyTimer = setTimeout(() => setMood('idle'), 6000);
    },
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', build);
  } else {
    build();
  }
})();
