"""
Internal LLM helper utilities for TRACE.

Users should never import from this module directly.
Configure the LLM endpoint via environment variables:

    OPENAI_BASE_URL  — defaults to http://127.0.0.1:1234/v1  (LM Studio)
    OPENAI_API_KEY   — defaults to "lm-studio"
"""

import httpx
import time
import os
import json


# ── LLM Call ──────────────────────────────────────────────────────────────────

def ChatGPT_API(
    model: str,
    prompt: str,
    api_key: str = None,
    chat_history: list = None,
    temperature: float = 0,
    max_tokens: int = None,
) -> str:
    """
    Send a prompt to any OpenAI-compatible chat endpoint and return the
    response text.  Retries up to 10 times on transient errors.

    Parameters
    ----------
    model        : Model ID string (e.g. "gpt-4o-mini", "meta-llama-3.1-8b").
    prompt       : The user message to send.
    api_key      : API key; falls back to OPENAI_API_KEY env var.
    chat_history : Optional list of prior messages (dicts with role/content).
    temperature  : Sampling temperature (0 = deterministic).
    max_tokens   : Hard cap on response tokens; None = model default.

    Returns
    -------
    str  — Response text, or "Error" after all retries are exhausted.
    """
    max_retries = 10
    base_url = os.getenv("OPENAI_BASE_URL", "http://127.0.0.1:1234/v1")
    api_key_to_use = api_key or os.getenv("OPENAI_API_KEY") or "lm-studio"

    for i in range(max_retries):
        try:
            if chat_history is not None:
                messages = list(chat_history)
                messages.append({"role": "user", "content": prompt})
            else:
                messages = [{"role": "user", "content": prompt}]

            payload = {"model": model, "messages": messages, "temperature": temperature}
            if max_tokens is not None:
                payload["max_tokens"] = max_tokens
                
            headers = {"Authorization": f"Bearer {api_key_to_use}", "Content-Type": "application/json"}
            url = base_url.rstrip("/") + "/chat/completions"

            with httpx.Client(timeout=60.0) as client:
                response = client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                return content if content is not None else ""
        except Exception:
            if i < max_retries - 1:
                time.sleep(1)
            else:
                return "Error"


# ── JSON Extraction ────────────────────────────────────────────────────────────

def _normalize_for_json(text: str) -> str:
    """Normalise common LLM JSON formatting quirks before parsing."""
    text = text.replace(": N/A", ": null")
    text = text.replace(": True",  ": true").replace(": False", ": false").replace(": None", ": null")
    text = text.replace(": True,", ": true,").replace(": False,", ": false,").replace(": None,", ": null,")
    text = text.replace(",}", "}").replace(",]", "]")
    text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    text = " ".join(text.split())
    return text


def _try_parse(text: str):
    return json.loads(_normalize_for_json(text))


def extract_json(content: str):
    """
    Robustly extract a JSON object or array from an LLM response string.

    Tries, in order:
      1. Content inside ```json … ``` fences.
      2. Content inside generic ``` … ``` fences.
      3. First { … } block.
      4. First [ … ] block.
      5. The raw string as-is.

    Returns the parsed Python object, or {} if nothing parses.
    """
    if content is None:
        return {}

    candidates = []

    json_start = content.find("```json")
    if json_start != -1:
        json_end = content.find("```", json_start + 7)
        if json_end != -1:
            candidates.append(content[json_start + 7 : json_end].strip())

    generic_start = content.find("```")
    if generic_start != -1:
        generic_end = content.find("```", generic_start + 3)
        if generic_end != -1:
            candidates.append(content[generic_start + 3 : generic_end].strip())

    brace_start = content.find("{")
    brace_end   = content.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        candidates.append(content[brace_start : brace_end + 1])

    bracket_start = content.find("[")
    bracket_end   = content.rfind("]")
    if bracket_start != -1 and bracket_end > bracket_start:
        candidates.append(content[bracket_start : bracket_end + 1])

    candidates.append(content.strip())

    for candidate in candidates:
        if not candidate:
            continue
        try:
            return _try_parse(candidate)
        except Exception:
            pass

    return {}
