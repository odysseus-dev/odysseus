# Odysseus Tool Runtime Hardening Plan

## Objective

Ship `odysseus-qwen3.5-tools-pre-heretic` with one compact, model-specific tool
runtime that supports realistic multi-turn use. Keep the existing RAG runtime
unchanged for every other model. Prove routing, execution, answer quality,
follow-ups, safety, rendering, latency, and native image/VL understanding through
the real 7011 Agent UI.

Current evidence is a baseline, not a ship claim:

- Corrected v2.5 + compact-v5 development is 327/344 raw (95.06%) and
  327/336 scorable (97.32%). Sealed blind is 311/344 raw (90.41%) and
  311/336 scorable (92.56%), with zero reasoning leakage.
- Notes, Skills, and Cookbook/admin clear 95% scorable blind. Calendar 87.5%,
  Shell/files 86.11%, and Tasks 87.5% remain below the 90% family ship floor.
- Compact-v5 hints improved Email, Search/HF quant, and Shell on development;
  a Calendar hint regressed and was rejected rather than shipped.

- Ten-family focused baseline: 19/20 functional and 20/20 routing/execution.
- Typo and cross-family read flows: 26/26 passed.
- Real use exposed untested write correction and search-to-fetch follow-ups.
- Email production access, browser interaction, search quality, and broader
  multi-turn mutations are not yet proven.
- Nine enabled chat-capable regular API models pass the ten-family read-only
  legacy-RAG baseline (90/90 combined). Their stricter typo/follow-up profile is
  178/180 turns: eight models are 20/20 and Luna is 18/20 due only to its
  misspelled Shell request. One pinned image-generation model is explicitly
  unsupported and two visible local models are currently offline.
- Native VL object/spatial recognition and reload follow-up pass. Exact OCR
  fails equally on the fine-tune and untouched 9B base and remains unresolved.
  PNG, JPEG, and WebP transport all pass.
- Reversible create/correct/API-verify/cleanup flows pass 6/6 across every
  stateful family.
- Search Web-toggle combinations pass 8/8 and the focused quality suite passes
  3/3. Production-path email account/inbox/referential reads pass 3/3.
- The latest regular-model regression is 90/90 across the nine enabled
  chat-capable API models, with zero failed model turns; two local endpoints
  remain offline and the image-only model is unsupported.
- The Epictetus OMLX endpoint was recovered after an unsupported
  `qwen3_5_mtp` model load wedged the server. Its supported Qwen 27B 4-bit
  model passes the ten-family real-7011 legacy-RAG smoke 10/10; the unsupported
  MTP artifact is recorded as a runtime limitation rather than a timeout.
- Fresh compact-v5 UI regressions pass stateful 6/6, Email 3/3, Search 3/3,
  private-browser 3/3, and VL workflow 3/3.
- The exact-model, family-scoped compact runtime now passes 20/20 direct and
  same-family turns across all ten families on the real 7011 Agent UI. A
  separate 36/36 robustness run passes misspellings, bounded repeats, browser
  and news continuation, ambiguous follow-ups, family switchbacks, and a
  greeting before a tool request.
- The mobile active-email editor path passes 1/1: `Write reply this email`
  offers and executes only `update_document`, mutates the open draft, and
  preserves its reply headers and quoted thread.
- The active-editor classifier now also covers short mobile wording without a
  pronoun (`Write reply` / `Draft a reply`) while explicit note, code, file, and
  new-object requests retain their own families. Whole-draft requests are bound
  to the sole offered `update_document` writer until one successful write, then
  tools are removed for the confirmation round. The deployed real-route email
  regression passes 3/3—including the exact unspecified `Write reply to this
  email` form—with one write, verified mutation, and preserved reply headers.
  Clean-v3 now also emits the established `doc_update` event and flattened
  document metadata on `tool_output`, so a successful database write updates
  the already-open editor instead of leaving stale UI beside a success message.
