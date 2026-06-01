# src/bedrock_client.py
"""Native AWS Bedrock support via boto3 (Converse API).

Bedrock doesn't speak either the OpenAI or the Anthropic HTTP shape that the
rest of Odysseus uses, so this module is the single translation layer between
Odysseus's OpenAI-style messages and Bedrock's Converse API. boto3 handles the
SigV4 request signing, so the caller only supplies IAM credentials + a region.

No DB migration is needed: credentials ride in the existing
``ModelEndpoint.api_key`` field (encrypted at rest) as a small JSON blob:

    {"access_key_id": "...", "secret_access_key": "...", "session_token": "..."}

and the region is taken from the endpoint host, e.g.
``bedrock-runtime.us-east-1.amazonaws.com``. If no credentials are stored,
boto3 falls back to the ambient AWS credential chain (env vars, shared config,
or an instance/SSO role) — handy for IAM-role deployments.
"""
import base64
import json
import logging
import re
from typing import Dict, Iterator, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Private header used to smuggle the (decrypted) AWS credential blob from
# endpoint_resolver.build_headers() through to llm_core without sending it
# over the wire. llm_core pops it before dispatching.
BEDROCK_CREDS_HEADER = "x-odysseus-bedrock-creds"

DEFAULT_REGION = "us-east-1"
DEFAULT_MAX_TOKENS = 4096


def is_bedrock_url(url: str) -> bool:
    """True if a base URL points at an AWS Bedrock endpoint."""
    u = (url or "").lower()
    return "bedrock" in u and "amazonaws.com" in u


def region_from_url(url: str, default: str = DEFAULT_REGION) -> str:
    """Extract the AWS region from a bedrock(-runtime).<region>.amazonaws.com host."""
    host = (urlparse(url).hostname or "") if url else ""
    m = re.search(r"bedrock(?:-runtime)?\.([a-z0-9-]+)\.amazonaws\.com", host)
    return m.group(1) if m else default


def parse_creds(blob) -> Dict[str, str]:
    """Normalize a stored credential blob into a dict.

    Accepts a dict, a JSON string, or a compact ``access:secret[:token]``
    string. Returns keys: access_key_id, secret_access_key, session_token,
    region (any may be empty).
    """
    d: Dict = {}
    if isinstance(blob, dict):
        d = blob
    elif blob:
        s = str(blob).strip()
        if s.startswith("{"):
            try:
                d = json.loads(s)
            except Exception:
                d = {}
        if not d and ":" in s and not s.startswith("http"):
            parts = s.split(":")
            d = {"access_key_id": parts[0], "secret_access_key": parts[1] if len(parts) > 1 else ""}
            if len(parts) > 2:
                d["session_token"] = parts[2]
    return {
        "access_key_id": str(d.get("access_key_id") or d.get("aws_access_key_id") or "").strip(),
        "secret_access_key": str(d.get("secret_access_key") or d.get("aws_secret_access_key") or "").strip(),
        "session_token": str(d.get("session_token") or d.get("aws_session_token") or "").strip(),
        "region": str(d.get("region") or "").strip(),
    }


def _resolve_region(url: str, creds: Dict[str, str]) -> str:
    return (creds.get("region") or "").strip() or region_from_url(url)


def _client(service: str, region: str, creds: Dict[str, str]):
    """Build a boto3 client. Falls back to the ambient credential chain when
    no explicit keys are stored (IAM role / env / shared config)."""
    import boto3  # imported lazily so the app still loads if boto3 is absent

    kwargs: Dict = {"region_name": region}
    if creds.get("access_key_id") and creds.get("secret_access_key"):
        kwargs["aws_access_key_id"] = creds["access_key_id"]
        kwargs["aws_secret_access_key"] = creds["secret_access_key"]
        if creds.get("session_token"):
            kwargs["aws_session_token"] = creds["session_token"]
    return boto3.client(service, **kwargs)


def friendly_error(e: Exception) -> str:
    """Map a botocore error to a short, user-readable sentence."""
    try:
        from botocore.exceptions import ClientError, NoCredentialsError, EndpointConnectionError
    except Exception:
        return str(e)[:200]
    if isinstance(e, NoCredentialsError):
        return "No AWS credentials — set the access key / secret in the endpoint settings."
    if isinstance(e, EndpointConnectionError):
        return "Cannot reach AWS Bedrock — check the region and your network."
    if isinstance(e, ClientError):
        err = e.response.get("Error", {}) if hasattr(e, "response") else {}
        code = err.get("Code", "")
        msg = err.get("Message", "") or str(e)
        if code in ("UnrecognizedClientException", "InvalidSignatureException", "AccessDeniedException"):
            return f"AWS rejected the credentials ({code}). Check the access key / secret / region. {msg}"[:300]
        if code in ("ValidationException", "ResourceNotFoundException"):
            return f"Bedrock rejected the request ({code}): {msg}"[:300]
        if code == "ThrottlingException":
            return "Bedrock throttled the request (rate limit). Try again shortly."
        return f"Bedrock error ({code}): {msg}"[:300]
    return str(e)[:200]


