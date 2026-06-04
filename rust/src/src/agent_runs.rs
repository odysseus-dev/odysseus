// src/agent_runs.rs  <- src/agent_runs.py
//! Detached agent-run manager.
//!
//! Keeps an agent/chat stream running server-side after the SSE client disconnects
//! (tab close, navigate away, refresh). The streaming generator is drained by a
//! background asyncio task into a per-session replay buffer; SSE clients SUBSCRIBE
//! to that buffer (replay everything so far, then live). Closing the SSE only drops
//! the subscriber — the drain task keeps going.
//!
//! The wrapped generator already persists the assistant message to the session on
//! completion, so reopening the session shows the finished result even if nobody
//! was connected when it finished. Reconnecting mid-run replays the buffer + streams
//! live (pick up where it is).
//!
//! Durability scope: in-memory, survives as long as the server process runs (tab
//! close / navigation / refresh). It does NOT survive a server restart.
//!
//! PORTING NOTES (Rust deviations from the Python, all DOCUMENTED, none faked):
//!
//! * Cooperative cancellation via `tokio_util::sync::CancellationToken`, NOT
//!   `JoinHandle::abort()`. Python's `asyncio.Task.cancel()` injects a
//!   `CancelledError` at the next `await` point, which the wrapped generator
//!   *catches* to persist its partial response (the whole point of this module is
//!   graceful stop/replay). `JoinHandle::abort()` drops the future without running
//!   a catchable handler, so the drain loop instead `select!`s the wrapped stream
//!   against a `CancellationToken`; on cancel it marks the run `stopped` and DROPS
//!   the stream. The wrapped stream's own `Drop` impl (the `_safe_stream` analogue
//!   in `agent_loop_web` / the chat route) is what performs the partial-persist —
//!   the same cleanup Python ran from its `except CancelledError: ... aclose()`.
//!
//! * `_drain`'s `await prev_task` before writing is preserved exactly (sequential
//!   session saves, no interleave) by capturing the previous run's `JoinHandle` at
//!   `start` time and awaiting it (errors swallowed, like Python's bare `except`).
//!
//! * Subscriber fan-out: one `tokio::sync::mpsc::UnboundedSender` per connected
//!   client (Python's per-client `asyncio.Queue`). Each carries the same
//!   `(Option<seq>, Option<event>)` tuples — `(None, None)` is the end sentinel.
//!   Subscribers are keyed by a monotonic `u64` so `subscribe`'s teardown can
//!   discard exactly its own channel (Python keyed by `asyncio.Queue` identity).

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use once_cell::sync::Lazy;
use tokio::sync::mpsc;
use tokio::task::JoinHandle;
use tokio_util::sync::CancellationToken;

/// One subscriber's wakeup payload: `(seq, event)`. `(None, None)` is the end
/// sentinel that closes the SSE (Python's `q.put_nowait((None, None))`).
type SubItem = (Option<usize>, Option<String>);

/// Status of a run. Python uses the bare strings "running" | "done" | "error" |
/// "stopped"; an enum makes the four-state machine explicit (sum-type rule).
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum RunStatus {
    Running,
    Done,
    Error,
    Stopped,
}

impl RunStatus {
    /// The exact Python status string, so callers that surface it match wire-for-wire.
    pub fn as_str(self) -> &'static str {
        match self {
            RunStatus::Running => "running",
            RunStatus::Done => "done",
            RunStatus::Error => "error",
            RunStatus::Stopped => "stopped",
        }
    }
}

/// `class _Run` — the per-session run state (Python `__slots__`).
///
/// Mutated under `Mutex` (short, non-`await` critical sections). `task` /
/// `evict_task` are the spawned `JoinHandle`s; `cancel` is the cooperative-cancel
/// token (the abort()-replacement, see the module docstring).
pub struct Run {
    /// ordered SSE event strings (replay log) — Python `self.buffer: list`
    pub buffer: Vec<String>,
    /// one mpsc sender per connected client, keyed by a monotonic id so teardown
    /// discards exactly its own channel — Python `self.subscribers: set`
    subscribers: HashMap<u64, mpsc::UnboundedSender<SubItem>>,
    /// running | done | error | stopped — Python `self.status: str`
    pub status: RunStatus,
    /// the drain task — Python `self.task: Optional[asyncio.Task]`
    task: Option<JoinHandle<()>>,
    /// the grace-period eviction timer — Python `self.evict_task: Optional[asyncio.Task]`
    evict_task: Option<JoinHandle<()>>,
    /// cooperative-cancel token consumed by `_drain`'s `select!` (the
    /// `JoinHandle::abort()` replacement; see the module docstring).
    cancel: CancellationToken,
}

