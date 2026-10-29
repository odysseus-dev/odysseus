// Pure aspect-fitting geometry for the editor. When a whole-image resize is
// asked for a target aspect that differs from the current image, content must
// be cropped to that aspect (centred on a focus point) and then scaled
// UNIFORMLY — never stretched.
//
// aspectFitCropRect returns the document-space rectangle to crop FIRST so a
// subsequent uniform scale-to-{targetW,targetH} preserves the subject aspect
// ratio. Returns the full image unchanged when the aspect already matches.

export function aspectFitCropRect({ width, height, focusX, focusY, targetAspect, tolerance = 0.005 } = {}) {
  const w = Number(width);
  const h = Number(height);
  const a = Number(targetAspect);
  if (!(w > 0) || !(h > 0) || !(a > 0)) return null;
  const curAspect = w / h;
  if (Math.abs(curAspect - a) / a <= tolerance) return { x: 0, y: 0, width: Math.round(w), height: Math.round(h) };
  const fx = Number.isFinite(Number(focusX)) ? Number(focusX) : w / 2;
  const fy = Number.isFinite(Number(focusY)) ? Number(focusY) : h / 2;
  let cw;
  let ch;
  if (curAspect > a) {
    ch = h;
    cw = Math.max(1, Math.round(ch * a));
  } else {
    cw = w;
    ch = Math.max(1, Math.round(cw / a));
  }
  let x = Math.round(fx - cw / 2);
  let y = Math.round(fy - ch / 2);
  x = Math.max(0, Math.min(w - cw, x));
  y = Math.max(0, Math.min(h - ch, y));
  return { x, y, width: cw, height: ch };
}

export default { aspectFitCropRect };
