"""
Google Meet background recipe.

Event model:
  meet_call_started  { code, url, startedAt }
  meet_captions      { code, captions:[{speaker,text}], ts }
  meet_call_ended    { code, endedAt, reason }
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Callable, Optional

from playwright.async_api import Page
from src.browser_recipes.schemas import LifecycleEvent, RecipeState

logger = logging.getLogger("browser_recipes.meet")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _icon_ligature(text: str) -> bool:
    if not text:
        return True
    t = text.strip()
    if not t:
        return True
    if re.fullmatch(r"[a-z0-9_]+", t) and len(t) < 40:
        return True
    return False


def _row_speaker(row) -> str:
    try:
        img = row.query_selector("img[alt]")
        if img:
            alt = (img.get_attribute("alt") or "").strip()
            if alt and len(alt) > 1 and not _icon_ligature(alt) and not re.fullmatch(r"avatar", alt, re.I):
                return alt
        name = row.get_attribute("data-self-name")
        if name and name.strip():
            return name.strip()
        spans = row.query_selector_all("span")
        for span in spans:
            t = (span.text_content or "").strip()
            if t and not _icon_ligature(t) and len(t) <= 40:
                return t
    except Exception:
        pass
    return "Unknown"


def _row_text(row) -> str:
    try:
        full = re.sub(r"\s+", " ", row.text_content or "").strip()
        if not full:
            return ""
        spans = row.query_selector_all("span")
        prefix = ""
        for span in spans:
            t = (span.text_content or "").strip()
            if t:
                prefix = t
                break
        stripped = full
        if prefix and full.lower().startswith(prefix.lower()):
            stripped = full[len(prefix):].strip()
        stripped = re.sub(r"\s*arrow_downward\s*Jump to bottom\s*$", "", stripped).strip()
        return stripped
    except Exception:
        return (row.text_content or "").strip()


def _looks_like_caption(text: str) -> bool:
    if not text or len(text) < 3 or _icon_ligature(text):
        return False
    if len(text) < 20 and not re.search(r"\s", text):
        return False
    if re.fullmatch(r"([a-z]+)([A-Z][a-z]*)", text):
        m = re.fullmatch(r"([a-z]+)([A-Z][a-z]*)", text)
        if m and m.group(2).lower() == m.group(1):
            return False
    if re.search(r"\b[a-z]+_[a-z]+\b", text):
        return False
    if re.search(
        r"Your meeting is safe|Your meeting's ready|Copy link|Meeting details|Add people|Jump to",
        text,
        re.I,
    ):
        return False
    if re.search(r"([a-z]{3,})\\\1", text, re.I):
        return False
    return True


def _score_caption_region(el) -> int:
    try:
        if not el:
            return 0
        imgs = el.query_selector_all("img[alt]")
        selves = el.query_selector_all("[data-self-name]")
        spans = el.query_selector_all("span")
        plausible = 0
        for img in imgs:
            alt = (img.get_attribute("alt") or "").strip()
            if alt and len(alt) > 1 and not _icon_ligature(alt) and not re.fullmatch(r"avatar", alt, re.I):
                plausible += 1
        for s in selves:
            if s.get_attribute("data-self-name"):
                plausible += 1
        if plausible == 0:
            return 0
        if len(spans) < 2:
            return 0
        return plausible * 10 + len(spans)
    except Exception:
        return 0


def _find_caption_region(page: Page):
    try:
        primary = page.query_selector('[jsname="tgaKEf"]')
        if primary and _score_caption_region(primary) > 0:
            return {"region": primary, "how": "jsname=tgaKEf"}
        if primary:
            logger.debug("primary jsname=tgaKEf matched but scored 0")
    except Exception:
        pass

    try:
        for el in page.query_selector_all('[role="region"][aria-label], [aria-label]'):
            label = (el.get_attribute("aria-label") or "").strip()
            if re.fullmatch(r"captions|sous-titres|untertitel|leyendas|字幕", label, re.I):
                return {"region": el, "how": f"label={label[:40]}"}
    except Exception:
        pass

    candidates = []
    try:
        candidates.extend(page.query_selector_all('[aria-label]'))
        candidates.extend(page.query_selector_all('[aria-live="polite"]'))
    except Exception:
        pass

    best = None
    best_score = 0
    best_how = ""
    for cand in candidates:
        try:
            score = _score_caption_region(cand)
            if score > best_score:
                best_score = score
                best = cand
                lbl = (cand.get_attribute("aria-label") or "").slice(0, 40) if hasattr(cand.get_attribute("aria-label") or "", "slice") else ""
                live = cand.get_attribute("aria-live") or ""
                best_how = f"fallback(label={lbl},live={live},score={score})"
        except Exception:
            continue
    if best:
        return {"region": best, "how": best_how}
    return None


def _caption_rows(page: Page):
    found = _find_caption_region(page)
    if not found:
        logger.debug(
            "no caption region found; jsname=%s",
            "present" if page.query_selector('[jsname="tgaKEf"]') else "absent",
        )
        return []
    rows = []
    try:
        region = found["region"]
        children = region.query_selector_all("*")
        for row in children:
            speaker = _row_speaker(row)
            text = _row_text(row)
            if not text:
                continue
            if not _looks_like_caption(text):
                continue
            if speaker == "Unknown" and len(text) < 12:
                continue
            rows.append({"speaker": speaker, "text": text})
    except Exception:
        pass
    return rows


class MeetAdapter:
    def __init__(self, browser_manager, *, poll_interval_ms: int = 5000,) -> None:
        self.browser_manager = browser_manager
        self._poll = poll_interval_ms / 1000
        self._last_captions_key = ""
        self._current_code: Optional[str] = None

    async def run(self, sink: Callable[[LifecycleEvent], None], state: RecipeState) -> None:
        while True:
            page = await self.browser_manager.new_page()
            try:
                await self._tick(sink, state, page)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("meet recipe tick error: %s", exc, exc_info=True)
            finally:
                try:
                    await page.close()
                except Exception:
                    pass
            await asyncio.sleep(self._poll)

    async def _tick(self, sink, state: RecipeState, page: Page):
        code = await page.evaluate(
            r"""() => {
              const m = location.pathname.match(/^\/([a-z]{3,4}-[a-z]{3,4}-[a-z]{3,4})(?:$|/|\?|&|#)/i);
              return m ? m[1] : null;
            }"""
        )
        in_call = bool(code) and await page.evaluate(
            "() => !!document.querySelector('[data-self-name], [data-participant-id]')"
        )
        if self._current_code and code != self._current_code:
            sink(LifecycleEvent(
                kind="meet_call_ended",
                payload={"code": self._current_code, "endedAt": _now_ms(), "reason": "switched-on-reload"},
                ts_ms=_now_ms(),
                adapter="google_meet",
            ))
            self._last_captions_key = ""
            self._current_code = code

        if code and in_call and self._current_code is None:
            started_at = _now_ms()
            self._current_code = code
            sink(LifecycleEvent(
                kind="meet_call_started",
                payload={"code": code, "url": page.url, "startedAt": started_at},
                ts_ms=started_at,
                adapter="google_meet",
            ))

        if not code or not in_call or self._current_code is None:
            return

        rows = _caption_rows(page)
        if not rows:
            return

        key = json.dumps(rows, ensure_ascii=False, sort_keys=True)
        if key == self._last_captions_key:
            return
        self._last_captions_key = key
        sink(LifecycleEvent(
            kind="meet_captions",
            payload={"code": self._current_code, "captions": rows, "ts": _now_ms()},
            ts_ms=_now_ms(),
            adapter="google_meet",
        ))