impl Run {
    fn new() -> Self {
        Run {
            buffer: Vec::new(),
            subscribers: HashMap::new(),
            status: RunStatus::Running,
            task: None,
            evict_task: None,
            cancel: CancellationToken::new(),
        }
    }
}

/// `_RUNS: Dict[str, _Run]` — the global registry of detached runs.
///
/// `Arc<Mutex<Run>>` so the drain/evict tasks and the public API can share each
/// run without holding the outer registry lock across awaits.
static RUNS: Lazy<Mutex<HashMap<String, Arc<Mutex<Run>>>>> =
    Lazy::new(|| Mutex::new(HashMap::new()));

/// Monotonic subscriber-id source (the `asyncio.Queue` identity analogue).
static NEXT_SUB_ID: AtomicU64 = AtomicU64::new(0);

/// How long a FINISHED run (and its full replay buffer) is retained after the
/// last subscriber disconnects, so a reconnect within the window can still
/// replay the result. After this, the run is evicted to bound memory — without
/// it, every session that ever streamed kept its entire event log forever.
const EVICT_GRACE_S: u64 = 180;

/// `_schedule_evict(session_id)` — (re)arm a grace-period eviction for a terminal
/// run with no subscribers. Identity-checked so a run that gets replaced/reused is
/// never evicted by a stale timer.
fn schedule_evict(session_id: &str) {
    // run = _RUNS.get(session_id); if run is None: return
    let run = {
        let runs = RUNS.lock().unwrap();
        match runs.get(session_id) {
            Some(r) => Arc::clone(r),
            None => return,
        }
    };

    // if run.evict_task and not run.evict_task.done(): run.evict_task.cancel()
    {
        let mut guard = run.lock().unwrap();
        if let Some(prev) = guard.evict_task.take() {
            prev.abort();
        }
    }

    let session_id = session_id.to_string();
    let run_ref = Arc::clone(&run);
    // async def _evict(run_ref): await asyncio.sleep(_EVICT_GRACE_S) ...
    let handle = tokio::spawn(async move {
        // The sleep is what `evict_task.abort()` (above / in start/subscribe)
        // cancels — Python's `except asyncio.CancelledError: return`. abort() here
        // is correct: the evict timer has no partial state to persist (it is not
        // the run's drain task), so dropping it on cancel is the faithful analogue.
        tokio::time::sleep(std::time::Duration::from_secs(EVICT_GRACE_S)).await;
        // cur = _RUNS.get(session_id)
        // if cur is run_ref and cur.status != "running" and not cur.subscribers:
        //     _RUNS.pop(session_id, None)
        let mut runs = RUNS.lock().unwrap();
        if let Some(cur) = runs.get(&session_id) {
            if Arc::ptr_eq(cur, &run_ref) {
                let guard = cur.lock().unwrap();
                if guard.status != RunStatus::Running && guard.subscribers.is_empty() {
                    drop(guard);
                    runs.remove(&session_id);
                }
            }
        }
    });

    run.lock().unwrap().evict_task = Some(handle);
}

/// `is_active(session_id) -> bool`
pub fn is_active(session_id: &str) -> bool {
    let runs = RUNS.lock().unwrap();
    match runs.get(session_id) {
        Some(r) => r.lock().unwrap().status == RunStatus::Running,
        None => false,
    }
}

/// `get_status(session_id) -> Optional[str]`
pub fn get_status(session_id: &str) -> Option<RunStatus> {
    let runs = RUNS.lock().unwrap();
    runs.get(session_id).map(|r| r.lock().unwrap().status)
}

