/** Minimal Adobe Photoshop (PSD) reader/writer.
 *
 * Exports a flattened 8-bit RGB composite as a valid `8BPS` document with
 * no layer records, and parses the same shape back. Also decodes RLE
 * (PackBits) image data, which is what Photoshop normally writes.
 */

const SIGNATURE = 0x38425053; // '8BPS'
const COLOR_MODE_RGB = 3;
const COLOR_MODE_GRAYSCALE = 1;
const COMPRESSION_RAW = 0;
const COMPRESSION_RLE = 1;

function writeUint16(view, offset, value) {
  view.setUint16(offset, value & 0xffff, false);
}

function writeUint32(view, offset, value) {
  view.setUint32(offset, value >>> 0, false);
}

function writeUint64(view, offset, value) {
  view.setBigUint64(offset, BigInt(value), false);
}

/** PackBits (RLE) encode a byte array. */
export function packBits(input) {
  const out = [];
  const length = input.length;
  let i = 0;
  while (i < length) {
    let runLength = 1;
    while (i + runLength < length && runLength < 128 && input[i + runLength] === input[i]) runLength++;
    if (runLength >= 2) {
      out.push((256 - (runLength - 1)) & 0xff, input[i]);
      i += runLength;
      continue;
    }
    let literalStart = i;
    let literalLength = 0;
    while (i < length && literalLength < 128) {
      let nextRun = 1;
      while (i + nextRun < length && nextRun < 128 && input[i + nextRun] === input[i]) nextRun++;
      if (nextRun >= 2 && literalLength > 0) break;
      i += 1;
      literalLength += 1;
      if (i >= length) break;
    }
    if (i === literalStart) i += 1;
    const count = i - literalStart;
    out.push(count - 1, ...input.slice(literalStart, i));
  }
  return Uint8Array.from(out);
}

/** PackBits (RLE) decode `length` bytes starting at `offset`. */
export function unpackBits(data, offset, length) {
  const out = new Uint8Array(length);
  let outPos = 0;
  let pos = offset;
  while (outPos < length && pos < data.length) {
    const header = data[pos] < 128 ? data[pos] : data[pos] - 256;
    pos += 1;
    if (header >= 0) {
      const count = header + 1;
      for (let i = 0; i < count && outPos < length; i++) out[outPos++] = data[pos + i];
      pos += count;
    } else if (header !== -128) {
      const count = 1 - header;
      const value = data[pos++];
      for (let i = 0; i < count && outPos < length; i++) out[outPos++] = value;
    }
  }
  return out;
}

/** Serialize a canvas as a flattened 8-bit RGB PSD. */
export function exportPsd(sourceCanvas) {
  const width = sourceCanvas.width;
  const height = sourceCanvas.height;
  const pixels = sourceCanvas.getContext('2d').getImageData(0, 0, width, height).data;
  const pixelCount = width * height;
  const red = new Uint8Array(pixelCount);
  const green = new Uint8Array(pixelCount);
  const blue = new Uint8Array(pixelCount);
  for (let i = 0; i < pixelCount; i++) {
    red[i] = pixels[i * 4];
    green[i] = pixels[i * 4 + 1];
    blue[i] = pixels[i * 4 + 2];
  }
  const channels = [red, green, blue];
  const rowLengths = [];
  const rows = [];
  for (const channel of channels) {
    let channelLength = 0;
    for (let y = 0; y < height; y++) {
      const row = packBits(channel.subarray(y * width, (y + 1) * width));
      rowLengths.push(row.length);
      rows.push(row);
      channelLength += row.length;
    }
  }
  const rleTableBytes = rowLengths.length * 2;
  const rleBodyBytes = rows.reduce((sum, row) => sum + row.length, 0);
  const headerBytes = 26;
  const imageDataBytes = 2 + rleTableBytes + rleBodyBytes;
  const total = headerBytes + 4 * 3 + imageDataBytes;
  const buffer = new ArrayBuffer(total);
  const bytes = new Uint8Array(buffer);
  const view = new DataView(buffer);
  let offset = 0;
  writeUint32(view, 0, SIGNATURE);
  writeUint16(view, 4, 1);
  offset = 10; // reserved 6 bytes
  writeUint16(view, offset, 3); offset += 2;
  writeUint32(view, offset, height); offset += 4;
  writeUint32(view, offset, width); offset += 4;
  writeUint16(view, offset, 8); offset += 2;
  writeUint16(view, offset, COLOR_MODE_RGB); offset += 2;
  // Color mode data + image resources + layer/mask sections are empty.
  writeUint32(view, offset, 0); offset += 4;
  writeUint32(view, offset, 0); offset += 4;
  writeUint32(view, offset, 0); offset += 4;
  writeUint16(view, offset, COMPRESSION_RLE); offset += 2;
  for (const length of rowLengths) {
    writeUint16(view, offset, length); offset += 2;
  }
  for (const row of rows) {
    bytes.set(row, offset);
    offset += row.length;
  }
  return bytes;
}

