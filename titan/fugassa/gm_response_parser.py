"""Parse GM LLM responses (ports GMResponseParser.gd — MVP subset)."""

from __future__ import annotations

import re
from typing import Any


def strip_thinking(text: str) -> str:
    src = str(text or "")
    src = re.sub(r"(?is)<think(?:ing)?[\s>][\s\S]*?(?:</think(?:ing)?>|$)", "", src)
    src = re.sub(r"<thinking>[\s\S]*?</thinking>", "", src, flags=re.IGNORECASE)
    src = re.sub(r"(?is)^thinking process:\s*[\s\S]*?(?=\n\n|\Z)", "", src)
    return src.strip()


_CLOSING_HOOK_RE = re.compile(r"(?im)^What do you do next\?\s*$")
_ROUND_SUMMARY_RE = re.compile(r"(?im)\*\*Round summary:\*\*")
_FIRST_PERSON_REASONING_RE = re.compile(r"(?im)^(?:I'm|I am|I'll|I've|I'd)\s+")


def truncate_duplicate_gm_reply(text: str) -> str:
    """Keep the first complete GM reply when the model loops or leaks reasoning."""
    src = str(text or "").strip()
    if not src:
        return src
    hook = _CLOSING_HOOK_RE.search(src)
    if hook:
        return src[: hook.end()].strip()
    summaries = list(_ROUND_SUMMARY_RE.finditer(src))
    if len(summaries) >= 2:
        return src[: summaries[1].start()].strip()
    reasoning = _FIRST_PERSON_REASONING_RE.search(src)
    if reasoning and reasoning.start() > 400:
        return src[: reasoning.start()].strip()
    return src


def _split_table_row(line: str) -> list[str]:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return cells


_TS_LABEL_KEYS = {
    "time of day": "time_of_day",
    "hh:mm am/pm": "hhmm",
    "era, year, month, day": "era_year",
    "moon phase": "moon_phase",
    "current location": "location",
    "season": "season",
    "weather": "weather",
}


def _fill_display(result: dict[str, Any]) -> dict[str, Any]:
    disp = []
    if result.get("time_of_day"):
        disp.append(str(result["time_of_day"]))
    if result.get("hhmm"):
        disp.append(str(result["hhmm"]))
    if result.get("era") or result.get("year"):
        disp.append(
            f"{result.get('era', '')} {result.get('year', '')} {result.get('month', '')} {result.get('day', '')}".strip()
        )
    elif result.get("display_era"):
        disp.append(str(result["display_era"]))
    if disp:
        result["display"] = " | ".join(disp)
    return result


def _split_era_year(era_year: str, result: dict[str, Any]) -> None:
    from titan.fugassa import world_time_engine

    parsed = world_time_engine.split_era_year_cell(era_year)
    if parsed:
        result.update(parsed)
    else:
        result["display_era"] = era_year


def _extract_timestamp_table_rows(lines: list[str]) -> dict[str, Any]:
    """Strict 2-row table: a header row (containing 'Time of Day') followed
    by a data row with matching cells, per OUTPUT FORMAT instructions."""
    seen_header = False
    for line in lines:
        line = line.strip()
        if not line.startswith("|"):
            continue
        if "Time of Day" in line and not seen_header:
            seen_header = True
            continue
        if not seen_header:
            continue
        cells = _split_table_row(line)
        if len(cells) < 3:
            continue
        result: dict[str, Any] = {}
        if cells[0]:
            result["time_of_day"] = cells[0]
        if cells[1]:
            result["hhmm"] = cells[1]
        if cells[2]:
            _split_era_year(cells[2], result)
        if len(cells) >= 4 and cells[3]:
            result["moon_phase"] = cells[3]
        if len(cells) >= 5 and cells[4]:
            result["location"] = cells[4]
        if len(cells) >= 6 and cells[5]:
            result["season"] = cells[5]
        if len(cells) >= 7 and cells[6]:
            result["weather"] = cells[6]
        return result
    return {}


def _extract_timestamp_flat_row(lines: list[str]) -> dict[str, Any]:
    """
    Some models collapse the 2-row table into a single row of alternating
    "Label | Value | Label | Value | ..." cells instead (observed in
    practice, e.g. "| Time of Day | 07:00 AM | Era, Year, Month, Day |
    Present, Day 1 | Moon Phase | ... |"). Salvage it label-by-label instead
    of discarding the whole (otherwise perfectly good) GM reply.
    """
    for line in lines:
        line = line.strip()
        if not line.startswith("|") or "time of day" not in line.lower():
            continue
        cells = _split_table_row(line)
        if len(cells) < 4:
            continue
        result: dict[str, Any] = {}
        i = 0
        while i < len(cells) - 1:
            key = _TS_LABEL_KEYS.get(cells[i].strip().lower())
            value = cells[i + 1].strip()
            if key and value:
                if key == "era_year":
                    _split_era_year(value, result)
                else:
                    result[key] = value
                i += 2
            else:
                i += 1
        if result:
            return result
    return {}


def _extract_timestamp(text: str) -> dict[str, Any]:
    lines = text.split("\n")
    result = _extract_timestamp_table_rows(lines) or _extract_timestamp_flat_row(lines)
    return _fill_display(result) if result else {}


