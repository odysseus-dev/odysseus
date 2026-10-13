/* Encode a completed document surface without blocking the editor thread. */
self.onmessage = async event => {
  const { bitmap, maxDim = 160, quality = 0.6 } = event.data || {};
  try {
    if (!bitmap?.width || !bitmap?.height) throw new Error('Thumbnail source is empty');
    const scale = Math.min(1, maxDim / Math.max(bitmap.width, bitmap.height));
    const width = Math.max(1, Math.round(bitmap.width * scale));
    const height = Math.max(1, Math.round(bitmap.height * scale));
    const canvas = new OffscreenCanvas(width, height);
    canvas.getContext('2d').drawImage(bitmap, 0, 0, width, height);
    const blob = await canvas.convertToBlob({ type: 'image/jpeg', quality });
    self.postMessage({ blob });
  } catch (error) {
    self.postMessage({ error: String(error?.message || error) });
  } finally {
    bitmap?.close?.();
  }
};
