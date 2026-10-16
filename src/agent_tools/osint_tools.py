"""Visual GEOINT (OSINT) geolocation, routed through the MCP gateway pool.

Backed by the htp.sh OSINT endpoint, which runs a vision model over an
uploaded image and returns a coordinate guess with its reasoning and the
clues it used. Used for "where was this photo taken" style questions and for
checking whether an image in a chat matches a claimed location.

Backends
--------
Two, selected by ``ODYSSEUS_OSINT_BACKEND``:

``gateway`` (default)
    The behaviour described above. Round-robins the configured MCP gateway
    pool and fails over on error.
``bedrock``
    Calls the AWS Bedrock Converse API directly with boto3, bypassing the
    gateway. Off unless the variable is set to ``bedrock``. Credentials are
    resolved by boto3's provider chain, so static keys, short-lived
    ``AWS_SESSION_TOKEN`` credentials, and IRSA/instance-profile roles all
    work without special handling here.

A Bedrock failure is returned as an error rather than silently falling back to
the gateway: a fallback would report success while quietly doubling cost and
latency, and would hide a misconfigured backend.

Transport
---------
Gateway calls go through :func:`src.mcp_gateway.get_pool`, so every request is
round-robined across the configured gateway pool and fails over on error. That
keeps this tool's failure behaviour identical to every other gateway consumer
rather than hardcoding one upstream.

The upstream contract is ``POST <base>/osint/api/analyze`` taking either
``multipart/form-data`` with an ``image`` field or a raw ``image/*`` body, and
returning JSON with at least ``guess.lat``, ``guess.lon``, ``guess.confidence``
and ``reasoning``.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Hard ceilings. The upstream model reads the whole image, so a large upload
# costs real money and wall-clock; refuse rather than silently truncating.
MAX_IMAGE_BYTES = int(os.getenv("ODYSSEUS_OSINT_MAX_BYTES", str(8 * 1024 * 1024)))
ANALYZE_PATH = os.getenv("ODYSSEUS_OSINT_PATH", "/osint/api/analyze")
# Measured live: a single small image took ~100s against the 27B vision model
# on a cold Workers AI start. The default has to clear that comfortably or
# every cold call fails as a timeout rather than succeeding slowly.
REQUEST_TIMEOUT = float(os.getenv("ODYSSEUS_OSINT_TIMEOUT", "240"))

_MAGIC = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)

# ---------------------------------------------------------------------------
# Optional AWS Bedrock backend.
#
# Off unless ODYSSEUS_OSINT_BACKEND=bedrock, so an existing deployment is
# unaffected. Credentials are never read from or written to .env by this
# module; they are resolved by boto3's own provider chain at call time, which
# covers static keys, AWS_SESSION_TOKEN short-lived STS credentials, and
# IRSA/instance-profile roles.
#
# Deliberately not routed through the provider layer in src/llm_core.py:
# specs/model-providers/bedrock.md records native Bedrock as unimplemented and
# warns that Bedrock is not an OpenAI-compatible host, so pointing that layer at
# a bedrock-runtime endpoint would be the wrong shape. This calls the Converse
# API directly instead — it is multimodal and family-agnostic, so there is no
# per-family payload mapping to get wrong.
# ---------------------------------------------------------------------------
BACKEND_ENV = "ODYSSEUS_OSINT_BACKEND"
BEDROCK_MODEL_ENV = "ODYSSEUS_BEDROCK_MODEL"
DEFAULT_BEDROCK_MODEL = "us.anthropic.claude-sonnet-4-20250514-v1:0"
DEFAULT_AWS_REGION = "us-east-1"

# Converse accepts this subset. sniff_image_type also returns bmp and tiff,
# which Bedrock has no image format for, so those are rejected rather than
# silently mislabelled.
BEDROCK_IMAGE_FORMATS = {
    "image/jpeg": "jpeg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}

# Asked for strict JSON because _normalize_bedrock parses the reply rather than
# trusting prose. Coordinates and a confidence figure are required; a model
# that cannot place the image should say so with low confidence instead of
# omitting the fields.
BEDROCK_PROMPT = (
    "You geolocate images. Reply with JSON only, no prose and no code fence, "
    "in exactly this shape:\n"
    '{"lat": <number|null>, "lon": <number|null>, "confidence": <0..1>, '
    '"radiusKm": <number|null>, "placeName": <string|null>, '
    '"country": <string|null>, "clues": [<string>, ...]}\n'
    "Use null for any field you cannot support from the image. Base the answer "
    "only on visual evidence in the image."
)


def bedrock_selected() -> bool:
    """True when the caller explicitly asked for the Bedrock backend."""
    return os.getenv(BACKEND_ENV, "gateway").strip().lower() == "bedrock"


def bedrock_region() -> str:
    return (
        os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or DEFAULT_AWS_REGION
    ).strip()


def bedrock_model() -> str:
    return (os.getenv(BEDROCK_MODEL_ENV) or DEFAULT_BEDROCK_MODEL).strip()


def _strip_code_fence(text: str) -> str:
    """Pull the JSON body out of a reply a model fenced in markdown.

    Vision models wrap JSON in ```json fences often enough that assuming a bare
    payload would fail on correct answers.
    """
    cleaned = text.strip()
    if not cleaned.startswith("```"):
        return cleaned
    # Drop the opening fence and any language tag, then the closing fence.
    _, _, rest = cleaned.partition("\n")
    body = rest.rsplit("```", 1)[0]
    return body.strip()


