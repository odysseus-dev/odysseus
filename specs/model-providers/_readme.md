# Provider Capability Specs

Last updated: dev@28d27ee | 2026-07-17

## Scope

This directory maps serving-provider/API dialect and model-catalog shapes into
the canonical layer defined by
[model-capability-canonical.md](../model-capability-canonical.md). It records
current Odysseus implementation evidence, merged fixes, reproducible user
observations, and current provider documentation without treating any single
source as global model truth.

## General To Specific Resolution

Read specs in this order:

1. [openai-compatible.md](openai-compatible.md) for the conservative general
   envelope and fallback contract;
2. the serving-provider file for native endpoints, headers, request/response
   shapes, and catalog fields;
3. [model-quirks.md](../model-quirks.md) only when exact structured model and
   version identity is available.

Provider files own transport. Model quirks own only deviations. Shared model
facts must not be copied into every provider file. An OpenAI-compatible
provider is not OpenAI: keep its provider identity even when it uses the
general reader.

Latest native catalog shapes precede explicitly listed legacy shapes. If no
provider is known, use an unambiguous native shape, then the general structural
reader, then unknown. Never identify a local engine from its default port.

## Provider Map

### Rich or native catalog readers

- [openai.md](openai.md): identity-only Models API plus Chat/Responses dialects.
- [openrouter.md](openrouter.md): rich architecture, modalities, parameters, and limits.
- [google.md](google.md): native paginated Gemini Models API and GenerateContent.
- [anthropic.md](anthropic.md): identity-only Models API and native Messages content blocks.
- [ollama.md](ollama.md): `/api/tags`, `/api/show`, native chat, and OpenAI compatibility.
- [lm-studio.md](lm-studio.md): native v1 catalog/chat, explicit v0 compatibility, and OpenAI compatibility.
- [llama-cpp.md](llama-cpp.md): `/props`, `/slots`, OpenAI/Responses/Anthropic surfaces.
- [mistral.md](mistral.md): rich model cards, reasoning controls, and structured content.
- [github-copilot.md](github-copilot.md): picker-scoped nested support metadata and required headers.
- [chatgpt-subscription.md](chatgpt-subscription.md): Codex model identity and Responses event shape.
- [vllm.md](vllm.md): served-model cards and deployment-configured capabilities.
- [sglang.md](sglang.md): `/model_info`, `/v1/models`, parser/config-dependent capabilities.
- [hugging-face.md](hugging-face.md): Hub pipeline metadata versus serving-provider truth.
- [cohere.md](cohere.md): native endpoint families, context limits, sampling defaults, and v2/compatibility transports.

### Provider identity with general/identity-only fallback

- [moonshot-kimi.md](moonshot-kimi.md)
- [deepseek.md](deepseek.md)
- [groq.md](groq.md)
- [nvidia-nim.md](nvidia-nim.md)
- [cerebras.md](cerebras.md)
- [together.md](together.md)
- [fireworks.md](fireworks.md)
- [xai.md](xai.md)
- [zai.md](zai.md)
- [opencode.md](opencode.md)
- [perplexity.md](perplexity.md)
- [github-models.md](github-models.md)
- [venice.md](venice.md)
- [azure-openai.md](azure-openai.md)
- [bedrock.md](bedrock.md)
- [cloudflare-workers-ai.md](cloudflare-workers-ai.md)
- [atlas-cloud.md](atlas-cloud.md)
- [siliconflow.md](siliconflow.md)
- [minimax.md](minimax.md)

### Other local/proxy serving identities

- [local-compatible-engines.md](local-compatible-engines.md): MLX LM, TGI,
  LMDeploy, LiteLLM, and unknown compatible deployments.

## Provider Spec Template

Each provider file records:

- provider identity and API dialects;
- latest native catalog endpoint/envelope and exact capability-bearing fields;
- known compatibility shapes in explicit order;
- request, tool, text, reasoning, and control paths;
- what remains per-model/unknown;
- Odysseus evidence and regressions;
- fallback/safety behavior and current gaps.

Marketing capability lists and curated picker lists may guide research but do
not automatically become model claims. Provider-returned false values can be
negative evidence only at the same provider/endpoint/model scope.