_SECTION_MARKER = re.compile(
    r"(?im)^(?:\s*(?:\d+[\).\)]\s*)?)?"
    r"(recap|current scene|round summary|suggestions|what do you do next)"
    r"(?:\s*\([^)]*\))?\s*:?\s*$"
)


def _lines_after_timestamp_table(lines: list[str]) -> list[str]:
    out: list[str] = []
    past_table = False
    for line in lines:
        if not past_table and line.strip().startswith("|"):
            continue
        if not past_table and line.strip() == "":
            past_table = True
            continue
        past_table = True
        out.append(line)
    return out


def extract_current_scene_narrative(text: str) -> str:
    """Return the GM 'Current scene' beat only — skips recap, round summary,
    suggestions, and the closing hook. Used for per-turn chat scene images."""
    cleaned = strip_thinking(text)
    if not cleaned:
        return ""
    lines = _lines_after_timestamp_table(cleaned.split("\n"))

    section: str | None = None
    chunks: dict[str, list[str]] = {"recap": [], "current scene": [], "other": []}
    for line in lines:
        marker = _SECTION_MARKER.match(line.strip())
        if marker:
            section = marker.group(1).lower()
            continue
        if section in ("round summary", "suggestions", "what do you do next"):
            break
        key = section if section in chunks else "other"
        chunks[key].append(line)

    scene = "\n".join(chunks["current scene"]).strip()
    if len(scene) >= 40:
        return scene

    # Models often omit the "Current scene" header — take prose after recap
    # (or from the top) until round summary / suggestions.
    body: list[str] = []
    in_recap = False
    recap_done = False
    for line in lines:
        marker = _SECTION_MARKER.match(line.strip())
        if marker:
            label = marker.group(1).lower()
            if label == "recap":
                in_recap = True
                recap_done = False
                continue
            if label == "current scene":
                in_recap = False
                recap_done = True
                continue
            if label in ("round summary", "suggestions", "what do you do next"):
                break
        if in_recap and not line.strip():
            in_recap = False
            recap_done = True
            continue
        if in_recap:
            continue
        if not recap_done and chunks["recap"]:
            continue
        if line.strip().startswith("- ") and len(body) > 2:
            break
        body.append(line)

    scene = "\n".join(body).strip()
    if len(scene) >= 40:
        return scene
    return _extract_narrative(text)


def _extract_narrative(text: str) -> str:
    cleaned = strip_thinking(text)
    # Drop timestamp table block at top if present.
    lines = cleaned.split("\n")
    out: list[str] = []
    past_table = False
    for line in lines:
        if not past_table and line.strip().startswith("|"):
            continue
        if not past_table and line.strip() == "":
            past_table = True
            continue
        past_table = True
        out.append(line)
    narrative = "\n".join(out).strip()
    return narrative or cleaned


def parse(raw: str) -> dict[str, Any]:
    cleaned = strip_thinking(raw)
    return {
        "timestamp": _extract_timestamp(cleaned),
        "narrative": _extract_narrative(cleaned),
        "options": [],
    }


def is_valid_timestamp(ts: dict[str, Any]) -> bool:
    """
    The full OUTPUT FORMAT asks the GM for time_of_day/hhmm/era/year/month/day,
    but models frequently drop or reshuffle a couple of these (e.g. skip a
    literal "Era" for a "Present"-day campaign, see `_split_era_year`).
    Requiring every single field turned "slightly off-format but otherwise
    perfectly good" GM replies into a hard failure — most damagingly during
    opening bootstrap, where that meant the whole first scene got silently
    discarded. Accept the timestamp as long as it establishes *a* clock time
    (hhmm or time_of_day) — the other fields are enrichment, not load-bearing.
    """
    if not ts:
        return False
    return bool(str(ts.get("hhmm", "")).strip() or str(ts.get("time_of_day", "")).strip())


def strip_chat_meta_sections(text: str) -> str:
    """Remove only the Round summary block; keep suggestion bullets and closing hook."""
    src = str(text or "").strip()
    if not src:
        return src
    m = re.search(r"(?im)(?:\n|^)\s*\*{0,2}round\s+summary\*{0,2}\s*:", src)
    if not m:
        return src
    head = src[: m.start()].rstrip()
    tail = src[m.end() :]
    rest: list[str] = []
    skipping = True
    for line in tail.split("\n"):
        stripped = line.strip()
        if skipping:
            if not stripped:
                continue
            if stripped.startswith("- ") or re.match(
                r"(?i)^\*{0,2}suggestions?\*{0,2}\s*:", stripped
            ):
                skipping = False
                rest.append(line)
            elif re.match(r"(?i)^what do you do next\?", stripped):
                skipping = False
                rest.append(line)
            continue
        rest.append(line)
    tail = "\n".join(rest).lstrip()
    if head and tail:
        return f"{head}\n\n{tail}".strip()
    return (head or tail).strip()


def assistant_text_from_response(raw: str) -> str:
    cleaned = truncate_duplicate_gm_reply(strip_thinking(raw))
    parsed = parse(cleaned)
    narrative = str(parsed.get("narrative") or "").strip()
    if narrative and len(narrative) >= 20:
        return strip_chat_meta_sections(narrative)
    if cleaned and len(cleaned) >= 20:
        return strip_chat_meta_sections(cleaned)
    return strip_chat_meta_sections(cleaned or narrative)