- The client now reuses the existing assistant bubble for `agent_step` round 1
  instead of replacing it before the first token. A real-7011 sampled
  greeting-to-Notes conversation passes 2/2 with stable first-round DOM
  identity; round 2+ remains the only continuation-bubble path.
- Clean-runtime metrics now expose provider-counted initial injected tokens,
  all-round input/output, TTFT, tok/s, schema count, agent rounds, and tool-call
  count. A real 7011 browser run passes 2/2 and visibly renders compact footers
  plus the full details popup; the sampled Notes turns streamed progressively.
- The deployed startup bottleneck was an unindexed quadratic transcript-FTS
  reconciliation. Live-database import fell from about 36 seconds to 0.54
  seconds; 7011 now answers in about 3 seconds after a controlled restart.
- A controlled identical-compact comparison already proves the fine-tune's
  accuracy benefit: 94.48% (325/344) versus the untouched base's 77.91%
  (268/344). Raw serving speed is effectively tied, so product speed comes
  from the compact contract and fewer failed/redundant rounds.
- A fully merged 10,000-row category-repair candidate reached 97.32% scorable
  development but only 92.26% scorable sealed blind. Calendar (87.5%), Tasks
  (87.5%), and Shell/files (86.11%) remained below the family floor, so it was
  rejected and not deployed. Compact-v4/full development A/Bs did not improve
  Calendar or Tasks over compact-v5; full-schema Shell also fell from 97.22%
  to 94.44%. This rules out compactness as the primary cause of the remaining
  blind gaps and supports keeping the compact contract.

## Non-negotiable architecture rules

1. Runtime selection follows exact model identity. The trained Odysseus model
   uses the clean compact runtime across endpoint aliases; all other models use
   legacy RAG. Add a regression test for both sides.
2. Resolve permissions, toggles, and available backends once per turn. Produce
   one immutable contract satisfying `required ⊆ offered ⊆ executable`.
3. Never offer a tool that the preview policy will categorically reject. Add a
   contract self-check covering every offered action/effect combination.
4. Follow-ups consume typed prior evidence: native call, result, success state,
   family, and object identifiers. Do not infer continuity from keyword RAG.
5. Contextual write authority may revise only a recently proven object in the
   same family. It may not authorize a new object, another family, a destructive
   action, or an external side effect.
6. The model chooses tools and valid arguments. The harness validates and
   executes; it does not silently substitute another family, rewrite arguments,
   fabricate success, or replace a failed tool with prose claiming completion.
7. One owner renders each turn: streamed prose or canonical structured output.
   Never both, and never expose hidden prompts or raw untrusted wrappers.
8. No exact-prompt production patches. A fix must name the failed layer, add a
   generic failing invariant test, and cover neighboring cases.

## Failure layers

Every failure is assigned to exactly one primary layer before code changes:

1. **Route:** wrong model runtime or endpoint identity.
2. **Contract:** required tool absent, forbidden tool present, or toggle drift.
3. **Model:** wrong/no tool or semantically wrong required arguments despite a
   correct contract.
4. **Policy:** valid proposed operation incorrectly allowed or denied.
5. **Execution:** canonical arguments, backend dispatch, timeout, or result
   envelope is wrong.
6. **Evidence:** result is empty, irrelevant, truncated badly, or insufficient.
7. **Answer:** model misstates or ignores valid tool evidence.
8. **Rendering:** duplicate, dump-at-end, missing structured output, or stopped
   stream.
9. **Performance:** startup, TTFT, tool latency, or oversized context.

Reports store aggregate category, relevant contract/tool metadata, timings, and
sanitized outputs. Do not copy private hidden benchmark prompts or create a log
dump that nobody can audit.

## Test matrix

Use the real authenticated 7011 Agent UI and the normal `preheret` picker alias.
Use `sft_alex_creator` for reversible writes. Never mutate the personal account
from an automated test.

### A. Every one of the ten families

