// Older saved turns kept image fields only inside the tool's JSON output.
export function generatedImageResult(event) {
  if (!event || event.error || (event.exit_code != null && event.exit_code !== 0)) return null;
  if (event.image_url) return event;
  if (!['generate_image', 'edit_image'].includes(event.tool) || typeof event.output !== 'string') return null;
  try {
    const result = JSON.parse(event.output);
    if (result.error || !result.image_url || typeof result.image_url !== 'string') return null;
    return result;
  } catch {
    return null;
  }
}
