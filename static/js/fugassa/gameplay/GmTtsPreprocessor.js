/** Extract narrative prose from GM messages for TTS (mirrors Python gm_tts_preprocessor.py). */

const TABLE_ROW_RE = /^\s*\|/;
const SECTION_BREAK_RE = /^(?:round\s+summary|suggestions?|player\s+options?|choices?)\s*:?\s*$/i;

export function extractNarrativeForTts(raw) {
  if (!raw || !String(raw).trim()) return '';

  let text = String(raw).replace(/\r\n/g, '\n').trim();

  const lines = text.split('\n');
  const filtered = lines.filter((line) => !TABLE_ROW_RE.test(line));
  text = filtered.join('\n').trim();

  const outLines = [];
  for (const line of text.split('\n')) {
    if (SECTION_BREAK_RE.test(line.trim())) break;
    outLines.push(line);
  }
  text = outLines.join('\n').trim();

  return text.replace(/\n{3,}/g, '\n\n').trim();
}

/** Flatten line breaks so TTS does not insert long pauses at paragraphs. */
export function normalizeTextForTts(text) {
  if (!text || !String(text).trim()) return '';
  return String(text)
    .replace(/\s*\n+\s*/g, ' ')
    .replace(/\s{2,}/g, ' ')
    .trim();
}
