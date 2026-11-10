import { decodeAIImage } from './ai-operation.js';
import { waitForImagePreferences } from '../settings/imagePreferences.js';

export function createImageGenerationAction({
  getDocument, placeResult, getSignal = () => null, onProgress = () => {},
  formatError = info => info.detail || 'Image generation failed',
  fetchImpl = (...args) => fetch(...args), timeoutMs = 330000,
}) {
  return async function generateImage(params = {}) {
    const document = getDocument();
    if (!document.key) throw new Error('Open a document before generating an image');
    const controller = new AbortController();
    const parentSignal = getSignal();
    const cancel = () => controller.abort();
    if (parentSignal?.aborted) cancel();
    parentSignal?.addEventListener('abort', cancel, { once: true });
    let timedOut = false;
    const deadline = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
    const started = Date.now();
    const progress = setInterval(() => onProgress(Math.floor((Date.now() - started) / 1000)), 1000);
    // A selection can change while the provider works; preserve this request's mask.
    let mask = null;
    try {
      if (params.masked && document.mask) {
        mask = globalThis.document.createElement('canvas');
        mask.width = document.mask.width; mask.height = document.mask.height;
        mask.getContext('2d').drawImage(document.mask, 0, 0);
      }
      await waitForImagePreferences();
      controller.signal.throwIfAborted();
      let response;
      try { response = await fetchImpl(window.geGenerateImageEndpoint || '/api/image/generate', {
        method: 'POST', credentials: 'same-origin', signal: controller.signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ _use_image_settings: true, prompt: params.prompt || '',
          width: Math.max(1, Math.round(Number(params.width) || document.width)),
          height: Math.max(1, Math.round(Number(params.height) || document.height)) }),
      }); } catch (error) {
        if (controller.signal.aborted) throw error;
        throw new Error(formatError({ network: true, detail: error.message }));
      }
      let data;
      try { data = await response.json(); }
      catch { throw new Error(response.ok ? 'Image server returned an invalid response' : `Image server failed (${response.status})`); }
      if (!response.ok || data.error) throw new Error(formatError({ status: response.status,
        detail: data.detail || data.error || `Image server failed (${response.status})` }));
      const image = data.image || data.images?.[0];
      if (!image) throw new Error('Image generation returned no image');
      const decoded = await decodeAIImage(image, controller.signal);
      controller.signal.throwIfAborted();
      const current = getDocument();
      if (current.key !== document.key || current.width !== document.width || current.height !== document.height) {
        throw new Error('The document changed during generation. The result was not placed in another document.');
      }
      const canvas = globalThis.document.createElement('canvas');
      canvas.width = decoded.naturalWidth; canvas.height = decoded.naturalHeight;
      const context = canvas.getContext('2d');
      context.imageSmoothingEnabled = true;
      context.imageSmoothingQuality = 'high';
      context.drawImage(decoded, 0, 0);
      return placeResult(canvas, 'Generated', mask);
    } catch (error) {
      if (timedOut) throw new Error('Image generation timed out. Try again or choose a faster image model.');
      throw error;
    } finally {
      clearTimeout(deadline);
      clearInterval(progress);
      parentSignal?.removeEventListener('abort', cancel);
    }
  };
}
