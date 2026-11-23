/** Trigger a blob download and release its object URL after a browser grace period. */
export function downloadBlob(blob, filename, revokeAfterMs = 1000) {
  const url = URL.createObjectURL(blob);
  let anchor;
  try {
    anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
  } finally {
    try {
      anchor?.remove();
    } finally {
      setTimeout(() => URL.revokeObjectURL(url), revokeAfterMs);
    }
  }
}
