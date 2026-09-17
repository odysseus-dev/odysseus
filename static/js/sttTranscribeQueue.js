// static/js/sttTranscribeQueue.js
//
// Sequential transcription queue for uploaded audio (#6319 follow-up).
//
// Local Whisper inference is CPU/RAM heavy: concurrent transcriptions can
// overload small hosts (e.g. Raspberry Pi), so this queue runs EXACTLY ONE
// job at a time (concurrency = 1). It is intentionally UI-agnostic and
// DOM-free so the lifecycle is unit-testable with node:test; callers supply
// per-job transcribe/save callbacks plus an onState observer that drives
// their own status lines. All I/O stays async — the queue never
// blocks the UI thread; jobs run in the background until finished.
//
// A job's document is created INSIDE its run, before the next item starts:
//   queued -> transcribing -> completed (doc exists) -> next queued item.
// A failure isolates to its own item; the queue always continues.
//
// Job: { key, transcribe: () => Promise<{text, language}>,
//        saveDoc: ({text, language}) => Promise<{id, title} | null>,
//        onState?: (state, info) => void }
// States: 'queued' (info: {position, total}), 'transcribing' (info:
//          {position, total}), 'completed' (info: {result, doc, reusedDoc}),
//          'failed' (info: {error, transcript?}).
//
// Microphone dictation does NOT go through this queue: mic -> STT ->
// composer stays a direct path in voiceRecorder.js.
//
// Combined-vs-separate document output is NOT decided here. The queue only
// guarantees order, isolation and per-job persistence. Batch assembly
// (collecting transcripts, creating one combined .md, opening the final
// document once) lives in the UI layer (chatRenderer.js) which observes
// these per-job states.

export const TRANSCRIBE_CONCURRENCY = 1;

/** Transcript destination by capture source. Uploaded audio must never
 *  resolve to the composer; mic must never resolve to a document. */
export function destinationFor(source) {
  if (source === 'mic') return 'composer';
  return 'document';
}

/** Watchdog so one wedged job can never freeze the queue: a phase that
 *  outlives its budget fails that item and the queue moves on. */
const DEFAULT_TRANSCRIBE_TIMEOUT_MS = 15 * 60 * 1000;
const DEFAULT_SAVE_TIMEOUT_MS = 3 * 60 * 1000;

function _withTimeout(promise, ms, message) {
  let timer = null;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => {
      const err = new Error(message);
      err.code = 'timeout';
      reject(err);
    }, ms);
  });
  return Promise.race([promise, timeout]).finally(() => {
    if (timer) clearTimeout(timer);
  });
}

