// Pure Markdown outline parsing kept separate from the editor DOM so heading
// detection can be tested without mounting the full document workspace.

function cleanHeadingLabel(value) {
  return String(value || '')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/[*_~`]+/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

export function parseMarkdownOutline(source) {
  const text = String(source || '').replace(/\r\n?/g, '\n');
  const lines = text.split('\n');
  const offsets = [];
  let offset = 0;
  for (const line of lines) {
    offsets.push(offset);
    offset += line.length + 1;
  }

  const entries = [];
  let fence = null;
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const fenceMatch = line.match(/^ {0,3}(`{3,}|~{3,})/);
    if (fenceMatch) {
      const marker = fenceMatch[1][0];
      if (!fence) fence = marker;
      else if (fence === marker) fence = null;
      continue;
    }
    if (fence) continue;

    const atx = line.match(/^ {0,3}(#{1,6})[\t ]+(.+?)\s*$/);
    if (atx) {
      const rawLabel = atx[2].replace(/[\t ]+#+[\t ]*$/, '');
      const label = cleanHeadingLabel(rawLabel);
      if (label) {
        entries.push({
          level: atx[1].length,
          text: label,
          start: offsets[index],
          end: offsets[index] + line.length,
          line: index,
        });
      }
      continue;
    }

    const setext = line.match(/^ {0,3}(=+|-+)[\t ]*$/);
    if (setext && index > 0) {
      const previous = lines[index - 1];
      const label = cleanHeadingLabel(previous.trim());
      if (label && !/^ {0,3}(?:[-*_][\t ]*){3,}$/.test(previous)) {
        entries.push({
          level: setext[1][0] === '=' ? 1 : 2,
          text: label,
          start: offsets[index - 1],
          end: offsets[index - 1] + previous.length,
          line: index - 1,
        });
      }
    }
  }
  return entries;
}
