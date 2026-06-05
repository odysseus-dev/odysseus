// Some reasoning models (e.g. DeepSeek-R1) emit their chain-of-thought inline as
// <think>...</think> within the normal token stream, rather than via the
// thinking:true flag. Split the accumulated answer text into the visible answer
// and the reasoning. We re-parse the whole accumulated string each update (it's
// small) so tags that straddle two stream chunks are handled without tracking
// partial-tag state.
export function splitThinking(raw: string): { answer: string; thinking: string } {
  const OPEN = '<think>';
  const CLOSE = '</think>';
  let answer = '';
  let thinking = '';
  let i = 0;
  while (i < raw.length) {
    const open = raw.indexOf(OPEN, i);
    if (open === -1) {
      answer += raw.slice(i);
      break;
    }
    answer += raw.slice(i, open);
    const close = raw.indexOf(CLOSE, open + OPEN.length);
    if (close === -1) {
      // Still inside an unclosed block: everything after <think> is reasoning.
      thinking += raw.slice(open + OPEN.length);
      break;
    }
    thinking += raw.slice(open + OPEN.length, close);
    i = close + CLOSE.length;
  }
  return { answer, thinking };
}