For calendar, notes, email, tasks, documents, memory, skills, Cookbook/admin,
search/browser, and shell/files, test:

- direct request;
- natural misspelling;
- ambiguous same-family follow-up;
- switch to another family and back;
- no-tool greeting before the tool request;
- requested count/field limit;
- backend failure rendered truthfully;
- reload the permalink before a follow-up.

### B. Stateful mutation families

For notes, calendar, tasks, documents, memory, and skills:

- create → verify by API → referential correction → verify;
- create → list/read → correction → verify;
- typo correction such as name/date/title without repeating the family noun;
- correction after one unrelated conversational turn;
- destructive request is denied atomically;
- failed write never produces a success claim;
- cleanup deletes only the UUID-owned test artifact and verifies absence.

### C. Search and browser conversations

- search → summarize existing results without a new call;
- search → inspect one result with `web_fetch`;
- poor results → refine query once;
- insufficient evidence → say so without fabrication;
- Web toggle combinations `00`, `01`, `10`, and `11` across two turns;
- private browser open/snapshot/click only after its permission boundary is
  deliberately enabled and specified; do not smuggle it in via web search.

Grade source relevance, freshness, authority, and whether claims are supported,
not merely whether `web_search` was called.

### D. Email and shell

- Separate fixture accuracy from production connectivity. A fixture pass cannot
  promote production email health.
- Test account listing, inbox listing, reading, and referential follow-up against
  the configured production-like backend before enabling email actions.
- Shell remains toggle-gated. Test off/on transitions, canonical raw command
  dispatch, read-only output, and denial of network/destructive commands.

### E. Rendering and performance

- Assert first visible streamed token, monotonic DOM growth, one final answer,
  persistence/reload equality, stop behavior, and structured list rendering.
- Record request preparation, TTFT, tool duration, post-tool TTFT, total time,
  input/output tokens, and tool-result bytes.
- Diagnose the 30–40 second 7011 restart separately from inference latency.
- Bound large calendar/search results before replaying them into later rounds,
  while preserving IDs and fields needed for follow-ups.

### F. Image/VL recognition

- Attach real PNG, JPEG, and WebP images through the 7011 UI and verify the
  trained model receives native multimodal message content on its clean route.
- Test object recognition, visible text/OCR, spatial relationships, charts, and
  screenshots. Score required facts instead of stylistic wording.
- Test image → ambiguous follow-up, image → tool request, and tool result → image
  comparison without requiring the user to attach the same image again.
- Verify image references survive persistence and permalink reload without raw
  base64, local paths, or hidden wrappers appearing in chat output.
- Separate direct model vision from `inspect_media`, browser screenshots, and
  image generation. The harness must not silently substitute one for another.
- Compare the fine-tune with its base VL model on the same images to detect
  whether tool training regressed visual understanding.

### G. Regular-model legacy RAG and tool coverage

- Inventory every enabled non-Odysseus endpoint/model visible in 7011, including
  its provider, schema mode, native-tool support, context limit, and configured
  permissions. Do not assume every provider supports the same wire format.
- Assert that no non-Odysseus model enters the clean-v3 runtime. These models
  retain the regular RAG/tool loop and are repaired only in that owning path.
- For each model, test every tool family the effective user policy offers:
  direct request, misspelling, ambiguous follow-up, family switch, backend
  failure, and Web/Bash toggle transitions. Record unsupported families as an
  explicit capability limitation, not a silent pass.
- Test full schemas versus compact schemas only where both are valid for that
  model. Store the selected schema mode in every report.
- Verify provider-native tool calls, textual fallback parsing where required,
  canonical argument conversion, execution, evidence replay, and rendering.
- Group fixes by shared legacy-runtime or provider-adapter defect. Do not add
  model-name prompt exceptions when a transport, schema, or RAG ranking issue is
  responsible.
- Maintain a per-model compatibility matrix so adding or changing an endpoint
  cannot silently regress previously working tools.

