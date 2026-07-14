"""Parse generate_image tool arguments (JSON, key-value block, or bare prompt)."""

from __future__ import annotations

import json
import re

from titan.image_params import normalize_image_args

_IMAGE_KEYS = {
    "prompt", "style", "aspect", "size", "quality",
    "negative_prompt", "negative", "confirm", "model",
    "n", "cfg_scale", "steps", "sampler", "scheduler", "seed",
}

_CONFIRM_TRUTHY = frozenset({
    "yes", "true", "1", "y", "on", "confirm", "confirmed",
    "approve", "approved", "ok", "go", "generate", "proceed",
})
_CONFIRM_FALSY = frozenset({"no", "false", "0", "n", "off"})


def coerce_confirm(value) -> bool | None:
    """Map confirm tool arg to a JSON boolean for the MCP schema."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return None
    if text in _CONFIRM_TRUTHY:
        return True
    if text in _CONFIRM_FALSY:
        return False
    return None


def parse_generate_image(content: str) -> dict:
    text = (content or "").strip()

    if text.startswith("{"):
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                args: dict = {}
                for k, v in data.items():
                    key = "negative_prompt" if k == "negative" else str(k).lower()
                    if key not in _IMAGE_KEYS:
                        continue
                    if key == "confirm":
                        coerced = coerce_confirm(v)
                        if coerced is not None:
                            args[key] = coerced
                    elif v is not None and str(v).strip() != "":
                        args[key] = v if isinstance(v, str) else str(v).strip()
                if args:
                    return normalize_image_args(args)
        except (json.JSONDecodeError, TypeError):
            pass

    lines = text.split("\n")
    args: dict = {}
    has_kv = False
    cur_key = None
    for line in lines:
        m = re.match(r"^\s*([A-Za-z_]+)\s*:\s*(.*)$", line)
        if m and m.group(1).lower() in _IMAGE_KEYS:
            has_kv = True
            key = m.group(1).lower()
            if key == "negative":
                key = "negative_prompt"
            raw = m.group(2).strip()
            if key == "confirm":
                coerced = coerce_confirm(raw)
                if coerced is not None:
                    args[key] = coerced
            else:
                args[key] = raw
            cur_key = key
        elif cur_key == "prompt" and line.strip():
            args["prompt"] = (args.get("prompt", "") + "\n" + line).strip()
    if not has_kv:
        args = {"prompt": lines[0].strip() if lines else ""}
    return normalize_image_args({k: v for k, v in args.items() if v != ""})
