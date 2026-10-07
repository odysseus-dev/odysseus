/* Worker adapter for the shared pure adjustment pixel pass. */

let applyAdjustment;

async function render(source, adjustment) {
  if (!applyAdjustment) {
    // pixel-pass only needs document.createElement('canvas'), so provide the
    // smallest worker-local adapter instead of maintaining a second renderer.
    self.document = { createElement: () => new OffscreenCanvas(source.width, source.height) };
    ({ applyAdjustment } = await import('./fx/pixel-pass.js'));
  }
  const input = new OffscreenCanvas(source.width, source.height);
  input.getContext('2d').drawImage(source, 0, 0);
  return applyAdjustment(input, adjustment);
}

self.onmessage = async event => {
  try {
    const { source, adjustment } = event.data;
    const output = await render(source, adjustment);
    const bitmap = output.transferToImageBitmap();
    self.postMessage({ bitmap }, [bitmap]);
  } catch (error) {
    self.postMessage({ error: String(error?.message || error) });
  }
};
