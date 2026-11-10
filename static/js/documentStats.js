// Unicode-aware text statistics shared by source and rich-text documents.

let _wordSegmenter;
let _graphemeSegmenter;

function _segmenter(granularity) {
  if (typeof Intl === 'undefined' || typeof Intl.Segmenter !== 'function') return null;
  try {
    if (granularity === 'word') {
      _wordSegmenter ||= new Intl.Segmenter(undefined, { granularity: 'word' });
      return _wordSegmenter;
    }
    _graphemeSegmenter ||= new Intl.Segmenter(undefined, { granularity: 'grapheme' });
    return _graphemeSegmenter;
  } catch (_) {
    return null;
  }
}

export function countDocumentWords(value) {
  const text = String(value ?? '');
  if (!text.trim()) return 0;
  const segmenter = _segmenter('word');
  if (segmenter) {
    let count = 0;
    for (const segment of segmenter.segment(text)) {
      if (segment.isWordLike) count += 1;
    }
    return count;
  }
  return (text.match(/[\p{L}\p{N}\p{M}]+(?:['’][\p{L}\p{N}\p{M}]+)*/gu) || []).length;
}

export function countDocumentCharacters(value) {
  const text = String(value ?? '');
  const segmenter = _segmenter('grapheme');
  if (segmenter) return Array.from(segmenter.segment(text)).length;
  return Array.from(text).length;
}

export function getDocumentStats(value) {
  const text = String(value ?? '').replace(/\r\n?/g, '\n');
  const words = countDocumentWords(text);
  const characters = countDocumentCharacters(text);
  const charactersNoSpaces = countDocumentCharacters(text.replace(/\s/gu, ''));
  const lineText = text.replace(/\n+$/u, '');
  const lines = lineText ? lineText.split('\n').length : 0;
  return {
    words,
    characters,
    charactersNoSpaces,
    lines,
    readingMinutes: words ? Math.max(1, Math.ceil(words / 225)) : 0,
  };
}
