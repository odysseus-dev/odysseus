/** Shared normalization for editable linear-gradient stops. */

export const MAX_GRADIENT_STOPS = 12;

function finite(value, fallback, min, max) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.max(min, Math.min(max, parsed)) : fallback;
}

function color(value, fallback) {
  const text = String(value || '').trim();
  return text || fallback;
}

export function legacyGradientStops({
  start = '#ffffff',
  mid = '#808080',
  midEnabled = false,
  midPosition = 50,
  end = '#000000',
} = {}) {
  const stops = [{ position: 0, color: color(start, '#ffffff') }];
  if (midEnabled) {
    stops.push({
      position: finite(midPosition, 50, 1, 99),
      color: color(mid, '#808080'),
    });
  }
  stops.push({ position: 100, color: color(end, '#000000') });
  return stops;
}

export function normalizeGradientStops(value, legacy = {}) {
  const source = Array.isArray(value) && value.length ? value : legacyGradientStops(legacy);
  const stops = source.map((stop, index) => ({
    position: finite(stop?.position, index === 0 ? 0 : 100, 0, 100),
    color: color(stop?.color, index === 0 ? '#ffffff' : '#000000'),
  }));
  stops.sort((a, b) => a.position - b.position);

  const unique = [];
  for (const stop of stops) {
    const previous = unique.at(-1);
    if (previous && Math.abs(previous.position - stop.position) < 0.0001) {
      unique[unique.length - 1] = stop;
    } else {
      unique.push(stop);
    }
  }
  if (!unique.length || unique[0].position > 0) {
    unique.unshift({ position: 0, color: unique[0]?.color || '#ffffff' });
  } else {
    unique[0].position = 0;
  }
  if (unique.at(-1).position < 100) {
    unique.push({ position: 100, color: unique.at(-1)?.color || '#000000' });
  } else {
    unique.at(-1).position = 100;
  }
  const first = unique[0];
  const last = unique.at(-1);
  return [first, ...unique.slice(1, -1).slice(0, MAX_GRADIENT_STOPS - 2), last];
}
