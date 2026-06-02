# src/bedrock_adapter.py
"""AWS Bedrock provider adapter.

Bedrock is not an OpenAI-compatible HTTP endpoint, so it does not fit the
``base_url`` + bearer-key model the other providers share. Instead it is
represented with a sentinel endpoint URL ``bedrock://<region>`` and AWS
credentials packed into the existing ``api_key`` field as
``access_key:secret_key[:session_token]`` (blank ⇒ the default boto3
credential chain — env vars / instance role / ``AWS_PROFILE``).

Everything here goes through the Bedrock **Converse API**, which gives a single
request/response shape across model families (Anthropic Claude, Amazon Nova/
Titan, Meta Llama, Mistral, …) so we don't have to hand-shape per-family
payloads. Model discovery uses the control-plane ``list_foundation_models``.

``boto3`` is an optional dependency: it is imported lazily and, if missing, the
caller gets a clear "install boto3" message instead of a crash.
"""
import json
import asyncio
import logging
import threading
from typing import List, Dict, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_INSTALL_HINT = (
    "AWS Bedrock support needs the optional 'boto3' package. "
    "Install it with: pip install -r requirements-optional.txt (or pip install boto3)."
)


class BedrockUnavailable(RuntimeError):
    """Raised when boto3 isn't installed."""


def _boto3():
    """Lazily import boto3; raise a readable error if it isn't installed."""
    try:
        import boto3  # noqa: F401
        return boto3
    except ImportError as e:
        raise BedrockUnavailable(_INSTALL_HINT) from e


def is_bedrock_url(url: Optional[str]) -> bool:
    try:
        return (urlparse(url or "").scheme or "").lower() == "bedrock"
    except Exception:
        return False


def parse_region(url: str) -> str:
    """Extract the AWS region from a ``bedrock://<region>`` sentinel URL."""
    parsed = urlparse(url or "")
    # bedrock://us-east-1  → netloc 'us-east-1'; tolerate bedrock:///us-east-1.
    region = (parsed.hostname or parsed.netloc or parsed.path.strip("/") or "").strip()
    return region


def parse_creds(api_key: Optional[str]) -> Dict[str, str]:
    """Turn the packed ``api_key`` into explicit boto3 credential kwargs.

    Empty/None ⇒ ``{}`` so boto3 falls back to its default credential chain
    (env vars, shared config, instance/role credentials, AWS_PROFILE).
    """
    raw = (api_key or "").strip()
    if not raw:
        return {}
    parts = raw.split(":")
    creds = {
        "aws_access_key_id": parts[0].strip(),
        "aws_secret_access_key": parts[1].strip() if len(parts) > 1 else "",
    }
    if len(parts) > 2 and parts[2].strip():
        creds["aws_session_token"] = parts[2].strip()
    return creds


def creds_from_headers(headers: Optional[Dict]) -> Optional[str]:
    """Recover the packed api_key from an ``Authorization: Bearer …`` header
    (how every non-Anthropic provider's key is threaded through dispatch)."""
    auth = (headers or {}).get("Authorization") or (headers or {}).get("authorization") or ""
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):].strip() or None
    return auth.strip() or None


def _client(service: str, region: str, api_key: Optional[str]):
    boto3 = _boto3()
    if not region:
        raise ValueError("AWS region is required for Bedrock (e.g. us-east-1).")
    return boto3.client(service, region_name=region, **parse_creds(api_key))


