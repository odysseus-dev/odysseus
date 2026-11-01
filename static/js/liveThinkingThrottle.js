// liveThinkingThrottle.js
//
// Pure trailing-edge coalescer for the live "thinking" block in chat.js.
//
// A reasoning stream delivers deltas far faster than a human can read them, and
// the only thing that matters on screen is the LATEST cumulative text. Committing
// every delta to the DOM makes the work grow with the length of the stream. This
// throttle collapses a burst of updates into one commit per `delay` ms, always
// carrying the most recent value.
//
// Timers are injected so the behaviour is testable without a browser or a clock:
//
//     const throttle = createLiveThinkingThrottle(commit, { prepare, schedule, cancel });
//
// Lifecycle contract, which the terminal paths in chat.js depend on:
//
//   update(value)  queue `value`; schedule a commit if one is not already pending
//   flush()        commit any pending value NOW and drop the timer; returns whether
//                  a commit happened, so a clean flush cannot duplicate a commit
//   cancel()       drop the timer AND the pending value — nothing lands later
//
// `cancel()` is what stops a finished (or backgrounded) stream from mutating a
// view the user has since navigated away to.

export function stripLiveThinkingTags(text) {
  return String(text ?? '').replace(
    /<\/?(?:think(?:ing)?|thought)(?:\s+[^>]*)?>/gi,
    '',
  );
}

const THINKING_BOUNDARY_RE = /<\/?(?:(?:mm:)?think(?:ing)?|thought)(?:\s+[^>]*)?>|<\|channel>(?:thought|response)|<channel\|>/gi;
const REPLY_PREFIX_SOURCE = "(?:Hey|Hi |Hi!|Hello|Sure|Yes|No |No,|Yo|OK|Here|Absolutely|Of course|Great|Alright|Thanks|Welcome|Good |I'm happy|I'd be)";
const REPLY_LINE_RE = new RegExp('(?:^|\\n)\\s*' + REPLY_PREFIX_SOURCE, 'gi');
const REPLY_INLINE_RE = new RegExp('[.!?]\\s*' + REPLY_PREFIX_SOURCE, 'gi');
const REASONING_PREFIX_CANDIDATES = [
  'thinking:', 'thinking process:', 'the user ', 'user wants', 'we need ',
  'i need ', 'i should ', 'i will ', "i'll ", 'i am going ', 'let me think',
  'let me look', 'let me see', 'let me check', 'let me read', 'let me review',
  'let me analyze', 'let me parse', 'let me figure', 'let me draft', 'let me write',
  'they are ', 'the question ', 'i can ',
];