export function createSttQueue(opts) {
  const waiting = []; // FIFO of jobs not yet started
  const completedDocs = new Map(); // key -> { text, docId, docTitle }
  const outcomes = new Map(); // key -> last terminal {status, result, doc, reusedDoc, error}
  let running = null;
  let seq = 0;
  let settledCount = 0; // terminal jobs so far (for stable transcribing positions)
  const transcribeTimeoutMs = (opts && opts.transcribeTimeoutMs) || DEFAULT_TRANSCRIBE_TIMEOUT_MS;
  const saveTimeoutMs = (opts && opts.saveTimeoutMs) || DEFAULT_SAVE_TIMEOUT_MS;

  function notify(job, state, info) {
    try {
      if (job && typeof job.onState === 'function') job.onState(state, info || {});
    } catch (_) { /* observer errors must not break the queue */ }
  }

  function isActive(key) {
    if (running && running.key === key) return true;
    return waiting.some((job) => job.key === key);
  }

  /** Current live total = running + waiting (settled jobs have drained). */
  function liveTotal() {
    return (running ? 1 : 0) + waiting.length;
  }

  function snapshot() {
    // Refresh queued positions so "Queued · i/n" stays truthful.
    // Position counts only queued slots (1-based); the running job
    // occupies the implicit first slot (see existing queue test).
    const total = liveTotal();
    waiting.forEach((job, i) => {
      notify(job, 'queued', { position: i + 1, total });
    });
  }

  async function processJob(job) {
    // text/language live outside try so a saveDoc failure still surfaces
    // the transcript for a save-only retry (no re-transcription).
    let text = '';
    let language = '';
    try {
      const result = await _withTimeout(
        job.transcribe(), transcribeTimeoutMs, 'Transcription timed out'
      );
      text = result && typeof result.text === 'string' ? result.text : '';
      if (!text) {
        const err = new Error('No speech detected');
        err.code = 'empty';
        throw err;
      }
      language = (result && result.language) || '';
      // Doc creation is part of the job: the next item starts only after
      // this item's document exists. Identical repeat runs reuse the
      // existing document instead of creating a duplicate.
      const memo = completedDocs.get(job.key);
      let doc = null;
      let reusedDoc = false;
      if (memo && memo.text === text) {
        doc = { id: memo.docId, title: memo.docTitle };
        reusedDoc = true;
      } else if (typeof job.saveDoc === 'function') {
        doc = await _withTimeout(
          job.saveDoc({ text, language }), saveTimeoutMs, 'Document save timed out'
        );
        if (doc && doc.id) {
          completedDocs.set(job.key, { text, docId: doc.id, docTitle: doc.title || '' });
        }
      }
      const outcome = { status: 'completed', result: { text, language }, doc, reusedDoc };
      outcomes.set(job.key, outcome);
      settledCount++;
      notify(job, 'completed', { result: outcome.result, doc, reusedDoc });
    } catch (e) {
      // Failure isolates to this item; the queue always continues. A
      // transcript that survived transcription (save failed) rides along
      // so the UI can offer a save-only retry without re-running STT.
      const outcome = { status: 'failed', error: (e && e.message) || 'Transcription failed', transcript: text };
      outcomes.set(job.key, outcome);
      settledCount++;
      notify(job, 'failed', { error: outcome.error, transcript: text });
    } finally {
      running = null;
      snapshot();
      pump();
    }
  }

  function pump() {
    if (running) return;
    const job = waiting.shift();
    if (!job) return;
    running = job;
    notify(job, 'transcribing', {
      position: settledCount + 1,
      total: settledCount + liveTotal(),
    });
    snapshot(); // refresh positions of the jobs still waiting
    processJob(job);
  }

  return {
    /** Enqueue one job. Returns 'queued', or 'duplicate' when the same key
     *  is already queued/running, or completed without {retry:true}.
     *  Pass {retry:true} for an explicit re-run (e.g. after a failure);
     *  identical text still reuses the existing document. */
    enqueue(job, opts) {
      if (!job || typeof job.key !== 'string' || !job.key) return 'duplicate';
      if (typeof job.transcribe !== 'function') return 'duplicate';
      if (isActive(job.key)) return 'duplicate';
      const retry = !!(opts && opts.retry);
      if (!retry && outcomes.get(job.key)?.status === 'completed') return 'duplicate';
      if (retry) outcomes.delete(job.key);
      job._seq = seq++;
      waiting.push(job);
      notify(job, 'queued', { position: waiting.length, total: liveTotal() });
      pump();
      return 'queued';
    },
    /** Enqueue multiple jobs at once, in order. Returns {added, skipped}. */
    enqueueAll(jobs) {
      let added = 0;
      let skipped = 0;
      (jobs || []).forEach((job) => {
        const res = this.enqueue(job);
        if (res === 'queued') added++;
        else skipped++;
      });
      return { added, skipped };
    },
    /** Live state for a key: 'queued' | 'transcribing' | 'completed' |
     *  'failed' | undefined (never seen). */
    getState(key) {
      if (running && running.key === key) return 'transcribing';
      if (waiting.some((job) => job.key === key)) return 'queued';
      const o = outcomes.get(key);
      return o ? o.status : undefined;
    },
    /** {position, total} for a queued/running key, or null when unknown.
     *  Queued positions count waiting slots only (1-based); total counts
     *  running + waiting — matches the queued notifications. */
    queuePosition(key) {
      if (running && running.key === key) {
        return { position: 1, total: liveTotal() };
      }
      const i = waiting.findIndex((job) => job.key === key);
      if (i < 0) return null;
      return { position: i + 1, total: liveTotal() };
    },
    /** Get full terminal outcome for a key (test/UI hook). */
    getOutcome(key) {
      return outcomes.get(key);
    },
    queueSize() { return waiting.length; },
    activeCount() { return running ? 1 : 0; },
    /** True when nothing is running and nothing is waiting. */
    isIdle() { return !running && waiting.length === 0; },
    /** Test/support hook: forget terminal + memo state for a key. */
    forget(key) {
      outcomes.delete(key);
      completedDocs.delete(key);
    },
  };
}

export const sttTranscribeQueue = createSttQueue();
