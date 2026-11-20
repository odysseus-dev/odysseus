// frontend/src/features/documents/domain/sourceSegmentation.js
/**
 * Exact source segmentation and reconstruction for Markdown documents.
 *
 * Guarantees the invariant:
 *   joinSegments(segmentMarkdown(source)) === source
 * for any string without modifications, preserving exact line endings (LF, CRLF,
 * mixed), blank lines, trailing spaces, tabs, and UTF-8 BOM byte-for-byte.
 */

const FENCE_OPEN_RE = /^[ ]{0,3}(`{3,}|~{3,})([^\n]*)$/;
const FENCE_CLOSE_RE = /^[ ]{0,3}(`{3,}|~{3,})[ \t]*$/;
const ATX_HEADING_RE = /^[ ]{0,3}#{1,6}([ \t]+.*|[ \t]*)$/;
const HR_RE = /^[ ]{0,3}(?:(\*[ \t]*){3,}|(-[ \t]*){3,}|(_[ \t]*){3,})$/;
const BLOCKQUOTE_RE = /^[ ]{0,3}> ?(.*)$/;
const LIST_ITEM_RE = /^([ \t]*)([-*+]|(\d{1,9})[.)])(?:([ \t]+)(.*))?$/;
const SETEXT_UNDERLINE_RE = /^[ ]{0,3}(?:=+|-+)[ \t]*$/;
const INDENTED_CODE_RE = /^(?:[ ]{4}|\t)(.*)$/;

/**
 * Split a string into lines while preserving each line's exact line ending.
 * Supports LF, CRLF, and lone CR without normalization.
 * @param {string} str
 * @returns {string[]}
 */
export function splitLinesExact(str) {
  if (!str) return [];
  // Scan once, even when one kind of newline is rare or absent.
  return str.match(/[^\r\n]*(?:\r\n|[\r\n])|[^\r\n]+$/g) || [];
}

/**
 * Return the line ending characters (\r\n, \n, \r, or empty string) of a line.
 * @param {string} line
 * @returns {string}
 */
export function lineEnding(line) {
  if (line.endsWith('\r\n')) return '\r\n';
  if (line.endsWith('\n')) return '\n';
  if (line.endsWith('\r')) return '\r';
  return '';
}

/**
 * Return the content of a line stripped of its trailing line ending.
 * @param {string} line
 * @returns {string}
 */
export function lineContent(line) {
  return line.replace(/[\r\n]+$/, '');
}

/**
 * Return true if the line contains only whitespace or is empty.
 * @param {string} line
 * @returns {boolean}
 */
export function isBlank(line) {
  return lineContent(line).trim() === '';
}

/**
 * Return true if the line is a GFM table delimiter line (e.g. |:---|---:|).
 * @param {string} line
 * @returns {boolean}
 */
export function isTableSeparatorLine(line) {
  const content = lineContent(line).trim();
  if (!content.includes('|') && !/^:?-{3,}:?$/.test(content)) return false;
  const cells = content.split('|').map(c => c.trim()).filter(Boolean);
  return cells.length > 0 && cells.every(c => /^:?-+:?$/.test(c));
}

/**
 * Return true if the line contains table pipe syntax.
 * @param {string} line
 * @returns {boolean}
 */
export function isTableLine(line) {
  const content = lineContent(line).trim();
  return content.length > 0 && content.includes('|');
}

/**
 * Return list item match if the line starts a list item and is not an HR.
 * @param {string} line
 * @returns {RegExpMatchArray|null}
 */
export function parseListItemLine(line) {
  const content = lineContent(line);
  if (HR_RE.test(content)) return null;
  return content.match(LIST_ITEM_RE);
}

/**
 * Return true if a line interrupts a running paragraph in CommonMark.
 * @param {string} line
 * @param {string|null} nextLine
 * @returns {boolean}
 */
