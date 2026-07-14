"""Titan constants shared across Odysseus modules."""

from __future__ import annotations

from typing import FrozenSet

# Legacy compatibility: previously these tools were hard-disabled at runtime.
# They are now governed by tool security policy (admin-only where applicable).
DEPRECATED_SERVE_TOOLS: FrozenSet[str] = frozenset()

DEPRECATION_MSG = (
    "Cookbook model serving is deprecated on Titan. Use Titan Model Hub "
    "(sidebar icon or `ui_control open_panel model_hub`) for models/downloads. "
    "GPU orchestration and VRAM status: VRAM Scheduler panel "
    "(`ui_control open_panel scheduler`). Image generation uses `generate_image`."
)

HUB_DOMAIN_RULES = """\
## Titan Model Hub rules (replaces Cookbook serve)
- Model load/unload/VRAM is managed by **Titan VRAM Scheduler** + Model Hub (host systemd + scheduler), not tmux Cookbook serve.
- "Open models" / "model hub" / "cookbook" panel → `ui_control open_panel model_hub`.
- "VRAM" / "GPU status" / scheduler / external jobs → `ui_control open_panel scheduler` (VRAM Scheduler panel).
- "What's running" → Scheduler panel Přehled tab or `app_api` GET `/api/titan/scheduler/status`.
- **Image generation** → ALWAYS `generate_image` (scheduler handles SD). NEVER `serve_model` for diffusion.
- **Load/start LLM or SD** → Model Hub (LLM/SD tabs) or VRAM Scheduler (Služby tab); agents cannot launch via deprecated serve tools.
- **Downloads** → prefer Hub download UI; `download_model` / `list_downloads` / `search_hf_models` / `list_cached_models` still work.
- `serve_model`, `serve_preset`, `list_served_models`, `stop_served_model`, `adopt_served_model`, `list_serve_presets`, `tail_serve_output`, `list_cookbook_servers` are **disabled**."""

GENERATE_IMAGE_RAG_DESC = (
    "Generate, create, draw, render or make ONE image, picture, illustration, "
    "logo, artwork, poster or photo from a text prompt using the local Stable "
    "Diffusion stack (realistic, anime, pixelart, or krea when registered). Smart: asks the user for style "
    "and confirmation when not provided."
)

GENERATE_IMAGE_TOOL_SECTION = """\
```generate_image
prompt: <what to depict>
style: realistic | anime | pixelart | krea
  realistic → ThisIsReal SDXL v3.0 (NOT RealVisXL — legacy name)
  anime → Nova Anime XL IL v19
  pixelart → Pixel Storm XL v1.0
  krea → Dark Beast KREA 2 (KREA2 — natural-language prose prompts, CFG≈1, ~12 steps)
aspect: square | portrait | landscape
quality: high
```
Generate via local Stable Diffusion. Use **generate_image** — NEVER ui_control.

When prompt AND style are set, generation runs **immediately** in one tool call.
Only omit `style` when the user has not chosen yet — the tool asks (realistic vs anime vs pixelart vs krea).
Use `confirm=true` only if the tool returned a confirm summary and the user approved.

Regenerate: op=regenerate, source_image_id=gallery_id, seed when same seed needed.
Same seed / batch count: you interpret the user's language. Use SESSION_IMAGE_CONTEXT (last_seed, last_gallery_id) for facts — pass seed=last_seed when they want same seed; set n=1..4 for multiple images; omit seed for random. Use ask_user when unclear. Never invent seed numbers."""
