# Background research → originating chat

Chat `trigger_research` calls carry a **dispatcher-supplied** `origin_chat_id`.
The research start route verifies chat ownership before registering a durable
`background_tool_jobs` row and starting the existing research service. Panel
jobs have no origin and never inject a chat reply.

- Chat default: **2 rounds**, 120-second *soft* research budget. Explicit
  deeper/Auto rounds regain the normal research time budget. Panel defaults
  remain unchanged. This is not a guaranteed two-minute wall-clock deadline.
- A completion callback stores the report and sources. A startup worker also
  reconciles missed callbacks and research errors/restarts.
- When the origin has no active foreground/detached run, its model summarizes
  the report with thinking off and no tools. An outer 75-second deadline also
  bounds model-slot waits. If synthesis is unavailable, deliver an honest
  notice plus the report link; preserve the evidence for follow-ups.
- Message and delivery marker commit in one transaction with a deterministic
  message ID. Report context is stored in server message metadata and injected
  as untrusted evidence in regular and compact model history. Long excerpts
  are explicitly marked; the saved full research report remains accessible.
- The browser polls owner-scoped `/api/research/chat-jobs/{chat_id}`, appending
  unseen message IDs only when that chat is current and not streaming. No
  transcript replacement or forced navigation. Reloaded history deduplicates.
- Chat uses the existing agent-thread rail and expandable rows. The compact
  header shows status and a right-aligned BG task label with the shared whirlpool
  while running; expanding reveals topic, phase/round, source count and report
  link. Rows update in place, preserving expansion/focus while chat streams.
  Completed rows remain visible; zero-source runs show a warning, not success.
  Progress polling excludes reports and internal fields.

Other tools are **not automatically backgrounded**. The durable handoff can be
reused, but each future producer needs explicit launch/result/permission wiring.

## Verification

```sh
<configured-path> -q tests/test_background_tool_jobs.py tests/test_research_chat_runtime.py
node --test tests/backgroundToolJobs.test.mjs
node scripts/verify_background_delivery_isolation.mjs
node scripts/verify_background_research_cards.mjs
node scripts/verify_background_research_chat.mjs
```

The last script uses disposable `sft_alex_creator` chats and real research/model
calls, then removes only its own reports/chats. Do not use real-user mutations.
It checks two-round launch, continued chat, automatic arrival, no transcript
rebuild/duplicates, reload, and a follow-up. Inspect retained report excerpts
and generated summary when it fails; do not equate job launch with good research.

Initial live runs verified delivery/navigation/follow-ups but exposed a summary
attempt-count bug (fixed: helper requires **1 attempt**, not `max_retries=0`).
A later full run was interrupted by an inference endpoint outage. The corrected
summary path separately passed a real-model evidence/limitations/citation probe.
All targeted Python tests passed (441); real DOM isolation checks passed. A clean
full live run with useful retrieved evidence remains to be recorded.