/// `_drain(session_id, agen, prev_task=None)` — pull every event from the wrapped
/// generator into the run buffer, fanning each out to live subscribers. Runs to
/// completion regardless of subscribers.
///
/// `stream` is the wrapped agent/chat stream (the `_safe_stream` analogue): it owns
/// the partial-persist-on-Drop logic. `cancel` is the run's cooperation token.
async fn drain<S>(
    session_id: String,
    stream: S,
    cancel: CancellationToken,
    prev_task: Option<JoinHandle<()>>,
) where
    S: futures_util::Stream<Item = String> + Send + 'static,
{
    use futures_util::{FutureExt, StreamExt};
    use std::panic::AssertUnwindSafe;

    // run = _RUNS.get(session_id); if run is None: return
    let run = {
        let runs = RUNS.lock().unwrap();
        match runs.get(&session_id) {
            Some(r) => Arc::clone(r),
            None => return,
        }
    };

    // If this run replaced an in-flight one (rapid double-send), wait for that one
    // to fully finish first. Its CancelledError handler calls aclose(), which
    // persists its partial response — letting it complete before we start writing
    // keeps the two runs' session saves sequential instead of interleaved.
    //
    // Python: `if prev_task is not None and not prev_task.done(): await asyncio.wait({prev_task})`
    // with `except CancelledError: raise` / `except Exception: pass`. Awaiting a
    // JoinHandle returns Err on cancel/panic; we swallow it (the `except Exception`
    // arm) — our own cancellation is handled by the `select!` below, not here.
    if let Some(prev) = prev_task {
        let _ = prev.await;
    }

    // Pin the wrapped stream so we can `select!` it against the cancel token AND so
    // that, on cancel, dropping `stream` runs its partial-persist `Drop` (Python's
    // `await agen.aclose()`).
    futures_util::pin_mut!(stream);

    // Set when polling the wrapped stream panics — the "TRUE crash" path (Python's
    // `except Exception`). A cancel (the cooperative token) is a *normal* stop, NOT a
    // crash, so it leaves this false and skips the error frames below.
    let mut crashed = false;
    loop {
        tokio::select! {
            biased;
            // Cooperative cancel (the abort()-replacement). Mark stopped and break;
            // the wrapped stream is dropped at end-of-scope, running its
            // partial-persist Drop (== Python's `except CancelledError: ... aclose()`).
            _ = cancel.cancelled() => {
                run.lock().unwrap().status = RunStatus::Stopped;
                break;
            }
            // async for ev in agen:
            //
            // Poll wrapped in `catch_unwind`: a panic while producing the next event is
            // the Rust analogue of Python's wrapped generator raising — the "TRUE crash"
            // that the `except Exception` arm handles. Without this, a panicking stream
            // would unwind the whole drain task and subscribers would never get a
            // sentinel; here we instead fall into the error path below (frames + DONE +
            // sentinel), wire-for-wire with Python.
            polled = AssertUnwindSafe(stream.next()).catch_unwind() => {
                match polled {
                    Ok(Some(ev)) => {
                        // run.buffer.append(ev); seq = len(run.buffer) - 1
                        // for q in list(run.subscribers): q.put_nowait((seq, ev))
                        let mut guard = run.lock().unwrap();
                        guard.buffer.push(ev.clone());
                        let seq = guard.buffer.len() - 1;
                        // try/except around the send mirrors Python: a closed
                        // channel (subscriber gone) is ignored, not surfaced.
                        for tx in guard.subscribers.values() {
                            let _ = tx.send((Some(seq), Some(ev.clone())));
                        }
                    }
                    Ok(None) => {
                        // Stream exhausted. if run.status == "running": run.status = "done"
                        let mut guard = run.lock().unwrap();
                        if guard.status == RunStatus::Running {
                            guard.status = RunStatus::Done;
                        }
                        break;
                    }
                    Err(_) => {
                        // except Exception as e:
                        //   logger.error("[agent-run] %s failed: %s", session_id, e, ...)
                        //   run.status = "error"
                        // The panic payload is already reported by the default panic hook;
                        // we mirror Python's structured log line here (pylog == the
                        // module's `logging.getLogger(__name__)` analogue).
                        crate::pylog::error(&format!(
                            "[agent-run] {} failed: panic while draining stream",
                            session_id
                        ));
                        run.lock().unwrap().status = RunStatus::Error;
                        crashed = true;
                        break;
                    }
                }
            }
        }
    }

    // except Exception: publish an explicit error frame + [DONE] BEFORE the close
    // sentinel, so clients see the failure instead of a bare stream close. Frame text
    // is wire-for-wire with Python's
    //   "event: error\n"
    //   f"data: {json.dumps({'error': 'Agent run failed before completion.', 'status': 500})}\n\n"
    // and the following "data: [DONE]\n\n". json.dumps's default separators put a space
    // after ':' and ',', which this literal reproduces exactly.
    if crashed {
        let err_frame = "event: error\n\
            data: {\"error\": \"Agent run failed before completion.\", \"status\": 500}\n\n"
            .to_string();
        let done_frame = "data: [DONE]\n\n".to_string();
        let mut guard = run.lock().unwrap();
        for frame in [err_frame, done_frame] {
            guard.buffer.push(frame.clone());
            let seq = guard.buffer.len() - 1;
            for tx in guard.subscribers.values() {
                let _ = tx.send((Some(seq), Some(frame.clone())));
            }
        }
    }

    // On cancel the wrapped stream's partial-persist Drop runs when `stream`
    // (pinned in place on this stack frame via `pin_mut!`) is dropped at end of
    // scope — equivalent to Python running `aclose()` in the cancellation path.
    // We can't drop it earlier here: `pin_mut!` rebinds `stream` to a
    // `Pin<&mut S>`, whose own drop is a no-op, so the underlying stream's Drop
    // is bound to scope end regardless.

    // NOTE on the error path: Python catches a generic Exception, logs it with
    // exc_info, sets status = "error", and publishes an explicit `event: error` +
    // `[DONE]` frame so clients see the failure rather than a bare close. A Rust
    // `Stream<Item=String>` cannot *yield* an error (the item type is `String`,
    // exactly like the Python generator yields strings) — an ordinary failure inside
    // the wrapped stream is encoded as a terminal SSE error event string, the normal
    // "running -> done" path. The remaining "TRUE crash" analogue is a PANIC while
    // polling the stream, which the `catch_unwind` arm above turns into status =
    // "error" + the error/[DONE] frames emitted just above this `finally:`, matching
    // Python wire-for-wire. A cooperative cancel is a normal stop, never the error path.

    // finally:
    //   Wake every subscriber with the end sentinel so their SSE closes.
    {
        let guard = run.lock().unwrap();
        for tx in guard.subscribers.values() {
            let _ = tx.send((None, None));
        }
    }
    // Run is terminal — arm the grace timer so it (and its buffer) is eventually
    // freed even if nobody ever reconnects. subscribe() cancels this on connect and
    // re-arms on disconnect.
    schedule_evict(&session_id);
}

