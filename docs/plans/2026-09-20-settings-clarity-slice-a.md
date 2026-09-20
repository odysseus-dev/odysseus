# Tracker: Settings copy (slice A)

**Status:** tracking only — do not implement in this PR  
**Depends on:** nothing  
**Unblocks:** optional; slice B supersedes most of this copy if B ships first

## Intent

Stop the Settings GUI from implying that Add Models, Added Models, AI Defaults, and Integrations are the same “add a model” story. Copy and subtitles only.

## In scope

- Relabel Add Models: cloud via overlay 9router vs local leftover.
- Relabel Added Models: old Odysseus endpoint inventory, not 9router connections.
- Relabel AI Defaults: not overlay chat routing (OpenHands + 9router routes).
- Relabel Integrations Codex/Claude Agent: inbound plugins into those CLIs, not 9router providers.

## Out of scope

- Moving cards, hiding tabs, wiring AI Defaults to 9router routes, deleting ModelEndpoint cloud admin (slices B–D).

## Done when

An operator can read those four screens and tell 9router / OpenHands / leftover Odysseus endpoints / inbound plugins apart — without the layout changing.