## Fix protocol

For each failure:

1. Preserve the raw report and reproduce once on a fresh test session.
2. Identify the primary failure layer from the taxonomy above.
3. Add the smallest generic red test at that layer.
4. Fix the owning module or invariant—not the literal prompt.
5. Run the focused unit tests, the original scenario, two adjacent scenarios,
   and the affected family suite.
6. After a batch of category fixes, rerun the ten-family matrix and legacy-RAG
   isolation test. Do not rerun training unless the contract and harness are
   proven correct and failures remain model-owned.

If three failures share a layer, pause case-by-case patching and refactor that
layer before continuing.

## Execution phases

### Phase 1 — Make the runtime auditable

- Add a sanitized per-turn decision record: model runtime, contract, proposed
  calls, policy decisions with reason codes, executions, render owner, timings.
- Add startup/runtime provenance to the UI so a linked chat proves which harness
  handled it.
- Add the offered-versus-policy compatibility self-test.
- Correct stale preview documentation.

### Phase 2 — Build the conversation suite

- Extend the current Playwright verifier with reusable multi-turn scenarios and
  reversible artifact fixtures.
- Implement the matrix above, prioritizing search continuations and all
  stateful corrections because real usage already exposed those gaps.
- Run independent family groups in parallel, but serialize writes that share a
  backend or fixture account.
- Add a small versioned VL fixture set with locally generated, non-private
  images and deterministic answer keys.

### Phase 3 — Repair by architecture category

- Consolidate model-specific runtime selection in one function.
- Represent prior successful objects explicitly for referential follow-ups.
- Align tool capability classification, contract offering, and policy decisions.
- Standardize tool results into bounded envelopes with source/object IDs.
- Keep search refinement and evidence sufficiency generic.

### Phase 4 — Accuracy and speed comparison

- Compare the clean fine-tune with the base model using identical compact tools,
  prompts, toggles, backend state, and semantic scoring.
- Report functional accuracy, argument accuracy, unsupported success claims,
  TTFT, total latency, and tokens. Do not compare one model on full schemas and
  another on compact schemas.
- Only consider more SFT/RL for failures classified as model-owned after the
  harness audit.

### Phase 4B — Regular-model repair and verification

- Snapshot the enabled non-Odysseus model inventory.
- Run the legacy-RAG compatibility matrix in bounded parallel groups, respecting
  endpoint rate limits and shared backend write serialization.
- Fix shared harness/provider defects first, then rerun all affected models.
- Publish separate per-model scores and limitations; do not blend them into the
  Odysseus fine-tune score.

### Phase 5 — Ship gate

Ship only when:

- every family is at least 90% on sealed functional holdout;
- overall functional accuracy is at least 95%;
- realistic follow-up suite is at least 95%, with no repeated failure category;
- image/VL fixture accuracy does not regress materially from the base model and
  all attachment/follow-up/persistence flows pass;
- routing/execution and safety invariants are 100%;
- all reversible writes are API-verified and cleaned up;
- search quality and production email are reported separately and honestly;
- non-Odysseus models demonstrably retain legacy RAG;
- every enabled regular model has a complete tested-tool compatibility record,
  and every tool advertised as supported passes its functional checks;
- no hidden prompt leakage, duplicate rendering, or false success remains;
- pre-heretic passing weights and merged adapter backups remain recoverable.

## Immediate next batch

1. Expand VL fixtures to charts, screenshots, and image-to-tool turns;
   investigate the shared base-model OCR limitation without hiding it behind a
   silent external fallback.
2. Add deliberately permissioned private-browser open/snapshot/click checks;
   keep browser interaction unavailable when its boundary is not enabled.
3. Bring the two configured local regular models online and run their matrix.
4. Compare fine-tune versus untouched base with identical compact contracts,
   backend state, prompts, and timing instrumentation.
5. Run the sealed all-action holdout and prioritize failures by shared
   layer rather than by prompt.
