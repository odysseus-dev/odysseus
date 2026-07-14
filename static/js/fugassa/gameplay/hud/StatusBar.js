export function mountStatusBar(el, {
  saveId,
  turn,
  turnPhase,
  campaignPhase,
  pipelineLocked,
  currentJobLabel,
  inCombat,
  partySize = 0,
  onBackToMenu,
  onUndo,
  canUndo,
  onPause,
}) {
  el.innerHTML = '';
  el.className = 'fugassa-hud-top';

  const left = document.createElement('div');
  left.className = 'fugassa-hud-top-left';
  const back = document.createElement('button');
  back.type = 'button';
  back.className = 'fugassa-btn fugassa-btn--ghost fugassa-btn--sm';
  back.textContent = '← Menu';
  back.addEventListener('click', () => onBackToMenu?.());
  const pause = document.createElement('button');
  pause.type = 'button';
  pause.className = 'fugassa-btn fugassa-btn--ghost fugassa-btn--sm';
  pause.textContent = 'Pause';
  pause.addEventListener('click', () => onPause?.());
  left.append(back, pause);

  const center = document.createElement('div');
  center.className = 'fugassa-hud-top-center';
  let phaseLabel = '';
  if (turnPhase === 'processing') phaseLabel = ' · GM…';
  else if (campaignPhase === 'generating_assets' || pipelineLocked) {
    phaseLabel = currentJobLabel ? ` · ${currentJobLabel}` : ' · Generating…';
  }
  const companionCount = Math.max(0, Number(partySize) - 1);
  const partyLine = companionCount > 0 ? ` · party ${Number(partySize)}` : '';
  center.innerHTML = `<strong>${escapeHtml(saveId || 'Campaign')}</strong><span class="fugassa-muted">Turn ${Number(turn) || 0}${partyLine}${phaseLabel}</span>`;

  const right = document.createElement('div');
  right.className = 'fugassa-hud-top-right';
  if (canUndo) {
    const undo = document.createElement('button');
    undo.type = 'button';
    undo.className = 'fugassa-btn fugassa-btn--ghost fugassa-btn--sm';
    undo.textContent = 'Undo';
    undo.addEventListener('click', () => onUndo?.());
    right.appendChild(undo);
  }
  if (inCombat) {
    const tag = document.createElement('span');
    tag.className = 'fugassa-hud-combat-tag';
    tag.textContent = 'Combat';
    right.appendChild(tag);
  }

  el.append(left, center, right);
}

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}
