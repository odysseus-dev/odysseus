# SGLang Provider Shape

Last updated: dev@e57f60b | 2026-07-20

## Scope

Canonical provider ID `sglang`; OpenAI Chat/Responses plus native generation;
Cookbook launch behavior in `routes/cookbook_routes.py` and serving UI modules.
There is no dedicated SGLang canonical reader on current `dev`.

## Metadata Shapes

Preferred native `GET /model_info` (legacy `/get_model_info`) returns:

- `model_path` and `tokenizer_path`;
- `is_generation`;
- `has_image_understanding` and `has_audio_understanding`;
- `model_type`, `architectures`, `weight_version`;
- `preferred_sampling_params`.

These are provider observations for a future dedicated reader. Current generic
normalization does not map `is_generation`, modality booleans, sampling keys,
or `max_model_len`.

`GET /v1/models` returns served IDs with `owned_by: sglang`, `root`, and
`max_model_len`; it supplies identity/context but not parser capability.

## Runtime Capability

Tools and reasoning depend on explicit `--tool-call-parser` and
`--reasoning-parser`; multimodality and context can also be launch-configured.
Cookbook recipes for Qwen, DeepSeek, GLM, Kimi, MiniMax, StepFun, and other
families are deployment observations, not universal model-name rules. Persist
the selected parser/config as endpoint evidence before canonical promotion.

## Fallback And Safety

Current reader detection identifies port 30000 as SGLang, or accepts an
explicit endpoint kind, then dispatches to the generic identity-only reader.
It does not infer SGLang from `/model_info` payload shape. Avoid normal
discovery through the broad admin `/server_info` dump.

## Current Gaps

- Endpoint records do not yet store parser/task configuration canonically.
- Non-generation task classification needs explicit serving metadata.
- No dedicated reader maps SGLang metadata today.
