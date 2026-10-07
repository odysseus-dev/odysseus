/**
 * Device-independent brush sampling. Rendering stays in stroke-pipeline.js;
 * this module only turns irregular pointer input into evenly spaced samples.
 */

export function normalisePressure(value) {
  const pressure = Number(value);
  if (!Number.isFinite(pressure) || pressure <= 0) return 1;
  return Math.max(0.01, Math.min(1, pressure));
}

export function pressureValue(base, pressure, enabled, minimum = 0.08) {
  if (!enabled) return base;
  return base * Math.max(minimum, normalisePressure(pressure));
}

export function createBrushSampler({ getDiameter, getSpacing, getSmoothing, emit }) {
  let emitted = null;
  let filtered = null;
  let remainder = 0;

  const output = (sample) => {
    emitted = { ...sample };
    emit(emitted);
  };

  const begin = (sample) => {
    emitted = null;
    filtered = { ...sample, pressure: normalisePressure(sample.pressure) };
    remainder = 0;
    output(filtered);
  };

  const add = (sample) => {
    const raw = { ...sample, pressure: normalisePressure(sample.pressure) };
    if (!filtered || !emitted) return begin(raw);
    const smoothing = Math.max(0, Math.min(0.95, Number(getSmoothing()) || 0));
    const response = 1 - smoothing;
    filtered = {
      x: filtered.x + (raw.x - filtered.x) * response,
      y: filtered.y + (raw.y - filtered.y) * response,
      pressure: filtered.pressure + (raw.pressure - filtered.pressure) * response,
    };
    const start = { ...emitted };
    const dx = filtered.x - start.x;
    const dy = filtered.y - start.y;
    const distance = Math.hypot(dx, dy);
    if (!distance) return;
    const diameter = Math.max(1, Number(getDiameter()) || 1);
    const spacing = Math.max(0.5, diameter * Math.max(0.01, Number(getSpacing()) || 0.01));
    let travelled = spacing - remainder;
    while (travelled <= distance) {
      const t = travelled / distance;
      output({
        x: start.x + dx * t,
        y: start.y + dy * t,
        pressure: start.pressure + (filtered.pressure - start.pressure) * t,
      });
      travelled += spacing;
    }
    remainder = Math.max(0, distance - (travelled - spacing));
  };

  const end = (sample) => {
    if (sample && emitted) {
      const finalSample = { ...sample, pressure: normalisePressure(sample.pressure) };
      if (Math.hypot(finalSample.x - emitted.x, finalSample.y - emitted.y) > 0.25) output(finalSample);
    }
    emitted = null;
    filtered = null;
    remainder = 0;
  };

  return { begin, add, end };
}