def sniff_image_type(data: bytes) -> Optional[str]:
    """Best-effort content type from magic bytes.

    Falls back to webp (RIFF....WEBP) and tiff, which do not have a fixed
    prefix. Returns None when nothing matches, so the caller can reject
    rather than send a mislabelled body.
    """
    for prefix, mime in _MAGIC:
        if data.startswith(prefix):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    return None


def decode_image_argument(value: str) -> bytes:
    """Decode a base64 image, or a ``data:`` URI, into raw bytes.

    Accepts a raw data URI so the model can pass through what the browser gave
    it without having to strip the prefix itself.
    """
    text = value.strip()
    if text.startswith("data:"):
        _, _, rest = text.partition(",")
        if not rest:
            raise ValueError("data URI has no payload after the comma")
        text = rest
    # Tolerate whitespace from wrapped payloads. Strip it before decoding so
    # validate=True can still reject genuinely malformed input.
    text = "".join(text.split())
    try:
        # validate=True is load-bearing: with validate=False, b64decode
        # silently discards non-alphabet characters, so "!!!" decodes to b""
        # and a malformed upload looks like an empty one.
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"image is not valid base64: {exc}") from exc


class OsintLocateTool:
    """Geolocate an image with a vision model and return its reasoning."""

    async def execute(self, content: str, ctx: dict) -> dict:
        try:
            args: Any = json.loads(content) if content.strip().startswith("{") else {"image_base64": content}
            if not isinstance(args, dict):
                return {"error": "osint_locate expects base64 image data or a JSON object", "exit_code": 1}

            raw = args.get("image_base64") or args.get("image") or args.get("data")
            if not isinstance(raw, str) or not raw.strip():
                return {"error": "osint_locate requires image_base64", "exit_code": 1}

            try:
                data = decode_image_argument(raw)
            except ValueError as exc:
                return {"error": str(exc), "exit_code": 1}

            if not data:
                return {"error": "osint_locate received an empty image", "exit_code": 1}
            if len(data) > MAX_IMAGE_BYTES:
                return {
                    "error": (
                        f"image is {len(data)} bytes, over the "
                        f"{MAX_IMAGE_BYTES}-byte OSINT limit; downscale first"
                    ),
                    "exit_code": 1,
                }

            mime = sniff_image_type(data)
            if mime is None:
                return {
                    "error": "unsupported image format; expected JPEG, PNG, GIF, BMP, WebP or TIFF",
                    "exit_code": 1,
                }

            # Backend selection is explicit, not automatic: the gateway stays
            # the default so an existing deployment changes nothing.
            if bedrock_selected():
                result = await self._analyze_bedrock(data, mime)
            else:
                result = await self._analyze(data, mime)
            if isinstance(result, dict) and "error" in result:
                return result

            payload = self._shape(result)
            return {
                "output": json.dumps(payload, ensure_ascii=False),
                "exit_code": 0,
                "evidence_status": "available",
            }
        except Exception as exc:  # tool boundary: never raise into the loop
            logger.warning("OSINT locate failed: %s", exc)
            return {"error": f"OSINT geolocation failed: {exc}", "exit_code": 1}

    async def _analyze(self, data: bytes, mime: str) -> Any:
        """POST the image to the gateway pool. Raw image body, not multipart.

        The upstream accepts either; raw keeps the pool path a simple JSON/text
        call and avoids re-encoding bytes into a multipart envelope.
        """
        import httpx

        from src.mcp_gateway import GatewayError, get_pool

        pool = get_pool()
        # Acquire once: a second acquire() advances the round-robin cursor and
        # could resolve the path against one endpoint while posting to another.
        endpoint = pool.acquire()
        path = pool.resolve_path(endpoint.base_url, "osint_analyze") or ANALYZE_PATH
        url = endpoint.base_url.rstrip("/") + path
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=False) as client:
                resp = await client.post(
                    url,
                    content=data,
                    headers={
                        "Content-Type": mime,
                        "X-Odysseus-Tool": "osint_locate",
                    },
                )
        except GatewayError as exc:
            return {"error": f"OSINT gateway unavailable: {exc}", "exit_code": 1}
        except Exception as exc:
            return {"error": f"OSINT request failed: {exc}", "exit_code": 1}

        if resp.status_code >= 400:
            return {
                "error": f"OSINT endpoint returned HTTP {resp.status_code}: {resp.text[:200]}",
                "exit_code": 1,
            }
        try:
            return resp.json()
        except ValueError:
            return {"error": f"OSINT endpoint returned non-JSON: {resp.text[:200]}", "exit_code": 1}

    async def _analyze_bedrock(self, data: bytes, mime: str) -> Any:
        """Call the Bedrock Converse API directly with the image.

        Runs the blocking boto3 call in a worker thread: the SDK is synchronous
        and there is no async Bedrock client in boto3, so calling it inline
        would stall the event loop for the whole model latency.

        Deliberately does NOT fall back to the gateway on failure. A silent
        fallback would hide a misconfigured backend and quietly double the cost
        and latency of every call while reporting success, so the error is
        returned and surfaced.
        """
        image_format = BEDROCK_IMAGE_FORMATS.get(mime)
        if image_format is None:
            return {
                "error": (
                    f"Bedrock cannot read {mime}; convert the image to JPEG or "
                    "PNG before geolocating it"
                ),
                "exit_code": 1,
            }

        try:
            import boto3
        except ImportError as exc:
            return {
                "error": (
                    "the Bedrock backend needs boto3, which is not installed; "
                    "run `pip install boto3` or set "
                    f"{BACKEND_ENV}=gateway to use the gateway instead"
                ),
                "exit_code": 1,
            }

        region = bedrock_region()
        model = bedrock_model()

        def call() -> str:
            client = boto3.client("bedrock-runtime", region_name=region)
            response = client.converse_model(
                modelId=model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"text": BEDROCK_PROMPT},
                            {"image": {"format": image_format, "source": {"bytes": data}}},
                        ],
                    }
                ],
                inferenceConfig={"maxTokens": 1024, "temperature": 0.2},
            )
            # Converse returns a content block list; the text block is the reply.
            blocks = response["output"]["message"]["content"]
            return next(b["text"] for b in blocks if "text" in b)

        try:
            text = await asyncio.to_thread(call)
        except Exception as exc:  # boto3 raises a wide family of ClientError
            return {"error": f"Bedrock request failed: {exc}", "exit_code": 1}

        return self._normalize_bedrock(text, model)

    @staticmethod
    def _normalize_bedrock(text: str, model: str) -> Dict[str, Any]:
        """Turn a Converse reply into the same envelope the gateway returns.

        Normalising here means _shape() and everything downstream stay
        backend-agnostic.
        """
        try:
            parsed = json.loads(_strip_code_fence(text))
        except ValueError:
            return {
                "error": f"Bedrock model returned non-JSON: {text[:200]}",
                "exit_code": 1,
            }
        if not isinstance(parsed, dict):
            return {
                "error": f"Bedrock model returned a non-object: {text[:200]}",
                "exit_code": 1,
        }
        # The gateway nests coordinates under `guess`; mirror that so _shape()
        # is unchanged. Clues arrive as a list from this prompt but upstream may
        # return them as a string, which _shape would slice character-wise.
        clues = parsed.get("clues")
        if isinstance(clues, str):
            clues = [clues]
        return {
            "guess": {
                "lat": parsed.get("lat"),
                "lon": parsed.get("lon"),
                "confidence": parsed.get("confidence"),
                "radiusKm": parsed.get("radiusKm"),
                "placeName": parsed.get("placeName"),
                "country": parsed.get("country"),
                "clues": clues if isinstance(clues, list) else [],
            },
            "reasoning": parsed.get("reasoning"),
            "engine": f"bedrock:{model}",
        }

    @staticmethod
    def _shape(result: Dict[str, Any]) -> Dict[str, Any]:
        """Flatten the upstream envelope into the fields a model actually uses.

        The upstream nests coordinates under ``guess`` and wraps free text in
        ``reasoning``; keep the clues too, because they are the auditable
        evidence for the coordinate rather than a bare number the model has to
        take on faith.
        """
        guess = result.get("guess") or {}
        return {
            "lat": guess.get("lat"),
            "lon": guess.get("lon"),
            "confidence": guess.get("confidence"),
            "radius_km": guess.get("radiusKm", guess.get("radius_km")),
            "place": guess.get("placeName", guess.get("place_name")),
            "country": guess.get("country"),
            "clues": (guess.get("clues") or [])[:8],
            "reasoning": result.get("reasoning"),
            "engine": result.get("engine"),
        }