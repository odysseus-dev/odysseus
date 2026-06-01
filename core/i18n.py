"""Server-side localization foundation.

Mirrors the client runtime (static/js/i18n.js): the SAME JSON catalogs under
``static/locales`` are the single source of truth, so a string only ever has
one translation regardless of whether it's rendered in the browser or emitted
by the backend.

Use this for any user-facing text the server produces (error detail messages,
notification bodies, emailed content, etc.). The frontend handles everything it
renders itself; this is the parity layer for server-originated strings.

Lookup order matches the client: requested locale -> default locale -> the raw
key. Nothing ever returns blank.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional

from core.constants import STATIC_DIR

LOCALES_DIR = os.path.join(STATIC_DIR, "locales")
BASE_LOCALE = "en"

_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_PLURAL_CATS = {"zero", "one", "two", "few", "many", "other"}


def _registry() -> Dict[str, Any]:
    try:
        with open(os.path.join(LOCALES_DIR, "index.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {
            "default": BASE_LOCALE,
            "fallback": BASE_LOCALE,
            "locales": [{"code": "en", "name": "English", "nativeName": "English", "dir": "ltr"}],
        }


def available_locales() -> List[Dict[str, str]]:
    """List of locale descriptors from the registry: code/name/nativeName/dir."""
    return list(_registry().get("locales", []))


def available_codes() -> List[str]:
    return [l.get("code") for l in available_locales() if l.get("code")]


def default_locale() -> str:
    return _registry().get("default", BASE_LOCALE)


def _is_plural(v: Any) -> bool:
    return isinstance(v, dict) and bool(v) and all(k in _PLURAL_CATS for k in v)


def _flatten(obj: Dict[str, Any], prefix: str, out: Dict[str, Any]) -> Dict[str, Any]:
    for k, v in obj.items():
        if k.startswith("_"):
            continue
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, str) or _is_plural(v):
            out[key] = v
        elif isinstance(v, dict):
            _flatten(v, key, out)
    return out


@lru_cache(maxsize=32)
def _catalog(code: str) -> Dict[str, Any]:
    """Flattened {dotted_key: str|plural_dict} for one locale (cached)."""
    try:
        with open(os.path.join(LOCALES_DIR, f"{code}.json"), "r", encoding="utf-8") as f:
            return _flatten(json.load(f), "", {})
    except (OSError, json.JSONDecodeError):
        return {}


def _select_plural(forms: Dict[str, str], count: int) -> str:
    # Minimal English-family rule (1 -> one, else other). Languages with richer
    # plural systems can be handled later; "other" is always a safe fallback.
    cat = "one" if count == 1 else "other"
    return forms.get(cat) or forms.get("other") or next(iter(forms.values()), "")


def translate(key: str, locale: Optional[str] = None, **params: Any) -> str:
    """Translate a dotted ``key`` into ``locale`` (default locale if omitted).

    Pass ``count=`` for plural keys and any ``{placeholder}`` values as kwargs::

        translate("settings.nav.appearance", "es")
        translate("chat.message_count", "en", count=3)
    """
    code = locale or default_locale()
    val = _catalog(code).get(key)
    if val is None:
        val = _catalog(BASE_LOCALE).get(key)
    if val is None:
        return key

    if _is_plural(val):
        val = _select_plural(val, int(params.get("count", 0)))

    if params:
        val = _PLACEHOLDER.sub(lambda m: str(params.get(m.group(1), m.group(0))), val)
    return val


def negotiate(accept_language: Optional[str]) -> str:
    """Pick the best available locale for an HTTP ``Accept-Language`` header.

    Honors q-weights and matches the primary subtag (``en-US`` -> ``en``).
    Falls back to the default locale.
    """
    codes = [c.lower() for c in available_codes()]
    default = default_locale()
    if not accept_language:
        return default

    ranked: List[tuple] = []
    for part in accept_language.split(","):
        part = part.strip()
        if not part:
            continue
        tag, _, qpart = part.partition(";")
        tag = tag.strip().lower()
        q = 1.0
        if qpart.strip().startswith("q="):
            try:
                q = float(qpart.strip()[2:])
            except ValueError:
                q = 1.0
        if tag:
            ranked.append((q, tag))
    ranked.sort(key=lambda x: x[0], reverse=True)

    for _, tag in ranked:
        if tag == "*":
            return default
        if tag in codes:
            return tag
        primary = tag.split("-")[0]
        for c in codes:
            if c == primary or c.split("-")[0] == primary:
                return c
    return default