function readSection(view, bytes, offset, long = false) {
  if (long) {
    const length = Number(view.getBigUint64(offset, false));
    return offset + 8 + length;
  }
  const length = view.getUint32(offset, false);
  return offset + 4 + length;
}

/** Parse an 8BPS buffer into `{ width, height, canvas }`. */
export function parsePsd(arrayBuffer) {
  const bytes = new Uint8Array(arrayBuffer);
  const view = new DataView(arrayBuffer);
  if (view.getUint32(0, false) !== SIGNATURE) throw new Error('Not a PSD file');
  const version = view.getUint16(4, false);
  if (version !== 1 && version !== 2) throw new Error('Unsupported PSD version');
  const psb = version === 2;
  const channels = view.getUint16(12, false);
  const height = view.getUint32(14, false);
  const width = view.getUint32(18, false);
  const depth = view.getUint16(22, false);
  const colorMode = view.getUint16(24, false);
  if (depth !== 8) throw new Error('Unsupported PSD bit depth');
  if (colorMode !== COLOR_MODE_RGB && colorMode !== COLOR_MODE_GRAYSCALE) {
    throw new Error('Unsupported PSD color mode');
  }
  let offset = 26;
  offset = readSection(view, bytes, offset); // color mode data (4-byte length)
  offset = readSection(view, bytes, offset); // image resources (4-byte length)
  offset = readSection(view, bytes, offset, psb); // layer & mask info (8-byte length in PSB)
  const compression = view.getUint16(offset, false);
  offset += 2;
  const pixelCount = width * height;
  const planes = [];
  if (compression === COMPRESSION_RAW) {
    for (let c = 0; c < channels; c++) {
      planes.push(bytes.subarray(offset, offset + pixelCount));
      offset += pixelCount;
    }
  } else if (compression === COMPRESSION_RLE) {
    const rowCount = channels * height;
    const rowLengths = [];
    for (let i = 0; i < rowCount; i++) {
      rowLengths.push(psb ? view.getUint32(offset, false) : view.getUint16(offset, false));
      offset += psb ? 4 : 2;
    }
    for (let c = 0; c < channels; c++) {
      const plane = new Uint8Array(pixelCount);
      for (let y = 0; y < height; y++) {
        const rowIndex = c * height + y;
        const decoded = unpackBits(bytes, offset, rowLengths[rowIndex]);
        plane.set(decoded.subarray(0, width), y * width);
        offset += rowLengths[rowIndex];
      }
      planes.push(plane);
    }
  } else {
    throw new Error('Unsupported PSD compression');
  }
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  const image = ctx.createImageData(width, height);
  const isGray = colorMode === COLOR_MODE_GRAYSCALE;
  for (let i = 0; i < pixelCount; i++) {
    const r = planes[0] ? planes[0][i] : 0;
    const g = isGray ? r : (planes[1] ? planes[1][i] : 0);
    const b = isGray ? r : (planes[2] ? planes[2][i] : 0);
    image.data[i * 4] = r;
    image.data[i * 4 + 1] = g;
    image.data[i * 4 + 2] = b;
    image.data[i * 4 + 3] = 255;
  }
  ctx.putImageData(image, 0, 0);
  return { width, height, canvas };
}

// ---------------------------------------------------------------------------
// Layered PSD (8BPS v1, 8-bit RGB) — layer records + raw channel planes.
// Encoder/decoder operate on plain RGBA arrays (no canvas) so they are
// usable and testable outside the browser.
// ---------------------------------------------------------------------------

