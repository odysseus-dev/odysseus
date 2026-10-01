# Settings clarity slice A — copy only

Agents: this slice is labels and helper text. Do not move Settings
panels, do not retarget Default Chat Model, do not delete ModelEndpoint
admin. Those are slices B–D.

## Intent

After overlay 9router landed on Add Models, leftover Odysseus surfaces
still read as if they were the cloud-connect path. Slice A names what
each leftover actually is.

## Surfaces

- Add Models local card: leftover Ollama/llama.cpp/vLLM, not overlay
  9router.
- Add Models 9router card: coarse overlay cloud control; ChatGPT /
  Copilot belong here, not Integrations.
- Added Models: pre-9router endpoint inventory. Empty API after a
  9router Connect is expected.
- AI Defaults: leftover Odysseus endpoint picks. Overlay interactive
  chat is OpenHands through 9router.
- Integrations Codex/Claude: inbound CLI plugins that call Odysseus,
  not Anthropic/OpenAI inference.

Nav labels stay Add Models / Added Models / AI Defaults / Integrations
(slice B owns IA).

## Tests

`tests/test_ninerouter_settings_static.py` asserts the leftover copy
and that Integrations picker strings include “calls Odysseus”.
