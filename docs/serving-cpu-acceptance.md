# CPU-first Cookbook → native chat acceptance

This is a prepared acceptance path, **not a record of a successful inference run**.
Do not run the installer, download a model, load weights, or change production
without the user's separate approval. Reuse existing accepted component/UI
checks; do not rerun the full regression or Windows process rehearsal.

## Gates before execution

1. Review the pinned combined candidate and frontend prerequisites below. Obtain
   separate approval for the target instance/activation and one small CPU model
   load. Running a code review is not deployment or inference permission.
2. Identify an existing complete, supported **small standalone GGUF**, preferably
   no larger than a 1–2B model. Check its exact path, nonzero size, GGUF header,
   required shards and bounded model metadata. Do not substitute a draft/MTP
   head, a cache reference, or a large available model. If none is selected,
   request a specific model/download approval rather than fetching anything.
3. Select an already-installed official standalone `llama-server` executable.
   Keep Python bindings separate. A dedicated CPU runtime is preferred; if only
   a CUDA build exists, its CPU mode may still initialize/enumerate a driver.
   Disclose that choice; do not install a different runtime automatically.
4. Use the approved instance's normal signed-in browser. Do not copy cookies,
   passwords, provider keys or grants. An isolated preview must have its own
   application data/DB and loopback port and must not inherit live MCP/provider
   connections. Do not point a scratch app at production's application data.
5. Choose a free non-service model port (for example 18101 after checking it).
   Never use the app, gateway or Chroma ports, and never replace a listener.

## Frontend prerequisites

The managed CPU launch must obtain a server-side plan and show any unknown-fit
warning. `allow_unestimated_context` remains false unless the user explicitly
acknowledges that warning. Do not silently invent a KV estimate or weaken the
backend's memory checks.

The native model selector joins serving `endpoint_id` to the normal chat catalog,
waits for `readiness: ready`, and uses a **runtime-reported model ID**, not the
friendly display label. A loading/unavailable/ownership-lost row is not a ready
selection. Refresh the catalog after readiness and after stop/restart; use the
returned replacement session/endpoint rather than a stale previous selection.

The retained failed Gemma task must show its backend error plus an accurate
“Original task log unavailable” explanation when `log_availability` says
unavailable. This is not runner output. Do not recreate the missing log or copy
an archived diagnostic into runtime state.

## One bounded acceptance flow

1. Open Cookbook → Launch, choose the verified small GGUF and installed runtime,
   select **CPU / GPU layers 0**, context **512**, and the free loopback port.
   Review the plan. Apply explicit unknown-fit acknowledgement only if approved.
   Do not enable automatic runtime setup or model download during this run.
2. Start through `POST /api/cookbook/serving/start`, not a raw subprocess smoke
   script. Save the returned `session_id` and `endpoint_id`. Starting is not ready.
   Verify worker diagnostics contain the matching published `source: serving`
   identity; the index itself grants no process-control rights.
3. Observe `wait-ready` with a **30-second** bound, then serving status. Require
   the expected ready service/model, verified live creation identity, managed
   provenance and the expected endpoint link. Timeout ends the acceptance attempt;
   it does not authorize automatic retries, a new download or force cleanup.
4. In native chat choose that endpoint and its actual model ID without changing
   default providers. Use a named fresh session, with no memory, attachments,
   fallback providers, background extraction or automatic title generation.
   Set `max_tokens_override: 32` through the session's `generation-settings`
   endpoint and read it back before sending. `ChatRequest` does not accept a
   direct `max_tokens` field; do not silently submit an ignored field.
   Send one non-streaming `POST /api/chat` with `use_web: false`,
   `use_research: false`, and the message “Do not use any tools. Reply with the
   word ready.” The existing guide-only policy blocks tool preprocessing and
   background extraction; this direct chat route does not use the agent loop.
   Limit generation to **32 tokens**; no tools, research, files, images or agents.
   Record the actual response/error and serving identity. A metadata fixture or
   HTTP health response is not an inference result.
5. Use typed `POST /api/cookbook/serving/stop` for that session only. Existing
   Windows model stop uses identity-gated process-tree termination; this is not
   a guarantee of graceful runtime shutdown. Approval must cover this explicit
   model stop, never termination of Odysseus, Hermes, Chroma or another server.
6. Verify `process_stopped: true`, the original process incarnation is gone, its
   listener is closed, and its managed chat entry is removed. Confirm unrelated
   endpoint configuration and services remain unchanged. Preserve task/worker
   history and diagnostics; do not delete evidence to make a guard pass.

## Evidence to retain

- Candidate/runtime/model identifiers, plan and limits; no credentials.
- Returned serving/endpoint IDs, readiness and ownership observations.
- Actual native-chat response or exact failure; no fabricated result.
- Typed stop result and post-stop listener/identity observations.
- Failure stays a failure. Unknown ownership or incomplete stop is a blocker,
  not permission to signal a guessed/recycled PID or escalate against the app.

Only after this passes and the user separately approves it should RTX 3070 GPU
offload be attempted, with a conservative plan and measured limits. Do not use
an old CUDA stress/stability script as a replacement for this CPU-first flow.