/// `start(session_id, agen) -> _Run` — start a detached run draining `agen` for a
/// session. If a run is already in flight for this session (e.g. a rapid
/// double-send), it's cancelled first.
///
/// Returns the new run's `Arc<Mutex<Run>>` (the Python `_Run` handle).
pub fn start<S>(session_id: &str, stream: S) -> Arc<Mutex<Run>>
where
    S: futures_util::Stream<Item = String> + Send + 'static,
{
    let mut prev_task: Option<JoinHandle<()>> = None;

    // prev = _RUNS.get(session_id)
    {
        let runs = RUNS.lock().unwrap();
        if let Some(prev) = runs.get(session_id) {
            let mut guard = prev.lock().unwrap();
            // if prev.task and not prev.task.done(): prev.task.cancel(); prev_task = prev.task
            //
            // Cooperative cancel (the token), NOT JoinHandle::abort(): we trigger the
            // token so the prev drain loop breaks at its `select!` and drops the
            // wrapped stream (partial-persist). We still capture its JoinHandle so the
            // new run awaits it before writing (sequential saves).
            if let Some(task) = guard.task.take() {
                if !task.is_finished() {
                    guard.cancel.cancel();
                    prev_task = Some(task);
                } else {
                    // already done — put it back so identity bookkeeping is unchanged
                    guard.task = Some(task);
                }
            }
            // if prev.evict_task and not prev.evict_task.done(): prev.evict_task.cancel()
            if let Some(evict) = guard.evict_task.take() {
                evict.abort();
            }
        }
    }

    // run = _Run(); _RUNS[session_id] = run
    let run = Arc::new(Mutex::new(Run::new()));
    let cancel = run.lock().unwrap().cancel.clone();
    {
        let mut runs = RUNS.lock().unwrap();
        runs.insert(session_id.to_string(), Arc::clone(&run));
    }

    // run.task = asyncio.create_task(_drain(session_id, agen, prev_task))
    let sid = session_id.to_string();
    let task = tokio::spawn(drain(sid, stream, cancel, prev_task));
    run.lock().unwrap().task = Some(task);

    run
}

