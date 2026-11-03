/** Stateless celebration renderer shared by Notes and Compare. */
export function spawnConfetti(cx, cy, count) {
  const colors = ['#ffd700', '#ff6b6b', '#5b8def', '#51cf66', '#ff922b', '#cc5de8', '#22b8cf', '#fff'];
  for (let i = 0; i < count; i++) {
    const el = document.createElement('div');
    el.className = 'confetti-piece';
    const color = colors[Math.floor(Math.random() * colors.length)];
    const size = 5 + Math.random() * 8;
    const isCircle = Math.random() > 0.5;
    el.style.width = size + 'px';
    el.style.height = (isCircle ? size : size * 0.6) + 'px';
    el.style.background = color;
    el.style.borderRadius = isCircle ? '50%' : '2px';
    el.style.left = cx + 'px';
    el.style.top = cy + 'px';
    const angle = Math.random() * Math.PI * 2;
    const speed = 60 + Math.random() * 160;
    const dx = Math.cos(angle) * speed;
    const dy = Math.sin(angle) * speed - 100;
    const duration = 1.0 + Math.random() * 1.0;
    el.animate([
      { transform: 'translate(0, 0) rotate(0deg) scale(1)', opacity: 1 },
      { transform: `translate(${dx}px, ${dy + 200}px) rotate(${400 + Math.random() * 400}deg) scale(0)`, opacity: 0 }
    ], { duration: duration * 1000, easing: 'cubic-bezier(0.15, 0.6, 0.35, 1)', fill: 'forwards' });
    document.body.appendChild(el);
    setTimeout(() => el.remove(), duration * 1000 + 50);
  }
}
