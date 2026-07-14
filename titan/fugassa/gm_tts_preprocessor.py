"""Extract narrative prose from GM messages for TTS (mirrors frontend GmTtsPreprocessor.js)."""

from __future__ import annotations

import re

_TABLE_ROW_RE = re.compile(r"^\s*\|")
_SECTION_BREAK_RE = re.compile(
    r"^(?:round\s+summary|suggestions?|player\s+options?|choices?)\s*:?\s*$",
    re.IGNORECASE,
)


def extract_narrative_for_tts(raw: str) -> str:
    """Return scene prose suitable for read-aloud; drop tables and meta sections."""
    if not raw or not str(raw).strip():
        return ""

    text = str(raw).replace("\r\n", "\n").strip()

    # Drop markdown pipe tables (timestamp header + row).
    lines = text.split("\n")
    filtered: list[str] = []
    for line in lines:
        if _TABLE_ROW_RE.match(line):
            continue
        filtered.append(line)
    text = "\n".join(filtered).strip()

    # Stop at round summary / suggestions headings (own line or inline **Round summary:**).
    cut = re.search(r"(?im)(?:\n|^)\s*\*{0,2}round\s+summary\*{0,2}\s*:", text)
    if cut:
        text = text[: cut.start()].strip()
    out_lines: list[str] = []
    for line in text.split("\n"):
        if _SECTION_BREAK_RE.match(line.strip()):
            break
        out_lines.append(line)
    text = "\n".join(out_lines).strip()

    # Collapse excessive blank lines.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