/// `subscribe(session_id)` — replay the run's buffer from the start, then stream
/// live until it ends. Safe to call repeatedly (reconnect) and from multiple
/// clients at once.
///
/// Returns an `impl Stream<Item = String>` (Python's `AsyncGenerator[str, None]`).
/// If there is no run for the session, the stream yields nothing and ends (Python's
/// bare `return` from the async generator).
pub fn subscribe(session_id: &str) -> impl futures_util::Stream<Item = String> {
    let session_id = session_id.to_string();

    async_stream::stream! {
        // run = _RUNS.get(session_id); if run is None: return
        let run = {
            let runs = RUNS.lock().unwrap();
            match runs.get(&session_id) {
                Some(r) => Arc::clone(r),
                None => return,
            }
        };

        // q: asyncio.Queue; run.subscribers.add(q)  (register BEFORE replaying so
        // nothing is missed). Cancel any pending grace timer so it can't evict the
        // run out from under us mid-replay.
        let sub_id = NEXT_SUB_ID.fetch_add(1, Ordering::Relaxed);
        let (tx, mut rx) = mpsc::unbounded_channel::<SubItem>();
        {
            let mut guard = run.lock().unwrap();
            guard.subscribers.insert(sub_id, tx);
            // if run.evict_task and not run.evict_task.done(): run.evict_task.cancel()
            if let Some(evict) = guard.evict_task.take() {
                evict.abort();
            }
        }

        // The `finally:` block (discard our subscriber + re-arm eviction) must run
        // even if the consumer drops this stream early (client disconnect). A guard
        // struct's Drop is the `try/finally` analogue for a generator that can be
        // dropped at any yield point.
        struct SubGuard {
            session_id: String,
            run: Arc<Mutex<Run>>,
            sub_id: u64,
        }
        impl Drop for SubGuard {
            fn drop(&mut self) {
                // run.subscribers.discard(q)
                let rearm = {
                    let mut guard = self.run.lock().unwrap();
                    guard.subscribers.remove(&self.sub_id);
                    // Last subscriber gone on a finished run — (re)arm eviction so the
                    // buffer doesn't linger indefinitely.
                    guard.subscribers.is_empty() && guard.status != RunStatus::Running
                };
                if rearm {
                    schedule_evict(&self.session_id);
                }
            }
        }
        let _sub_guard = SubGuard {
            session_id: session_id.clone(),
            run: Arc::clone(&run),
            sub_id,
        };

        // next_seq = 0
        // while next_seq < len(run.buffer): yield run.buffer[next_seq]; next_seq += 1
        let mut next_seq: usize = 0;
        loop {
            let item = {
                let guard = run.lock().unwrap();
                if next_seq < guard.buffer.len() {
                    Some(guard.buffer[next_seq].clone())
                } else {
                    None
                }
            };
            match item {
                Some(ev) => {
                    yield ev;
                    next_seq += 1;
                }
                None => break,
            }
        }

        // if run.status != "running": return
        if run.lock().unwrap().status != RunStatus::Running {
            return;
        }

        // while True: seq, ev = await q.get()
        loop {
            match rx.recv().await {
                // q closed without a sentinel (shouldn't happen — _drain always
                // sends one) is treated like the end sentinel: flush tail + break.
                None => {
                    loop {
                        let item = {
                            let guard = run.lock().unwrap();
                            if next_seq < guard.buffer.len() {
                                Some(guard.buffer[next_seq].clone())
                            } else {
                                None
                            }
                        };
                        match item {
                            Some(ev) => { yield ev; next_seq += 1; }
                            None => break,
                        }
                    }
                    break;
                }
                Some((None, _)) => {
                    // if seq is None:  # end sentinel
                    //   while next_seq < len(run.buffer):  # flush any tail the sentinel raced
                    //       yield run.buffer[next_seq]; next_seq += 1
                    //   break
                    loop {
                        let item = {
                            let guard = run.lock().unwrap();
                            if next_seq < guard.buffer.len() {
                                Some(guard.buffer[next_seq].clone())
                            } else {
                                None
                            }
                        };
                        match item {
                            Some(ev) => { yield ev; next_seq += 1; }
                            None => break,
                        }
                    }
                    break;
                }
                Some((Some(seq), ev)) => {
                    // if seq >= next_seq:  # skip events already replayed from the buffer
                    //   yield ev; next_seq = seq + 1
                    if seq >= next_seq {
                        if let Some(ev) = ev {
                            yield ev;
                        }
                        next_seq = seq + 1;
                    }
                }
            }
        }

        // _sub_guard's Drop runs the `finally:` cleanup as the stream ends here.
        drop(_sub_guard);
    }
}