function rgbaToPlanes(data, width, height) {
  const count = width * height;
  const r = new Uint8Array(count);
  const g = new Uint8Array(count);
  const b = new Uint8Array(count);
  const a = new Uint8Array(count);
  for (let i = 0; i < count; i++) {
    r[i] = data[i * 4];
    g[i] = data[i * 4 + 1];
    b[i] = data[i * 4 + 2];
    a[i] = data[i * 4 + 3];
  }
  return [r, g, b, a];
}

function blendRgbaInto(target, layer, docWidth, docHeight) {
  const lw = layer.width;
  const lh = layer.height;
  const ox = layer.x | 0;
  const oy = layer.y | 0;
  const alpha = layer.opacity == null ? 1 : layer.opacity;
  const data = layer.data;
  for (let y = 0; y < lh; y++) {
    const ty = oy + y;
    if (ty < 0 || ty >= docHeight) continue;
    for (let x = 0; x < lw; x++) {
      const tx = ox + x;
      if (tx < 0 || tx >= docWidth) continue;
      const si = (y * lw + x) * 4;
      const ti = (ty * docWidth + tx) * 4;
      const sa = (data[si + 3] / 255) * alpha;
      if (sa <= 0) continue;
      const da = target[ti + 3] / 255;
      const outA = sa + da * (1 - sa);
      for (let c = 0; c < 3; c++) {
        const sc = data[si + c];
        const dc = target[ti + c];
        target[ti + c] = outA ? Math.round((sc * sa + dc * da * (1 - sa)) / outA) : 0;
      }
      target[ti + 3] = Math.round(outA * 255);
    }
  }
}

function latin1Bytes(text) {
  const out = [];
  for (const char of String(text)) out.push(char.charCodeAt(0) & 0xff);
  return out;
}

/** Serialize layered RGBA layers + a flattened composite as an 8BPS PSD
 *  (version 1) or PSB (version 2, "large document format", 8-byte lengths). */