def friendly_error(e: Exception) -> str:
    """Map common boto3/AWS errors to a readable, non-blanket message."""
    if isinstance(e, BedrockUnavailable):
        return str(e)
    name = type(e).__name__
    msg = str(e)
    low = msg.lower()
    if name in ("NoCredentialsError", "PartialCredentialsError") or "unable to locate credentials" in low:
        return ("AWS credentials not found. Provide an access key + secret in the key field "
                "(access:secret), or configure the default AWS credential chain (env/role/profile).")
    if "expiredtoken" in low or "the security token included in the request is expired" in low:
        return "AWS credentials expired — refresh your access keys / session token."
    if name == "AccessDeniedException" or "accessdenied" in low or "not authorized" in low:
        return ("Access denied by AWS. The IAM principal needs bedrock:ListFoundationModels and "
                "bedrock:InvokeModel/InvokeModelWithResponseStream, and the model must be enabled in this region.")
    if "could not connect to the endpoint" in low or name == "EndpointConnectionError":
        return "Could not reach AWS Bedrock — check the region is valid and reachable (e.g. us-east-1)."
    if name == "ValidationException" or "validationexception" in low:
        return f"Bedrock rejected the request: {msg}"
    if name == "ThrottlingException" or "throttl" in low or "too many requests" in low:
        return "AWS Bedrock throttled the request (rate limit). Retry shortly."
    if name == "ResourceNotFoundException" or "resourcenotfound" in low:
        return ("Model not found/enabled in this region. Some models require an inference profile "
                "(cross-region) modelId, or must be enabled in the AWS console first.")
    return f"AWS Bedrock error: {msg}"


# ── Model discovery ───────────────────────────────────────────────────────

def list_models(base_url: str, api_key: Optional[str] = None) -> List[str]:
    """Return on-demand, text-capable Bedrock modelIds for the region.

    modelIds are kept intact (they contain dots/colons, e.g.
    ``anthropic.claude-3-5-sonnet-20240620-v1:0``).
    """
    region = parse_region(base_url)
    client = _client("bedrock", region, api_key)
    resp = client.list_foundation_models()
    out: List[str] = []
    for m in resp.get("modelSummaries") or []:
        model_id = m.get("modelId")
        if not model_id:
            continue
        # Keep only text-OUT chat models that can be invoked on demand.
        out_modalities = m.get("outputModalities") or []
        if out_modalities and "TEXT" not in out_modalities:
            continue
        inf = m.get("inferenceTypesSupported") or []
        if inf and "ON_DEMAND" not in inf:
            # PROVISIONED / INFERENCE_PROFILE-only models can't be called with a
            # bare modelId; skip them so the picker only shows usable models.
            continue
        out.append(model_id)
    # Stable, de-duplicated order.
    seen = set()
    return [m for m in out if not (m in seen or seen.add(m))]


# ── Message mapping (internal OpenAI-ish format → Converse) ─────────────────

def _to_converse(messages: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """Map sanitized internal messages to Converse (system_blocks, messages).

    The caller (stream_llm / llm_call_async) has already consolidated system
    messages to a single leading entry and merged consecutive users. Converse
    requires alternating user/assistant turns with non-empty content, so we
    drop empties and coerce non-string content to text.
    """
    system_blocks: List[Dict] = []
    conv: List[Dict] = []
    for m in messages or []:
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, list):
            # Already block-ish; flatten any {"text": …}/str pieces.
            text = "".join(
                (c.get("text") if isinstance(c, dict) else str(c)) or "" for c in content
            )
        elif content is None:
            text = ""
        else:
            text = str(content)
        if role == "system":
            if text:
                system_blocks.append({"text": text})
            continue
        if role == "tool":
            # v1 has no native toolResult mapping; fold the result in as user text.
            role = "user"
        if role not in ("user", "assistant"):
            continue
        if not text:
            continue
        if conv and conv[-1]["role"] == role:
            # Merge same-role neighbours to preserve strict alternation.
            conv[-1]["content"].append({"text": text})
        else:
            conv.append({"role": role, "content": [{"text": text}]})
    # Converse requires the first turn to be 'user'.
    while conv and conv[0]["role"] != "user":
        conv.pop(0)
    return system_blocks, conv


def _inference_config(temperature: Optional[float], max_tokens: Optional[int]) -> Dict:
    cfg: Dict = {}
    if max_tokens and max_tokens > 0:
        cfg["maxTokens"] = int(max_tokens)
    if temperature is not None:
        cfg["temperature"] = float(temperature)
    return cfg


