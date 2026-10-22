# Tracker: AI Defaults → overlay 9router (slice C)

**Status:** tracking only — do not implement in this PR  
**Depends on:** slice B (Settings IA)  
**Unblocks:** nothing required; slice D may follow

## Intent

Default Chat Model on AI Defaults must **do** overlay routing, not only describe it. Today it still picks Odysseus `ModelEndpoint` + model name, which is not how OpenHands + 9router chat works.

## In scope

- Default Chat Model writes/reads 9router curated routes (`automatic` / `fast` / `balanced` / `best`) and opaque entitlement as needed.
- Stop showing empty Odysseus endpoint dropdowns as the overlay chat control.
- Utility / Vision: either stay honestly on local leftover endpoints, or a documented 9router policy — pick one in the implementation plan, do not leave both looking like chat routing.
- Prefs API + tests that currently assume endpoint+model for interactive chat defaults.

## Out of scope

- Full deletion of cloud ModelEndpoint admin (slice D).
- Integrations Codex/Claude plugin installers (slice B).

## Done when

AI Defaults Default Chat Model is the fine control for overlay chat (9router routes), consistent with the composer, not a second endpoint catalog.
