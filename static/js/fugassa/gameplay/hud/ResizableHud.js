const MIN_CHAT = 180;
const MIN_CENTER = 220;
const MIN_RIGHT = 180;
// Party cards stack the portrait above the HP/AC block (not side-by-side —
// see fugassa.css .fugassa-hud-party-card), so the bar needs enough height
// for both the portrait banner and the text underneath it, not just a
// single text-row's worth of height.
const MIN_PARTY = 112;
const MAX_PARTY = 440;

export function clampHudSplits(splits, container) {
  const rect = container.getBoundingClientRect();
  const w = Math.max(rect.width, 640);
  // chat and right are the only user-resizable, fixed-pixel columns; center
  // (flex: 1 1 auto in CSS) always auto-fills whatever remains between
  // them, so it is deliberately NOT computed/stored here — a hand-computed
  // "center = w - chat - right - fudge" pixel value drifts out of sync the
  // moment the surrounding markup gains/loses a wrapper, margin or splitter.
  const chat = Math.max(MIN_CHAT, Math.min(w - MIN_CENTER - MIN_RIGHT - 24, Number(splits.chat) || 280));
  const right = Math.max(MIN_RIGHT, Math.min(w - chat - MIN_CENTER - 24, Number(splits.right) || 220));
  const party = Math.max(MIN_PARTY, Math.min(MAX_PARTY, Number(splits.party) || 118));
  const top = Math.max(32, Math.min(56, Number(splits.top) || 40));
  return { top, chat, right, party };
}

export function applyHudSplits(root, splits) {
  const chat = root.querySelector('.fugassa-hud-chat');
  const right = root.querySelector('.fugassa-hud-right');
  const party = root.querySelector('.fugassa-hud-party');
  const top = root.querySelector('.fugassa-hud-top');
  if (top) top.style.minHeight = `${splits.top}px`;
  // Set an explicit `flex` (not just `width`) so this fixed-pixel sizing
  // can never be silently overridden by a `flex` shorthand elsewhere in
  // the stylesheet — see the .fugassa-hud-right flex:0 0 auto rule this
  // used to conflict with.
  if (chat) chat.style.flex = `0 0 ${splits.chat}px`;
  if (right) right.style.flex = `0 0 ${splits.right}px`;
  if (party) party.style.height = `${splits.party}px`;
  // .fugassa-hud-center is flex:1 1 auto in CSS and fills the center column
  // above the party bar — no JS width needed.
}

export function wireHudSplitters(root, getSplits, setSplits) {
  const bind = (sel, axis, apply) => {
    const handle = root.querySelector(sel);
    if (!handle) return;
    handle.addEventListener('mousedown', (ev) => {
      ev.preventDefault();
      const start = ev[axis];
      const base = { ...getSplits() };
      const move = (e) => {
        const delta = e[axis] - start;
        const next = apply(base, delta);
        setSplits(next);
      };
      const up = () => {
        window.removeEventListener('mousemove', move);
        window.removeEventListener('mouseup', up);
      };
      window.addEventListener('mousemove', move);
      window.addEventListener('mouseup', up);
    });
  };
  bind('[data-split="chat"]', 'clientX', (s, d) => ({ ...s, chat: s.chat + d }));
  // Dragging this splitter only needs to resize `right` — `center` isn't a
  // stored split anymore, it auto-fills whatever space that leaves behind.
  bind('[data-split="center"]', 'clientX', (s, d) => ({ ...s, right: Math.max(MIN_RIGHT, s.right - d) }));
  bind('[data-split="party"]', 'clientY', (s, d) => ({ ...s, party: Math.max(MIN_PARTY, s.party - d) }));
}
