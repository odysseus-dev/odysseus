# Model Behavior Observations

Last updated: dev@2e2bb52 | 2026-08-16

## Scope

This file records model- or provider+model-specific behavior observed in
Odysseus code, tests, Issues, PRs, commits, and provider documentation. It is a
compact evidence map, not a runtime matcher. General canonical rules belong in
[model-capability-canonical.md](model-capability-canonical.md); provider-wide
transport belongs in [the provider map](model-providers/_readme.md).

The canonical capability layer intentionally has no
`src/model_behavior_quirks.py`.
Adding a registry before runtime call sites carry structured provider, model,
version, and dialect identity would create another model-name matching layer.

## General Observation Template

Record only the fields supported by the evidence:

- provider and endpoint/dialect scope;
- exact provider-returned model ID or family;
- structured model/provider version when available;
- capability or request/response behavior observed;
- exact native request field/value and response field when relevant;
- source, confidence, status, and reproduction date;
- whether the behavior is already implemented in runtime code.

If exact structured identity is unavailable, keep the observation here and in
its current tested runtime location. Do not promote it through substring,
regex, prose, or serialized-prompt parsing in the canonical layer.

## Model-Specific Observation Map

| Observation | Scope | Behavior | Evidence/status |
| --- | --- | --- | --- |
| Moonshot Kimi K2.5/K2.6 fixed temperature | official Moonshot, K2.5/K2.6, OpenAI Chat | omit `temperature`; thinking mode owns its fixed value | #3960, `f5d3e509`; implemented in current runtime |
| Moonshot reasoning tool history | same provider/models/dialect | preserve assistant `reasoning_content` across tool continuation | #3118, `2e6fff22`; implemented |
| Claude Opus 4.7+ sampling omission | Anthropic Messages, Opus 4.7+ and major-only later IDs such as `claude-opus-5` | omit `temperature`, `top_p`, and `top_k` where the runtime rule applies | #3117, `4f48cfa9`, #5761; implemented through current runtime identity logic |
| Mistral structured reasoning | reasoning-capable Mistral model through native/compatible response shape | use graded effort where accepted; keep typed thinking separate from text | #4698, `bd9149f7`, provider docs; partly implemented |
| Ollama native reasoning control | selected reasoning model/deployment | native `think`; reasoning in `message.thinking`/`thinking` | #3031 and provider docs; deployment scoped |
| Ollama native `gpt-oss` reasoning level | `gpt-oss` served through Ollama native | `think` accepts low/medium/high and does not represent off | provider docs; deployment scoped |
| Ollama compatibility disable observation | Ollama 0.20.6+, observed Qwen3.5 compatibility path | `reasoning_effort: none` was reported to disable reasoning | #5503; unmerged/low confidence until reproduced |

Issue and commit references are evidence identifiers, not runtime dependencies.
Open or unmerged observations remain provisional until reproduced or supported
by current provider documentation.

## Other Model-Level Observations

- Kimi K2.5/K2.6 multimodality differs from older K2 variants (#2522). Promote
  only from an exact provider card or scoped registry, never the `kimi` token.
- Google product names suggest media tasks to humans, but its Models resource
  does not publish complete modalities. Keep those modalities unknown without
  stronger model-scoped evidence.
- Ollama `/api/tags` names can omit vision markers (#3743, #4487). Use selected
  model `/api/show.capabilities`, not its name.
- Local reasoning controls vary by serving template/config: message/system
  directives, `chat_template_kwargs.enable_thinking`, native booleans,
  structured objects, budgets, and effort levels were all observed (#3031).
  These are endpoint/deployment facts, not universal checkpoint properties.
- DeepSeek, vLLM/NIM, Mistral, Moonshot, Ollama, and harmony-style servers use
  different structured reasoning channels. Provider/dialect evidence chooses
  the channel; generic response-text scanning is not capability discovery.
- Current runtime recognizes DeepSeek V4 identifiers in its thinking-model patterns; that is request/response handling evidence, not proof that every V4-named endpoint exposes identical capabilities.
- GPT-OSS deployments can reserve native tool names. Runtime aliases colliding Odysseus tool names at the provider boundary and reverses the alias before local execution; this is dialect compatibility, not extra tool authorization.
- Cohere native and compatibility transports expose different thinking
  controls/channels. The Cohere model list does not itself prove reasoning.
- MiniMax M2.7 exposes different thinking channels through Anthropic and
  OpenAI-compatible transports. Its current model list is identity-only.
- Gemma/Phi/Qwen vision behavior has changed across serving engines (#1430,
  #1704, #1478). Native engine metadata or a verified endpoint probe outranks
  a model-family name list.

## Promotion Gate

Before an observation becomes canonical runtime behavior, a consumer must
already have the necessary structured identity and tests must cover both its
positive scope and a neighboring negative scope. Request control and response
visibility remain separate: hiding reasoning text is not the same as disabling
reasoning at the provider (#2905).

## Current Gaps

- Runtime still contains model-name helpers for several implemented behaviors;
  this spec records them but the canonical catalog does not duplicate them.
- Hosted aliases and provider behavior can change; there is no durable
  observation expiry/revalidation layer yet.
- Detail/probe-only model facts cannot safely be populated from list discovery.
