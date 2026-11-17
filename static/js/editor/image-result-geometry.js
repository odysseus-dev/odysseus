export function imageResultGeometry(width, height, canvasWidth, canvasHeight) {
  const matchingRatio = Math.abs(width * canvasHeight - height * canvasWidth) <= Math.max(canvasWidth, canvasHeight);
  if (matchingRatio) {
    const scale = Math.max(canvasWidth / width, canvasHeight / height);
    const drawWidth = width * scale, drawHeight = height * scale;
    return { width: canvasWidth, height: canvasHeight, offsetX: 0, offsetY: 0,
      drawX: (canvasWidth - drawWidth) / 2, drawY: (canvasHeight - drawHeight) / 2, drawWidth, drawHeight };
  }
  return { width, height, offsetX: Math.round((canvasWidth - width) / 2), offsetY: Math.round((canvasHeight - height) / 2),
    drawX: 0, drawY: 0, drawWidth: width, drawHeight: height };
}
