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

  // Emit evenly spaced dabs from the last emitted sample toward `target`,
  // carrying the sub-spacing remainder forward. Shared by drag updates and
  // the final pointer-up so a stroke ends on the same spacing grid instead
  // of stamping one detached dab (which reads as a dot where drawing stopped).
  const emitSpaced = (target) => {
    if (!emitted) return;
    const start = { ...emitted };
    const dx = target.x - start.x;
    const dy = target.y - start.y;
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
        pressure: start.pressure + (target.pressure - start.pressure) * t,
      });
      travelled += spacing;
    }
    remainder = Math.max(0, distance - (travelled - spacing));
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
    emitSpaced(filtered);
  };

  const end = (sample) => {
    // Reach the true pointer-up point along the spacing grid rather than with
    // a single far dab, so the stroke does not terminate in a detached dot.
    if (sample && emitted) {
      emitSpaced({ ...sample, pressure: normalisePressure(sample.pressure) });
    }
    emitted = null;
    filtered = null;
    remainder = 0;
  };

  return { begin, add, end };
}
