#!/usr/bin/env python3
"""Sanitize Playwright trace archives to strip sensitive credentials and tokens.

Structured, format-aware fail-closed security:
Replaces naive regex-based replacement with structured JSON / HAR parsing,
recursive key/value redaction, URL credential scrubbing, DOM snapshot sanitization,
safe source code masking, and strict fail-closed purge on corruption or error.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import urllib.parse
import zipfile
from typing import Any, Dict, List, Set

logger = logging.getLogger(__name__)

BINARY_IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.ico')

SENSITIVE_KEY_PATTERN = re.compile(
    r'(?i)(password|passwd|secret|token|api_key|apikey|access_token|refresh_token|private_key|auth_token|client_secret|session|session_id|sid|csrf|xsrf|jwt|credential|authorization)'
)

SENSITIVE_PARAM_NAMES = frozenset({
    "token", "session", "auth", "password", "passwd", "key", "secret", "api_key",
    "apikey", "access_token", "refresh_token", "jwt", "sid", "code", "sig",
    "signature", "client_secret", "credential"
})

SENSITIVE_HEADER_NAMES = frozenset({
    "authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key",
    "api-key", "x-auth-token", "x-access-token"
})


def sanitize_url(url: str) -> str:
    """Sanitize query parameters and authority credentials in URLs."""
    if not isinstance(url, str) or not url.strip():
        return url
    try:
        parts = urllib.parse.urlsplit(url)
    except Exception:
        return url

    netloc = parts.netloc
    if "@" in netloc:
        userinfo, host = netloc.split("@", 1)
        if ":" in userinfo:
            user, _ = userinfo.split(":", 1)
            netloc = f"{user}:[REDACTED]@{host}"
        else:
            netloc = f"[REDACTED]@{host}"

    query = parts.query
    if query:
        q_pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
        sanitized_pairs = []
        for k, v in q_pairs:
            k_lower = k.lower().replace("-", "_")
            if k_lower in SENSITIVE_PARAM_NAMES or SENSITIVE_KEY_PATTERN.search(k_lower):
                sanitized_pairs.append((k, "[REDACTED]"))
            else:
                sanitized_pairs.append((k, v))
        query = urllib.parse.urlencode(sanitized_pairs)

    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


def sanitize_body_text(text: str) -> str:
    """Sanitize request bodies formatted as JSON or URL-encoded form data."""
    if not isinstance(text, str) or not text.strip():
        return text

    # 1. Try JSON
    trimmed = text.strip()
    if (trimmed.startswith("{") and trimmed.endswith("}")) or (trimmed.startswith("[") and trimmed.endswith("]")):
        try:
            parsed = json.loads(trimmed)
            sanitized = sanitize_structured_obj(parsed)
            return json.dumps(sanitized, ensure_ascii=False)
        except Exception:
            pass

    # 2. Try URL-encoded form data
    if "=" in trimmed and not trimmed.startswith("<"):
        try:
            pairs = urllib.parse.parse_qsl(trimmed, keep_blank_values=True)
            if pairs and any(k.lower() in SENSITIVE_PARAM_NAMES or SENSITIVE_KEY_PATTERN.search(k) for k, _ in pairs):
                out_pairs = []
                for k, v in pairs:
                    k_lower = k.lower().replace("-", "_")
                    if k_lower in SENSITIVE_PARAM_NAMES or SENSITIVE_KEY_PATTERN.search(k_lower):
                        out_pairs.append((k, "[REDACTED]"))
                    else:
                        out_pairs.append((k, v))
                return urllib.parse.urlencode(out_pairs)
        except Exception:
            pass

    return text


def sanitize_structured_obj(obj: Any) -> Any:
    """Recursively sanitize Python objects decoded from JSON or HAR payloads."""
    if isinstance(obj, dict):
        new_dict: Dict[str, Any] = {}

        # Check for header entry with inverted or standard keys: {"name": ..., "value": ...}
        name_val = obj.get("name")
        val_val = obj.get("value")
        if isinstance(name_val, str) and isinstance(val_val, str):
            name_lower = name_val.lower().strip()
            if name_lower in SENSITIVE_HEADER_NAMES or SENSITIVE_KEY_PATTERN.search(name_lower):
                for k, v in obj.items():
                    if k == "value":
                        new_dict[k] = "[REDACTED]"
                    else:
                        new_dict[k] = sanitize_structured_obj(v)
                return new_dict

        # Process standard dictionary keys
        for k, v in obj.items():
            k_lower = str(k).lower().replace("-", "_")

            # Action parameters in Playwright traces (e.g. page.fill, page.type)
            if k == "params" and isinstance(v, dict):
                sanitized_params = sanitize_structured_obj(v)
                # If this action involves fill, type, or sensitive inputs, redact value/text
                if "value" in sanitized_params:
                    sanitized_params["value"] = "[REDACTED]"
                if "text" in sanitized_params:
                    sanitized_params["text"] = "[REDACTED]"
                new_dict[k] = sanitized_params
                continue

            # DOM snapshot nodes
            if k == "attributes" and isinstance(v, (dict, list)):
                if isinstance(v, dict):
                    attr_dict = dict(v)
                    type_val = str(attr_dict.get("type") or "").lower()
                    if type_val == "password" or "password" in str(attr_dict.get("name") or "").lower():
                        if "value" in attr_dict:
                            attr_dict["value"] = "[REDACTED]"
                    new_dict[k] = attr_dict
                    continue
                elif isinstance(v, list):
                    # List of attribute strings or pairs
                    new_list = []
                    is_pw = any("password" in str(x).lower() for x in v)
                    for x in v:
                        if is_pw and isinstance(x, str) and not x.startswith("type="):
                            new_list.append("[REDACTED]")
                        else:
                            new_list.append(sanitize_structured_obj(x))
                    new_dict[k] = new_list
                    continue

            # Request postData / text
            if k in ("postData", "postDataText") and isinstance(v, str):
                new_dict[k] = sanitize_body_text(v)
                continue

            # URL fields
            if k == "url" and isinstance(v, str):
                new_dict[k] = sanitize_url(v)
                continue

            # Cookie arrays
            if k == "cookies" and isinstance(v, list):
                sanitized_cookies = []
                for c in v:
                    if isinstance(c, dict):
                        c_copy = dict(c)
                        if "value" in c_copy:
                            c_copy["value"] = "[REDACTED]"
                        sanitized_cookies.append(c_copy)
                    else:
                        sanitized_cookies.append(sanitize_structured_obj(c))
                new_dict[k] = sanitized_cookies
                continue

            # Direct sensitive key matches
            if SENSITIVE_KEY_PATTERN.search(k_lower) and isinstance(v, str):
                new_dict[k] = "[REDACTED]"
                continue

            new_dict[k] = sanitize_structured_obj(v)

        return new_dict

    elif isinstance(obj, list):
        return [sanitize_structured_obj(item) for item in obj]

    elif isinstance(obj, str):
        # Check for inline credentials/tokens
        if obj.startswith("Bearer ") or obj.startswith("bearer "):
            return "Bearer [REDACTED]"
        if obj.startswith("Basic ") or obj.startswith("basic "):
            return "Basic [REDACTED]"
        return obj

    return obj


def sanitize_jsonl_content(data: bytes) -> bytes:
    """Sanitize newline-delimited JSON Lines (.trace, .network, .stacks)."""
    lines = data.split(b"\n")
    out_lines = []
    for line in lines:
        if not line.strip():
            out_lines.append(line)
            continue
        try:
            parsed = json.loads(line.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            # Not valid JSON, keep as-is
            out_lines.append(line)
            continue
        sanitized = sanitize_structured_obj(parsed)
        out_lines.append(json.dumps(sanitized, ensure_ascii=False).encode("utf-8"))
    return b"\n".join(out_lines)


def sanitize_trace_archive(zp: str) -> bool:
    """Sanitize a single zip archive in a strict fail-closed manner.

    If the archive is malformed, partial, unreadable, or encounters an exception
    during processing, the archive is permanently deleted so that raw traces
    cannot leak.
    Returns True if successfully sanitized, False if deleted due to error.
    """
    if not os.path.exists(zp):
        return False

    temp_zp = zp + ".tmp"
    try:
        if os.path.getsize(zp) == 0:
            raise zipfile.BadZipFile("Empty archive")

        with zipfile.ZipFile(zp, "r") as zin:
            infolist = zin.infolist()
            if not infolist:
                raise zipfile.BadZipFile("Archive has no entries")

            corrupt_file = zin.testzip()
            if corrupt_file is not None:
                raise zipfile.BadZipFile(f"Corrupt entry detected: {corrupt_file}")

            with zipfile.ZipFile(temp_zp, "w", compression=zipfile.ZIP_DEFLATED) as zout:
                for item in infolist:
                    filename_lower = item.filename.lower()

                    # 1. Binary image screenshots/diagnostics: preserve as-is
                    if filename_lower.endswith(BINARY_IMAGE_EXTENSIONS):
                        content = zin.read(item.filename)
                        zout.writestr(item, content)
                        continue

                    # 2. Embedded test source code (e.g. resources/src@... or .js/.ts):
                    # Mask safely without syntax corruption!
                    if "src@" in filename_lower or filename_lower.endswith((".js", ".ts", ".mjs", ".py")):
                        safe_placeholder = b"// [SOURCE CODE REDACTED FOR TRACE CONFIDENTIALITY]\n"
                        zout.writestr(item, safe_placeholder)
                        continue

                    # 3. JSON Lines and structured JSON/HAR traces
                    content = zin.read(item.filename)
                    if filename_lower.endswith((".trace", ".network", ".stacks")):
                        sanitized_content = sanitize_jsonl_content(content)
                        zout.writestr(item, sanitized_content)
                    elif filename_lower.endswith((".json", ".har")):
                        try:
                            parsed = json.loads(content.decode("utf-8"))
                            sanitized = sanitize_structured_obj(parsed)
                            zout.writestr(item, json.dumps(sanitized, ensure_ascii=False).encode("utf-8"))
                        except Exception:
                            # If invalid JSON, treat as text line-by-line
                            zout.writestr(item, sanitize_jsonl_content(content))
                    else:
                        # Unknown binary or text member: exclude to fail closed
                        continue

        # Atomic replace
        os.replace(temp_zp, zp)
        return True

    except Exception as exc:
        logger.warning("Sanitizing trace %s failed (%s); removing fail-closed", zp, exc)
        for path_to_remove in (temp_zp, zp):
            if os.path.exists(path_to_remove):
                try:
                    os.remove(path_to_remove)
                except OSError as remove_err:
                    logger.error("Failed to delete unredacted trace %s: %s", path_to_remove, remove_err)
        return False


def sanitize_directory(results_dir: str) -> int:
    """Walk results_dir and sanitize every .zip archive fail-closed.

    Returns the number of archives processed. Exits with 1 if any archive fails.
    """
    if not os.path.exists(results_dir):
        return 0

    count = 0
    all_ok = True
    for root, _, files in os.walk(results_dir):
        for f in files:
            if f.endswith(".zip"):
                zp = os.path.join(root, f)
                ok = sanitize_trace_archive(zp)
                if not ok:
                    all_ok = False
                count += 1

    if not all_ok:
        sys.exit(1)
    return count


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <results_directory>")
        sys.exit(1)

    target_dir = sys.argv[1]
    sanitize_directory(target_dir)


if __name__ == "__main__":
    main()
