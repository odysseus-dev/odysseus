# Agent turn contract

Scope: product Agent turns on 7011. Environment-owned native/TUI bridges retain
their existing execution contract. No model weights or training settings change.

## Boundaries

1. `src/turn_contract.py` classifies capabilities, including explicit compound
   requests and referential follow-ups. Classification is selection, not permission.
2. `routes/chat_routes.py` resolves toggles, privileges, global/plan/incognito
   restrictions, fixture restrictions and available schema inventory before
   freezing the offered set. Web enabled alone does not select web tools.
3. `TurnContract` checks `required <= offered <= executable`, stores immutable
   serialized schema copies, and records unavailable requirements. An unavailable
   request stops without inference or substitution; unknown actions ask for clarity.
   Exact account-discovery requests narrow selection to account metadata only;
   compounds retain their declared family scope. Media operations declare their
   existing tool dependencies rather than falling back to shell generation.
4. The agent's prompt/schema route and fallback use that same logical scope.
   Native versus textual serialization remains model-specific. Answer-only phases
   can suppress tool calls without granting a different scope.
   Contract turns preserve the already-compacted conversation and tool-call/result
   IDs. The standalone specialist prompt's latest-message-only behavior is not used
   for these product turns. Prompt domains also come from the contract.
   Accepted in-scope calls retain their model-provided arguments and native IDs;
   the explicit-intent fallback must not overwrite them with the whole user turn.
5. The context-bound dispatcher checks membership **and** existing runtime policy,
   owner restrictions and exact-action approvals. A contract is not authorization
   to bypass those gates. Contract work bypasses terminating legacy shortcuts.
6. `_AgentRenderState` explicitly identifies streamed versus canonical output.
   Later synthesis transfers ownership with turn-scoped replacement. The frontend
   reconciles visible DOM, not just accumulated strings; tool evidence is retained.
   Ownership is included in saved metrics and `message_saved` events.
   History and resume honor replacement scope. Single-capability turns retain
   canonical output: an always-synthesize trial caused a live notes loop and was
   reverted. Compound turns cannot terminate after only one capability's result.

## Verification

Use the project's configured Python environment, not an unrelated system Python:

```sh
python -m pytest -q \
  tests/test_turn_contract.py tests/test_turn_contract_integration.py \
  tests/test_agent_turn_contract_boundaries.py tests/test_turn_rendering_js.py \
  tests/test_contract_prompt_conversation.py tests/test_product_turn_contract_route.py \
  tests/test_contract_explicit_fallback.py \
  tests/test_history_resume_rendering_js.py \
  tests/test_chat_route_tool_policy.py tests/test_tool_policy.py \
  tests/test_frontend_module_version_parity.py
node scripts/verify_agent_turn_contract.mjs --max-turns 80 --total-ms 900000
```

The browser verifier uses `sft_alex_creator` and actual 7011 Agent controls. It
captures request toggles, SSE contract/tool events, visible output and persisted
history. Ten families have four initial/follow-up Web-toggle combinations.
Blocked or unrun cases are not passes. Email requires verified fixture isolation;
do not enable global fixture mode on the user's live service to make a test pass.

## Remaining limits

- Classification is deterministic and vocabulary-based, not a proof of semantic
  understanding. Add independent behavior examples for confirmed misses.
- Schema registration and policy permission do not guarantee a remote provider
  stays healthy throughout a turn. Runtime failure must remain visible.
- Separate tool/argument errors, tool-service failures, rendering failures and
  verifier defects in reports. Do not infer model accuracy from routing alone.
- Canonical summaries can still ignore presentation constraints such as a
  requested item count. Do not count those as full functional passes. Forcing an
  extra model round is not a validated general repair for this deployed model.
- Keep all imports of a local JS module on the same URL identity. Distinct query
  versions instantiate separate module state even when source files are identical.

Live baseline and current matrix results are in `reports/agent-turn-contract-*`.
The implementation is not a claim that every family has passed live verification.
