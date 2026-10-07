/* Encode a batch of canvas bitmaps without blocking the editor thread. */
self.onmessage = async event => {
  const bitmaps = Array.isArray(event.data?.bitmaps) ? event.data.bitmaps : [];
  try {
    const blobs = [];
    for (const bitmap of bitmaps) {
      if (!bitmap?.width || !bitmap?.height) {
        blobs.push(null);
        continue;
      }
      const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
      canvas.getContext('2d').drawImage(bitmap, 0, 0);
      blobs.push(await canvas.convertToBlob({ type: 'image/png' }));
      bitmap.close?.();
    }
    self.postMessage({ blobs });
  } catch (error) {
    for (const bitmap of bitmaps) bitmap?.close?.();
    self.postMessage({ error: String(error?.message || error) });
  }
};