const DISPLAY_FILTER_BOUNDARY_RE = /\[\/?TOOL_CALL\]|```(?:create_document|documen(?:t)?)(?:\s|$)|```[\w-]+[ \t]*[\[{]|<(?:[\w]+:)?(?:tool_call|function_call)>|<invoke\b|<\s*\/?\s*[｜|]+\s*DSML\s*[｜|]+|(?:\[\s*)?\{\s*"function"\s*:|<\/?\|(?:assistant|assistan|user|system|tool|end)\|?>|(?:^|[\r\n])\s*(?:stdout|stderr|exit_code):/i;

function hasFreshMatch(text, regex, cursor, minStart = 0) {
  regex.lastIndex = 0;
  for (const match of text.matchAll(regex)) {
    const end = match.index + match[0].length;
    if (end > cursor && match.index >= minStart) return true;
  }
  return false;
}

// Incrementally decides when chat.js needs its compatibility-heavy cumulative
// thinking analysis. The gate inspects only a short overlap plus the new text;
// ordinary answer/reasoning deltas therefore stay O(delta) while split tags,
// namespaced tags, non-tag reply boundaries, and false-close grace deadlines
// still request the canonical full analysis.
export function createThinkingAnalysisGate({
  startsWithReasoningPrefix = () => false,
  now = () => Date.now(),
  overlap = 512,
} = {}) {
  let cursor = 0;
  let prefixSettled = false;
  let prefixProbe = '';

  return {
    shouldAnalyze(text, {
      isThinking = false,
      nonTagThinking = false,
      recheckAt = 0,
    } = {}) {
      const fullText = String(text ?? '');
      if (fullText.length < cursor) {
        cursor = 0;
        prefixSettled = false;
        prefixProbe = '';
      }
      const previousCursor = cursor;
      if (!prefixSettled && prefixProbe.length < overlap) {
        // Build the initial probe from deltas so arbitrary leading whitespace
        // cannot strand the gate in its undecided state. The retained state is
        // bounded even if a provider emits a very large whitespace prefix.
        prefixProbe = (prefixProbe + fullText.slice(previousCursor))
          .trimStart()
          .slice(0, overlap);
      }
      const scanStart = Math.max(0, previousCursor - overlap);
      const freshText = fullText.slice(scanStart);
      const relativeCursor = previousCursor - scanStart;
      const hasBoundary = hasFreshMatch(freshText, THINKING_BOUNDARY_RE, relativeCursor);
      const hasReplyBoundary = nonTagThinking && (
        hasFreshMatch(freshText, REPLY_LINE_RE, relativeCursor)
        || hasFreshMatch(freshText, REPLY_INLINE_RE, relativeCursor, Math.max(0, 20 - scanStart))
      );
      cursor = fullText.length;

      if (hasBoundary || hasReplyBoundary) return true;
      if (isThinking) return recheckAt > 0 && now() >= recheckAt;
      if (prefixSettled) return false;

      if (!prefixProbe) return false;
      if (startsWithReasoningPrefix(prefixProbe)) {
        prefixSettled = true;
        return true;
      }
      const lowerProbe = prefixProbe.toLowerCase();
      if (REASONING_PREFIX_CANDIDATES.some((candidate) => candidate.startsWith(lowerProbe))) {
        return false;
      }
      prefixSettled = true;
      return false;
    },
    reset() {
      cursor = 0;
      prefixSettled = false;
      prefixProbe = '';
    },
  };
}

// Keep the common prose path append-only. At the first structured/tool
// boundary, filter only the preceding visible prefix and hide the structured
// tail until the authoritative terminal render.
export function createIncrementalDisplayProjector(filter, { overlap = 512 } = {}) {
  let projected = '';
  let boundaryTail = '';
  let rawLength = 0;
  let structuredTailHidden = false;

  return {
    append(delta, fullText) {
      const chunk = String(delta ?? '');
      const raw = String(fullText ?? '');
      if (raw.length < rawLength) this.reset();
      const boundaryProbe = boundaryTail + chunk;
      const boundaryMatch = !structuredTailHidden
        ? DISPLAY_FILTER_BOUNDARY_RE.exec(boundaryProbe)
        : null;
      if (boundaryMatch) {
        // Filter the visible prefix, not the incomplete marker itself: several
        // compatibility regexes intentionally match only completed blocks.
        const boundaryStart = Math.max(0, raw.length - boundaryProbe.length + boundaryMatch.index);
        structuredTailHidden = true;
        projected = String(filter(raw.slice(0, boundaryStart)) ?? '');
      } else if (!structuredTailHidden) {
        projected += chunk;
      }
      boundaryTail = (boundaryTail + chunk).slice(-overlap);
      rawLength = raw.length;
      return projected;
    },
    current() {
      return projected;
    },
    reset() {
      projected = '';
      boundaryTail = '';
      rawLength = 0;
      structuredTailHidden = false;
    },
  };
}

export function createLiveThinkingThrottle(commit, {
  delay = 100,
  prepare = (value) => String(value ?? ''),
  schedule = (callback, ms) => setTimeout(callback, ms),
  cancel = (timer) => clearTimeout(timer),
} = {}) {
  let timer = null;
  let latest = null;
  let dirty = false;

  const commitLatest = () => {
    timer = null;
    if (!dirty) return false;
    dirty = false;
    commit(prepare(latest));
    return true;
  };

  return {
    update(value) {
      latest = value;
      dirty = true;
      if (timer === null) timer = schedule(commitLatest, delay);
    },
    flush() {
      if (timer !== null) {
        cancel(timer);
        timer = null;
      }
      return commitLatest();
    },
    cancel() {
      if (timer !== null) cancel(timer);
      timer = null;
      dirty = false;
    },
  };
}

export default createLiveThinkingThrottle;
