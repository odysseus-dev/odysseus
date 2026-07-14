export function mountPartyBar(el, { party, saveId, onMemberClick } = {}) {
  el.className = 'fugassa-hud-party';
  el.innerHTML = '<div class="fugassa-hud-party-row"></div>';
  const row = el.querySelector('.fugassa-hud-party-row');
  const members = Array.isArray(party) && party.length ? party : [{ name: 'Hero', hp: 100, max_hp: 100 }];

  members.forEach((member, index) => {
    const card = document.createElement('div');
    card.className = 'fugassa-hud-party-card';
    if (typeof onMemberClick === 'function') {
      card.classList.add('fugassa-hud-party-card--clickable');
      card.tabIndex = 0;
      card.setAttribute('role', 'button');
      card.setAttribute('aria-label', `View ${member.name || 'party member'}`);
      card.addEventListener('click', () => onMemberClick(member, index));
      card.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter' || ev.key === ' ') {
          ev.preventDefault();
          onMemberClick(member, index);
        }
      });
    }
    const hp = Number(member.hp ?? 100);
    const maxHp = Number(member.max_hp ?? 100) || 100;
    const pct = Math.max(0, Math.min(100, Math.round((hp / maxHp) * 100)));
    const name = member.name || 'Hero';
    const portraitUrl = member.portrait_file && saveId
      ? `/api/fugassa/saves/${encodeURIComponent(saveId)}/assets/${encodeURIComponent(member.portrait_file)}`
      : null;
    const avatar = portraitUrl
      ? `<img class="fugassa-hud-party-avatar" src="${portraitUrl}" alt="" />`
      : `<div class="fugassa-hud-party-avatar fugassa-hud-party-avatar--placeholder">${escapeHtml(name.charAt(0).toUpperCase())}</div>`;
    card.innerHTML = `
      ${avatar}
      <div class="fugassa-hud-party-info">
        <div class="fugassa-hud-party-name">${escapeHtml(name)}</div>
        <div class="fugassa-hud-party-meta fugassa-muted">HP ${hp}/${maxHp} · AC ${Number(member.ac ?? 12)}</div>
        <div class="fugassa-hud-party-hp"><span style="width:${pct}%"></span></div>
      </div>
    `;
    row.appendChild(card);
  });
}

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}