export function interruptsParagraph(line, nextLine = null) {
  const content = lineContent(line);
  if (ATX_HEADING_RE.test(content) || HR_RE.test(content) || FENCE_OPEN_RE.test(content) || BLOCKQUOTE_RE.test(content)) {
    return true;
  }
  if (parseListItemLine(line)) return true;
  if (isTableLine(line) && nextLine && isTableSeparatorLine(nextLine)) return true;
  return false;
}

/**
 * Detect predominant line ending in the given text. Defaults to '\n'.
 * @param {string} source
 * @returns {'\r\n'|'\n'}
 */
export function detectLineEnding(source) {
  const text = String(source || '');
  const crlfCount = (text.match(/\r\n/g) || []).length;
  const lfCount = (text.match(/(?<!\r)\n/g) || []).length;
  return crlfCount > lfCount ? '\r\n' : '\n';
}

/**
 * Deterministic hash function for string content and revision tracking.
 * Pure JS implementation with zero dependencies.
 * @param {string} text
 * @returns {string}
 */
export function computeSourceRevision(text) {
  if (typeof text !== 'string') return '';
  let h1 = 0xdeadbeef ^ text.length;
  let h2 = 0x41c64e6d ^ text.length;
  for (let i = 0; i < text.length; i++) {
    const ch = text.charCodeAt(i);
    h1 = Math.imul(h1 ^ ch, 2654435761);
    h2 = Math.imul(h2 ^ ch, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(16).padStart(14, '0');
}

/**
 * Segment Markdown text into an ordered array of block and separator segments.
 *
 * Each block segment has:
 *   {
 *     type: 'block',
 *     blockType: string,
 *     raw: string,
 *     index: number,
 *     startOffset: number,
 *     endOffset: number,
 *     blockId: string
 *   }
 * Each separator segment has:
 *   {
 *     type: 'separator',
 *     raw: string,
 *     startOffset: number,
 *     endOffset: number
 *   }
 *
 * Joining all segment `raw` properties reconstructs `source` byte-for-byte.
 *
 * @param {string} source
 * @returns {Array<object>}
 */
export function segmentMarkdown(source) {
  if (!source) return [];

  let prefix = '';
  let text = source;
  if (text.startsWith('\uFEFF')) {
    prefix = '\uFEFF';
    text = text.slice(1);
  }

  const rawLines = splitLinesExact(text);
  if (!rawLines.length) {
    return prefix ? [{ type: 'separator', raw: prefix, startOffset: 0, endOffset: prefix.length }] : [];
  }

  const segments = [];
  let curSep = prefix;
  let i = 0;
  let blockIndex = 0;
  let currentOffset = 0;

  function pushSeparator(raw) {
    if (!raw) return;
    const startOffset = currentOffset;
    currentOffset += raw.length;
    segments.push({
      type: 'separator',
      raw,
      startOffset,
      endOffset: currentOffset,
    });
  }

  function pushBlock(blockType, raw) {
    const startOffset = currentOffset;
    currentOffset += raw.length;
    segments.push({
      type: 'block',
      blockType,
      raw,
      index: blockIndex++,
      startOffset,
      endOffset: currentOffset,
      blockId: computeSourceRevision(`${blockIndex - 1}:${raw}`),
    });
  }

  while (i < rawLines.length) {
    // 1. Accumulate blank lines into curSep
    if (isBlank(rawLines[i])) {
      curSep += rawLines[i];
      i++;
      continue;
    }

    // Flush separator before this block
    if (curSep) {
      pushSeparator(curSep);
      curSep = '';
    }

    const startLine = rawLines[i];
    const trimmed = lineContent(startLine);

    // 2. Fenced Code Block
    const fenceMatch = trimmed.match(FENCE_OPEN_RE);
    if (fenceMatch) {
      const char = fenceMatch[1][0];
      const minLen = fenceMatch[1].length;
      let blockLines = [trimmed];
      let lastEnding = lineEnding(startLine);
      i++;
      while (i < rawLines.length) {
        const cur = rawLines[i];
        const curTrim = lineContent(cur);
        const curEnding = lineEnding(cur);
        blockLines[blockLines.length - 1] += lastEnding;
        blockLines.push(curTrim);
        lastEnding = curEnding;
        i++;
        const closeMatch = curTrim.match(FENCE_CLOSE_RE);
        if (closeMatch && closeMatch[1][0] === char && closeMatch[1].length >= minLen) {
          break;
        }
      }
      pushBlock('code', blockLines.join(''));
      curSep = lastEnding;
      continue;
    }

    // 3. ATX Heading
    if (ATX_HEADING_RE.test(trimmed)) {
      pushBlock('heading', trimmed);
      curSep = lineEnding(startLine);
      i++;
      continue;
    }

    // 4. Horizontal Rule
    if (HR_RE.test(trimmed)) {
      pushBlock('hr', trimmed);
      curSep = lineEnding(startLine);
      i++;
      continue;
    }

    // 5. GFM Pipe Table
    if (isTableLine(startLine) && i + 1 < rawLines.length && isTableSeparatorLine(rawLines[i + 1])) {
      let blockLines = [trimmed];
      let lastEnding = lineEnding(startLine);
      i++;
      // delimiter row
      blockLines[blockLines.length - 1] += lastEnding;
      blockLines.push(lineContent(rawLines[i]));
      lastEnding = lineEnding(rawLines[i]);
      i++;
      // data rows
      while (i < rawLines.length && isTableLine(rawLines[i]) && !isBlank(rawLines[i])) {
        blockLines[blockLines.length - 1] += lastEnding;
        blockLines.push(lineContent(rawLines[i]));
        lastEnding = lineEnding(rawLines[i]);
        i++;
      }
      pushBlock('table', blockLines.join(''));
      curSep = lastEnding;
      continue;
    }

    // 6. Blockquote (with lazy continuation support)
    if (BLOCKQUOTE_RE.test(trimmed)) {
      let blockLines = [trimmed];
      let lastEnding = lineEnding(startLine);
      i++;
      while (i < rawLines.length) {
        const cur = rawLines[i];
        if (isBlank(cur)) break;
        const curTrim = lineContent(cur);
        const next = i + 1 < rawLines.length ? rawLines[i + 1] : null;
        if (!BLOCKQUOTE_RE.test(lineContent(cur)) && interruptsParagraph(cur, next)) break;
        blockLines[blockLines.length - 1] += lastEnding;
        blockLines.push(curTrim);
        lastEnding = lineEnding(cur);
        i++;
      }
      pushBlock('blockquote', blockLines.join(''));
      curSep = lastEnding;
      continue;
    }

    // 7. List. A blank line ends the block, exactly as the visual codec starts a
    // new list there, so `- a\n\n- b` is two blocks joined by their original
    // blank-line separator and each item keeps its own source bytes. Only an
    // indented line after a blank belongs to the previous item (continuation
    // paragraph or nested content); the codec rejects those documents.
    if (parseListItemLine(startLine)) {
      const firstItem = parseListItemLine(startLine);
      const listKind = item => item[3] !== undefined ? 'ordered'
        : /^\[[ xX]\](?:[ \t]|$)/.test(item[5] || '') ? 'task' : 'unordered';
      let blockLines = [trimmed];
      let lastEnding = lineEnding(startLine);
      i++;
      while (i < rawLines.length) {
        const cur = rawLines[i];
        if (isBlank(cur)) {
          let lookAhead = i + 1;
          while (lookAhead < rawLines.length && isBlank(rawLines[lookAhead])) {
            lookAhead++;
          }
          if (lookAhead < rawLines.length && /^[ \t]{2,}/.test(lineContent(rawLines[lookAhead]))) {
            // Indented content after a blank line continues the item.
            while (i < lookAhead) {
              blockLines[blockLines.length - 1] += lastEnding;
              blockLines.push(lineContent(rawLines[i]));
              lastEnding = lineEnding(rawLines[i]);
              i++;
            }
            continue;
          }
          break;
        }

        const curTrim = lineContent(cur);
        const next = i + 1 < rawLines.length ? rawLines[i + 1] : null;
        const item = parseListItemLine(cur);
        if (item && item[1].length === firstItem[1].length && listKind(item) !== listKind(firstItem)) break;
        if (interruptsParagraph(cur, next) && !item) break;

        blockLines[blockLines.length - 1] += lastEnding;
        blockLines.push(curTrim);
        lastEnding = lineEnding(cur);
        i++;
      }
      pushBlock('list', blockLines.join(''));
      curSep = lastEnding;
      continue;
    }

    // 8. Indented Code Block (4 spaces or 1 tab outside paragraph)
    if (INDENTED_CODE_RE.test(trimmed)) {
      let blockLines = [trimmed];
      let lastEnding = lineEnding(startLine);
      i++;
      while (i < rawLines.length) {
        const cur = rawLines[i];
        if (isBlank(cur)) {
          // Look ahead: does code block continue after blank line?
          let lookAhead = i + 1;
          while (lookAhead < rawLines.length && isBlank(rawLines[lookAhead])) {
            lookAhead++;
          }
          if (lookAhead < rawLines.length && INDENTED_CODE_RE.test(lineContent(rawLines[lookAhead]))) {
            while (i < lookAhead) {
              blockLines[blockLines.length - 1] += lastEnding;
              blockLines.push(lineContent(rawLines[i]));
              lastEnding = lineEnding(rawLines[i]);
              i++;
            }
            continue;
          }
          break;
        }
        const curTrim = lineContent(cur);
        if (!INDENTED_CODE_RE.test(curTrim)) break;
        blockLines[blockLines.length - 1] += lastEnding;
        blockLines.push(curTrim);
        lastEnding = lineEnding(cur);
        i++;
      }
      pushBlock('code', blockLines.join(''));
      curSep = lastEnding;
      continue;
    }

    // 9. Paragraph / Setext Heading
    let blockLines = [trimmed];
    let lastEnding = lineEnding(startLine);
    let isSetext = false;
    i++;
    while (i < rawLines.length) {
      const cur = rawLines[i];
      if (isBlank(cur)) break;
      const curTrim = lineContent(cur);
      if (SETEXT_UNDERLINE_RE.test(curTrim)) {
        blockLines[blockLines.length - 1] += lastEnding;
        blockLines.push(curTrim);
        lastEnding = lineEnding(cur);
        i++;
        isSetext = true;
        break;
      }
      const next = i + 1 < rawLines.length ? rawLines[i + 1] : null;
      if (interruptsParagraph(cur, next)) break;
      blockLines[blockLines.length - 1] += lastEnding;
      blockLines.push(curTrim);
      lastEnding = lineEnding(cur);
      i++;
    }

    pushBlock(isSetext ? 'setext-heading' : 'paragraph', blockLines.join(''));
    curSep = lastEnding;
  }

  if (curSep) {
    pushSeparator(curSep);
  }

  return segments;
}

/**
 * Join segmented elements back into a Markdown document string.
 * Unmodified segments emit their exact raw text. Modified segments emit `seg.text`.
 * @param {Array<{ type: string, raw: string, modified?: boolean, text?: string }>} segments
 * @returns {string}
 */
export function joinSegments(segments) {
  if (!Array.isArray(segments) || !segments.length) return '';
  let out = '';
  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    if (seg.modified && typeof seg.text === 'string') {
      out += seg.text;
    } else {
      out += seg.raw || '';
    }
  }
  return out;
}

/**
 * Filter and return only the block segments from a segmented document.
 * @param {Array<{ type: string }>} segments
 * @returns {Array<object>}
 */
export function extractBlockSegments(segments) {
  if (!Array.isArray(segments)) return [];
  return segments.filter(seg => seg.type === 'block');
}

/**
 * Convenience helper returning only block segments from source markdown.
 * @param {string} source
 * @returns {Array<object>}
 */
export function segmentMarkdownBlocks(source) {
  return extractBlockSegments(segmentMarkdown(source));
}
