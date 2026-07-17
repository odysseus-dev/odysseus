# Model Behavior Quirks

Last updated: dev@28d27ee | 2026-07-17

## Scope

This spec covers model- or provider+model-specific behavior represented by
`src/model_behavior_quirks.py`, `ReasoningControl` in
`src/model_capabilities.py`, provider request/response shapes in
`src/provider_capability_schemas.py`, and existing observations encoded in
`src/llm_core.py`, provider adapters, tests, Issues, PRs, and merged commits.
Provider-wide transport belongs in [the provider map](model-providers/_readme.md);
general canonical rules belong in
[model-capability-canonical.md](model-capability-canonical.md).

## General Quirk Shape

A quirk selector can use only structured values already known by the caller:

- provider ID;
- exact model ID and/or provider-reported model family;
- structured model and provider version tuples;
- API dialect;
- required canonical capabilities.

A quirk can then describe exact request fields to omit/fix, required assistant
history fields, response reasoning paths, and reasoning-control shapes. Raw
model-name substring matching, regex version extraction, and prose parsing are
not part of this layer. If structured identity is missing, the quirk does not
match and normal provider/general fallback applies.

## Encoded Quirks

| Quirk | Structured scope | Canonical behavior | Evidence |
| --- | --- | --- | --- |
| `moonshot.kimi-k2.5-k2.6.provider-fixed-temperature` | Moonshot, Kimi K2.5/K2.6, OpenAI Chat | omit `temperature`; provider thinking mode owns its fixed value | #3960, `f5d3e509` |
| `moonshot.kimi-k2.5-k2.6.tool-history-reasoning-content` | same provider/families/dialect | preserve `reasoning_content` on assistant tool-call history and responses | #3118, `2e6fff22` |
| `anthropic.claude-opus-4.7-plus.omit-sampling-controls` | Anthropic Messages, family `claude-opus`, structured version >= 4.7 | omit `temperature`, `top_p`, and `top_k` | #3117, `4f48cfa9` |
| `mistral.reasoning.structured-content` | Mistral reasoning-capable Magistral/Small/Medium family | send graded `reasoning_effort`; read typed thinking blocks separately from text | #4698, `bd9149f7`, Mistral reasoning docs |
| `ollama.native.reasoning-control` | Ollama native, explicit Qwen3/DeepSeek reasoning family | native `think` boolean; reasoning in `message.thinking`/`thinking` | #3031, Ollama thinking docs |
| `ollama.native.gpt-oss-reasoning-level` | Ollama native, provider family `gpt-oss` | `think` accepts low/medium/high and cannot represent off | Ollama thinking docs |
| `ollama.openai-compat.0.20.6-reasoning-disable` | Ollama >= 0.20.6, Qwen3.5 family, OpenAI Chat | observed compatibility path uses `reasoning_effort: none` to disable | #5503; low-confidence until merged/independently verified |

Issue/commit references are evidence identifiers, not runtime dependencies.
Open/unmerged observations keep claimed/heuristic status until reproduced or
corroborated by provider documentation/current implementation.

## Observed But Not Yet Promoted

- Kimi K2.5 and K2.6 were reported with image input and K2.6 with video input,
  while older K2/K2-thinking variants differed (#2522). Promote only from an
  exact Moonshot catalog/registry model record, not the `kimi` token.
- Google model IDs can identify image/video/audio products to humans, but the
  native Models resource currently exposes methods and limits rather than
  complete modalities. Keep generation models unknown unless a stronger
  provider record or scoped registry supplies modality.
- Ollama `/api/tags` names can omit vision markers (#3743, #4487). Use
  `/api/show.capabilities`, not the model name.
- Local model reasoning switches vary by serving template/config: message
  directives, system directives, `chat_template_kwargs.enable_thinking`,
  native booleans, structured provider objects, budgets, and graded effort
  were all observed (#3031). This is endpoint/deployment evidence, not a
  universal intrinsic property of a checkpoint.
- DeepSeek, vLLM/NIM, Mistral, Moonshot, Ollama, and harmony-style servers use
  different structured reasoning response channels. Provider dialect and
  model/deployment evidence choose the channel; generic response text scanning
  is not capability discovery.
- Cohere native reasoning uses typed `thinking` content blocks and a structured
  `thinking` control, while its OpenAI compatibility layer currently maps only
  `reasoning_effort` values `none` and `high`. Keep this exact-model evidence;
  the Cohere model catalog does not itself mark reasoning support.
- MiniMax M2.7 exposes typed/interleaved thinking through its recommended
  Anthropic-compatible transport and `reasoning_content` through OpenAI
  compatibility. Its current model list is identity-only, so the response
  channel must not become a provider-wide capability claim.
- Gemma/Phi/Qwen vision support has repeatedly changed across serving engines
  (#1430, #1704, #1478). Native engine metadata or a verified endpoint probe
  outranks family-name lists.

## Reasoning Control Template

Each promoted reasoning quirk should specify:

- canonical mechanism: message directive, system directive, template kwarg,
  native bool, structured object, budget, or effort;
- canonical accepted semantics: on, off, and/or auto;
- provider-native values and exact request path;
- exact structured response paths;
- model/provider/API scope and version;
- assertion status, source, confidence, and evidence.

Disabling display of reasoning is not the same as disabling reasoning at the
provider (#2905). Store request control and response visibility independently.

## Current Gaps

- Runtime still contains model-name helpers for several observations; they
  should not be replaced until structured endpoint/model identity is available
  at those call sites.
- The registry has no expiry/revalidation mechanism for changing hosted model
  aliases.
- Some provider APIs expose capability only through per-model detail/probe
  endpoints, so list-only discovery cannot safely populate all quirks.
