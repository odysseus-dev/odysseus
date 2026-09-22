# Provider Capability Specs

Last updated: dev@e71f8ce | 2026-08-25

## Scope

This directory maps serving-provider observations and current model-catalog
normalization into the canonical layer defined by
[model-capability-canonical.md](../model-capability-canonical.md). It records
current Odysseus implementation evidence, merged fixes, reproducible user
observations, and provider documentation without treating any single source as
global model truth.

## General To Specific Resolution

Read specs in this order:

1. [openai-compatible.md](openai-compatible.md) for the conservative general
   identity-only reader;
2. the serving-provider file for native endpoints, headers, request/response
   observations, and catalog fields;
3. [model-quirks.md](../model-quirks.md) for model-specific observations.

Provider files document transport; runtime adapters still own it. Model quirks
record only deviations and are not a second runtime matcher. Shared model facts
must not be copied into every provider file. An OpenAI-compatible provider is
not OpenAI: an explicitly supplied vendor string is preserved even when it uses
the generic reader.

Current reader dispatch does not infer a provider from payload shape. It uses an explicit vendor, then endpoint kind, label-bounded hostname matches, and common local-port hints. The port hints map 11434 to Ollama, 1234 to LM Studio, 8000 to vLLM, and 30000 to SGLang. Those hints are normalization behavior, not endpoint trust.

## Provider Map

### Implemented canonical readers

- [openai.md](openai.md): identity-only Models API plus Chat/Responses dialects.
- [openai-compatible.md](openai-compatible.md): generic compatible catalog and runtime dialect boundaries.
- [openrouter.md](openrouter.md): rich architecture, modalities, parameters, and limits.
- [google.md](google.md): native paginated Gemini Models API and GenerateContent.
- [ollama.md](ollama.md): `/api/tags`, `/api/show`, native chat, and OpenAI compatibility.
- [lm-studio.md](lm-studio.md): native v1 catalog/chat, explicit v0 compatibility, and OpenAI compatibility.
- [llama-cpp.md](llama-cpp.md): `/props`, `/slots`, OpenAI/Responses/Anthropic surfaces.

### Placeholder identities using the generic reader

- [anthropic.md](anthropic.md): identity-only Models API and native Messages runtime adapter.
- [vllm.md](vllm.md): common-port identity hint; deployment capability remains unknown.
- [sglang.md](sglang.md): common-port identity hint; parser/config-dependent capability remains unknown.
- [hugging-face.md](hugging-face.md): Hub observations and download/fit metadata without a canonical reader.

### Provider observations without a dedicated canonical reader

- [mistral.md](mistral.md): rich model cards, reasoning controls, and structured runtime content.
- [github-copilot.md](github-copilot.md): account model-list observations and required runtime headers.
- [chatgpt-subscription.md](chatgpt-subscription.md): Codex model identity and Responses event shape.
- [cohere.md](cohere.md): native endpoint/catalog observations; not currently normalized.

### Other provider identity and general/identity-only observations

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
- latest observed native catalog endpoint/envelope and capability-bearing fields;
- whether current source has a dedicated reader or only generic fallback;
- observed request, tool, text, reasoning, and control paths owned by runtime
  adapters rather than the catalog reader;
- what remains per-model/unknown;
- Odysseus evidence and regressions;
- fallback/safety behavior and current gaps.

Marketing capability lists and curated picker lists may guide research but do
not automatically become model claims. Provider-returned false values can be
negative evidence only at the same provider/endpoint/model scope.