export function encodeLayeredPsd({ width, height, layers = [], psb = false } = {}) {
  const longLen = psb ? 8 : 4;
  const composite = new Uint8ClampedArray(width * height * 4);
  const ordered = layers.slice().reverse(); // PSD stores bottom-first
  for (const layer of ordered) {
    if (layer.visible === false) continue;
    blendRgbaInto(composite, layer, width, height);
  }

  const records = [];
  const channelBlocks = [];
  for (const layer of ordered) {
    const lw = layer.width;
    const lh = layer.height;
    const planes = rgbaToPlanes(layer.data, lw, lh);
    const channelIds = [0, 1, 2, -1];
    const channelLength = 2 + lw * lh;
    const name = latin1Bytes(layer.name || 'Layer');
    const namePadded = 1 + name.length + ((4 - ((1 + name.length) % 4)) % 4);
    const extraLength = 4 + 4 + namePadded;

    const record = new Uint8Array(4 * 4 + 2 + channelIds.length * (2 + longLen) + 4 + 4 + 4 + 4 + extraLength);
    const rv = new DataView(record.buffer);
    let o = 0;
    rv.setInt32(o, layer.y | 0, false); o += 4;
    rv.setInt32(o, layer.x | 0, false); o += 4;
    rv.setInt32(o, (layer.y | 0) + lh, false); o += 4;
    rv.setInt32(o, (layer.x | 0) + lw, false); o += 4;
    rv.setUint16(o, channelIds.length, false); o += 2;
    for (const id of channelIds) {
      rv.setInt16(o, id, false); o += 2;
      if (psb) { rv.setBigUint64(o, BigInt(channelLength), false); o += 8; }
      else { rv.setUint32(o, channelLength, false); o += 4; }
    }
    record.set([0x38, 0x42, 0x49, 0x4d], o); o += 4; // '8BIM'
    record.set(latin1Bytes((layer.blendKey || 'norm').padEnd(4, ' ').slice(0, 4)), o); o += 4;
    record[o++] = Math.round((layer.opacity == null ? 1 : layer.opacity) * 255);
    record[o++] = 0; // clipping
    record[o++] = layer.visible === false ? 2 : 0; // flags (bit1 = hidden)
    record[o++] = 0; // filler
    rv.setUint32(o, extraLength, false); o += 4;
    rv.setUint32(o, 0, false); o += 4; // layer mask data length
    rv.setUint32(o, 0, false); o += 4; // layer blending ranges length
    record[o++] = name.length;
    record.set(name, o); o += name.length;
    while (o < record.length) record[o++] = 0; // name padding
    records.push(record);

    for (const plane of planes) {
      const block = new Uint8Array(2 + plane.length);
      block[0] = 0;
      block[1] = COMPRESSION_RAW;
      block.set(plane, 2);
      channelBlocks.push(block);
    }
  }

  const recordBytes = records.reduce((sum, record) => sum + record.length, 0);
  const channelBytes = channelBlocks.reduce((sum, block) => sum + block.length, 0);
  const layerInfoLength = 2 + recordBytes + channelBytes;

  const layerInfo = new Uint8Array(longLen + layerInfoLength);
  const liv = new DataView(layerInfo.buffer);
  if (psb) liv.setBigUint64(0, BigInt(layerInfoLength), false); else liv.setUint32(0, layerInfoLength, false);
  liv.setInt16(longLen, ordered.length, false);
  let p = longLen + 2;
  for (const record of records) { layerInfo.set(record, p); p += record.length; }
  for (const block of channelBlocks) { layerInfo.set(block, p); p += block.length; }

  const sectionDataLength = layerInfo.length + 4; // layerInfo blob + global mask (0)
  const section = new Uint8Array(longLen + sectionDataLength);
  const sv = new DataView(section.buffer);
  if (psb) sv.setBigUint64(0, BigInt(sectionDataLength), false); else sv.setUint32(0, sectionDataLength, false);
  section.set(layerInfo, longLen);
  sv.setUint32(longLen + layerInfo.length, 0, false); // global layer mask info length

  // Composite image data (RLE), matching exportPsd.
  const compositePlanes = rgbaToPlanes(composite, width, height);
  const rowLengths = [];
  const rows = [];
  for (const plane of compositePlanes) {
    for (let y = 0; y < height; y++) {
      const row = packBits(plane.subarray(y * width, (y + 1) * width));
      rowLengths.push(row.length);
      rows.push(row);
    }
  }
  const rleTableBytes = rowLengths.length * (psb ? 4 : 2);
  const rleBodyBytes = rows.reduce((sum, row) => sum + row.length, 0);
  const imageDataBytes = 2 + rleTableBytes + rleBodyBytes;

  const headerBytes = 26;
  const total = headerBytes + 4 + 4 + section.length + imageDataBytes;
  const buffer = new ArrayBuffer(total);
  const bytes = new Uint8Array(buffer);
  const view = new DataView(buffer);
  writeUint32(view, 0, SIGNATURE);
  writeUint16(view, 4, psb ? 2 : 1);
  writeUint16(view, 12, 3); // channels
  writeUint32(view, 14, height);
  writeUint32(view, 18, width);
  writeUint16(view, 22, 8);
  writeUint16(view, 24, COLOR_MODE_RGB);
  writeUint32(view, 26, 0); // color mode data
  writeUint32(view, 30, 0); // image resources
  let offset = 34;
  bytes.set(section, offset); offset += section.length;
  writeUint16(view, offset, COMPRESSION_RLE); offset += 2;
  for (const length of rowLengths) {
    if (psb) { writeUint32(view, offset, length); offset += 4; }
    else { writeUint16(view, offset, length); offset += 2; }
  }
  for (const row of rows) { bytes.set(row, offset); offset += row.length; }
  return bytes;
}

