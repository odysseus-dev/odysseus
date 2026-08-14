#!/usr/bin/env python3
"""aaak.py — self-contained AAAK dialect compressor for the store.

AAAK is the structured-symbolic summary format researched for lightweight
memory (entity codes + topic keywords + key quote + emotion flags). It is a
LOSSLESS-adjacent summary layer: it points at the verbatim entry (drawer) via
a tiny zettel, so the full text stays authoritative while the summary costs a
few tokens at injection time. Any LLM reads it natively — no decoder needed.

The store keeps this inline (no dependency on the mempalace tool package) so
the shippable zip stays self-contained. Compression quality is heuristic and
deterministic (no model calls).

FORMAT (a single-line zettel):
    FILE|ENTITY|DATE|TOPIC
    ZID:KEYWORDS|"key_quote"|FLAGS

Usage:
  from aaak import compress
  zettel = compress(text, topic="diet", title="canary", source_file="x")
"""

import re


# Universal emotion/flag codes (subset of the researched dialect).
_FLAG_WORDS = {
    "critical": "CRITICAL",
    "danger": "DANGER",
    "never": "NEVER",
    "always": "ALWAYS",
    "mandatory": "MANDATORY",
    "must": "MUST",
    "secret": "SENSITIVE",
    "private": "SENSITIVE",
    "confidential": "SENSITIVE",
    "decision": "DECISION",
    "decided": "DECISION",
    "agreed": "DECISION",
    "do not": "FORBIDDEN",
    "prohibit": "FORBIDDEN",
}

_STOP = {"the", "and", "for", "with", "that", "this", "from", "into",
         "when", "then", "were", "have", "been", "will", "was", "are",
         "but", "not", "you", "your", "also", "its", "his", "her", "him",
         "over", "under", "them", "they", "there", "about", "after",
         "what", "which", "who", "how", "why", "where", "while", "just",
         "more", "each", "than", "then", "very", "such", "some", "only",
         "with", "from", "has", "had", "were", "been", "being"}


def _extract_entities(text, known=()):
    """Proper-noun-ish tokens: Capitalized words (excluding sentence starts)."""
    out = []
    for m in re.finditer(r"\b[A-Z][a-z]{2,}(?:[ -][A-Z][a-z]{2,})?\b", text or ""):
        tok = m.group(0)
        if tok not in out and not tok.lower() in _STOP:
            out.append(tok)
    for k in known:
        if k and k not in out and k.lower() in (text or "").lower():
            out.append(k)
    return out[:4]


def _key_sentence(text, max_len=120):
    """First substantive sentence (drops headers and empty lines)."""
    text = re.sub(r"^[#>*\-\d\. ]+", "", (text or "").strip())
    for sep in ("\n", ". ", "! ", "? "):
        idx = text.find(sep)
        if idx > 10:
            cand = text[:idx]
            if len(cand) <= max_len:
                return cand.strip()
    return (text or "")[:max_len].strip()


def _topic_keywords(text, max_keywords=6):
    words = re.findall(r"[a-z]{4,}", (text or "").lower())
    freq = {}
    for w in words:
        if w not in _STOP:
            freq[w] = freq.get(w, 0) + 1
    ranked = sorted(freq, key=lambda w: (-freq[w], w))
    return ranked[:max_keywords]


def _flags(text):
    low = (text or "").lower()
    found = []
    for word, flag in _FLAG_WORDS.items():
        if word in low and flag not in found:
            found.append(flag)
    return ",".join(found)


def compress(text, title="", topic="", source_file="", entities=None):
    """Compress `text` into an AAAK zettel (single line, ~10-30 tokens)."""
    text = (text or "").strip()
    if not text:
        return ""
    ents = _extract_entities(text, known=entities or ())
    kw = _topic_keywords(text)
    key = _key_sentence(text)
    flags = _flags(text)
    topic_code = (topic or "").lower().replace(" ", "_") or "general"
    if len(topic_code) > 24:
        topic_code = topic_code[:24]
    ent_codes = ",".join(ents[:3]) or "?"
    header = f"{source_file or '?'}|{ent_codes}|{topic_code}"
    zettel = f"0:"
    if kw:
        zettel += " ".join(kw[:5]) + "|"
    zettel += f'"{key}"'
    if flags:
        zettel += f"|{flags}"
    return f"{header}\n{zettel}"


def token_estimate(zettel):
    """Rough token count (chars/4), matching the store's budget math."""
    return max(1, len(zettel) // 4)