# ── Model listing ──

def list_models(url: str, creds_blob, timeout: int = 8) -> List[str]:
    """List invokable Bedrock model IDs for the endpoint's region.

    Returns text-capable foundation models plus inference-profile IDs (the
    cross-region ``us.anthropic.claude-...`` aliases most on-demand models now
    require). IDs are deduped, profiles first so they sort to the top.
    """
    creds = parse_creds(creds_blob)
    region = _resolve_region(url, creds)
    ctrl = _client("bedrock", region, creds)
    profiles: List[str] = []
    models: List[str] = []
    try:
        resp = ctrl.list_inference_profiles(maxResults=1000)
        for p in resp.get("inferenceProfileSummaries", []):
            pid = p.get("inferenceProfileId")
            if pid:
                profiles.append(pid)
    except Exception as e:
        logger.debug(f"Bedrock list_inference_profiles failed: {e}")
    try:
        resp = ctrl.list_foundation_models(byOutputModality="TEXT")
        for m in resp.get("modelSummaries", []):
            mid = m.get("modelId")
            if not mid:
                continue
            # Skip models only reachable through a provisioned/inference profile
            # at the foundation level — those surface via the profile list.
            lifecycle = (m.get("modelLifecycle", {}) or {}).get("status", "ACTIVE")
            if lifecycle and lifecycle != "ACTIVE":
                continue
            models.append(mid)
    except Exception as e:
        logger.warning(f"Bedrock list_foundation_models failed: {e}")
    seen = set()
    out: List[str] = []
    for mid in profiles + models:
        if mid not in seen:
            seen.add(mid)
            out.append(mid)
    return out


# ── OpenAI → Converse message translation ──

_IMG_FORMATS = {"image/png": "png", "image/jpeg": "jpeg", "image/jpg": "jpeg",
                "image/gif": "gif", "image/webp": "webp"}


def _content_to_blocks(content) -> List[Dict]:
    """Convert an OpenAI message ``content`` (str or multimodal list) to
    Converse content blocks."""
    if content is None:
        return []
    if isinstance(content, str):
        return [{"text": content}] if content else []
    blocks: List[Dict] = []
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                if part:
                    blocks.append({"text": str(part)})
                continue
            ptype = part.get("type")
            if ptype == "text" and part.get("text"):
                blocks.append({"text": part["text"]})
            elif ptype == "image_url":
                iu = part.get("image_url") or {}
                src = iu.get("url", "") if isinstance(iu, dict) else str(iu)
                if src.startswith("data:"):
                    try:
                        header, b64 = src.split(",", 1)
                        media = header.split(";")[0].replace("data:", "")
                        fmt = _IMG_FORMATS.get(media.lower(), "png")
                        blocks.append({"image": {"format": fmt,
                                                 "source": {"bytes": base64.b64decode(b64)}}})
                    except Exception:
                        pass
    return blocks


def _flatten_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content
                         if isinstance(p, dict) and p.get("type") == "text")
    return str(content or "")