/** Decode a layered PSD into `{ width, height, layers }` (RGBA planes, no canvas). */
export function decodeLayeredPsd(arrayBuffer) {
  const bytes = new Uint8Array(arrayBuffer);
  const view = new DataView(arrayBuffer);
  if (view.getUint32(0, false) !== SIGNATURE) throw new Error('Not a PSD file');
  const version = view.getUint16(4, false);
  if (version !== 1 && version !== 2) throw new Error('Unsupported PSD version');
  const psb = version === 2;
  const longLen = psb ? 8 : 4;
  const height = view.getUint32(14, false);
  const width = view.getUint32(18, false);
  if (view.getUint16(22, false) !== 8) throw new Error('Unsupported PSD bit depth');
  let offset = 26;
  offset = readSection(view, bytes, offset); // color mode data (4-byte length)
  offset = readSection(view, bytes, offset); // image resources (4-byte length)
  const sectionLength = psb ? Number(view.getBigUint64(offset, false)) : view.getUint32(offset, false);
  offset += longLen;
  if (!sectionLength) return { width, height, layers: [] };
  const sectionEnd = offset + sectionLength;
  const layerInfoLength = psb ? Number(view.getBigUint64(offset, false)) : view.getUint32(offset, false);
  offset += longLen;
  const layerInfoEnd = offset + layerInfoLength;
  const rawCount = view.getInt16(offset, false);
  offset += 2;
  const layerCount = Math.abs(rawCount);
  const metas = [];
  for (let l = 0; l < layerCount; l++) {
    const top = view.getInt32(offset, false); offset += 4;
    const left = view.getInt32(offset, false); offset += 4;
    const bottom = view.getInt32(offset, false); offset += 4;
    const right = view.getInt32(offset, false); offset += 4;
    const channelCount = view.getUint16(offset, false); offset += 2;
    const channels = [];
    for (let c = 0; c < channelCount; c++) {
      const id = view.getInt16(offset, false); offset += 2;
      const length = psb ? Number(view.getBigUint64(offset, false)) : view.getUint32(offset, false);
      offset += longLen;
      channels.push({ id, length });
    }
    offset += 4; // '8BIM'
    const blendKey = String.fromCharCode(bytes[offset], bytes[offset + 1], bytes[offset + 2], bytes[offset + 3]); offset += 4;
    const opacity = bytes[offset++] / 255;
    offset += 1; // clipping
    const flags = bytes[offset++];
    offset += 1; // filler
    const extraLength = view.getUint32(offset, false); offset += 4;
    const extraEnd = offset + extraLength;
    const maskLength = view.getUint32(offset, false); offset += 4 + maskLength;
    const rangesLength = view.getUint32(offset, false); offset += 4 + rangesLength;
    const nameLength = bytes[offset++];
    let name = '';
    for (let i = 0; i < nameLength; i++) name += String.fromCharCode(bytes[offset + i]);
    offset = extraEnd;
    metas.push({ top, left, bottom, right, channelCount, channels, blendKey, opacity, visible: (flags & 2) === 0, name });
  }
  // Channel image data follows the records; the metas loop above already
  // left `offset` at the start of the channel data.
  const layers = [];
  for (const meta of metas) {
    const lw = meta.right - meta.left;
    const lh = meta.bottom - meta.top;
    const count = lw * lh;
    const planes = { 0: new Uint8Array(count), 1: new Uint8Array(count), 2: new Uint8Array(count), '-1': new Uint8Array(count) };
    for (let c = 0; c < meta.channelCount; c++) {
      const channel = meta.channels[c];
      const compression = view.getUint16(offset, false);
      offset += 2;
      if (compression === COMPRESSION_RAW) {
        const plane = bytes.subarray(offset, offset + count);
        if (planes[channel.id]) planes[channel.id].set(plane);
        offset += count;
      } else if (compression === COMPRESSION_RLE) {
        const rowLengths = [];
        for (let y = 0; y < lh; y++) {
          rowLengths.push(psb ? view.getUint32(offset, false) : view.getUint16(offset, false));
          offset += psb ? 4 : 2;
        }
        const plane = planes[channel.id] || new Uint8Array(count);
        for (let y = 0; y < lh; y++) {
          const decoded = unpackBits(bytes, offset, rowLengths[y]);
          plane.set(decoded.subarray(0, lw), y * lw);
          offset += rowLengths[y];
        }
        planes[channel.id] = plane;
      } else {
        throw new Error('Unsupported PSD layer compression');
      }
    }
    const data = new Uint8ClampedArray(count * 4);
    const alpha = planes['-1'];
    for (let i = 0; i < count; i++) {
      data[i * 4] = planes[0][i];
      data[i * 4 + 1] = planes[1][i];
      data[i * 4 + 2] = planes[2][i];
      data[i * 4 + 3] = alpha ? alpha[i] : 255;
    }
    layers.push({
      name: meta.name,
      x: meta.left,
      y: meta.top,
      width: lw,
      height: lh,
      opacity: meta.opacity,
      visible: meta.visible,
      blendKey: meta.blendKey,
      data,
    });
  }
  return { width, height, layers: layers.reverse() };
}