/// `stop(session_id) -> bool` — cancel an in-flight run (the wrapped generator
/// saves its partial). Triggers the cooperative-cancel token rather than
/// `JoinHandle::abort()` (see the module docstring).
pub fn stop(session_id: &str) -> bool {
    let runs = RUNS.lock().unwrap();
    if let Some(run) = runs.get(session_id) {
        let guard = run.lock().unwrap();
        // if run and run.task and not run.task.done(): run.task.cancel(); return True
        if let Some(task) = guard.task.as_ref() {
            if !task.is_finished() {
                guard.cancel.cancel();
                return true;
            }
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;
    use futures_util::StreamExt;
    use std::time::Duration;

    /// Helper: collect a subscribe() stream to completion (with a generous timeout
    /// so a hung test fails loudly rather than hanging the suite).
    async fn collect(session: &str) -> Vec<String> {
        let s = subscribe(session);
        futures_util::pin_mut!(s);
        let mut out = Vec::new();
        while let Some(ev) = tokio::time::timeout(Duration::from_secs(5), s.next())
            .await
            .expect("subscribe stream timed out")
        {
            out.push(ev);
        }
        out
    }

    #[tokio::test]
    async fn drain_to_done_replays_full_buffer() {
        // A finished run replays its whole buffer + reports `done`.
        let session = "test-done";
        let events = vec!["a".to_string(), "b".to_string(), "c".to_string()];
        let stream = async_stream::stream! {
            for e in events.clone() { yield e; }
        };
        start(session, stream);
        // Let the drain task run to completion.
        for _ in 0..50 {
            if get_status(session) == Some(RunStatus::Done) {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        assert_eq!(get_status(session), Some(RunStatus::Done));
        assert!(!is_active(session));
        let got = collect(session).await;
        assert_eq!(got, vec!["a", "b", "c"]);
    }

    #[tokio::test]
    async fn subscribe_replays_then_lives() {
        // Subscribe mid-run: replay buffered events, then receive live ones.
        let session = "test-live";
        let (tx, rx) = mpsc::unbounded_channel::<String>();
        let stream = async_stream::stream! {
            let mut rx = rx;
            while let Some(e) = rx.recv().await { yield e; }
        };
        start(session, stream);
        tx.send("first".to_string()).unwrap();
        // Give the drain task a moment to buffer "first".
        tokio::time::sleep(Duration::from_millis(50)).await;
        assert!(is_active(session));

        // Subscribe now: should replay "first", then see "second" live, then end.
        let s = subscribe(session);
        futures_util::pin_mut!(s);
        let a = tokio::time::timeout(Duration::from_secs(5), s.next())
            .await
            .unwrap();
        assert_eq!(a, Some("first".to_string()));
        tx.send("second".to_string()).unwrap();
        let b = tokio::time::timeout(Duration::from_secs(5), s.next())
            .await
            .unwrap();
        assert_eq!(b, Some("second".to_string()));
        drop(tx); // close the source -> drain finishes -> sentinel
        let end = tokio::time::timeout(Duration::from_secs(5), s.next())
            .await
            .unwrap();
        assert_eq!(end, None);
    }

    #[tokio::test]
    async fn stop_marks_stopped() {
        // stop() cooperatively cancels an in-flight run.
        let session = "test-stop";
        let (_tx, rx) = mpsc::unbounded_channel::<String>();
        let stream = async_stream::stream! {
            let mut rx = rx;
            // Never completes on its own — only the cancel token ends it.
            while let Some(e) = rx.recv().await { yield e; }
        };
        start(session, stream);
        tokio::time::sleep(Duration::from_millis(50)).await;
        assert!(is_active(session));
        assert!(stop(session));
        for _ in 0..50 {
            if get_status(session) == Some(RunStatus::Stopped) {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        assert_eq!(get_status(session), Some(RunStatus::Stopped));
        // stop() on an already-finished run returns false.
        assert!(!stop(session));
    }

    #[tokio::test]
    async fn drain_crash_emits_error_then_done_before_close() {
        // A TRUE crash (panic while draining) marks the run `error` and publishes an
        // explicit `event: error` frame + `[DONE]` BEFORE the close sentinel, so a
        // client sees the failure instead of a bare stream close. Wire-for-wire with
        // Python's `except Exception` arm.
        let session = "test-crash";
        let stream = async_stream::stream! {
            yield "before-crash".to_string();
            // The panic is the Rust analogue of the wrapped generator raising; the
            // drain loop's catch_unwind turns it into the error path (not a task abort).
            panic!("boom");
            #[allow(unreachable_code)]
            { yield "never".to_string(); }
        };
        start(session, stream);
        // Let the drain task crash and reach the terminal error state.
        for _ in 0..50 {
            if get_status(session) == Some(RunStatus::Error) {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        assert_eq!(get_status(session), Some(RunStatus::Error));
        assert!(!is_active(session));

        // A reconnect replays: the pre-crash event, then the error frame, then [DONE],
        // then the stream ends (the sentinel is sent AFTER the two frames).
        let got = collect(session).await;
        assert_eq!(
            got,
            vec![
                "before-crash".to_string(),
                "event: error\n\
                 data: {\"error\": \"Agent run failed before completion.\", \"status\": 500}\n\n"
                    .to_string(),
                "data: [DONE]\n\n".to_string(),
            ]
        );
    }

    #[tokio::test]
    async fn live_subscriber_sees_error_frames_before_close() {
        // A subscriber connected at crash time receives the error frame + [DONE] live
        // (in order) and only then sees the stream end — never a bare close.
        let session = "test-crash-live";
        let (tx, rx) = mpsc::unbounded_channel::<String>();
        let stream = async_stream::stream! {
            let mut rx = rx;
            while let Some(e) = rx.recv().await {
                if e == "__panic__" {
                    panic!("boom");
                }
                yield e;
            }
        };
        start(session, stream);
        tx.send("hello".to_string()).unwrap();
        tokio::time::sleep(Duration::from_millis(50)).await;
        assert!(is_active(session));

        let s = subscribe(session);
        futures_util::pin_mut!(s);
        let first = tokio::time::timeout(Duration::from_secs(5), s.next())
            .await
            .unwrap();
        assert_eq!(first, Some("hello".to_string()));

        // Trigger the crash and confirm the live subscriber gets the error frames
        // in order, then a clean end.
        tx.send("__panic__".to_string()).unwrap();
        let err = tokio::time::timeout(Duration::from_secs(5), s.next())
            .await
            .unwrap();
        assert_eq!(
            err,
            Some(
                "event: error\n\
                 data: {\"error\": \"Agent run failed before completion.\", \"status\": 500}\n\n"
                    .to_string()
            )
        );
        let done = tokio::time::timeout(Duration::from_secs(5), s.next())
            .await
            .unwrap();
        assert_eq!(done, Some("data: [DONE]\n\n".to_string()));
        let end = tokio::time::timeout(Duration::from_secs(5), s.next())
            .await
            .unwrap();
        assert_eq!(end, None);
        assert_eq!(get_status(session), Some(RunStatus::Error));
    }

    #[tokio::test]
    async fn stop_does_not_emit_error_frames() {
        // A cooperative cancel is a NORMAL stop, not a crash: no error/[DONE] frames,
        // status `stopped`, and the buffer holds only the real events.
        let session = "test-stop-clean";
        let (tx, rx) = mpsc::unbounded_channel::<String>();
        let stream = async_stream::stream! {
            let mut rx = rx;
            while let Some(e) = rx.recv().await { yield e; }
        };
        start(session, stream);
        tx.send("one".to_string()).unwrap();
        tokio::time::sleep(Duration::from_millis(50)).await;
        assert!(stop(session));
        for _ in 0..50 {
            if get_status(session) == Some(RunStatus::Stopped) {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        assert_eq!(get_status(session), Some(RunStatus::Stopped));
        let got = collect(session).await;
        assert_eq!(got, vec!["one".to_string()]);
    }

    #[tokio::test]
    async fn no_run_subscribe_yields_nothing() {
        let got = collect("no-such-session").await;
        assert!(got.is_empty());
        assert_eq!(get_status("no-such-session"), None);
        assert!(!is_active("no-such-session"));
        assert!(!stop("no-such-session"));
    }
}
