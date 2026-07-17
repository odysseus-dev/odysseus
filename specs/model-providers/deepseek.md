# DeepSeek Provider Shape

Last updated: dev@28d27ee | 2026-07-17

## Scope

Canonical provider ID `deepseek`; official cloud OpenAI-compatible API;
curation/detection in `routes/model_routes.py` and runtime reasoning handling in
`src/llm_core.py`.

## Shape And Observations

Use the general model-list identity shape unless the provider returns explicit
modalities/capabilities. Cloud response history can use
`reasoning_content`; preserve it structurally for reasoning turns and tool
continuation (#968, #3152). `deepseek-chat`, reasoning models, distilled local
variants, and future V4 models do not share one capability record.

Cloud endpoint evidence can support tools while a local DeepSeek-R1 deployment
may not have a working tool parser. Existing tool-support tests intentionally
separate official host from local engine/model-name heuristics.

## Fallback And Current Gaps

Exact `*.deepseek.com` selects provider identity; self-hosted checkpoints use
Ollama/vLLM/SGLang/llama.cpp identity. Curated model IDs and pricing/context
tables are compatibility data, not authoritative capability. A rich official
model-card reader is still absent.
