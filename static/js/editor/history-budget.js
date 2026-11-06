/** Adaptive bounds for raw ImageData history snapshots. */

export const MAX_HISTORY_ENTRIES = 30;
export const MAX_HISTORY_BYTES = 192 * 1024 * 1024;

function imageDataBytes(imageData) {
  return Number(imageData?.data?.byteLength || imageData?.data?.length || 0);
}

function pixelSlots(snapshot) {
  const slots = [];
  const add = (object, key = 'imageData') => { if (object?.[key]) slots.push([object, key]); };
  add(snapshot?.wand); add(snapshot?.lastSelection);
  for (const item of snapshot?.savedSelections || []) add(item);
  for (const group of snapshot?.layerGroups || []) {
    for (const mask of group.masks || []) add(mask);
    for (const effect of group.effects || []) add(effect.mask);
  }
  for (const layer of snapshot?.layers || []) {
    add(layer); add(layer.placed, 'sourceImageData');
    for (const mask of layer.masks || []) add(mask);
    for (const effect of layer.effects || []) add(effect.mask);
  }
  return slots;
}

function samePixels(image, other) {
  if (image === other) return true;
  if (image.width !== other.width || image.height !== other.height || image.data.length !== other.data.length) return false;
  const length = image.data.length;
  // Compare 4 bytes at a time: the byte-by-byte loop dominated selection
  // commits on large documents (every layer of the previous snapshot was
  // re-scanned one byte at a time).
  if (length % 4 === 0) {
    const a = new Uint32Array(image.data.buffer, image.data.byteOffset, length >> 2);
    const b = new Uint32Array(other.data.buffer, other.data.byteOffset, length >> 2);
    for (let i = 0; i < a.length; i += 1) if (a[i] !== b[i]) return false;
    return true;
  }
  for (let i = 0; i < length; i += 1) if (image.data[i] !== other.data[i]) return false;
  return true;
}

export function shareSnapshotPixels(snapshot, previous) {
  if (!previous) return snapshot;
  const candidates = pixelSlots(previous).map(([object, key]) => object[key]);
  for (const [object, key] of pixelSlots(snapshot)) {
    const image = object[key];
    const match = candidates.find(other => samePixels(image, other));
    if (match) object[key] = match;
  }
  return snapshot;
}

export function historyByteSize(stack) {
  const buffers = new Set();
  let bytes = 0;
  for (const snapshot of stack) {
    const slots = pixelSlots(snapshot);
    if (!slots.length) { bytes += snapshotByteSize(snapshot); continue; }
    for (const [object, key] of slots) {
      const image = object[key], buffer = image.data.buffer || image.data;
      if (!buffers.has(buffer)) { buffers.add(buffer); bytes += buffer.byteLength || imageDataBytes(image); }
    }
  }
  return bytes;
}

export function snapshotByteSize(snapshot) {
  if (!snapshot) return 0;
  if (Number.isFinite(snapshot._bytes)) return snapshot._bytes;
  let bytes = imageDataBytes(snapshot.wand?.imageData);
  bytes += imageDataBytes(snapshot.lastSelection?.imageData);
  for (const selection of snapshot.savedSelections || []) bytes += imageDataBytes(selection.imageData);
  for (const group of snapshot.layerGroups || []) {
    for (const mask of group.masks || []) bytes += imageDataBytes(mask.imageData);
  }
  for (const layer of snapshot.layers || []) {
    bytes += imageDataBytes(layer.imageData);
    bytes += imageDataBytes(layer.placed?.sourceImageData);
    for (const mask of layer.masks || []) bytes += imageDataBytes(mask.imageData);
  }
  return bytes;
}

export function trimHistoryStack(
  stack,
  maxEntries = MAX_HISTORY_ENTRIES,
  maxBytes = MAX_HISTORY_BYTES,
) {
  while (stack.length > maxEntries) stack.shift();
  let bytes = historyByteSize(stack);
  // Keep the newest state even when one snapshot alone exceeds the budget.
  while (stack.length > 1 && bytes > maxBytes) {
    stack.shift();
    bytes = historyByteSize(stack);
  }
  return bytes;
}
