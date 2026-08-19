"""
LinkedIn Messaging background recipe.

Events emitted:
  linkedin_conversation_list  { items:[{chatId,name,preview,unread,timeText,ts}] }
  linkedin_active_thread      { chatId, messages:[{from,body,timestamp,fromMe}] }
  linkedin_requests           { requests:[{name,subtitle}] }
  linkedin_notification       { title, body, tag, silent }
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from playwright.async_api import Page

from src.browser_recipes.schemas import LifecycleEvent, RecipeState

logger = logging.getLogger("browser_recipes.linkedin")


def _text_of(el) -> str:
    try:
        return (el.text_content or "").strip()
    except Exception:
        return ""


def _first_query(row, selectors):
    for sel in selectors:
        try:
            el = row.query_selector(sel)
            if el:
                return el
        except Exception:
            continue
    return None


def _parse_relative_ms(text: str) -> Optional[int]:
    if not text:
        return None
    t = text.strip().lower()
    m = re.match(r"^(\d+)\s*([smhdw])", t)
    if not m:
        return None
    n = int(m.group(1))
    units = {"s": 1000, "m": 60000, "h": 3600000, "d": 86400000, "w": 604800000}
    return int(time.time() * 1000) - n * (units.get(m.group(2), 0))


def _chat_id_from_href(href: Optional[str]) -> Optional[str]:
    if not href:
        return None
    m = re.search(r"(?:thread|conversations?)[/=]([^/?#&]+)", href, re.I)
    return m.group(1) if m else None


def _iso_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class LinkedInAdapter:
    def __init__(self, browser_manager, poll_interval_ms: int = 8000) -> None:
        self._browser_manager = browser_manager
        self._poll = poll_interval_ms / 1000
        self._last_list_key = ""
        self._last_thread_key = ""
        self._last_requests_key = ""
        self._prev_unread: Dict[str, int] = {}

    async def run(self, sink: Callable[[LifecycleEvent], None], state: RecipeState) -> None:
        while True:
            page = await self._browser_manager.new_page()
            try:
                await self._tick(sink, state, page)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("linkedin recipe tick error: %s", exc, exc_info=True)
            finally:
                try:
                    await page.close()
                except Exception:
                    pass
            await asyncio.sleep(self._poll)

    async def _tick(self, sink, state: RecipeState, page: Page):
        today = _iso_today()
        items = await self._scrape_conversation_list(sink, state, page)
        await self._scrape_active_thread(sink, state, page)
        await self._scrape_connection_requests(sink, state, page)

    async def _scrape_conversation_list(self, sink, state: RecipeState, page: Page) -> Optional[list]:
        selectors = (
            "li.msg-conversation-listitem",
            ".msg-conversations-container__pillar li",
            ".scaffold-layout__list-container li[data-id]",
            ".msg-conversations-container li",
        )
        rows = await page.query_selector_all(", ".join(selectors))
        if not rows:
            return None

        items = []
        for idx, row in enumerate(rows):
            name_el = _first_query(
                row,
                (
                    ".msg-conversation-listitem__participant-names",
                    ".msg-conversation-card__participant-names",
                    ".msg-conversation-card__title",
                    '[data-control-name="overlay.open_conversation"] span',
                    "h3",
                    "h4",
                ),
            )
            preview_el = _first_query(
                row,
                (
                    ".msg-conversation-card__message-snippet",
                    ".msg-conversation-listitem__message-snippet",
                    ".msg-conversation-card__message-snippet-body",
                    '[class*="conversation-card__message"]',
                    '[class*="message-snippet"]',
                ),
            )
            unread_el = _first_query(
                row,
                (
                    ".notification-badge__count",
                    ".msg-conversation-card__unread-count",
                    '[class*="unread-count"]',
                    '[class*="badge__count"]',
                ),
            )
            time_el = _first_query(
                row,
                (
                    ".msg-conversation-card__time-stamp",
                    ".msg-conversation-listitem__time-stamp",
                    "time",
                    '[class*="time-stamp"]',
                ),
            )
            link_el = _first_query(row, ('a[href*="messaging"]', 'a[href*="conversation"]'))
            href = await link_el.get_attribute("href") if link_el else None
            chat_id = _chat_id_from_href(href)
            name = _text_of(name_el)
            preview = _text_of(preview_el)
            unread_text = _text_of(unread_el)
            unread = int(unread_text) if unread_text.isdigit() else 0
            time_text = _text_of(time_el)
            approx_ms = _parse_relative_ms(time_text) or int(time.time() * 1000)
            if not name and not preview:
                continue
            items.append({
                "chatId": chat_id or f"li:{name or str(idx)}",
                "name": name or None,
                "preview": preview or None,
                "unread": unread,
                "timeText": time_text or None,
                "ts": approx_ms,
            })

        if not items:
            return None

        for item in items:
            prev = self._prev_unread.get(item["chatId"], 0)
            if item["unread"] > 0 and item["unread"] > prev:
                sink(LifecycleEvent(
                    kind="linkedin_notification",
                    payload={
                        "title": f"LinkedIn: {item['name'] or 'New message'}",
                        "body": item["preview"] or "",
                        "tag": f"linkedin:{item['chatId']}",
                        "silent": False,
                    },
                    ts_ms=int(time.time() * 1000),
                    adapter="linkedin",
                ))
            self._prev_unread[item["chatId"]] = item["unread"]

        list_key = json.dumps({
            "n": len(items),
            "first": [f"{x['name']}|{x['preview']}" for x in items[:5]],
        }, ensure_ascii=False, sort_keys=True)
        if list_key == self._last_list_key:
            return items
        self._last_list_key = list_key

        sink(LifecycleEvent(
            kind="linkedin_conversation_list",
            payload={"items": items, "snapshotKey": list_key},
            ts_ms=int(time.time() * 1000),
            adapter="linkedin",
        ))
        for item in items:
            if not item["preview"]:
                continue
            sink(LifecycleEvent(
                kind="linkedin_conversation",
                payload={
                    "chatId": item["chatId"],
                    "chatName": item["name"],
                    "day": today,
                    "messages": [
                        {
                            "from": item["name"],
                            "body": item["preview"],
                            "timestamp": item["ts"],
                            "fromMe": False,
                        }
                    ],
                    "isSeed": False,
                },
                ts_ms=int(time.time() * 1000),
                adapter="linkedin",
            ))
        return items

    async def _scrape_active_thread(self, sink, state: RecipeState, page: Page) -> Optional[dict]:
        m = re.search(r"(?:thread|conversation(?:s)?)[/=]([^/?#&]+)", page.url, re.I)
        if not m:
            return None
        chat_id = m.group(1)
        events = await page.query_selector_all(
            ".msg-s-event-listitem, .msg-s-message-list__event, [class*='s-event-listitem']"
        )
        if not events:
            return None

        msgs = []
        for ev in events:
            body_el = _first_query(
                ev,
                (
                    ".msg-s-event-listitem__body",
                    ".msg-s-event-listitem__message-text",
                    '[class*="event-listitem__body"]',
                ),
            )
            sender_el = _first_query(
                ev,
                (
                    ".msg-s-event-listitem__sender",
                    ".msg-s-event-listitem__author",
                    '[class*="event-listitem__sender"]',
                ),
            )
            time_el = _first_query(
                ev,
                (
                    ".msg-s-message-list-content__timestamp",
                    "time",
                    '[class*="timestamp"]',
                ),
            )
            body = _text_of(body_el)
            if not body:
                continue
            sender = _text_of(sender_el)
            time_attr = await time_el.get_attribute("datetime") if time_el else None
            ts_ms = None
            if time_attr:
                try:
                    dt = datetime.fromisoformat(time_attr.replace("Z", "+00:00"))
                    ts_ms = int(dt.timestamp() * 1000)
                except Exception:
                    ts_ms = None
            from_me = bool(await ev.evaluate(
                "e => e.classList.contains('msg-s-event-listitem--own-message') || e.querySelector('[class*=\"own-message\"]') !== null"
            ))
            msgs.append({
                "from": sender or None,
                "body": body,
                "timestamp": int(ts_ms / 1000) if ts_ms else None,
                "fromMe": from_me,
            })

        if not msgs:
            return None
        last_body = msgs[-1]["body"][:40]
        thread_key = json.dumps({"chatId": chat_id, "count": len(msgs), "last": last_body}, ensure_ascii=False, sort_keys=True)
        if thread_key == self._last_thread_key:
            return {"chatId": chat_id, "msgs": msgs}
        self._last_thread_key = thread_key

        sink(LifecycleEvent(
            kind="linkedin_active_thread",
            payload={"chatId": chat_id, "chatName": None, "day": today, "messages": msgs, "isSeed": True},
            ts_ms=int(time.time() * 1000),
            adapter="linkedin",
        ))
        return {"chatId": chat_id, "msgs": msgs}

    async def _scrape_connection_requests(self, sink, state: RecipeState, page: Page) -> Optional[list]:
        href = page.url
        if "invitation" not in href and "mynetwork" not in href:
            return None
        cards = await page.query_selector_all(
            ".invitation-card, [data-view-name='manage-received-invitation'], [class*='invitation-card']"
        )
        if not cards:
            return None
        requests = []
        for card in cards:
            name_el = _first_query(
                card,
                (
                    ".invitation-card__title",
                    "h3",
                    '[class*="invitation-card__title"]',
                ),
            )
            subtitle_el = _first_query(
                card,
                (
                    ".invitation-card__subtitle",
                    '[class*="invitation-card__subtitle"]',
                ),
            )
            name = _text_of(name_el)
            if not name:
                continue
            requests.append({"name": name, "subtitle": _text_of(subtitle_el) or None})
        if not requests:
            return None
        requests_key = json.dumps([r["name"] for r in requests], ensure_ascii=False, sort_keys=True)
        if requests_key == self._last_requests_key:
            return requests
        self._last_requests_key = requests_key
        sink(LifecycleEvent(
            kind="linkedin_requests",
            payload={"requests": requests},
            ts_ms=int(time.time() * 1000),
            adapter="linkedin",
        ))
        return requests
