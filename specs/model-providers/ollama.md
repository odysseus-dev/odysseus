# Ollama Provider Shape

Last updated: dev@28d27ee | 2026-07-17

## Scope

Canonical provider ID `ollama`; native Ollama chat/generate plus OpenAI
compatibility; reader `src/model_capability_readers/ollama.py`; discovery and
runtime code in `routes/model_routes.py` and `src/llm_core.py`.

## Catalog And Detail Shapes

Use two native steps:

1. `GET /api/tags` returns `models[]` identity (`name`/`model`, digest,
   `details.family|families`, format, parameter size, quantization). Tags do not
   claim capabilities.
2. `POST /api/show` for a selected model returns explicit `capabilities[]`,
   `details`, and `model_info`. Map completion/chat, embedding, vision, tools,
   and thinking/reasoning tokens. Map context from exact `context_length` or
   native `<architecture>.context_length` fields.

The `parameters` field is serialized Modelfile text and is not reparsed for
capability or context truth. Native structured metadata wins. This prevents
late text parsing and allows new architectures to use the documented
`*.context_length` key shape without model-name tables.

## Request And Response Shape

Native chat uses `/api/chat`, `messages`, optional OpenAI-shaped tool
definitions, `format`, `options`, and model-dependent `think`. Responses use
`message.content`, `message.thinking`, and `message.tool_calls`. Generate uses
top-level `response` and `thinking`. OpenAI compatibility is a separate dialect
and can change control names independently.

Thinking control is model-specific: most documented reasoning families accept
a native bool, while GPT-OSS accepts low/medium/high and cannot be fully
disabled. A reported Ollama 0.20.6 Qwen3.5 OpenAI-compat path requires
`reasoning_effort: none` rather than `think: false` (#5503); keep it versioned
and low-confidence until corroborated.

## Fallback And Safety

Port 11434 alone does not identify Ollama. Use explicit kind, Ollama Cloud host,
or native shape. Names that contain `vision`, `embed`, or `qwen` are not
capability evidence (#3743, #4487).

## Current Gaps

- List discovery needs an orchestrated `/api/show` detail step per model.
- Runtime OpenAI-compat thinking suppression still contains name heuristics.