# ── Chat (non-streaming) ────────────────────────────────────────────────────

def chat(base_url: str, model: str, messages: List[Dict],
         temperature: Optional[float] = None, max_tokens: Optional[int] = None,
         api_key: Optional[str] = None) -> str:
    region = parse_region(base_url)
    client = _client("bedrock-runtime", region, api_key)
    system_blocks, conv = _to_converse(messages)
    kwargs = {"modelId": model, "messages": conv}
    if system_blocks:
        kwargs["system"] = system_blocks
    inf = _inference_config(temperature, max_tokens)
    if inf:
        kwargs["inferenceConfig"] = inf
    resp = client.converse(**kwargs)
    blocks = (((resp or {}).get("output") or {}).get("message") or {}).get("content") or []
    return "".join(b.get("text", "") for b in blocks if isinstance(b, dict))


# ── Chat (streaming) — bridge boto3's blocking EventStream to async SSE ──────

def _iter_sse(base_url: str, model: str, messages: List[Dict],
              temperature: Optional[float], max_tokens: Optional[int],
              api_key: Optional[str]):
    """Synchronous generator of SSE strings, matching stream_llm's shape:
    ``data: {"delta": …}`` / ``{"type":"usage", …}`` / ``[DONE]`` / error."""
    try:
        region = parse_region(base_url)
        client = _client("bedrock-runtime", region, api_key)
        system_blocks, conv = _to_converse(messages)
        kwargs = {"modelId": model, "messages": conv}
        if system_blocks:
            kwargs["system"] = system_blocks
        inf = _inference_config(temperature, max_tokens)
        if inf:
            kwargs["inferenceConfig"] = inf
        resp = client.converse_stream(**kwargs)
    except Exception as e:
        logger.warning(f"Bedrock converse_stream init failed: {type(e).__name__}: {e}")
        yield f'event: error\ndata: {json.dumps({"error": friendly_error(e), "status": 502})}\n\n'
        return
    try:
        for event in resp.get("stream", []):
            if "contentBlockDelta" in event:
                delta = event["contentBlockDelta"].get("delta") or {}
                text = delta.get("text")
                if text:
                    yield f'data: {json.dumps({"delta": text})}\n\n'
                reasoning = (delta.get("reasoningContent") or {}).get("text")
                if reasoning:
                    yield f'data: {json.dumps({"delta": reasoning, "thinking": True})}\n\n'
            elif "metadata" in event:
                usage = event["metadata"].get("usage") or {}
                if usage:
                    yield f'data: {json.dumps({"type": "usage", "data": {"input_tokens": usage.get("inputTokens", 0), "output_tokens": usage.get("outputTokens", 0)}})}\n\n'
        yield "data: [DONE]\n\n"
    except Exception as e:
        logger.warning(f"Bedrock stream error: {type(e).__name__}: {e}")
        yield f'event: error\ndata: {json.dumps({"error": friendly_error(e), "status": 502})}\n\n'


async def stream(base_url: str, model: str, messages: List[Dict],
                 temperature: Optional[float] = None, max_tokens: Optional[int] = None,
                 api_key: Optional[str] = None):
    """Async wrapper: runs the blocking boto3 EventStream on a worker thread and
    relays chunks through a queue so the event loop is never blocked."""
    queue: "asyncio.Queue[Optional[str]]" = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def _pump():
        try:
            for chunk in _iter_sse(base_url, model, messages, temperature, max_tokens, api_key):
                loop.call_soon_threadsafe(queue.put_nowait, chunk)
        except Exception as e:  # defensive — _iter_sse already maps its own errors
            loop.call_soon_threadsafe(
                queue.put_nowait,
                f'event: error\ndata: {json.dumps({"error": friendly_error(e), "status": 502})}\n\n',
            )
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=_pump, name="bedrock-stream", daemon=True).start()
    while True:
        chunk = await queue.get()
        if chunk is None:
            break
        yield chunk
