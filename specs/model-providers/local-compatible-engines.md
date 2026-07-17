# Other Local And Proxy Compatible Engines

Last updated: dev@28d27ee | 2026-07-17

## Scope

Canonical explicit identities `mlx_lm`, `text_generation_inference`,
`lmdeploy`, and `litellm`, plus unknown OpenAI-compatible deployments not
covered by the native Ollama, LM Studio, llama.cpp, vLLM, or SGLang specs.

## Shape

Use explicit endpoint kind when known; otherwise use only the general model
list envelopes for inventory identity. Capability-looking structural fields
remain raw. Local host and port do not distinguish these engines.
MLX/Cookbook launch recipes, TGI task configuration, LMDeploy
adapters, and LiteLLM upstream routing can all change capability independently
of the model ID.

Proxy model aliases are endpoint scoped. A proxy may return richer fields, but
unknown keys remain raw until a versioned shape is added. Provider-specific
headers/extensions must not be applied based on a port or upstream model name.

## Fallback And Safety

Discovery can probe cheap native identity endpoints when available, but
capability probes execute only explicit bounded test contracts. Never read
broad server/environment dumps as ordinary model metadata. Unknown compatible
servers should still list identities and make conservative text calls where
explicitly configured, without appearing on capability-gated surfaces.

## Current Gaps

- These engines need individual safe metadata fixtures before they can graduate
  from general fallback.
- Gateway upstream identity and effective downstream model capability are not
  yet represented as a chain.
