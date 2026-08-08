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
