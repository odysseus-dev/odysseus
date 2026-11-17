/**
 * Color-mode support (PhotoCraft parity, Phase 10 slice).
 *
 * The editor works in an RGBA8 canvas, so only RGB and Grayscale are
 * convertible destructively; CMYK/Lab/Indexed remain metadata-only menu
 * entries (disabled) because they need a color-managed pipeline we do not
 * have. Bit depth is tracked as document metadata only.
 */

export const COLOR_MODES = Object.freeze(['rgb', 'grayscale', 'cmyk', 'lab', 'indexed']);
export const BIT_DEPTHS = Object.freeze([8, 16, 32]);

/** True for modes we can actually convert layer pixels into/out of. */
export function isConvertibleMode(mode) {
  return mode === 'rgb' || mode === 'grayscale';
}

/** Convert an ImageData-like buffer to grayscale in place (Rec. 709 luma). */
export function rgbaToGrayscale(data) {
  for (let i = 0; i < data.length; i += 4) {
    const luma = Math.round(0.2126 * data[i] + 0.7152 * data[i + 1] + 0.0722 * data[i + 2]);
    data[i] = luma;
    data[i + 1] = luma;
    data[i + 2] = luma;
  }
  return data;
}

/** Convert a single canvas to the requested mode in place. Returns true on success. */
export function convertCanvasToMode(canvas, mode) {
  if (!canvas || !canvas.getContext) return false;
  if (!isConvertibleMode(mode)) return false;
  if (mode === 'grayscale') {
    const ctx = canvas.getContext('2d');
    const image = ctx.getImageData(0, 0, canvas.width, canvas.height);
    rgbaToGrayscale(image.data);
    ctx.putImageData(image, 0, 0);
  }
  // RGB is the native working space; nothing to do.
  return true;
}

/** Apply a mode to every layer canvas. Returns the number of layers converted. */
export function applyColorMode(layers, mode) {
  if (!isConvertibleMode(mode)) return 0;
  let converted = 0;
  for (const layer of layers || []) {
    if (layer && layer.canvas && convertCanvasToMode(layer.canvas, mode)) converted += 1;
  }
  return converted;
}

/** Only 8 bits/channel is supported by the RGBA8 working canvas. */
export function supportsBitDepth(bits) {
  return bits === 8;
}
