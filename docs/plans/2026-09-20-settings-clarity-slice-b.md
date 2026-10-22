# Tracker: Settings information architecture (slice B)

**Status:** tracking only — do not implement in this PR  
**Depends on:** slice A optional (B can include honest copy)  
**Unblocks:** slice C (AI Defaults behavior), slice D (retire leftover cloud admin)

## Intent

Make the user-facing Settings GUI match the overlay stack: Odysseus is the board, OpenHands executes, overlay 9router owns cloud credentials and chat routing. This is the **minimum** scope that clears the confusion on Add Models / Added Models / AI Defaults / Integrations.

## Hierarchy this slice must make visible (coarse → fine)

1. 9router connections (which clouds are connected)
2. 9router routes (`automatic` / `fast` / `balanced` / `best`)
3. OpenHands runtime (Native / OpenCode)
4. Chat vs Agent workspace privilege
5. Local leftover (Ollama / llama.cpp / vLLM), clearly not 9router

## In scope

- One Inference area: 9router connections then 9router routes.
- Hide or drop Added Models **API** as overlay chat inventory; keep LOCAL only if still needed.
- Move or retitle Integrations **Codex Agent** / **Claude Agent** so they read as “Odysseus as a plugin in those CLIs,” not 9router providers.
- Tests for nav/copy/static structure.

## Out of scope

- Wiring Default Chat Model prefs to 9router routes (slice C).
- Deleting remaining cloud `ModelEndpoint` / `/setup` paste-key surfaces (slice D).

## Done when

Phone Settings no longer presents four competing “add model” stories. Codex/Claude on Integrations cannot be mistaken for 9router Connect.
