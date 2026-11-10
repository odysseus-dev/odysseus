# Odysseus Fix Workstreams

Evidence source: 626 historical `sft_alex_creator` contract sessions, deduplicated
to 54 flows and replayed through the current 7011 Agent runtime on 2026-09-11.

## Harness

- **Resolved — canonical item limits:** Notes and Calendar now honor explicit
  limits such as “at most three” while retaining hidden expansion payloads.
- **Evaluate separately — shell/files:** two WebUI failures occurred because bash
  is not consistently offered on follow-up. Shell/files belongs to the validated
  `odysseus-native` workspace runtime; do not train the model on WebUI refusals.
- **Resolved — Calendar argument continuity:** referential repeats preserve the
  preceding successful range; an explicitly new period still replaces it.
- **Resolved — evaluator:** historical one-turn probes are now retained, and the
  judge treats HTML-comment expansion rows as hidden rather than visible overflow.

## Model / SFT

- **Remaining — browser evidence use:** the IKEA task routes correctly to
  `private_browser`, but the model clicks opaque refs repeatedly and never extracts
  a chair answer. This is the confirmed SFT repair class.
- **Remaining — identity attribution:** after successful Email → Calendar
  switching, “Who are you?” can add the false phrase “trained by Google.” Keep
  this as SFT data; do not restore a forced harness identity response.
- **Resolved in harness — Memory synthesis:** row evidence is compacted before the
  observation cap instead of being truncated inside invalid JSON; Memory is 3/3.
- **Resolved in harness — Search recovery and source rendering:** equivalent empty
  queries stop after two attempts, freshness words survive query shortening, and
  exact source-link requests render the best relevant first-party result. Search is
  15/16, with only the browser reasoning case above remaining.
- **Resolved in harness — Cookbook synthesis:** configured server rows use a
  bounded evidence-owned renderer; Cookbook is 3/3.

Build repair examples from these behavior classes only after exact replay confirms
the failure with the intended runtime and rendering owner.

## Backend / Data

- The Python packaging query returned an unrelated OWASP result. The model reported
  the failure honestly, but should attempt a bounded recovery before stopping.
- Synthetic email account servers are unavailable. The harness now renders that as
  an outage and blocks invented message IDs; restore the fixture separately.

## Current measurement

- Historical source sessions: **626**
- Unique replay flows: **54**
- Initial judge result: **36 pass / 18 flagged**
- Post-renderer replay for Notes, Calendar, and switching: **14 pass / 2 flagged**.
- Final Notes + Calendar replay after continuity and judge fixes: **9 pass / 0 flagged**.
- Latest Search replay: **15 pass / 1 confirmed SFT failure**.
- Memory replay: **3 pass / 0 flagged**; Cookbook replay: **3 pass / 0 flagged**.
- Final WebUI-valid historical matrix: **49 pass / 2 confirmed SFT failures = 96.1%**.

Artifacts:

- Full run: `tmp/odysseus-conversation-qa/run-20260911-092930.json`
- Post-renderer replay: `tmp/odysseus-conversation-qa/run-20260911-093333.json`
- Final Notes + Calendar replay: `tmp/odysseus-conversation-qa/run-20260911-093752.json`
- Latest Search replay: `tmp/odysseus-conversation-qa/run-20260911-100239.json`
- Memory replay: `tmp/odysseus-conversation-qa/run-20260911-095320.json`
- Final WebUI-valid matrix: `tmp/odysseus-conversation-qa/run-20260911-101229.json`
- Deduplicated queue: `tmp/odysseus-conversation-qa/historical-sft-alex-queue.json`
