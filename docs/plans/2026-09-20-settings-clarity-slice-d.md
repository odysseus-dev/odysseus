# Tracker: finish Settings cutover (slice D)

**Status:** tracking only — do not implement in this PR  
**Depends on:** slice B; ideally slice C  
**Unblocks:** none

## Intent

Remove leftover Odysseus cloud-inference admin from Settings and related surfaces so the GUI cannot grow a parallel key/URL path beside overlay 9router.

## In scope

- Remove cloud `POST /api/model-endpoints` from Settings connect paths.
- Stop treating Added Models **API** as the operator’s cloud model list.
- Align `/setup` slash copy, onboarding, and remaining paste-a-key UI with 9router BFF.
- Keep local OpenAI-compatible leftover only if still a product need; otherwise defer to a later local-only spec.

## Out of scope

- Bumping overlay 9router image.
- Host-wide 9router.
- OpenHands/Canvas product changes.

## Done when

No Settings path invites storing a cloud API key on Odysseus. Overlay chat inference admin is 9router connections + 9router routes only.
