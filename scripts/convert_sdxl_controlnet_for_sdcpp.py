#!/usr/bin/env python3
"""Convert diffusers SDXL ControlNet safetensors → sd.cpp (A1111) key layout."""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

from safetensors.torch import load_file, save_file

# Reuse HF's SDXL UNet diffusers→original mapping (controlnet trunk matches UNet).
import convert_diffusers_to_original_sdxl as _sdxl_conv  # type: ignore


def _convert_unet_trunk(trunk: dict) -> dict:
    """Like convert_unet_state_dict but skips tensors controlnet doesn't have."""
    mapping = {k: k for k in trunk.keys()}
    for sd_name, hf_name in _sdxl_conv.unet_conversion_map:
        if hf_name in trunk:
            mapping[hf_name] = sd_name
    for k, v in list(mapping.items()):
        if "resnets" in k:
            for sd_part, hf_part in _sdxl_conv.unet_conversion_map_resnet:
                v = v.replace(hf_part, sd_part)
            mapping[k] = v
    for k, v in list(mapping.items()):
        for sd_part, hf_part in _sdxl_conv.unet_conversion_map_layer:
            v = v.replace(hf_part, sd_part)
        mapping[k] = v
    return {sd_name: trunk[hf_name] for hf_name, sd_name in mapping.items() if hf_name in trunk}


def _convert_cond_embedding(state: dict) -> dict:
    out: dict = {}
    for key, tensor in state.items():
        if not key.startswith("controlnet_cond_embedding."):
            continue
        rest = key[len("controlnet_cond_embedding.") :]
        if rest.startswith("conv_in."):
            out["input_hint_block.0." + rest.split(".", 1)[1]] = tensor
        elif rest.startswith("conv_out."):
            out["input_hint_block.14." + rest.split(".", 1)[1]] = tensor
        elif rest.startswith("blocks."):
            m = re.match(r"blocks\.(\d+)\.(weight|bias)$", rest)
            if not m:
                continue
            idx = int(m.group(1))
            out[f"input_hint_block.{2 * (idx + 1)}.{m.group(2)}"] = tensor
    return out


def _convert_zero_convs(state: dict) -> dict:
    out: dict = {}
    for key, tensor in state.items():
        m = re.match(r"controlnet_down_blocks\.(\d+)\.(weight|bias)$", key)
        if m:
            out[f"zero_convs.{m.group(1)}.0.{m.group(2)}"] = tensor
    mid_w = state.get("controlnet_mid_block.weight")
    mid_b = state.get("controlnet_mid_block.bias")
    if mid_w is not None:
        out["middle_block_out.0.weight"] = mid_w
    if mid_b is not None:
        out["middle_block_out.0.bias"] = mid_b
    return out


def convert_controlnet_state_dict(state: dict) -> dict:
    skip_prefixes = (
        "controlnet_down_blocks.",
        "controlnet_mid_block.",
        "controlnet_cond_embedding.",
    )
    trunk = {k: v for k, v in state.items() if not k.startswith(skip_prefixes)}
    converted = _convert_unet_trunk(trunk)
    converted.update(_convert_zero_convs(state))
    converted.update(_convert_cond_embedding(state))
    return converted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="diffusers controlnet .safetensors")
    parser.add_argument("output", type=Path, help="sd.cpp-compatible output path")
    parser.add_argument("--backup", action="store_true", help="keep .diffusers.bak copy of input")
    args = parser.parse_args()

    state = load_file(str(args.input))
    out_state = convert_controlnet_state_dict(state)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.backup and args.input.resolve() != args.output.resolve():
        bak = args.input.with_suffix(args.input.suffix + ".diffusers.bak")
        if not bak.exists():
            shutil.copy2(args.input, bak)
    save_file(out_state, str(args.output))
    print(f"Wrote {len(out_state)} tensors → {args.output}")


if __name__ == "__main__":
    main()
