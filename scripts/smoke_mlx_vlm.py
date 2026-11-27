#!/usr/bin/env python3
"""Prove that a running mlx-vlm server actually answers about an image.

Why this exists: `pipeline_tag: image-text-to-text` says the model DECLARES
image input. It does not promise that this checkpoint loads under mlx-vlm, nor
that the serve you just started can take a picture. A server that came up
healthy proves neither — that was the original bug, in a different disguise.

So "verified" is only ever recorded by a real round trip. This sends an image
whose content cannot be guessed (a coloured shape plus a digit, both chosen at
random per run) and requires both facts back. On success the checkpoint is
added to data/mlx_vlm_verified.json; on failure nothing is recorded.

It deliberately does NOT launch anything. Start the server the normal way —
through Cookbook, so the command under test is the one the app really builds —
then point this at it:

    python3 scripts/smoke_mlx_vlm.py \\
        --url http://127.0.0.1:8090 \\
        --model mlx-community/Qwen2.5-VL-3B-Instruct-4bit

Any Python with Pillow can run it; it talks to the server over HTTP and needs
neither mlx nor mlx-vlm itself.
"""

import argparse
import base64
import io
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.constants import DATA_DIR  # noqa: E402

VERIFIED_FILE = os.path.join(DATA_DIR, "mlx_vlm_verified.json")

_SHAPES = ("triangle", "square", "circle")
_COLOURS = {
    "purple": (128, 0, 200),
    "green": (0, 150, 60),
    "orange": (235, 120, 0),
    "blue": (20, 80, 220),
}


def _probe_image(shape, colour_name, digit):
    """A shape of a known colour with a digit on it, drawn fresh each run."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (384, 384), "white")
    d = ImageDraw.Draw(img)
    fill = _COLOURS[colour_name]
    if shape == "triangle":
        d.polygon([(192, 60), (330, 300), (54, 300)], fill=fill)
    elif shape == "square":
        d.rectangle([(70, 70), (314, 314)], fill=fill)
    else:
        d.ellipse([(60, 60), (324, 324)], fill=fill)
    font = None
    for path in ("/System/Library/Fonts/Helvetica.ttc",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            font = ImageFont.truetype(path, 150)
            break
        except Exception:
            continue
    d.text((150, 150), str(digit), fill="white", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _ask(url, model, png, prompt, timeout):
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {
                "url": "data:image/png;base64," + base64.b64encode(png).decode()}},
            {"type": "text", "text": prompt},
        ]}],
        "max_tokens": 80,
    }
    req = urllib.request.Request(
        url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.load(resp)
    msg = body["choices"][0]["message"]["content"]
    return msg, body.get("usage") or {}


def _record(model, detail):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            with open(VERIFIED_FILE, "r", encoding="utf-8") as fh:
                state = json.load(fh)
        except Exception:
            state = {}
        if not isinstance(state, dict):
            state = {}
        state[model] = detail
        tmp = VERIFIED_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
        os.replace(tmp, VERIFIED_FILE)
        return True
    except Exception as e:
        print(f"WARNING: could not record the result in {VERIFIED_FILE}: {e}")
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8090",
                    help="base URL of the running mlx-vlm server (default: %(default)s)")
    ap.add_argument("--model", required=True, help="model id, as /v1/models reports it")
    ap.add_argument("--timeout", type=float, default=300.0,
                    help="seconds to wait for the reply (default: %(default)s)")
    ap.add_argument("--dry-run", action="store_true",
                    help="run the check but record nothing")
    args = ap.parse_args()

    shape = random.choice(_SHAPES)
    colour = random.choice(list(_COLOURS))
    digit = random.randint(1, 9)
    png = _probe_image(shape, colour, digit)
    print(f"[probe] {colour} {shape}, digit {digit} ({len(png)} bytes)")

    prompt = ("Answer in two short lines. Line 1: what geometric shape is in this "
              "image and what colour is it? Line 2: what single digit is written on it?")
    started = time.time()
    try:
        answer, usage = _ask(args.url, args.model, png, prompt, args.timeout)
    except urllib.error.URLError as e:
        print(f"FAIL: cannot reach {args.url}: {e}")
        print("Is the server running? Start it from Cookbook with the MLX VLM backend.")
        return 2
    except Exception as e:
        print(f"FAIL: request failed: {e}")
        return 2
    elapsed = time.time() - started

    print(f"[reply in {elapsed:.1f}s] {answer.strip()}")
    low = answer.lower()
    missing = []
    if shape not in low:
        missing.append(f"shape {shape!r}")
    if colour not in low:
        missing.append(f"colour {colour!r}")
    if str(digit) not in low:
        missing.append(f"digit {digit!r}")

    prompt_tokens = usage.get("prompt_tokens")
    # A text-only prompt of this length is well under a hundred tokens; an
    # image pushes it far higher. A low count means the picture never reached
    # the model even if the words happened to match.
    if isinstance(prompt_tokens, int) and prompt_tokens < 100:
        missing.append(f"image not tokenized (prompt_tokens={prompt_tokens})")

    if missing:
        print("FAIL: the reply does not account for " + ", ".join(missing))
        print("This checkpoint is NOT verified for image input on this host.")
        return 1

    print(f"PASS: shape, colour and digit all read correctly "
          f"(prompt_tokens={prompt_tokens}).")
    if args.dry_run:
        print("--dry-run: nothing recorded.")
        return 0
    detail = {
        "verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "url": args.url,
        "probe": {"shape": shape, "colour": colour, "digit": digit},
        "prompt_tokens": prompt_tokens,
        "seconds": round(elapsed, 1),
    }
    if _record(args.model, detail):
        print(f"Recorded as verified in {VERIFIED_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