def to_converse(messages: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """Translate OpenAI-style messages into (system_blocks, converse_messages).

    Handles tool calls/results and consolidates consecutive same-role turns,
    which the Converse API requires to strictly alternate.
    """
    system: List[Dict] = []
    conv: List[Dict] = []

    def _push(role: str, blocks: List[Dict]):
        if not blocks:
            blocks = [{"text": " "}]
        if conv and conv[-1]["role"] == role:
            conv[-1]["content"].extend(blocks)
        else:
            conv.append({"role": role, "content": blocks})

    for m in messages or []:
        role = m.get("role")
        if role == "system":
            txt = _flatten_text(m.get("content"))
            if txt:
                system.append({"text": txt})
        elif role == "tool":
            _push("user", [{"toolResult": {
                "toolUseId": m.get("tool_call_id", ""),
                "content": [{"text": str(m.get("content", ""))}],
            }}])
        elif role == "assistant" and isinstance(m.get("tool_calls"), list):
            blocks: List[Dict] = []
            if m.get("content"):
                blocks.append({"text": m["content"]})
            for tc in m["tool_calls"]:
                fn = tc.get("function", {}) or {}
                args = fn.get("arguments", "{}")
                try:
                    args = json.loads(args) if isinstance(args, str) else args
                except Exception:
                    args = {}
                blocks.append({"toolUse": {
                    "toolUseId": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "input": args or {},
                }})
            _push("assistant", blocks)
        else:
            _push("assistant" if role == "assistant" else "user",
                  _content_to_blocks(m.get("content")))

    # Converse requires the first turn to be a user turn.
    if conv and conv[0]["role"] != "user":
        conv.insert(0, {"role": "user", "content": [{"text": " "}]})
    return system, conv


def to_tool_config(tools: Optional[List[Dict]]) -> Optional[Dict]:
    """Translate OpenAI function tools into a Converse toolConfig."""
    if not tools:
        return None
    specs = []
    for t in tools:
        if t.get("type") != "function":
            continue
        fn = t.get("function", {}) or {}
        specs.append({"toolSpec": {
            "name": fn.get("name", ""),
            "description": fn.get("description", "") or fn.get("name", ""),
            "inputSchema": {"json": fn.get("parameters") or {"type": "object", "properties": {}}},
        }})
    return {"tools": specs} if specs else None


def _inference_config(temperature: float, max_tokens: int) -> Dict:
    cfg: Dict = {"maxTokens": max_tokens if max_tokens and max_tokens > 0 else DEFAULT_MAX_TOKENS}
    if temperature is not None:
        # Bedrock temperature range is 0..1; clamp Odysseus's 0..2 scale.
        cfg["temperature"] = max(0.0, min(1.0, float(temperature)))
    return cfg


def _build_kwargs(url, creds, model, messages, temperature, max_tokens, tools):
    region = _resolve_region(url, creds)
    system, conv = to_converse(messages)
    kwargs: Dict = {
        "modelId": model,
        "messages": conv,
        "inferenceConfig": _inference_config(temperature, max_tokens),
    }
    if system:
        kwargs["system"] = system
    tc = to_tool_config(tools)
    if tc:
        kwargs["toolConfig"] = tc
    return region, kwargs


# ── Non-streaming ──

def converse(url, creds_blob, model, messages, temperature=1.0, max_tokens=0, tools=None) -> str:
    """Synchronous Bedrock Converse call. Returns the assistant text."""
    creds = parse_creds(creds_blob)
    region, kwargs = _build_kwargs(url, creds, model, messages, temperature, max_tokens, tools)
    client = _client("bedrock-runtime", region, creds)
    resp = client.converse(**kwargs)
    blocks = resp.get("output", {}).get("message", {}).get("content", [])
    return "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and "text" in b)


# ── Streaming ──

def converse_stream_events(url, creds_blob, model, messages,
                           temperature=1.0, max_tokens=0, tools=None) -> Iterator[tuple]:
    """Synchronous generator yielding normalized stream events as tuples:

        ("delta", text)
        ("tool_call_delta", index, name, partial_json)
        ("tool_calls", [{"id","name","arguments"}, ...])
        ("usage", input_tokens, output_tokens)
        ("error", message, status)

    llm_core bridges this onto its async SSE protocol.
    """
    creds = parse_creds(creds_blob)
    try:
        region, kwargs = _build_kwargs(url, creds, model, messages, temperature, max_tokens, tools)
        client = _client("bedrock-runtime", region, creds)
        resp = client.converse_stream(**kwargs)
    except Exception as e:
        yield ("error", friendly_error(e), 502)
        return

    # contentBlockIndex → {"id","name","arguments"}
    tool_blocks: Dict[int, Dict] = {}
    try:
        for event in resp.get("stream", []):
            if "contentBlockStart" in event:
                start = event["contentBlockStart"].get("start", {})
                idx = event["contentBlockStart"].get("contentBlockIndex", 0)
                tu = start.get("toolUse")
                if tu:
                    tool_blocks[idx] = {"id": tu.get("toolUseId", f"call_{idx}"),
                                        "name": tu.get("name", ""), "arguments": ""}
            elif "contentBlockDelta" in event:
                cbd = event["contentBlockDelta"]
                idx = cbd.get("contentBlockIndex", 0)
                delta = cbd.get("delta", {})
                if "text" in delta and delta["text"]:
                    yield ("delta", delta["text"])
                elif "toolUse" in delta:
                    partial = delta["toolUse"].get("input", "")
                    if idx in tool_blocks and partial:
                        tool_blocks[idx]["arguments"] += partial
                        yield ("tool_call_delta", idx, tool_blocks[idx]["name"], partial)
            elif "metadata" in event:
                usage = event["metadata"].get("usage", {})
                if usage:
                    yield ("usage", usage.get("inputTokens", 0), usage.get("outputTokens", 0))
            elif "messageStop" in event:
                if tool_blocks:
                    calls = [tool_blocks[i] for i in sorted(tool_blocks)]
                    yield ("tool_calls", calls)
    except Exception as e:
        yield ("error", friendly_error(e), 502)
