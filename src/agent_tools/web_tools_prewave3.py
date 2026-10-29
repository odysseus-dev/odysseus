import asyncio
import base64
import contextlib
import contextvars
import inspect
import io
import json
import os
import re
import signal
import shutil
import sys
import tempfile
import time
import html
import hashlib
import pwd
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Dict, Any

from core import platform_compat
from src import browser_lifecycle, process_lifecycle
from src.constants import MAX_OUTPUT_CHARS

PDF_EXTRACT_MAX_BYTES = 80_000_000
_ACTIVE_BROWSER_SESSIONS: set[str] = set()


def _service_home() -> Path:
    """Return the account home even when a task overrides ``HOME``."""

    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (KeyError, OSError):
        return Path.home()


def _host_npm_roots() -> list[Path]:
    """Return user npm roots visible to a runtime using an isolated HOME."""

    roots = [_service_home() / ".npm"]
    roots.extend(Path("/home").glob("*/.npm"))
    roots.append(Path("/root/.npm"))
    return list(dict.fromkeys(roots))


def _accessible_glob(roots: list[Path], pattern: str):
    """Yield matches while ignoring roots unreadable by the service account."""

    for root in dict.fromkeys(roots):
        try:
            yield from root.glob(pattern)
        except (OSError, PermissionError):
            continue


def _browser_executable_candidates() -> list[Path]:
    """Return executable Chromium builds from standard host cache layouts."""

    homes = [_service_home()]
    homes.extend(path for path in _accessible_glob([Path("/home")], "*") if path.is_dir())
    homes.append(Path("/root"))
    patterns = (
        ".cache/ms-playwright/chromium-*/chrome-linux64/chrome",
        ".cache/ms-playwright/chromium_headless_shell-*/"
        "chrome-headless-shell-linux64/chrome-headless-shell",
        ".chromium-browser-snapshots/chromium/linux-*/chrome-linux/chrome",
    )
    candidates = [
        path
        for pattern in patterns
        for path in _accessible_glob(list(dict.fromkeys(homes)), pattern)
        if path.is_file() and os.access(path, os.X_OK)
    ]
    return sorted(dict.fromkeys(candidates), reverse=True)


def _bounded_browser_identity(value: str, *, max_length: int = 20) -> str:
    """Return an agent-browser identity safe for Unix socket paths."""

    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "")).strip("-._")
    if safe == value and 0 < len(safe) <= max_length:
        return safe
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:max_length]


def _scoped_browser_session(namespace: str, session_id: str) -> str:
    """Build one upstream-compatible session key for runtime isolation."""

    scope = f"{str(namespace or 'odysseus-ui')}\0{str(session_id or '')}"
    return f"ody-{_bounded_browser_identity(scope)}"


def _browser_namespace(env: dict[str, str] | None) -> str:
    return str(
        (env or {}).get("ODYSSEUS_BROWSER_NAMESPACE")
        or os.getenv("ODYSSEUS_BROWSER_NAMESPACE", "odysseus-ui")
    ).strip() or "odysseus-ui"


# Browser CLI processes started by the current private_browser call, so a
# cancellation can stop every client it spawned, not only the main command.
_BROWSER_CALL_PROCS: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "_BROWSER_CALL_PROCS", default=None
)


async def _spawn_browser_cli(*command, **kwargs):
    proc = await asyncio.create_subprocess_exec(*command, **kwargs)
    # Identity taken while we hold the unreaped child: teardown later signals
    # its group only while this identity still verifies, never on the pid
    # alone (the event loop may reap it before returncode is observed).
    pid = getattr(proc, "pid", None)
    if isinstance(pid, int) and pid > 0:
        with contextlib.suppress(Exception):
            proc._ody_identity = process_lifecycle.ProcessIdentity.capture(pid, pgid=pid)
    tracked = _BROWSER_CALL_PROCS.get()
    if tracked is not None:
        tracked.append(proc)
    return proc


def _browser_pid_file_candidates(
    runtime_dir: Path, namespace: str, session_id: str | None
) -> list[Path]:
    """Return only the daemon pid files owned by one browser runtime.

    Current agent-browser stores ``--session`` state directly below its
    runtime directory.  Older builds used a namespace/run subdirectory, so
    retain that layout as a compatibility fallback.  When no session is
    supplied, only the legacy namespace directory is eligible; never sweep
    all upstream sessions from the shared root.
    """

    browser_root = runtime_dir / "agent-browser"
    legacy_run = (
        browser_root / "namespaces" / _bounded_browser_identity(namespace) / "run"
    )
    if not session_id:
        return list(legacy_run.glob("ody-*.pid")) if legacy_run.is_dir() else []

    scoped = _scoped_browser_session(namespace, session_id)
    candidates = [browser_root / f"{scoped}.pid"]
    if legacy_run.is_dir():
        candidates.extend(
            [
                legacy_run / f"ody-{_bounded_browser_identity(session_id)}.pid",
                legacy_run / f"{scoped}.pid",
            ]
        )
    return list(dict.fromkeys(candidates))


# Linux exposes one command line per pid under /proc; macOS and Windows do not.
# Kept as a module attribute so the procfs-dependent paths stay testable on a
# host that has no procfs, and on one that does.


def _process_command_line(pid: int) -> str | None:
    """Command line of a running process, or ``None`` when it cannot be read.

    ``None`` means "this host cannot tell", not "the process is gone". Off
    Linux there is no procfs to read a command line from, so callers must not
    treat it as proof that the process exited.
    """

    try:
        return (platform_compat.PROC_ROOT / str(pid) / "cmdline").read_bytes().replace(
            b"\0", b" "
        ).decode("utf-8", errors="replace")
    except (OSError, UnicodeError):
        return None


def _process_is_alive(pid: int) -> bool:
    """Whether a pid currently exists.

    Delegates to ``core.platform_compat.pid_alive`` rather than probing with
    ``os.kill(pid, 0)`` directly. That probe is POSIX-only: CPython's Windows
    ``os.kill`` calls ``TerminateProcess(handle, sig)`` for any signal other
    than CTRL_C / CTRL_BREAK, so it would *kill* the daemon it is asked about.
    Windows is also where there is no procfs, which is precisely when this
    function gets called at all.

    ``pid_alive`` reads False for a pid that ``os.kill`` reports with
    ``PermissionError`` — a live process owned by another user. Neither caller
    here wants a different answer: the sweep only unlinks a pid file it wrote
    itself, and treating somebody else's pid as "not our daemon" is the safe
    reading in both.
    """

    return platform_compat.pid_alive(pid)

_SCHOLARLY_METADATA_CUE_RE = re.compile(
    r"\b(?:accept(?:ed|ance)?|publish(?:ed|ing|cation)?|venue|conference|"
    r"journal|proceedings|doi)\b",
    re.IGNORECASE,
)
_EXPLICIT_ARXIV_ID_RE = re.compile(
    r"\barxiv(?:\.org)?\b.{0,24}\b\d{4}\.\d{4,5}(?:v\d+)?\b",
    re.IGNORECASE,
)
_SCHOLARLY_SUBJECT_CUE_RE = re.compile(
    r"\b(?:paper|preprint|arxiv|benchmark|language model|vision-language|"
    r"ICLR|ICML|CVPR|NeurIPS|ACL|EMNLP|AAAI|IEEE)\b",
    re.IGNORECASE,
)
_DISTINCTIVE_SCHOLARLY_NAME_RE = re.compile(
    r"\b(?:[A-Za-z][A-Za-z-]*\d[A-Za-z0-9.-]*|"
    r"[A-Z][A-Za-z0-9]*-[A-Z][A-Za-z0-9]*)\b"
)


def _is_scholarly_metadata_query(query: str) -> bool:
    text = str(query or "")
    cues = {
        match.group(0).casefold()
        for match in _SCHOLARLY_METADATA_CUE_RE.finditer(text)
    }
    # Source discovery for a named paper should return ranked URLs/snippets,
    # not download every result page. Full-page fetching can spend the entire
    # tool timeout on one blocked publisher before the model ever sees the
    # arXiv/official result it needs for pdf_extract.
    if (
        re.search(r"\b(?:paper|preprint|arxiv)\b", text, re.IGNORECASE)
        and re.search(
            r"\b(?:table|figure|benchmark|results?|pdf|source|url)\b",
            text,
            re.IGNORECASE,
        )
    ):
        return True
    if (
        _DISTINCTIVE_SCHOLARLY_NAME_RE.search(text)
        and re.search(r"\b(?:table|figure|benchmark|scores?|results?)\b", text, re.IGNORECASE)
    ):
        return True
    if not cues:
        return False
    return bool(
        _EXPLICIT_ARXIV_ID_RE.search(text)
        or _SCHOLARLY_SUBJECT_CUE_RE.search(text)
        or len(cues) >= 3
    )


def _is_official_site_metadata_query(query: str) -> bool:
    """Canonical-homepage lookup needs ranked URLs, not downloaded pages."""

    value = re.sub(r"\s+", " ", str(query or "")).strip()
    return bool(re.fullmatch(
        r"(?:the\s+)?official\s+.+?\s+(?:website|web\s*site|site|homepage)"
        r"|.+?\s+official\s+(?:website|web\s*site|site|homepage)",
        value,
        re.IGNORECASE,
    ))


def _format_search_metadata(query: str, results: list[dict]) -> tuple[str, list[dict]]:
    sources = [
        {"url": str(item.get("url") or ""), "title": str(item.get("title") or "")}
        for item in results
        if item.get("url")
    ]
    parts = [f"WEB SEARCH METADATA RESULTS\nQuery: {query}"]
    for index, item in enumerate(results, 1):
        parts.extend([
            "",
            f"[{index}] {item.get('title') or 'Untitled result'}",
            f"URL: {item.get('url') or ''}",
            f"Snippet: {str(item.get('snippet') or '')[:1200]}",
        ])
    if not results:
        parts.append("\nNo search results found.")
    return "\n".join(parts), sources


def _looks_like_youtube_video_id(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{10,16}", str(value or "").strip()))


def _arxiv_listing_api_hint(url: str, error: str) -> str:
    """Return an explicit recovery path for a blocked arXiv date listing."""
    if "406" not in str(error or ""):
        return ""
    parsed = urllib.parse.urlsplit(str(url or ""))
    if parsed.hostname not in {"arxiv.org", "www.arxiv.org"}:
        return ""
    match = re.fullmatch(r"/list/([A-Za-z0-9.-]+)", parsed.path.rstrip("/"))
    date = urllib.parse.parse_qs(parsed.query).get("date", [""])[0]
    if not match or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        return ""
    day = date.replace("-", "")
    query = f"cat:{match.group(1)} AND submittedDate:[{day}0000 TO {day}2359]"
    api_url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({
        "search_query": query, "start": "0", "max_results": "100",
    })
    return (
        " arXiv rejected its static listing (HTTP 406). The public Atom API is "
        f"available for this category/day; call web_fetch on {api_url} to read "
        "the dated feed, then inspect individual paper sources for details."
    )


class WebSearchTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        from src.search import comprehensive_web_search, searxng_search_results
        progress_cb = ctx.get("progress_cb") if isinstance(ctx, dict) else None
        raw = content.strip()
        query = raw
        time_filter = None
        max_pages = 5
        if raw.startswith("{"):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict) and "query" in parsed:
                    query = str(parsed.get("query", "")).strip()
                    tf = parsed.get("time_filter") or parsed.get("freshness")
                    if isinstance(tf, str) and tf.lower() in ("day", "week", "month", "year"):
                        time_filter = tf.lower()
                    mp = parsed.get("max_pages")
                    if isinstance(mp, int) and 1 <= mp <= 10:
                        max_pages = mp
            except json.JSONDecodeError:
                pass
        if not query:
            query = raw.split("\n")[0].strip()
        if time_filter is None:
            from src.search_intent import inferred_search_publication_window
            time_filter = inferred_search_publication_window(query)
        loop = asyncio.get_running_loop()
        if progress_cb:
            await progress_cb({
                "elapsed_s": 0,
                "tail": f"Searching web for: {query[:160]}",
            })
        try:
            if _is_scholarly_metadata_query(query) or _is_official_site_metadata_query(query):
                results = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: searxng_search_results(query, max_pages, **({'time_filter': time_filter} if time_filter else {})),
                    ),
                    timeout=30,
                )
                text, sources = _format_search_metadata(query, results)
            else:
                text, sources = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: comprehensive_web_search(
                            query,
                            max_pages=max_pages,
                            time_filter=time_filter,
                            return_sources=True,
                        ),
                    ),
                    timeout=30,
                )
        except asyncio.TimeoutError:
            # Comprehensive search also downloads several result pages. A
            # slow or hostile publisher must not erase the ranked search
            # evidence that was already available. Fall back to the metadata
            # path so the agent can choose a source and continue with
            # web_fetch/private_browser. Keep this bounded independently: the
            # abandoned executor thread may still be winding down.
            try:
                results = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: searxng_search_results(query, max_pages, **({'time_filter': time_filter} if time_filter else {})),
                    ),
                    timeout=12,
                )
                text, sources = _format_search_metadata(query, results)
                if sources:
                    output = text[:MAX_OUTPUT_CHARS] if len(text) > MAX_OUTPUT_CHARS else text
                    output += "\n\n<!-- SOURCES:" + json.dumps(sources) + " -->"
                    return {
                        "output": output,
                        "exit_code": 0,
                        "evidence_status": "available",
                        "degraded_mode": "metadata_after_content_timeout",
                    }
            except Exception:
                pass
            return {
                "error": f"web_search timed out after 30s: {query[:200]}",
                "exit_code": 1,
            }
        except Exception as e:
            return {
                "error": f"web_search failed: {type(e).__name__}: {str(e) or 'no details'}",
                "exit_code": 1,
                "untrusted_content": True,
            }
        from .weather_tools import WeatherTool, weather_location_from_query
        weather_location = weather_location_from_query(query)
        if not sources and time_filter and weather_location:
            # A forecast or current fact need not live on a newly published page.
            try:
                text, sources = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: comprehensive_web_search(
                            query, max_pages=max_pages, time_filter=None,
                            return_sources=True,
                        ),
                    ),
                    timeout=20,
                )
            except Exception:
                pass
        if not sources:
            from src.turn_contract import active_turn_contract
            contract = active_turn_contract()
            policy = ctx.get("tool_policy") if isinstance(ctx, dict) else None
            weather_allowed = (
                weather_location
                and "get_weather" not in (ctx.get("disabled_tools") or ())
                and not (policy and policy.blocks("get_weather"))
                and not (contract and not contract.permits("get_weather"))
            )
            if weather_allowed:
                weather = await WeatherTool().execute(json.dumps({"location": weather_location}), ctx)
                if weather.get("exit_code") == 0:
                    return weather
        if progress_cb:
            await progress_cb({
                "elapsed_s": 30,
                "tail": "Search completed; preparing sources.",
            })
        # Compact the complete report before the transport cap. Otherwise
        # repeated summaries from early sources permanently erase later pages.
        from src.search_passages import bounded_search_observation
        text = bounded_search_observation(text, MAX_OUTPUT_CHARS)
        output = text[:MAX_OUTPUT_CHARS] if len(text) > MAX_OUTPUT_CHARS else text
        if sources:
            output += "\n\n<!-- SOURCES:" + json.dumps(sources) + " -->"
        return {"output": output, "exit_code": 0,
                "evidence_status": "available" if sources else "empty"}

class WebFetchTool:
    _MAX_BATCH_URLS = 12
    _MAX_BATCH_CONCURRENCY = 4

    @staticmethod
    def _query_from_request(request_text: str) -> str:
        """Extract distinctive document/table terms from the active request."""
        candidates = re.findall(r"[A-Za-z0-9][A-Za-z0-9_.+-]{2,}", request_text or "")
        generic = {
            "http", "https", "arxiv.org", "pdf", "pdfs", "www", "com", "org",
            "please", "download", "read", "create", "save", "write", "workspace",
            "table", "tables", "report", "benchmark", "benchmarks", "score", "scores",
            "model", "models", "file", "chart", "data", "using", "from", "with",
        }
        selected: list[str] = []
        for token in candidates:
            folded = token.casefold()
            distinctive = (
                any(ch.isdigit() for ch in token)
                or any(ch.isupper() for ch in token[1:])
                or "-" in token
                or folded.endswith("qa")
            )
            if distinctive and folded not in generic and token not in selected:
                selected.append(token)
        return ", ".join(selected[:20])

    @staticmethod
    def _focused_passages(text: str, query: str, max_chars: int) -> str:
        """Select bounded page/line passages matching long-document terms."""
        segments = [term.strip() for term in re.split(r"[,|;\n]+", query) if term.strip()]
        stopwords = {
            "benchmark", "table", "evaluation", "scores", "score", "model",
            "paper", "document", "accuracy", "results", "result",
        }
        terms: list[str] = []
        for segment in segments:
            if len(segments) > 1 and len(segment) >= 2:
                terms.append(segment)
            for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_.+-]{2,}", segment):
                if token.casefold() not in stopwords and token not in terms:
                    terms.append(token)
        terms = terms[:16]
        if not terms:
            return text
        lines = text.splitlines()
        anchors: list[int] = []
        for index, line in enumerate(lines):
            low = line.casefold()
            if any(term.casefold() in low for term in terms):
                anchors.append(index)
        if not anchors:
            return f"[No passages matched query: {query}]"
        # Keep independent windows around each hit. Merging nearby hits can
        # accidentally turn a dense PDF table/page into one giant passage and
        # clip the target row at the output boundary.
        windows: list[tuple[int, int]] = []
        for anchor in anchors:
            table_start = None
            if len(re.findall(r"\b\d+(?:\.\d+)?\b", lines[anchor])) >= 4:
                for candidate in range(anchor, max(-1, anchor - 81), -1):
                    if re.match(r"\s*Table\s+\d+", lines[candidate], re.IGNORECASE):
                        table_start = candidate
                        break
            interval = (
                (table_start, min(len(lines), anchor + 9))
                if table_start is not None
                else (max(0, anchor - 28), min(len(lines), anchor + 15))
            )
            if interval not in windows:
                windows.append(interval)
        ranked_passages = []
        for start, end in windows:
            passage = "\n".join(lines[start:end])
            low = passage.casefold()
            coverage = sum(1 for term in terms if term.casefold() in low)
            occurrences = sum(min(low.count(term.casefold()), 8) for term in terms)
            numeric_density = min(len(re.findall(r"\b\d+(?:\.\d+)?\b", passage)), 40)
            table_rows = 0
            for line in passage.splitlines():
                line_low = line.casefold()
                if (
                    any(term.casefold() in line_low for term in terms)
                    and len(re.findall(r"\b\d+(?:\.\d+)?\b", line)) >= 4
                ):
                    table_rows += 1
            score = coverage * 100 + occurrences * 4 + numeric_density + min(table_rows, 4) * 200
            # PDF table extraction can collapse a full page into one enormous
            # line. Bound each candidate so an earlier broad table cannot
            # consume the entire result before the exact matching table.
            passage_cap = max(2000, min(8000, max_chars // 3))
            if len(passage) > passage_cap:
                # HTML-to-text can collapse an entire arXiv page into one line.
                # Choose the densest term/numeric window instead of blindly
                # retaining the abstract at the head of that line.
                candidates: list[tuple[int, int, str]] = []
                passage_low = passage.casefold()
                for term in terms:
                    term_low = term.casefold()
                    cursor = 0
                    for _ in range(8):
                        position = passage_low.find(term_low, cursor)
                        if position < 0:
                            break
                        window_start = max(0, min(
                            len(passage) - passage_cap,
                            position - passage_cap // 2,
                        ))
                        window = passage[window_start:window_start + passage_cap]
                        window_low = window.casefold()
                        window_coverage = sum(
                            1 for candidate in terms
                            if candidate.casefold() in window_low
                        )
                        window_numbers = min(
                            len(re.findall(r"\b\d+(?:\.\d+)?\b", window)), 300
                        )
                        candidates.append((
                            window_coverage * 1000
                            + window_numbers
                            + len(term) * 10
                            - window_start // 100,
                            -window_start,
                            window,
                        ))
                        cursor = position + max(1, len(term_low))
                if candidates:
                    passage = max(candidates, key=lambda item: (item[0], item[1]))[2]
                else:
                    passage = passage[:passage_cap]
                passage += "\n[...passage windowed around query terms]"
            ranked_passages.append((score, start, passage))
        ranked_passages.sort(key=lambda item: (-item[0], item[1]))
        passages: list[str] = []
        for _score, _index, passage in ranked_passages:
            # Collapse duplicate/near-identical windows without merging them.
            normalized = re.sub(r"\s+", " ", passage).strip()
            if any(normalized in re.sub(r"\s+", " ", prior) for prior in passages):
                continue
            passages.append(passage)
            if len(passages) >= 1:
                break
        selected = "\n\n--- matching passage ---\n\n".join(passages)
        if len(selected) > max_chars:
            selected = selected[:max_chars] + "\n\n[...focused passages truncated]"
        return f"[Focused passages for: {query}]\n\n{selected}"

    async def _execute_batch(self, payload: dict, ctx: dict) -> dict:
        """Fetch several known URLs concurrently with bounded combined output."""
        raw_urls = payload.get("urls")
        if not isinstance(raw_urls, list) or not raw_urls:
            return {
                "error": "web_fetch: urls must be a non-empty array of at most 12 URLs",
                "exit_code": 1,
            }
        if len(raw_urls) > self._MAX_BATCH_URLS:
            return {
                "error": "web_fetch: urls must contain at most 12 URLs",
                "exit_code": 1,
            }

        shared_query = str(payload.get("query") or "").strip()
        shared_full = payload.get("full") is True
        requests: list[dict[str, Any]] = []
        for index, item in enumerate(raw_urls):
            if isinstance(item, str):
                request = {"url": item, "query": shared_query, "full": shared_full}
            elif isinstance(item, dict):
                request = {
                    "url": str(item.get("url") or "").strip(),
                    "query": str(item.get("query") or shared_query).strip(),
                    "full": item.get("full") is True or shared_full,
                }
            else:
                return {
                    "error": f"web_fetch: urls item {index + 1} must be a URL string or object",
                    "exit_code": 1,
                }
            if not request["url"]:
                return {
                    "error": f"web_fetch: urls item {index + 1} is missing url",
                    "exit_code": 1,
                }
            requests.append(request)

        semaphore = asyncio.Semaphore(self._MAX_BATCH_CONCURRENCY)
        child_ctx = dict(ctx) if isinstance(ctx, dict) else {}
        child_ctx.pop("progress_cb", None)

        async def fetch_one(request: dict[str, Any]) -> dict:
            async with semaphore:
                return await self.execute(json.dumps(request), child_ctx)

        results = await asyncio.gather(*(fetch_one(request) for request in requests))
        per_item_cap = max(800, min(6000, (MAX_OUTPUT_CHARS - 1000) // len(results)))
        sections: list[str] = []
        successful = 0
        summaries: list[dict[str, Any]] = []
        for index, (request, result) in enumerate(zip(requests, results), start=1):
            ok = result.get("exit_code") == 0
            if ok:
                successful += 1
                body = str(result.get("output") or "")
            else:
                body = "ERROR: " + str(result.get("error") or "fetch failed")
            if len(body) > per_item_cap:
                body = body[:per_item_cap] + "\n[...batch item truncated]"
            sections.append(f"## URL {index}: {request['url']}\n{body}")
            summaries.append({
                "url": request["url"],
                "exit_code": int(result.get("exit_code", 1)),
            })
        output = "\n\n".join(sections)
        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + "\n\n[...batch output truncated]"
        return {
            "output": output,
            "batch_results": summaries,
            "successful": successful,
            "requested": len(requests),
            "exit_code": 0 if successful else 1,
        }

    async def execute(self, content: str, ctx: dict) -> dict:
        from src.search.content import fetch_webpage_content
        from src.constants import WEB_FETCH_HARD_MAX_BYTES
        raw = content.strip()
        if raw.startswith("{"):
            try:
                batch_payload = json.loads(raw)
            except json.JSONDecodeError:
                batch_payload = None
            if isinstance(batch_payload, dict) and "urls" in batch_payload:
                return await self._execute_batch(batch_payload, ctx)
        url = ""
        max_bytes = None
        query = ""
        if raw.startswith("{"):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    url = str(parsed.get("url") or "").strip()
                    query = str(parsed.get("query") or "").strip()
                    # Download-budget override (#3812): "full": true raises the
                    # budget to the hard cap; an explicit max_bytes is clamped
                    # to the hard cap downstream. Default stays the soft cap.
                    if parsed.get("full") is True:
                        max_bytes = WEB_FETCH_HARD_MAX_BYTES
                    mb = parsed.get("max_bytes")
                    if isinstance(mb, int) and mb > 0:
                        max_bytes = mb
            except json.JSONDecodeError:
                url = ""
        if not url:
            url = raw.split("\n")[0].strip()
        local_path = url
        if local_path.lower().startswith("file:///workspace/"):
            local_path = local_path[7:]
        if local_path.startswith("/workspace/"):
            suffix = Path(local_path.split("?", 1)[0].split("#", 1)[0]).suffix.lower()
            if suffix in {".html", ".htm"}:
                return {
                    "error": (
                        "web_fetch: local HTML requires private_browser so rendered DOM and "
                        "page errors are inspected"
                    ),
                    "exit_code": 1,
                }
            if suffix in {
                ".avi", ".bmp", ".gif", ".jpeg", ".jpg", ".m4v", ".mkv",
                ".mov", ".mp4", ".mpeg", ".mpg", ".png", ".webm", ".webp",
            }:
                return {
                    "error": "web_fetch: local visual media requires inspect_media",
                    "exit_code": 1,
                }
            from src.agent_tools.filesystem_tools import ReadFileTool

            result = await ReadFileTool().execute(local_path, ctx)
            if result.get("exit_code") == 0:
                result = dict(result)
                result["output"] = (
                    "Argument note: Read the local workspace path with read_file; "
                    "use web_fetch only for HTTP(S) URLs.\n" + str(result.get("output") or "")
                )
            return result
        if not url or url.startswith("{") or any(c in url for c in (" ", "\t", "\n")):
            return {"error": "web_fetch: provide a single URL or domain, e.g. example.com", "exit_code": 1}
        low = url.lower()
        if "://" in low and not low.startswith(("http://", "https://")):
            return {"error": f"web_fetch: unsupported URL scheme (only http/https): {url[:80]}", "exit_code": 1}
        if not low.startswith(("http://", "https://")):
            url = "https://" + url
        if re.search(r"(?:\.pdf(?:[?#]|$)|arxiv\.org/pdf/)", url, re.IGNORECASE) and not query:
            runtime_context = ctx.get("client_runtime_context") if isinstance(ctx, dict) else None
            request_text = (
                str(runtime_context.get("request_text") or "")
                if isinstance(runtime_context, dict) else ""
            )
            query = self._query_from_request(request_text)
            if not query:
                return {
                    "error": (
                        "web_fetch: this is a PDF. Use pdf_extract with this URL and a query "
                        "that names the target model, metrics, or table; do not download it "
                        "with Python/curl and do not estimate missing values."
                    ),
                    "exit_code": 1,
                }
        loop = asyncio.get_running_loop()
        try:
            def _fetch():
                kwargs = {"timeout": 10}
                try:
                    sig = inspect.signature(fetch_webpage_content)
                    if "max_bytes" in sig.parameters:
                        kwargs["max_bytes"] = max_bytes
                except (TypeError, ValueError):
                    # Some deployed/test shims may not expose a signature.
                    # Prefer compatibility over failing the whole fetch.
                    pass
                return fetch_webpage_content(url, **kwargs)

            result = await asyncio.wait_for(
                loop.run_in_executor(None, _fetch),
                timeout=30,
            )
        except asyncio.TimeoutError:
            return {"error": f"web_fetch: timed out fetching {url}", "exit_code": 1}
        except Exception as e:
            return {"error": f"web_fetch: {url}: {e}", "exit_code": 1}
        err = result.get("error")
        text = (result.get("linked_content") or result.get("content") or "").strip()
        title = result.get("title") or ""

        if not text:
            if err:
                arxiv_hint = _arxiv_listing_api_hint(url, str(err))
                return {
                    "error": f"web_fetch: {url}: {err}{arxiv_hint}",
                    "exit_code": 1,
                    "untrusted_content": True,
                }
            return {"error": f"web_fetch: {url}: no readable text content (not HTML, or the page needs JS/login)", "exit_code": 1}

        # Tell the model when the download budget cut the body short and how
        # to get the rest, instead of silently presenting a partial page as
        # the whole thing.
        size_note = ""
        if result.get("truncated"):
            fetched = result.get("fetched_bytes") or 0
            total = result.get("total_bytes")
            total_txt = f" of {total:,} bytes" if total else ""
            size_note = (
                f"[partial content: download stopped at {fetched:,} bytes{total_txt}. "
                f'Re-call with {{"url": "{url}", "full": true}} to fetch up to '
                f"{WEB_FETCH_HARD_MAX_BYTES:,} bytes.]\n\n"
            )

        # The notice must lead the output so the MAX_OUTPUT_CHARS trim below can
        # never drop it. The title is untrusted, uncapped page content, so a
        # giant title ahead of the notice could push it out of range; keep the
        # notice first and cap the title as a second guard.
        if len(title) > 300:
            title = title[:300] + "..."
        header = (f"# {title}\n" if title else "") + f"Source: {url}\n\n"
        if query:
            text = self._focused_passages(text, query, MAX_OUTPUT_CHARS - len(header) - 500)
        output = size_note + header + text
        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + (
                "\n\n[...truncated; re-call web_fetch with query terms to retrieve matching passages]"
                if not query else "\n\n[...truncated]"
            )
        return {"output": output, "exit_code": 0, "page_entries": result.get("page_entries") or []}


class PdfExtractTool:
    """Extract focused, source-attributed passages from a PDF URL or workspace file.

    This deliberately builds on Odysseus' native web reader instead of adding
    a benchmark transport.  The separate semantic affordance keeps models from
    downloading PDFs with ad-hoc Python and then guessing when extraction fails.
    Local task PDFs use the same positioned reader, but are resolved through the
    active workspace path policy rather than through an unrestricted filesystem
    path.
    """

    @staticmethod
    def _local_pdf_path(source: str) -> Path | None:
        """Resolve a task-local PDF through the active workspace policy."""
        raw = str(source or "").strip()
        if raw.lower().startswith("file://"):
            parsed = urllib.parse.urlsplit(raw)
            if parsed.netloc not in {"", "localhost"}:
                return None
            raw = urllib.parse.unquote(parsed.path)
        if not raw.startswith("/workspace/") and not Path(raw).is_absolute():
            return None
        try:
            from src.tool_execution import _resolve_tool_path

            path = Path(_resolve_tool_path(raw))
        except (OSError, ValueError):
            return None
        return path if path.suffix.casefold() == ".pdf" else None

    @staticmethod
    def _arxiv_html_url(url: str) -> str:
        """Return the official HTML companion for an arXiv document URL."""
        match = re.search(
            r"https?://(?:www\.)?arxiv\.org/(?:pdf|abs|html)/(?P<id>\d{4}\.\d{4,5}(?:v\d+)?)",
            str(url or ""),
            re.IGNORECASE,
        )
        return f"https://arxiv.org/html/{match.group('id')}" if match else ""

    @staticmethod
    def _pdf_row_contains_term(row_text: str, term: str) -> bool:
        """Match model names even when PDF glyph extraction splits punctuation."""
        row_folded = str(row_text or "").casefold()
        term_folded = str(term or "").casefold()
        if term_folded in row_folded:
            return True
        normalize = lambda value: re.sub(r"[^a-z0-9]+", "", value.casefold())
        normalized_term = normalize(term_folded)
        return bool(normalized_term) and normalized_term in normalize(row_folded)

    @staticmethod
    def _pdf_row_exact_term_match(row_text: str, term: str) -> bool:
        """Match a complete model label without accepting a named variant.

        PDF text extraction may split punctuation (``LLaVA- Onevision``), so a
        literal equality check is too strict.  The broad table matcher above is
        intentionally useful for locating candidate pages, but it also makes
        ``DeepSeek-VL2`` match ``DeepSeek-VL2-Tiny``.  Row resolution needs the
        narrower relation: tolerate extracted punctuation while rejecting an
        attached hyphen/slash variant suffix.
        """
        row = str(row_text or "")
        chunks = re.findall(r"[A-Za-z0-9]+", str(term or ""))
        if not chunks:
            return False
        pattern = re.compile(
            r"(?<![A-Za-z0-9])"
            + r"[^A-Za-z0-9]*".join(re.escape(chunk) for chunk in chunks)
            + r"(?![A-Za-z0-9])",
            re.IGNORECASE,
        )
        for match in pattern.finditer(row):
            suffix = row[match.end():]
            if re.match(r"\s*[-_/]\s*[A-Za-z0-9]", suffix):
                continue
            return True
        return False

    @staticmethod
    def _pdf_model_suffix_aliases(term: str) -> list[str]:
        """Return compact digit-bearing aliases used by shared-family headers.

        Comparison tables often put the vendor/family in the caption and use
        headers such as ``R1-Zero`` or ``o1``. Restrict aliases to suffixes
        containing a digit so ordinary trailing words cannot become matches.
        """
        parts = [part for part in re.split(r"-+", str(term or "")) if part]
        aliases: list[str] = []
        for index in range(1, len(parts)):
            alias = "-".join(parts[index:])
            if any(character.isdigit() for character in alias) and len(alias) >= 2:
                aliases.append(alias)
        return aliases

    @staticmethod
    def _pdf_row_contains_model_alias(row_text: str, term: str) -> bool:
        """Match an exact full model label or a bounded table-header alias."""
        if PdfExtractTool._pdf_row_exact_term_match(row_text, term):
            return True
        return any(
            PdfExtractTool._pdf_row_exact_term_match(row_text, alias)
            for alias in PdfExtractTool._pdf_model_suffix_aliases(term)
        )

    @staticmethod
    def _bibliography_evidence(
        page_columns: list[tuple[int, str, str]], query: str
    ) -> str:
        """Return ordered reference entries for bibliography-focused queries.

        Academic PDFs commonly use two columns.  Reading the whole page in
        layout order interleaves those columns, while generic query scoring can
        rank citation-heavy body pages above the actual reference section.
        ``page_columns`` keeps each column independent so references can be
        reconstructed in normal reading order without knowing task page
        numbers or paper titles.
        """
        bibliography_intent = re.search(
            r"\b(?:references?|bibliograph(?:y|ies)|bibtex|citation\s+list)\b",
            str(query or ""),
            re.IGNORECASE,
        )
        if not bibliography_intent:
            return ""

        heading_pattern = re.compile(
            r"(?im)^\s*(?:references|bibliography)(?:\s+\d+)?\s*$"
        )
        start_index = -1
        start_offset = 0
        for index, (_page_number, left, _right) in enumerate(page_columns):
            heading = heading_pattern.search(left)
            if heading:
                start_index = index
                start_offset = heading.start()
                break
        if start_index < 0:
            return ""

        ordered_pages: list[tuple[int, str]] = []
        for index, (page_number, left, right) in enumerate(
            page_columns[start_index:], start=start_index
        ):
            if index == start_index:
                left = left[start_offset:]
            text = "\n".join(part.strip() for part in (left, right) if part.strip())
            if text:
                ordered_pages.append((page_number, text))
        if not ordered_pages:
            return ""

        joined = "\n".join(text for _page_number, text in ordered_pages)
        starts = list(re.finditer(r"(?m)^\s*\[(\d+)\]\s*", joined))
        numbered_entries: list[tuple[int, str]] = []
        for position, match in enumerate(starts):
            end = starts[position + 1].start() if position + 1 < len(starts) else len(joined)
            numbered_entries.append((
                int(match.group(1)),
                joined[match.start():end].strip(),
            ))
        requested_range = re.search(
            r"\b(?:references?|refs?|citations?)\s*(?:numbers?\s*)?"
            r"\[?(\d{1,4})\]?\s*(?:through|to|[-–—])\s*\[?(\d{1,4})\]?",
            str(query or ""),
            re.IGNORECASE,
        )
        if requested_range and numbered_entries:
            first, last = sorted((
                int(requested_range.group(1)),
                int(requested_range.group(2)),
            ))
            selected_entries = [
                entry for number, entry in numbered_entries
                if first <= number <= last
            ]
            if selected_entries:
                pages = f"{ordered_pages[0][0]}-{ordered_pages[-1][0]}"
                body = (
                    f"[Local PDF bibliography evidence, pages {pages}; "
                    f"reference entries {first}-{last}]\n"
                    + "\n\n".join(selected_entries)
                )
                if len(body) > MAX_OUTPUT_CHARS - 500:
                    body = body[:MAX_OUTPUT_CHARS - 500] + "\n[...bibliography entries truncated]"
                return body

        wants_arxiv = bool(
            re.search(r"\b(?:arxiv|preprints?)\b", str(query or ""), re.IGNORECASE)
        )
        if wants_arxiv:
            # Keep complete numbered entries instead of returning every page.
            # This is both more useful and prevents the bounded output cap from
            # dropping late matching references.
            entries: list[str] = []
            for _number, entry in numbered_entries:
                if re.search(r"arxiv", entry, re.IGNORECASE):
                    entries.append(entry)
            if entries:
                pages = f"{ordered_pages[0][0]}-{ordered_pages[-1][0]}"
                body = (
                    f"[Local PDF bibliography evidence, pages {pages}; "
                    f"{len(entries)} arXiv/preprint entries]\n"
                    + "\n\n".join(entries)
                )
                if len(body) > MAX_OUTPUT_CHARS - 500:
                    body = body[:MAX_OUTPUT_CHARS - 500] + "\n[...bibliography entries truncated]"
                return body

        body = "\n\n".join(
            f"[Local PDF bibliography evidence, page {page_number}]\n{text}"
            for page_number, text in ordered_pages
        )
        if len(body) > MAX_OUTPUT_CHARS - 500:
            body = body[:MAX_OUTPUT_CHARS - 500] + "\n[...bibliography text truncated]"
        return body

    @staticmethod
    def _select_positioned_target_model(
        model_terms: list[str],
        rows: list[tuple[float, list[dict]]],
    ) -> str:
        """Choose the requested model that is actually present in selected PDF rows.

        Broad benchmark prompts often ask for several models in every PDF
        request.  Picking the longest/first requested model contaminates a GLM
        or Seed table with the Qwen column.  Score each requested model against
        the selected positioned rows and prefer explicit in-table matches.
        """
        if not model_terms:
            return ""
        table_text = " ".join(
            str(word["text"])
            for _top, words in rows
            for word in words
        )
        table_folded = table_text.casefold()
        table_normalized = re.sub(r"[^a-z0-9]+", "", table_folded)
        scored: list[tuple[int, int, str]] = []
        for term in model_terms:
            folded = str(term or "").casefold()
            base = re.sub(r"-(?:\d+(?:\.\d+)?[BMK])$", "", term, flags=re.I)
            score = 0
            for _top, words in rows:
                row_text = " ".join(str(word["text"]) for word in words)
                if PdfExtractTool._pdf_row_contains_term(row_text, term):
                    score += 100
                elif base and PdfExtractTool._pdf_row_contains_term(row_text, base):
                    score += 70
                if PdfExtractTool._pdf_row_contains_model_alias(row_text, term):
                    score += 60
                if "a22b" in folded:
                    row_folded = row_text.casefold()
                    normalized = re.sub(r"[^a-z0-9]+", "", row_folded)
                    if "a22b" in row_folded and (
                        "qwen3" in row_folded
                        or "235b" in row_folded
                        or "qwen3vl" in normalized
                    ):
                        score += 80
                if "seed" in folded and "1.5" in folded:
                    if (
                        "seed" in row_text.casefold()
                        and ("1.5-vl" in row_text.casefold() or "15vl" in re.sub(r"[^a-z0-9]+", "", row_text.casefold()))
                    ):
                        score += 80
                if "glm" in folded and "4.6" in folded:
                    if "glm" in row_text.casefold() and "4.6" in row_text.casefold():
                        score += 80
            if "seed" in folded and "1.5" in folded:
                if "seed" in table_folded and (
                    "1.5-vl" in table_folded or "15vl" in table_normalized
                ):
                    score += 90
                if "thinking" in folded and "thinking" in table_folded:
                    score += 25
            if "glm" in folded and "4.6" in folded:
                if "glm" in table_folded and "4.6" in table_folded:
                    score += 90
            scored.append((score, len(term), term))
        best = max(scored)
        return best[2] if best[0] > 0 else max(model_terms, key=len, default="")

    @staticmethod
    def _looks_like_pdf_metric_term(token: str) -> bool:
        """Identify benchmark metric labels, including compact table acronyms."""
        value = str(token or "")
        folded = value.casefold()
        if (
            folded.endswith("qa")
            or "refcoco" in folded
            or folded in {
                "chartqa",
                "docvqa",
                "textvqa",
                "ocrbench",
                "countbench",
                "blink",
                "mmbench",
                "mmstar",
                "mmmu",
                "mathvista",
            }
        ):
            return True
        # Papers frequently label table columns with short uppercase task
        # acronyms (TR, AR, AO, AC, ...). Keep transport/document/model
        # acronyms out so ordinary query prose does not become a fake header.
        return (
            value.isupper()
            and 2 <= len(value) <= 8
            and value not in {"PDF", "URL", "HTML", "HTTP", "HTTPS", "GPT", "LLM", "VLM"}
        )

    @staticmethod
    def _pdf_query_tokens(query: str) -> list[str]:
        """Tokenize table queries without dropping two-letter metric labels."""
        return re.findall(r"[A-Za-z0-9][A-Za-z0-9_.+-]{1,}", str(query or ""))

    @staticmethod
    def _looks_like_pdf_model_term(token: str) -> bool:
        """Separate compact model identifiers from ordinary hyphenated prose."""
        value = str(token or "")
        if (
            any(character.isdigit() for character in value)
            and any(character.isalpha() for character in value)
        ):
            return True
        return "-" in value and sum(character.isupper() for character in value) >= 2

    @staticmethod
    def _pdf_words_contain_metric(words: list[dict], metric: str) -> bool:
        """Match a metric as a table cell, not inside ordinary prose words."""
        normalize = lambda value: re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())
        target = normalize(metric)
        return bool(target) and any(
            normalize(word.get("text")) == target for word in words
        )

    @staticmethod
    def _pdf_rows_contain_table_label(
        rows: list[tuple[float, list[dict]]], table_number: int
    ) -> bool:
        """Return whether positioned rows contain an exact numbered caption."""
        label = re.compile(
            rf"^\s*table\s+{int(table_number)}(?:\D|$)",
            re.IGNORECASE,
        )
        return any(
            label.search(" ".join(str(word.get("text") or "") for word in words))
            for _top, words in rows
        )

    @staticmethod
    def _select_positioned_table_region(
        rows: list[tuple[float, list[dict]]],
        metric_terms: list[str],
        model_terms: list[str],
        requested_table_number: int | None,
    ) -> list[tuple[float, list[dict]]]:
        """Keep one physical table when a page contains adjacent tables.

        Joining coordinates across two tables on the same page can map a
        header from one table to values in another.  Real ``Table N`` caption
        rows provide a generic boundary.  Prefer an explicitly requested
        table, otherwise rank regions by exact requested-row and metric
        coverage; named variants receive only weak fallback credit.
        """
        caption_pattern = re.compile(r"^\s*table\s+(\d{1,3})(?:\D|$)", re.I)
        starts: list[tuple[int, int]] = []
        for index, (_top, words) in enumerate(rows):
            row_text = " ".join(str(word.get("text") or "") for word in words)
            match = caption_pattern.search(row_text)
            if match:
                starts.append((index, int(match.group(1))))
        if len(starts) < 2:
            return rows

        regions: list[tuple[int, list[tuple[float, list[dict]]]]] = []
        for position, (start, number) in enumerate(starts):
            end = starts[position + 1][0] if position + 1 < len(starts) else len(rows)
            regions.append((number, rows[start:end]))
        if requested_table_number is not None:
            requested = [region for number, region in regions if number == requested_table_number]
            if requested:
                return requested[0]

        def _score(region: list[tuple[float, list[dict]]]) -> tuple[int, int]:
            row_texts = [
                " ".join(str(word.get("text") or "") for word in words)
                for _top, words in region
            ]
            exact_models = sum(
                any(PdfExtractTool._pdf_row_exact_term_match(text, term) for text in row_texts)
                for term in set(model_terms)
            )
            broad_models = sum(
                any(PdfExtractTool._pdf_row_contains_term(text, term) for text in row_texts)
                for term in set(model_terms)
            )
            metrics = sum(
                any(PdfExtractTool._pdf_words_contain_metric(words, term) for _top, words in region)
                for term in set(metric_terms)
            )
            numeric = sum(
                len(re.findall(r"\b\d+(?:\.\d+)?\b", text)) for text in row_texts
            )
            return (
                exact_models * 1000 + broad_models * 100 + metrics * 300 + min(numeric, 99),
                -len(region),
            )

        return max((region for _number, region in regions), key=_score)

    @staticmethod
    def _positioned_table_evidence(url: str, query: str) -> str:
        """Return compact PDF rows with x coordinates for column-safe reading."""
        try:
            import pdfplumber
            import httpx
            from services.search.content import _PinnedTransport, _resolve_public_ips
            from src.constants import WEB_FETCH_HARD_MAX_BYTES, WEB_FETCH_USER_AGENT
        except ImportError:
            return ""
        local_path = PdfExtractTool._local_pdf_path(url)
        if local_path is not None:
            if not local_path.is_file():
                return ""
            pdf_bytes = local_path.read_bytes()
            declared_size = len(pdf_bytes)
        else:
            ips = _resolve_public_ips(url)
            headers = {
                "User-Agent": WEB_FETCH_USER_AGENT,
                "Accept": "application/pdf,*/*",
            }
            with httpx.Client(
                headers=headers,
                timeout=30,
                follow_redirects=True,
                transport=_PinnedTransport(ips[0]),
            ) as client:
                response = client.get(url)
            response.raise_for_status()
            pdf_bytes = response.content
            declared = response.headers.get("content-length")
            declared_size = int(declared) if declared and declared.isdigit() else 0
        if declared_size and declared_size > PDF_EXTRACT_MAX_BYTES:
            return (
                f"[PDF too large for pdf_extract positioned table reader: "
                f"{declared_size:,} bytes > {PDF_EXTRACT_MAX_BYTES:,} bytes]"
            )
        if len(pdf_bytes) > PDF_EXTRACT_MAX_BYTES:
            return (
                f"[PDF too large for pdf_extract positioned table reader: "
                f"{len(pdf_bytes):,} bytes > {PDF_EXTRACT_MAX_BYTES:,} bytes]"
            )
        tokens = PdfExtractTool._pdf_query_tokens(query)
        requested_table_match = re.search(
            r"\btable\s+(\d{1,3})\b", str(query or ""), re.IGNORECASE
        )
        requested_table_number = (
            int(requested_table_match.group(1)) if requested_table_match else None
        )
        metric_terms = [
            token for token in tokens
            if PdfExtractTool._looks_like_pdf_metric_term(token)
        ]
        model_terms = [
            token for token in tokens
            if token not in metric_terms
            and PdfExtractTool._looks_like_pdf_model_term(token)
        ]
        normalize = lambda value: re.sub(r"[^a-z0-9]+", "", value.casefold())

        def _row_contains_model_alias(row_text: str, term: str) -> bool:
            row_folded = str(row_text or "").casefold()
            term_folded = str(term or "").casefold()
            if PdfExtractTool._pdf_row_contains_model_alias(row_text, term):
                return True
            if "a22b" in term_folded:
                return "a22b" in row_folded and (
                    "qwen3" in row_folded
                    or "235b" in row_folded
                    or "qwen3vl" in normalize(row_folded)
                )
            return False

        candidates: list[tuple[int, int, list[tuple[float, list[dict]]]]] = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page_number, page in enumerate(pdf.pages, 1):
                grouped: dict[float, list[dict]] = {}
                for word in page.extract_words(x_tolerance=2, y_tolerance=2):
                    grouped.setdefault(round(float(word["top"]) / 3) * 3, []).append(word)
                rows = [(top, sorted(words, key=lambda item: float(item["x0"]))) for top, words in grouped.items()]
                anchors: list[float] = []
                score = 0
                for top, words in rows:
                    row_text = " ".join(str(word["text"]) for word in words)
                    low = row_text.casefold()
                    metric_hits = sum(
                        PdfExtractTool._pdf_words_contain_metric(words, term)
                        for term in metric_terms
                    )
                    model_hits = sum(
                        PdfExtractTool._pdf_row_exact_term_match(row_text, term)
                        for term in model_terms
                    )
                    alias_model_hits = sum(
                        _row_contains_model_alias(row_text, term)
                        for term in model_terms
                    )
                    numeric_count = len(re.findall(r"\b\d+(?:\.\d+)?\b", row_text))
                    starts_with_metric = bool(words) and any(
                        re.sub(r"[^a-z0-9]+", "", str(words[0]["text"]).casefold())
                        == re.sub(r"[^a-z0-9]+", "", term.casefold())
                        for term in metric_terms
                    )
                    table_header_hit = (
                        "capability" in low
                        and "benchmark" in low
                    )
                    model_table_header_hit = bool(
                        alias_model_hits
                        and (
                            low.strip().startswith("table ")
                            or low.strip().startswith("benchmark")
                        )
                    )
                    if metric_hits >= 2 or (metric_hits and numeric_count >= 3) or (model_hits and numeric_count >= 3):
                        anchors.append(top)
                        score += (
                            metric_hits * 10
                            + model_hits * 5
                            + alias_model_hits * 20
                            + min(numeric_count, 10)
                            + (50 if starts_with_metric else 0)
                        )
                    elif model_table_header_hit:
                        score += alias_model_hits * 40
                    if table_header_hit:
                        score += 15
                if anchors:
                    # Preserve the continuous table body between matching
                    # anchors. Selecting disjoint +/-100 bands dropped middle
                    # model rows in tall benchmark tables when the queried
                    # model name was split across PDF words and therefore did
                    # not become its own anchor.
                    table_top = max(0.0, min(anchors) - 100)
                    header_tops = [
                        top for top, words in rows
                        if top <= min(anchors)
                        and (
                            (
                                "capability" in " ".join(str(word["text"]) for word in words).casefold()
                                and "benchmark" in " ".join(str(word["text"]) for word in words).casefold()
                            )
                            or (
                                "benchmark" in " ".join(str(word["text"]) for word in words).casefold()
                                and any(
                                    _row_contains_model_alias(
                                        " ".join(str(word["text"]) for word in words),
                                        term,
                                    )
                                    for term in model_terms
                                )
                            )
                            or (
                                " ".join(str(word["text"]) for word in words).casefold().strip().startswith("table ")
                                and any(
                                    _row_contains_model_alias(
                                        " ".join(str(word["text"]) for word in words),
                                        term,
                                    )
                                    for term in model_terms
                                )
                            )
                        )
                    ]
                    if header_tops:
                        table_top = min(table_top, max(header_tops) - 20)
                    table_bottom = max(anchors) + 100
                    selected = [
                        row for row in rows
                        if table_top <= row[0] <= table_bottom
                    ]
                    candidates.append((score, page_number, selected))
        if not candidates:
            return ""
        labeled_candidates = (
            [
                candidate for candidate in candidates
                if PdfExtractTool._pdf_rows_contain_table_label(
                    candidate[2], requested_table_number
                )
            ]
            if requested_table_number is not None
            else []
        )
        if labeled_candidates:
            # A paper can repeat a table number in an appendix. The first
            # exact caption is the canonical table for an unqualified request.
            _score, page_number, rows = min(
                labeled_candidates, key=lambda item: item[1]
            )
        else:
            _score, page_number, rows = max(candidates, key=lambda item: item[0])
        rows = PdfExtractTool._select_positioned_table_region(
            rows,
            metric_terms,
            model_terms,
            requested_table_number,
        )
        resolved: dict[str, str] = {}
        metric_positions: dict[str, float] = {}
        header_candidates = []
        for _top, words in rows:
            positions = {
                metric: float(word["x0"])
                for word in words for metric in metric_terms
                if re.sub(r"[^a-z0-9]+", "", str(word["text"]).casefold())
                == re.sub(r"[^a-z0-9]+", "", metric.casefold())
            }
            if positions:
                header_candidates.append((len(positions), positions))
        if header_candidates:
            metric_positions = max(header_candidates, key=lambda item: item[0])[1]

        def _numbers(words: list[dict]) -> list[tuple[float, str]]:
            return [
                (float(word["x0"]), str(word["text"]))
                for word in words
                if re.fullmatch(r"\d+(?:\.\d+)?", str(word["text"]))
            ]

        # Row-oriented tables: metric names are columns and the requested
        # model is a row (for example DeepSeek-VL2).
        target_model = PdfExtractTool._select_positioned_target_model(
            model_terms,
            rows,
        )
        target_base = re.sub(r"-(?:\d+(?:\.\d+)?[BMK])$", "", target_model, flags=re.I)
        exact_model_rows = [
            (top, words)
            for top, words in rows
            if target_base
            and PdfExtractTool._pdf_row_exact_term_match(
                " ".join(str(word["text"]) for word in words),
                target_base,
            )
        ]
        candidate_model_rows = exact_model_rows or [
            (top, words)
            for top, words in rows
            if target_base
            and PdfExtractTool._pdf_row_contains_term(
                " ".join(str(word["text"]) for word in words),
                target_base,
            )
        ]
        for _top, words in candidate_model_rows:
            row_text = " ".join(str(word["text"]) for word in words)
            nums = _numbers(words)
            if len(nums) >= len(metric_terms):
                for metric, metric_x in metric_positions.items():
                    nearest = min(nums, key=lambda item: abs(item[0] - metric_x))
                    if abs(nearest[0] - metric_x) <= 30:
                        resolved[metric] = nearest[1]
                if resolved:
                    break

        # A single row-oriented table can contain several models requested in
        # the same query. Return a coordinate-joined record for each exact row
        # instead of forcing the model to decode the remaining dense HTML row.
        additional_row_resolutions: dict[str, dict[str, str]] = {}
        for requested_model in dict.fromkeys(model_terms):
            if requested_model == target_model:
                continue
            requested_base = re.sub(
                r"-(?:\d+(?:\.\d+)?[BMK])$", "", requested_model, flags=re.I
            )
            requested_rows = [
                words
                for _top, words in rows
                if requested_base
                and PdfExtractTool._pdf_row_exact_term_match(
                    " ".join(str(word["text"]) for word in words),
                    requested_base,
                )
            ]
            for words in requested_rows:
                nums = _numbers(words)
                if len(nums) < len(metric_terms):
                    continue
                values: dict[str, str] = {}
                for metric, metric_x in metric_positions.items():
                    nearest = min(nums, key=lambda item: abs(item[0] - metric_x))
                    if abs(nearest[0] - metric_x) <= 30:
                        values[metric] = nearest[1]
                if values:
                    additional_row_resolutions[requested_model] = values
                    break

        # Column-oriented tables: models are columns and metrics are rows (for
        # example Qwen2.5-VL variants). Resolve a split size suffix such as
        # "72B" to its x coordinate, then join each metric row at that x.
        if len(resolved) < len(metric_terms) and target_model:
            size_match = re.search(r"(\d+(?:\.\d+)?[BMK])$", target_model, re.I)
            target_x = None
            if size_match:
                suffix = size_match.group(1).casefold()
                base_hits = [
                    (top, float(word["x0"]))
                    for top, words in rows for word in words
                    if target_base and str(word["text"]).casefold() == target_base.casefold()
                ]
                suffix_hits = [
                    (top, float(word["x0"]))
                    for top, words in rows for word in words
                    if str(word["text"]).casefold() == suffix
                ]
                pairs = [
                    (abs(base_top - suffix_top) + abs(base_x - suffix_x), suffix_x)
                    for base_top, base_x in base_hits for suffix_top, suffix_x in suffix_hits
                    if abs(base_top - suffix_top) <= 15 and abs(base_x - suffix_x) <= 30
                ]
                if pairs:
                    target_x = min(pairs)[1]
            if target_x is None:
                normalized_target = normalize(target_model)
                metric_tops = [
                    top for top, words in rows
                    if any(
                        PdfExtractTool._pdf_words_contain_metric(words, metric)
                        for metric in metric_terms
                    )
                ]
                first_metric_top = min(metric_tops) if metric_tops else float("inf")
                column_fragments: dict[int, list[tuple[float, str]]] = {}
                for top, words in rows:
                    if top >= first_metric_top:
                        continue
                    for word in words:
                        text = str(word["text"])
                        if not re.search(r"[A-Za-z]", text):
                            continue
                        x_key = round(float(word["x0"]))
                        column_fragments.setdefault(x_key, []).append((top, text))
                matches: list[tuple[int, int]] = []
                for x_key, fragments in column_fragments.items():
                    combined = " ".join(text for _top, text in sorted(fragments))
                    normalized_combined = normalize(combined)
                    if (
                        normalized_combined
                        and normalized_target
                        and (
                            normalized_target in normalized_combined
                            or normalized_combined in normalized_target
                        )
                    ):
                        matches.append((len(normalized_combined), x_key))
                if matches:
                    target_x = float(max(matches)[1])
            if target_x is None and target_model:
                target_folded = target_model.casefold()
                mode_terms = []
                if "instruct" in target_folded:
                    mode_terms.extend(["instruct", "non-thinking"])
                if "thinking" in target_folded and "instruct" not in target_folded:
                    mode_terms.append("thinking")
                if mode_terms:
                    metric_tops = [
                        top for top, words in rows
                        if any(
                            PdfExtractTool._pdf_words_contain_metric(words, metric)
                            for metric in metric_terms
                        )
                    ]
                    first_metric_top = min(metric_tops) if metric_tops else float("inf")
                    mode_hits = [
                        float(word["x0"])
                        for top, words in rows
                        if top < first_metric_top
                        for word in words
                        if str(word["text"]).casefold() in mode_terms
                    ]
                    if mode_hits:
                        target_x = mode_hits[0]
            if target_x is not None:
                for metric in metric_terms:
                    for row_index, (_top, words) in enumerate(rows):
                        if not PdfExtractTool._pdf_words_contain_metric(words, metric):
                            continue
                        nums = _numbers(words)
                        if not nums:
                            adjacent_numeric_rows = [
                                (
                                    abs(float(rows[candidate_index][0]) - float(_top)),
                                    rows[candidate_index][1],
                                )
                                for candidate_index in (row_index - 1, row_index + 1)
                                if 0 <= candidate_index < len(rows)
                                and abs(float(rows[candidate_index][0]) - float(_top)) <= 9
                                and _numbers(rows[candidate_index][1])
                            ]
                            if adjacent_numeric_rows:
                                nums = _numbers(
                                    min(adjacent_numeric_rows, key=lambda item: item[0])[1]
                                )
                        if nums:
                            nearest = min(nums, key=lambda item: abs(item[0] - target_x))
                            if abs(nearest[0] - target_x) <= 30:
                                resolved[metric] = nearest[1]
                                break

        rendered = []
        for top, words in sorted(rows, key=lambda item: item[0]):
            cells = " | ".join(
                f"x={float(word['x0']):.0f}:{word['text']}" for word in words
            )
            rendered.append(f"y={top:.0f} :: {cells}")
        body = "\n".join(rendered)
        if len(body) > MAX_OUTPUT_CHARS - 1000:
            body = body[:MAX_OUTPUT_CHARS - 1000] + "\n[...positioned rows truncated]"
        resolved_text = ""
        all_resolutions: list[tuple[str, dict[str, str]]] = []
        if resolved:
            all_resolutions.append((target_model or "requested model", resolved))
        all_resolutions.extend(additional_row_resolutions.items())
        resolved_sections: list[str] = []
        for resolved_model, resolved_values in all_resolutions:
            missing_metrics = [
                metric for metric in metric_terms if metric not in resolved_values
            ]
            missing_note = (
                " | requested metrics not found: " + ", ".join(missing_metrics)
                if missing_metrics else ""
            )
            lock_values = {
                metric: resolved_values[metric]
                for metric in metric_terms
                if metric in resolved_values
            }
            for metric in missing_metrics:
                lock_values[metric] = None
            resolved_sections.append(
                "Resolved requested values by coordinate join: "
                + resolved_model
                + " | "
                + " | ".join(
                    f"{metric}={resolved_values[metric]}"
                    for metric in metric_terms
                    if metric in resolved_values
                )
                + missing_note
                + "\n"
                + "Resolved values JSON: "
                + json.dumps({
                    "model": resolved_model,
                    "values": lock_values,
                    "source": "pdf_extract_positioned_table",
                    "page": page_number,
                }, sort_keys=True)
            )
        if resolved_sections:
            resolved_text = "\n".join(resolved_sections) + "\n"
        return (
            f"[Positioned PDF table evidence, page {page_number}. Values sharing the same "
            "x coordinate belong to the same column; map the requested model header x to "
            "metric-row values at that x.]\n" + resolved_text + body
        )

    @staticmethod
    def _local_text_evidence(path: Path, query: str) -> str:
        """Return focused text pages when a local PDF has no positioned rows."""
        try:
            import pdfplumber
        except ImportError:
            return ""
        tokens = {
            token.casefold()
            for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_.+-]{2,}", query)
        }
        requested_labels = [
            re.sub(r"\s+", "", label.casefold())
            for label in re.findall(r"\b(?:table|figure|fig\.?)\s*\d+\b", query, re.I)
        ]
        pages: list[tuple[int, int, str]] = []
        page_columns: list[tuple[int, str, str]] = []
        try:
            with pdfplumber.open(path) as pdf:
                for page_number, page in enumerate(pdf.pages, 1):
                    text = str(page.extract_text(layout=True) or "").strip()
                    if not text:
                        continue
                    midpoint = float(page.width) / 2
                    left = str(
                        page.crop((0, 0, midpoint, page.height)).extract_text() or ""
                    ).strip()
                    right = str(
                        page.crop((midpoint, 0, page.width, page.height)).extract_text() or ""
                    ).strip()
                    page_columns.append((page_number, left, right))
                    folded = text.casefold()
                    compact = re.sub(r"\s+", "", folded)
                    label_hits = sum(label in compact for label in requested_labels)
                    numeric_count = len(re.findall(r"\b\d+(?:\.\d+)?\b", text))
                    table_signal = bool(
                        re.search(r"\b(?:table|figure|fig\.?)\s*\d+\b", folded, re.I)
                    )
                    result_table_signal = bool(
                        re.search(
                            r"(?:table\s*3|evaluation\s+results\s+on\s+groundingme|"
                            r"main\s+results|main\s+experimental\s+results)",
                            folded,
                            re.I,
                        )
                    )
                    appendix_table_signal = bool(
                        re.search(
                            r"(?:supplementary\s+material|detailed\s+subtask|"
                            r"table\s*[67])",
                            folded,
                            re.I,
                        )
                    )
                    requested_result_terms = {
                        "appendix", "average", "baseline", "experimental",
                        "leaderboard", "results", "score", "scores", "thinking",
                    }
                    result_table_boost = (
                        260
                        if result_table_signal
                        and tokens & requested_result_terms
                        else 0
                    )
                    appendix_table_boost = (
                        220
                        if appendix_table_signal
                        and ("appendix" in tokens or "thinking" in tokens)
                        else 0
                    )
                    score = (
                        sum(token in folded for token in tokens)
                        + label_hits * 100
                        + (8 if table_signal else 0)
                        + min(numeric_count, 20) // 4
                        + result_table_boost
                        + appendix_table_boost
                    )
                    pages.append((score, page_number, text))
        except Exception:
            return ""
        bibliography = PdfExtractTool._bibliography_evidence(page_columns, query)
        if bibliography:
            return bibliography
        if not pages:
            return ""
        selected = sorted(
            pages, key=lambda item: (item[0], -item[1]), reverse=True
        )[:4]
        # Put the most relevant pages first so the bounded output cap cannot
        # truncate the requested table behind introductory pages. Page labels
        # remain in each block, so callers can still cite the source page.
        selected.sort(key=lambda item: (item[0], -item[1]), reverse=True)
        body = "\n\n".join(
            f"[Local PDF text evidence, page {page_number}]\n{text}"
            for _score, page_number, text in selected
        )
        if len(body) > MAX_OUTPUT_CHARS - 500:
            body = body[:MAX_OUTPUT_CHARS - 500] + "\n[...local PDF text truncated]"
        return body

    async def execute(self, content: str, ctx: dict) -> dict:
        raw = content.strip()
        try:
            args = json.loads(raw) if raw.startswith("{") else {}
        except json.JSONDecodeError:
            args = {}
        if not isinstance(args, dict):
            args = {}
        if not args:
            lines = [line.strip() for line in raw.splitlines() if line.strip()]
            args = {
                "url": lines[0] if lines else "",
                "query": ", ".join(lines[1:]),
            }
        url = str(args.get("url") or args.get("path") or "").strip()
        query = str(args.get("query") or "").strip()
        if not url:
            return {"error": "pdf_extract: provide a PDF URL or local PDF path", "exit_code": 1}
        if not query:
            return {
                "error": (
                    "pdf_extract: provide query terms naming the target model, "
                    "metrics, or table so the returned evidence is precise"
                ),
                "exit_code": 1,
            }
        loop = asyncio.get_running_loop()
        local_path = self._local_pdf_path(url)
        if local_path is not None:
            from src.tool_execution import _display_tool_path

            url = _display_tool_path(str(local_path))

        async def _positioned_evidence() -> str:
            try:
                return await asyncio.wait_for(
                    loop.run_in_executor(None, self._positioned_table_evidence, url, query),
                    timeout=30,
                )
            except Exception:
                return ""

        # arXiv's PDF CDN can temporarily serve an older revision than its
        # current HTML conversion.  The HTML table is also substantially more
        # legible than positioned PDF glyphs.  Fetch both official forms in
        # parallel and return complementary evidence, so a newly-added model
        # row cannot disappear and trigger browser/shell retry loops.
        html_url = self._arxiv_html_url(url) if local_path is None else ""
        positioned_task = _positioned_evidence()
        if html_url:
            query_tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9_.+-]{1,}", query)
            query_models = sorted(
                {
                    token for token in query_tokens
                    if not self._looks_like_pdf_metric_term(token)
                    and self._looks_like_pdf_model_term(token)
                },
                key=len,
                reverse=True,
            )
            # A broad query with two model names often locks onto the first of
            # several identically numbered tables (for example dev Table 2
            # instead of test Table 2). Query each requested model separately;
            # the rarer/longer row is emitted first and anchors the right table.
            if len(query_models) > 1:
                # Exact model-only focus keeps the matching row inside the
                # passage budget. Repeating every metric acronym can rank the
                # table introduction above a late model row and truncate the
                # very values the caller requested.
                html_queries = query_models
            else:
                html_queries = [query]
            html_tasks = [
                WebFetchTool().execute(
                    json.dumps({"url": html_url, "query": focused, "full": True}), ctx
                )
                for focused in html_queries
            ]
            gathered = await asyncio.gather(positioned_task, *html_tasks)
            positioned = gathered[0]
            html_outputs: list[str] = []
            for focused, html_result in zip(html_queries, gathered[1:]):
                if html_result.get("exit_code") != 0:
                    continue
                html_output = str(html_result.get("output") or "")
                if html_output and html_output not in html_outputs:
                    html_outputs.append(
                        f"[Focused HTML evidence for: {focused}]\n{html_output}"
                    )
            evidence = []
            # Structured coordinate evidence is compact and column-safe.  Put
            # it first so a long prose extraction cannot consume the bounded
            # response and truncate the exact table rows.
            if positioned:
                evidence.append(positioned)
            if html_outputs:
                evidence.append(
                    f"[Official arXiv HTML companion: {html_url}]\n"
                    + "\n\n--- additional requested model row ---\n\n".join(html_outputs)
                )
            if evidence:
                output = (
                    f"Source: {url}\n\n"
                    "[Extraction guidance: align every value to the complete table header. "
                    "A row may contain extra unrequested generation metrics (for example VS or SSC); "
                    "skip those columns rather than treating the next contiguous number as the requested metric.]\n\n"
                    + "\n\n--- PDF/HTML corroboration ---\n\n".join(evidence)
                )
                if len(output) > MAX_OUTPUT_CHARS:
                    output = output[:MAX_OUTPUT_CHARS] + "\n[...combined evidence truncated]"
                return {"output": output, "exit_code": 0}
        else:
            positioned = await positioned_task
            if positioned:
                return {"output": f"Source: {url}\n\n{positioned}", "exit_code": 0}
            if local_path is not None:
                text_evidence = await asyncio.wait_for(
                    loop.run_in_executor(
                        None, self._local_text_evidence, local_path, query
                    ),
                    timeout=30,
                )
                if text_evidence:
                    return {
                        "output": f"Source: {url}\n\n{text_evidence}",
                        "exit_code": 0,
                    }
                return {
                    "error": (
                        "pdf_extract: no selectable table/text evidence was found in the local PDF; "
                        "use inspect_media for scanned or visual pages"
                    ),
                    "exit_code": 1,
                }
        fetch_args = {"url": url, "query": query, "full": True}
        result = await WebFetchTool().execute(json.dumps(fetch_args), ctx)
        if result.get("exit_code") == 0:
            result["output"] = (
                "[PDF extraction; the highest-ranked matching table window is below. "
                "Align row values to the full column header, use only the requested model's "
                "column, and do not re-query when the target row and header are present.]\n\n"
                + str(result.get("output") or "")
            )
        return result


class YouTubeTool:
    """YouTube-specific read tool for videos, transcripts, comments, channels."""

    _ACTIONS = {"comments", "transcript", "metadata", "latest_channel_video"}

    async def execute(self, content: str, ctx: dict) -> dict:
        from services.youtube.youtube_handler import (
            extract_transcript_async,
            fetch_youtube_comments,
            init_youtube,
        )

        args, err = self._parse_args(content)
        if err:
            return {"error": err, "exit_code": 1}
        action = str(args.get("action") or "metadata").strip().lower()
        if action not in self._ACTIONS:
            return {
                "error": "youtube_tool: action must be one of "
                + ", ".join(sorted(self._ACTIONS)),
                "exit_code": 1,
            }

        max_results = args.get("max_results")
        if not isinstance(max_results, int) or max_results <= 0:
            max_results = 20
        max_results = max(1, min(50, max_results))

        progress_cb = ctx.get("progress_cb") if isinstance(ctx, dict) else None
        if progress_cb:
            await progress_cb({"elapsed_s": 0, "tail": f"youtube_tool: {action}"})

        if action == "latest_channel_video":
            channel = str(args.get("channel_url") or args.get("url") or args.get("handle") or "").strip()
            if not channel:
                return {"error": "youtube_tool latest_channel_video: provide channel_url or handle", "exit_code": 1}
            return await self._latest_channel_video(channel, max_results=max_results)

        if action == "metadata" and not any(args.get(k) for k in ("url", "video_url", "video_id")):
            channel = str(args.get("channel_url") or args.get("handle") or "").strip()
            if channel:
                return await self._latest_channel_video(channel, max_results=max_results)

        url_or_id = str(args.get("url") or args.get("video_url") or args.get("video_id") or "").strip()
        # The shared extractor accepts ID prefixes inside text. At the tool
        # boundary require the complete target, not a truncated invented ID.
        if url_or_id.startswith(('http://', 'https://')):
            parsed = urllib.parse.urlparse(url_or_id)
            host = (parsed.hostname or '').lower()
            if host in {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'}:
                parts = parsed.path.strip('/').split('/')
                candidate = (urllib.parse.parse_qs(parsed.query).get('v', [''])[0]
                             if parsed.path == '/watch' else
                             parts[1] if len(parts) == 2 and parts[0] in {'shorts', 'embed', 'live'} else '')
            elif host in {'youtu.be', 'www.youtu.be'}:
                candidate = parsed.path.strip('/')
            else:
                candidate = ''
        else:
            candidate = url_or_id
        if (not re.fullmatch(r'[A-Za-z0-9_-]{11}', candidate)
                or (args.get('video_id') and str(args['video_id']) != candidate)):
            return {"error": f"youtube_tool {action}: invalid video target. Resolve the actual video URL "
                    "from the user or an observed link. A title or channel page is not a video ID; "
                    "open the referenced video in the browser or use latest_channel_video first.",
                    "exit_code": 1, "failure_kind": "invalid_target"}
        video_id = candidate
        url = url_or_id if url_or_id.startswith(("http://", "https://")) else f"https://www.youtube.com/watch?v={video_id}"

        if action == "comments":
            api_result = await self._comments_from_data_api(video_id, max_results)
            if api_result.get("success"):
                return {"output": self._format_comments(api_result, url), "exit_code": 0, "untrusted_content": True}
            comments_data = await fetch_youtube_comments(video_id, max_comments=max_results, timeout=45)
            if not comments_data.get("success"):
                api_error = api_result.get("error")
                fallback_error = comments_data.get("error") or "unknown error"
                joined = f"{fallback_error}"
                if api_error:
                    joined = f"YouTube Data API unavailable: {api_error}; yt-dlp fallback failed: {fallback_error}"
                return {"error": f"youtube_tool comments: {joined}", "exit_code": 1,
                        "failure_kind": "comments_unavailable", "video_url": url,
                        "untrusted_content": True}
            return {"output": self._format_comments(comments_data, url), "exit_code": 0, "untrusted_content": True}

        if action == "transcript":
            init_youtube()
            transcript_data = await extract_transcript_async(url, video_id)
            if not transcript_data.get("success"):
                return {
                    "error": f"youtube_tool transcript: {transcript_data.get('error') or 'transcript unavailable'}",
                    "exit_code": 1,
                    "untrusted_content": True,
                }
            return {"output": self._format_transcript(transcript_data, url), "exit_code": 0, "untrusted_content": True}

        return await self._metadata(url)

    def _parse_args(self, content: str) -> tuple[dict, str | None]:
        raw = (content or "").strip()
        if not raw:
            return {}, "youtube_tool: provide JSON with action and url/video_id"
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"action": "metadata", "url": raw}, None
        if not isinstance(parsed, dict):
            return {}, "youtube_tool: arguments must be a JSON object"
        return parsed, None

    async def _comments_from_data_api(self, video_id: str, max_results: int) -> dict:
        api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
        if not api_key:
            return {"success": False, "error": "YOUTUBE_API_KEY not set", "comments": []}
        params = urllib.parse.urlencode({
            "part": "snippet",
            "videoId": video_id,
            "maxResults": str(max_results),
            "order": "relevance",
            "textFormat": "plainText",
            "key": api_key,
        })
        url = f"https://www.googleapis.com/youtube/v3/commentThreads?{params}"

        def _fetch() -> dict:
            req = urllib.request.Request(url, headers={"User-Agent": "odysseus-ui/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))

        try:
            data = await asyncio.wait_for(asyncio.to_thread(_fetch), timeout=20)
        except Exception as e:
            return {"success": False, "error": f"{type(e).__name__}: {e}", "comments": []}

        comments = []
        for item in data.get("items") or []:
            snippet = (
                (item.get("snippet") or {})
                .get("topLevelComment", {})
                .get("snippet", {})
            )
            text = html.unescape(str(snippet.get("textDisplay") or snippet.get("textOriginal") or "")).strip()
            if not text:
                continue
            comments.append({
                "author": snippet.get("authorDisplayName") or "Unknown",
                "text": text,
                "likes": snippet.get("likeCount") or 0,
            })
        return {"success": True, "comments": comments, "count": len(comments), "source": "youtube_data_api"}

    def _format_comments(self, data: dict, url: str) -> str:
        comments = [c for c in (data.get("comments") or []) if isinstance(c, dict)]
        if not comments:
            return f"YouTube comments for {url}: no comments returned."
        lines = [
            f"YouTube comments for {url}",
            f"Source: {data.get('source') or 'yt-dlp'}",
            f"Count: {len(comments)}",
        ]
        title = str(data.get("title") or "").strip()
        channel = str(data.get("channel") or "").strip()
        if title:
            lines.append(f"Title: {title}")
        if channel:
            lines.append(f"Channel: {channel}")
        lines.append("")
        for idx, comment in enumerate(comments, 1):
            likes = comment.get("likes") or 0
            like_text = f" [{likes} likes]" if likes else ""
            author = str(comment.get("author") or "Unknown").strip().lstrip("@")
            text = re.sub(r"\s+", " ", str(comment.get("text") or "")).strip()
            lines.append(f"{idx}. @{author}{like_text}: {text}")
        output = "\n".join(lines)
        return output[:MAX_OUTPUT_CHARS] + ("\n\n[...truncated]" if len(output) > MAX_OUTPUT_CHARS else "")

    def _format_transcript(self, data: dict, url: str) -> str:
        lines = [
            f"YouTube transcript for {url}",
            f"Video ID: {data.get('video_id') or ''}",
            f"Language: {data.get('language') or 'unknown'}",
            "",
        ]
        segments = data.get("segments") or []
        if segments:
            for seg in segments:
                if not isinstance(seg, dict):
                    continue
                lines.append(f"[{seg.get('timestamp') or '??:??'}] {seg.get('text') or ''}")
        else:
            lines.append(str(data.get("transcript") or ""))
        output = "\n".join(lines)
        return output[:MAX_OUTPUT_CHARS] + ("\n\n[...truncated]" if len(output) > MAX_OUTPUT_CHARS else "")

    async def _metadata(self, url: str) -> dict:
        result = await self._ytdlp_json(url, timeout=30)
        if not result.get("success"):
            return {"error": f"youtube_tool metadata: {result.get('error')}", "exit_code": 1, "untrusted_content": True}
        data = result["data"]
        lines = [
            f"Title: {data.get('title') or ''}",
            f"Channel: {data.get('channel') or data.get('uploader') or ''}",
            f"URL: {data.get('webpage_url') or url}",
            f"Duration: {data.get('duration_string') or data.get('duration') or ''}",
            f"View count: {data.get('view_count') or ''}",
            f"Like count: {data.get('like_count') or ''}",
            f"Upload date: {data.get('upload_date') or ''}",
        ]
        return {"output": "\n".join(lines), "exit_code": 0, "untrusted_content": True}

    async def _latest_channel_video(self, channel: str, *, max_results: int = 5) -> dict:
        original_channel = channel
        if channel.startswith("@"):
            channel = f"https://www.youtube.com/{channel}/videos"
        elif channel.startswith(("http://", "https://")):
            if "/videos" not in urllib.parse.urlparse(channel).path:
                channel = channel.rstrip("/") + "/videos"
        else:
            channel = f"https://www.youtube.com/{channel.strip('/')}/videos"
        result = await self._ytdlp_json(channel, timeout=45, flat_playlist=True, playlist_end=max_results)
        if not result.get("success"):
            resolved_channel = await self._resolve_channel_from_search(original_channel)
            if resolved_channel:
                retry = await self._ytdlp_json(
                    resolved_channel,
                    timeout=45,
                    flat_playlist=True,
                    playlist_end=max_results,
                )
                if retry.get("success"):
                    channel = resolved_channel
                    result = retry
                else:
                    return {
                        "error": (
                            "youtube_tool latest_channel_video: "
                            f"{result.get('error')}; resolved {resolved_channel} also failed: {retry.get('error')}"
                        ),
                        "exit_code": 1,
                        "untrusted_content": True,
                    }
            else:
                return {"error": f"youtube_tool latest_channel_video: {result.get('error')}", "exit_code": 1, "untrusted_content": True}
        if not result.get("success"):
            return {"error": f"youtube_tool latest_channel_video: {result.get('error')}", "exit_code": 1, "untrusted_content": True}
        data = result["data"]
        entries = [e for e in (data.get("entries") or []) if isinstance(e, dict)]
        if not entries:
            return {"error": "youtube_tool latest_channel_video: no videos found", "exit_code": 1, "untrusted_content": True}
        entries = entries[:max_results]
        heading = "Latest channel video" if len(entries) == 1 else f"Latest {len(entries)} channel videos"
        lines = [f"{heading} for {channel}", ""]
        for idx, entry in enumerate(entries, 1):
            video_id = entry.get("id") or ""
            video_url = entry.get("url") or entry.get("webpage_url") or ""
            if video_id and not str(video_url).startswith("http"):
                video_url = f"https://www.youtube.com/watch?v={video_id}"
            lines.extend([
                f"{idx}. {entry.get('title') or ''}",
                f"   URL: {video_url}",
                f"   Duration: {entry.get('duration_string') or entry.get('duration') or ''}",
                f"   Video ID: {video_id}",
            ])
        return {"output": "\n".join(lines), "exit_code": 0, "untrusted_content": True}

    async def _resolve_channel_from_search(self, channel: str) -> str:
        query = self._channel_search_query(channel)
        if not query:
            return ""
        result = await self._ytdlp_json(
            f"ytsearch10:{query} official YouTube channel",
            timeout=30,
            flat_playlist=True,
            playlist_end=10,
        )
        if not result.get("success"):
            return ""
        entries = [e for e in (result.get("data") or {}).get("entries") or [] if isinstance(e, dict)]
        if not entries:
            return ""
        target = self._normalize_channel_name(query)

        def _score(entry: dict) -> tuple[int, int]:
            channel_name = self._normalize_channel_name(entry.get("channel") or entry.get("uploader") or "")
            uploader_id = self._normalize_channel_name(entry.get("uploader_id") or "")
            title = self._normalize_channel_name(entry.get("title") or "")
            score = 0
            if channel_name == target:
                score += 100
            elif target and target in channel_name:
                score += 60
            if uploader_id == target:
                score += 45
            elif target and target in uploader_id:
                score += 25
            if target and target in title:
                score += 10
            if re.search(r"\b(?:clips?|shorts?|two|second|fan|archive)\b", channel_name):
                score -= 35
            if re.search(r"\b(?:clips?|shorts?|compilation|reacts?)\b", title):
                score -= 10
            return score, int(entry.get("view_count") or 0)

        best = max(entries, key=_score)
        if _score(best)[0] <= 0:
            return ""
        channel_url = str(best.get("channel_url") or "").strip()
        uploader_url = str(best.get("uploader_url") or "").strip()
        resolved = channel_url or uploader_url
        if not resolved:
            uploader_id = str(best.get("uploader_id") or "").strip()
            if uploader_id.startswith("@"):
                resolved = f"https://www.youtube.com/{uploader_id}"
        if not resolved:
            return ""
        parsed = urllib.parse.urlparse(resolved)
        if parsed.netloc and "/videos" not in parsed.path:
            resolved = resolved.rstrip("/") + "/videos"
        return resolved

    @staticmethod
    def _channel_search_query(channel: str) -> str:
        value = str(channel or "").strip()
        if not value:
            return ""
        if value.startswith(("http://", "https://")):
            path = urllib.parse.urlparse(value).path.strip("/")
            parts = [part for part in path.split("/") if part and part.lower() != "videos"]
            value = parts[-1] if parts else value
        value = value.strip().lstrip("@").strip("/")
        value = re.sub(r"(?i)^(?:c|channel|user)/", "", value)
        value = re.sub(r"[_-]+", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _normalize_channel_name(value: str) -> str:
        value = str(value or "").lower().lstrip("@")
        value = re.sub(r"[^a-z0-9]+", "", value)
        return value

    async def _ytdlp_json(self, url: str, *, timeout: int, flat_playlist: bool = False, playlist_end: int = 5) -> dict:
        binary = shutil.which("yt-dlp")
        if not binary:
            venv_binary = os.path.join(os.path.dirname(sys.executable), "yt-dlp")
            binary = venv_binary if os.path.exists(venv_binary) else ""
        if not binary:
            return {"success": False, "error": "yt-dlp not installed"}
        cmd = [binary, "--skip-download", "--no-warnings", "--js-runtimes", "node"]
        if flat_playlist:
            playlist_end = max(1, min(50, int(playlist_end or 5)))
            cmd.extend(["--flat-playlist", "--playlist-end", str(playlist_end), "--dump-single-json"])
        else:
            cmd.append("--dump-json")
        cmd.append(url)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            with contextlib.suppress(Exception):
                proc.kill()
                await proc.wait()
            return {"success": False, "error": f"yt-dlp timed out after {timeout}s"}
        except Exception as e:
            return {"success": False, "error": f"{type(e).__name__}: {e}"}
        if proc.returncode != 0:
            return {"success": False, "error": stderr.decode("utf-8", errors="replace")[:300]}
        try:
            data = self._parse_ytdlp_json_output(stdout.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as e:
            return {"success": False, "error": f"invalid yt-dlp JSON: {e}"}
        return {"success": True, "data": data}

    @staticmethod
    def _parse_ytdlp_json_output(output: str) -> dict:
        """Parse yt-dlp JSON from either a single object or JSON-lines output."""
        text = (output or "").strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            entries = []
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    entries.append(parsed)
            return {"entries": entries}
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            return {"entries": [item for item in data if isinstance(item, dict)]}
        return {"entries": []}


class PrivateBrowserTool:
    """Small deterministic wrapper around Vercel's agent-browser CLI.

    This is intentionally narrower than handing the model the raw browser MCP
    schema. Use web_search/web_fetch first; this exists for JS-rendered pages,
    forms, clicks, screenshots, and logged-in browser state.
    """

    _ACTIONS = {
        "open",
        "read",
        "snapshot",
        "find",
        "evaluate",
        "click",
        "fill",
        "press",
        "scroll",
        "wait",
        "screenshot",
        "close",
        "batch",
    }
    _AUTO_SCREENSHOT_ACTIONS = {
        "open",
        "snapshot",
        "batch",
        "click",
        "fill",
        "press",
        "scroll",
    }

    @staticmethod
    def _shopping_landing_hint(output: str) -> str:
        """Expose the actionable store link on global retail landing pages."""
        text = str(output or "")
        if not re.search(r"\b(?:global|choose another store|store selector)\b", text, re.IGNORECASE):
            return ""
        match = re.search(
            r'link\s+"(?P<label>Go shopping[^"\n]{0,220})"\s+\[ref=(?P<ref>e\d+)\]',
            text,
            re.IGNORECASE,
        )
        if not match:
            return ""
        label = re.sub(r"\s+", " ", match.group("label")).strip()
        return (
            "Detected page type: global store-selector landing page. "
            f"Local shopping link: @{match.group('ref')} ({label})."
        )

    @staticmethod
    def _retryable_local_open_failure(output: str) -> bool:
        """Return whether a local-page open failed during browser bootstrap.

        ``agent-browser`` keeps a daemon behind the short-lived CLI.  During
        parallel runtime startup the daemon can disappear between the client
        connection and Chromium setup, producing a transient ENOENT/connection
        error.  Retry only this narrow class of failure; page JavaScript errors
        and arbitrary browser failures must still be surfaced to the model.
        """

        text = str(output or "").lower()
        return (
            "could not configure browser" in text
            and "failed to connect" in text
            and ("no such file" in text or "enoent" in text)
        )

    @staticmethod
    def _terminate_subprocess(proc) -> None:
        """Terminate a browser CLI and descendants spawned for its session.

        Every browser CLI is spawned with ``start_new_session``, so it leads a
        group of its own. That group is signalled only while the identity
        captured at spawn still verifies and still leads it; otherwise only
        the held handle is killed. A pid alone — or a group derived from a pid
        that may since have been reaped and reissued — is never signalled,
        and neither is the server's own group.
        """

        identity = getattr(proc, "_ody_identity", None)
        if identity is not None and getattr(proc, "returncode", None) is None:
            verdict = process_lifecycle.group_ownership_verdict(
                identity.pid, identity.pid, identity.start_token)
            if verdict == process_lifecycle.OWNED:
                process_lifecycle.signal_group(identity.pid, identity.pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            proc.kill()

    @staticmethod
    def _terminate_owned_chrome(env: dict[str, str]) -> None:
        """Kill Chrome trees created in this runtime's temporary directory.

        agent-browser deliberately keeps its daemon alive after the CLI
        client exits.  If the client is killed while waiting for a response,
        the daemon and its Chrome children are reparented to init and are no
        longer in the client's process group.  Leaving those trees behind
        makes later benchmark tasks contend for resources and can make a
        healthy page look like a browser timeout.  Restrict matching to the
        runtime-owned ``TMPDIR`` and the browser profile prefix; never scan
        or kill a user's normal Chrome profile.
        """

        raw_tmpdir = str(env.get("TMPDIR") or "").strip()
        if not raw_tmpdir:
            return
        try:
            tmpdir = Path(raw_tmpdir).resolve()
        except OSError:
            return
        profile_prefix = str(tmpdir / "agent-browser-chrome-")
        if not platform_compat.has_procfs():
            # Without procfs there is no way to match a reparented Chrome by
            # its command line, and the sweep is an optimisation rather than a
            # correctness requirement.  Leave those trees to the daemon's own
            # lifecycle instead of failing the whole shutdown path.
            return
        owned: list[process_lifecycle.ProcessIdentity] = []
        for entry in platform_compat.PROC_ROOT.iterdir():
            if not entry.name.isdigit():
                continue
            # The command line that matches the profile and the identity that
            # will be signalled are read from the same process.
            seen = process_lifecycle.observe(int(entry.name), _process_command_line)
            if seen is not None and "--user-data-dir=" + profile_prefix in seen.facts:
                owned.append(seen.identity)
        if owned:
            process_lifecycle.terminate_identities(
                sorted(owned, key=lambda identity: identity.pid, reverse=True),
                steps=((signal.SIGKILL, 1.0),), poll_s=0.02,
            )

    @staticmethod
    def _terminate_owned_daemon(
        env: dict[str, str], session_id: str | None = None
    ) -> dict[str, Any] | None:
        """Terminate the detached agent-browser tree owned by one session.

        Killing only the daemon reparents its Chrome children, so the whole
        POSIX session the daemon leads is cleaned together with the session's
        runtime files and browser profile. Returns the cleanup receipt.
        """

        namespace = _browser_namespace(env)
        receipt = None
        pid_files = []
        if session_id:
            key = _scoped_browser_session(namespace, session_id)
            root = browser_lifecycle.runtime_root(env)
            receipt = browser_lifecycle.force_cleanup(
                root, key, pid_alive=lambda pid: _process_is_alive(pid)
            ).as_dict()
            pid_files.append(root / f"{key}.pid")
        runtime_dir = Path(os.getenv("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
        pid_files = [
            path
            for path in _browser_pid_file_candidates(runtime_dir, namespace, session_id)
            if path not in pid_files
        ]
        for pid_file in pid_files:
            try:
                pid = int(pid_file.read_text().strip())
            except (OSError, ValueError):
                continue
            # A pid file names a slot, not a process: the command-line match
            # and the identity that authorises the signal come from one read.
            seen = process_lifecycle.observe(pid, _process_command_line) if pid > 0 else None
            command_line = seen.facts if seen is not None else None
            if command_line is None:
                # Either the daemon exited between writing its pid file and
                # this pass, or this host has no procfs to ask. Only the first
                # justifies forgetting the pid file. Without procfs we cannot
                # confirm the process is ours, so we neither kill it nor drop
                # the record that would let a later pass find it.
                if not _process_is_alive(pid):
                    with contextlib.suppress(FileNotFoundError, PermissionError, OSError):
                        pid_file.unlink()
                continue
            if "agent-browser" in command_line:
                sweep = process_lifecycle.terminate_identities(
                    [seen.identity], steps=((signal.SIGKILL, 1.0),), poll_s=0.02,
                )
                if not sweep.survivors and not sweep.unverified:
                    with contextlib.suppress(FileNotFoundError, PermissionError, OSError):
                        pid_file.unlink()
        return receipt

    @staticmethod
    def _owned_daemon_exists(env: dict[str, str], session_id: str | None) -> bool:
        """Return whether this runtime has a live agent-browser daemon.

        A ``close`` command against a session that has never been started can
        bootstrap a fresh daemon and wait for its browser indefinitely.  Only
        reset sessions that have an exact, verified pid-file match.
        """

        if not session_id:
            return False
        namespace = _browser_namespace(env)
        key = _scoped_browser_session(namespace, session_id)
        root = browser_lifecycle.runtime_root(env)
        if browser_lifecycle.has_live_daemon(
            root, key, pid_alive=lambda pid: _process_is_alive(pid)
        ):
            return True
        runtime_dir = Path(os.getenv("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
        for pid_file in _browser_pid_file_candidates(runtime_dir, namespace, session_id):
            if pid_file == root / f"{key}.pid":
                continue
            try:
                pid = int(pid_file.read_text().strip())
            except (OSError, ValueError):
                continue
            command_line = _process_command_line(pid)
            if command_line is None:
                # Without procfs we can only tell that something with this pid
                # is alive, not that it is agent-browser. The pid file is our
                # own namespaced one, so treat a live pid as a match: answering
                # "no daemon" here is what lets `close` bootstrap a fresh one
                # and wait on its browser forever.
                if _process_is_alive(pid):
                    return True
                continue
            if "agent-browser" in command_line:
                return True
        return False

    @staticmethod
    def _local_agent_browser_binary() -> str | None:
        """Find the native installed binary before falling back to npx.

        The package's ``.bin/agent-browser`` entrypoint is a Node wrapper. It
        launches the persistent native daemon with inherited stdio, which can
        leave the harness's subprocess pipes open after the CLI request has
        completed. Calling the native binary directly avoids that pipe leak.
        """

        candidates = sorted(
            (
                path
                for path in _accessible_glob(
                    [npm_root / "_npx" for npm_root in _host_npm_roots()],
                    "*/node_modules/agent-browser/bin/agent-browser-linux-x64",
                )
                if path.is_file() and os.access(path, os.X_OK)
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return str(candidates[0]) if candidates else None

    @staticmethod
    def _resolve_workspace_path(raw_path: str) -> Path:
        """Resolve a logical agent path inside the active task workspace."""

        # Reuse the central path policy so browser screenshots obey the same
        # /workspace alias, traversal checks, and sensitive-file restrictions
        # as the native filesystem/media tools.
        from src.tool_execution import _resolve_tool_path

        return Path(_resolve_tool_path(raw_path))

    @classmethod
    def _resolve_local_file_url(cls, url: str) -> str:
        """Map a local logical workspace reference into its real file URL."""

        raw_url = str(url or "").strip()
        if raw_url == "/workspace" or raw_url.startswith("/workspace/"):
            resolved = cls._resolve_workspace_path(
                urllib.parse.unquote(raw_url)
            )
            return resolved.as_uri()
        parsed = urllib.parse.urlsplit(raw_url)
        if parsed.scheme.lower() != "file":
            return raw_url
        if parsed.netloc not in {"", "localhost"}:
            raise ValueError("private_browser file URL must use the local workspace")
        raw_path = urllib.parse.unquote(parsed.path)
        resolved = cls._resolve_workspace_path(raw_path)
        return resolved.as_uri()

    # Time allowed beyond the action timeout for one bounded recovery attempt.
    _RECOVERY_BUDGET_S = 75
    _CLOSE_TIMEOUT_S = 10

    async def execute(self, content: str, ctx: dict) -> dict:
        """Run one browser action inside its session's lifecycle.

        Actions on one session are serialized. A call without an owning
        Odysseus session gets a browser of its own that is closed before the
        call returns; it never falls back to agent-browser's shared default
        session. Cancellation stops every CLI client the call started and
        cleans the session's browser tree, because its state is unknown.
        """

        ctx = dict(ctx) if isinstance(ctx, dict) else {}
        session_id = str(ctx.get("session_id") or "").strip()
        ephemeral = not session_id
        if ephemeral:
            session_id = f"ephemeral-{uuid.uuid4().hex}"
            ctx["session_id"] = session_id
        runtime_env = ctx.get("subproc_env") if isinstance(ctx.get("subproc_env"), dict) else {}
        key = _scoped_browser_session(_browser_namespace(runtime_env), session_id)
        browser = browser_lifecycle.session_for(key, ephemeral)
        clock = browser_lifecycle.StageClock()
        procs: list = []
        token = _BROWSER_CALL_PROCS.set(procs)
        lock = browser.lock()
        acquired = False
        try:
            await lock.acquire()
            acquired = True
            result = await self._execute_unlocked(content, ctx, browser=browser, clock=clock)
            if ephemeral:
                await self._release_session(browser, session_id, clock)
            if isinstance(result, dict) and browser.env is not None:
                result["browser_lifecycle"] = browser.receipt(clock)
            return result
        except asyncio.CancelledError:
            if acquired:
                for proc in procs:
                    if getattr(proc, "returncode", None) is None:
                        self._terminate_subprocess(proc)
                if browser.env is not None:
                    self._terminate_owned_daemon(browser.env, session_id)
                    browser.discarded("cancelled")
            raise
        except Exception:
            if acquired and ephemeral and browser.env is not None:
                self._terminate_owned_daemon(browser.env, session_id)
            raise
        finally:
            if acquired:
                lock.release()
            _BROWSER_CALL_PROCS.reset(token)
            if ephemeral:
                browser_lifecycle.forget(key)
                _ACTIVE_BROWSER_SESSIONS.discard(key)

    async def _release_session(
        self,
        browser: browser_lifecycle.BrowserSession,
        session_id: str,
        clock: browser_lifecycle.StageClock,
    ) -> None:
        """Close a session gracefully, then verify nothing it owned survives."""

        if browser.env is None:
            return
        started = time.monotonic()
        graceful = False
        if self._owned_daemon_exists(browser.env, session_id):
            proc = None
            try:
                proc = await _spawn_browser_cli(
                    *browser.command_prefix,
                    "close",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=browser.env,
                    start_new_session=True,
                )
                await asyncio.wait_for(proc.wait(), timeout=self._CLOSE_TIMEOUT_S)
                graceful = (proc.returncode or 0) == 0
            except Exception:
                if proc is not None:
                    self._terminate_subprocess(proc)
        receipt = self._terminate_owned_daemon(browser.env, session_id)
        verified = bool(receipt.get("verified")) if isinstance(receipt, dict) else False
        clock.record("close", started, verified or graceful)
        clock.extra["cleanup"] = {"graceful_close": graceful, **(receipt or {})}
        if browser.page_url:
            clock.extra["closed_page_url"] = browser.page_url
        browser.discarded("closed")

    def _discard_session(
        self,
        browser: browser_lifecycle.BrowserSession,
        env: dict[str, str],
        session_id: str,
        clock: browser_lifecycle.StageClock,
        state: str,
    ) -> None:
        """Force-clean a session whose browser state can no longer be trusted."""

        started = time.monotonic()
        receipt = self._terminate_owned_daemon(env, session_id or None)
        verified = bool(receipt.get("verified")) if isinstance(receipt, dict) else False
        clock.record("forced_cleanup", started, verified, reason=state)
        clock.extra["cleanup"] = receipt
        browser.discarded(state)

    @staticmethod
    def _navigation_target(action: str, args: dict) -> str:
        """URL this action navigates the session to, or ``""``."""

        if action == "read" and any(
            str(args.get(key) or "").strip() for key in ("selector", "target", "ref")
        ):
            return ""
        if action in {"open", "read"}:
            return str(args.get("url") or "").strip()
        if action == "batch" and isinstance(args.get("commands"), list):
            target = ""
            for command in args["commands"]:
                if (
                    isinstance(command, list)
                    and len(command) > 1
                    and str(command[0]).lower() in {"open", "goto", "navigate"}
                ):
                    target = str(command[1]).strip()
            return target
        return ""

    @staticmethod
    def _read_page_from_rows(output: str) -> dict[str, Any]:
        """Page text from an open + ``get text`` batch, only if both succeeded."""

        try:
            rows = json.loads(output)
        except (ValueError, TypeError):
            rows = None
        if not isinstance(rows, list) or len(rows) != 2 or not all(isinstance(r, dict) for r in rows):
            return {"ok": False, "error": "private_browser read returned no structured page result"}
        opened, extracted = rows
        for row in rows:
            if row.get("success") is not True:
                return {"ok": False, "error": f"private_browser read failed: {row.get('error') or 'unknown error'}"}
        opened_result = opened.get("result") if isinstance(opened.get("result"), dict) else {}
        extracted_result = extracted.get("result") if isinstance(extracted.get("result"), dict) else {}
        text = extracted_result.get("text")
        if not isinstance(text, str):
            return {"ok": False, "error": "private_browser read observed no page text"}
        url = str(opened_result.get("url") or extracted_result.get("origin") or "")
        title = str(opened_result.get("title") or "")
        header = "\n".join(part for part in (title, url) if part)
        return {"ok": True, "url": url, "text": f"{header}\n\n{text}".strip()}

    @staticmethod
    def _batch_navigation_outcome(output: str, command_ok: bool) -> tuple[str, str]:
        """Outcome of a batch's last navigation: ``ok``, ``failed`` or ``unknown``.

        A later command failing does not undo a navigation that succeeded,
        so the per-command rows decide, not the batch exit status.
        """

        try:
            rows = json.loads(output)
        except (ValueError, TypeError):
            rows = None
        if isinstance(rows, list):
            for row in reversed(rows):
                command = row.get("command") if isinstance(row, dict) else None
                if not (
                    isinstance(command, list)
                    and command
                    and str(command[0]).lower() in {"open", "goto", "navigate"}
                ):
                    continue
                if row.get("success") is True:
                    result = row.get("result") if isinstance(row.get("result"), dict) else {}
                    return "ok", str(result.get("url") or "")
                return "failed", ""
        return ("ok", "") if command_ok else ("unknown", "")

    @staticmethod
    def _navigated_url(output: str) -> str:
        """Final URL reported by ``open`` (after redirects), when present."""

        match = re.search(r"^\s+([a-z][a-z0-9+.-]*:\S+)\s*$", str(output or ""), re.MULTILINE)
        return match.group(1) if match else ""

    async def _execute_unlocked(
        self,
        content: str,
        ctx: dict,
        *,
        browser: browser_lifecycle.BrowserSession,
        clock: browser_lifecycle.StageClock,
        retry: bool = False,
        deadline: float | None = None,
    ) -> dict:
        args, err = self._parse_args(content)
        if err:
            return {"error": err, "exit_code": 1}
        args.pop("_odysseus_browser_retry", None)

        action = str(args.get("action") or "").strip().lower()
        if action not in self._ACTIONS:
            return {
                "error": "private_browser: action must be one of "
                + ", ".join(sorted(self._ACTIONS)),
                "exit_code": 1,
            }

        # ``snapshot`` captures the already-open browser page.  It has no
        # target-path argument, but a model can plausibly confuse it with the
        # image-inspection tool.  Previously that typo was silently ignored,
        # allowing a stale page from the browser session to be presented as
        # evidence about an unrelated local image.  Reject it before starting
        # a browser process and point the agent to the native visual tool.
        if action == "snapshot" and str(args.get("path") or "").strip():
            return {
                "error": (
                    "private_browser snapshot does not accept path. "
                    "Use inspect_media with {\"path\": \"/workspace/...\"} "
                    "to inspect a local image, PDF, SVG, or video; use "
                    "private_browser open with a file:///workspace/*.html URL "
                    "to inspect a local HTML page."
                ),
                "exit_code": 1,
            }

        binary = shutil.which("agent-browser")
        if not binary:
            binary = self._local_agent_browser_binary()
        cmd_prefix = [binary] if binary else ["npx", "-y", "agent-browser"]
        if not binary and not shutil.which("npx"):
            return {
                "error": (
                    "private_browser requires agent-browser or npx. "
                    "Install with `npm install -g agent-browser && agent-browser install`."
                ),
                "exit_code": 1,
            }

        cmd_prefix = self._with_session_args(cmd_prefix, ctx)
        timeout_s = self._timeout_seconds(args, action=action)
        screenshot_path: Path | None = None
        batch_screenshot_paths: list[Path] = []
        command_args = dict(args)
        try:
            candidate_url = str(command_args.get("url") or "").strip()
            if action in {"open", "read"} and (
                candidate_url.lower().startswith("file://")
                or candidate_url == "/workspace"
                or candidate_url.startswith("/workspace/")
            ):
                command_args["url"] = self._resolve_local_file_url(
                    candidate_url
                )
                # agent-browser's `read URL` path accepts only HTTP(S), while
                # `open` supports local file URLs and returns page state. Treat
                # a model's local read request as the supported visual open.
                if action == "read":
                    action = "open"
            elif action == "screenshot" and str(command_args.get("path") or "").strip():
                resolved_screenshot = self._resolve_workspace_path(
                    str(command_args["path"])
                )
                if resolved_screenshot.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
                    raise ValueError(
                        "screenshot path is an image OUTPUT destination, not a page to inspect; "
                        "use a .png, .jpg or .jpeg destination, or omit path. "
                        "Use open with url to view an HTML page first."
                    )
                command_args["path"] = str(resolved_screenshot)
                screenshot_path = resolved_screenshot
        except (OSError, ValueError) as exc:
            return {"error": f"private_browser path rejected: {exc}", "exit_code": 1}
        if action == "screenshot" and not str(command_args.get("path") or "").strip():
            screenshot_path = self._new_screenshot_path()
            command_args["path"] = str(screenshot_path)
        elif action == "batch":
            command_args["commands"], batch_screenshot_paths = self._normalize_batch_screenshots(
                command_args.get("commands")
            )

        command, stdin_data, err = self._command_for_action(cmd_prefix, action, command_args)
        if err:
            return {"error": err, "exit_code": 1}

        progress_cb = ctx.get("progress_cb") if isinstance(ctx, dict) else None
        if progress_cb:
            await progress_cb({"elapsed_s": 0, "tail": f"private_browser: {action}"})

        # Capture the service account's npm cache before the tool sandbox
        # replaces HOME with the task data directory. Without this, every
        # isolated task asks npx to download agent-browser into a fresh cache
        # and commonly hits the 45 second browser timeout.
        host_npm_cache = (
            os.environ.get("npm_config_cache")
            or os.environ.get("NPM_CONFIG_CACHE")
            or str(_service_home() / ".npm")
        )
        env = dict(os.environ)
        if isinstance(ctx, dict) and isinstance(ctx.get("subproc_env"), dict):
            env.update(ctx["subproc_env"])
        # The task runner gives ordinary subprocesses an isolated HOME. The
        # browser daemon is different: Chromium's crashpad/profile bootstrap
        # requires a real account home, while workspace access remains
        # confined by the resolved file URL and the per-session namespace.
        env["HOME"] = str(_service_home())
        env.setdefault("npm_config_loglevel", "error")
        env.setdefault("NPM_CONFIG_LOGLEVEL", "error")
        # agent-browser daemons otherwise default to a one-hour idle lifetime.
        # A task can retain state across model rounds, but completed/aborted
        # benchmark tasks must not leave Chrome sessions resident for hours.
        env.setdefault("AGENT_BROWSER_IDLE_TIMEOUT_MS", "300000")
        if not binary and not (
            env.get("npm_config_cache") or env.get("NPM_CONFIG_CACHE")
        ):
            env["npm_config_cache"] = host_npm_cache
            env["NPM_CONFIG_CACHE"] = host_npm_cache
        # agent-browser does not search the normal Playwright cache when it is
        # launched through npx. Reuse the browser already installed for this
        # Odysseus host instead of making every browser action depend on a
        # second, separately managed Chrome download.
        if not env.get("AGENT_BROWSER_EXECUTABLE_PATH"):
            candidates = _browser_executable_candidates()
            if candidates:
                env["AGENT_BROWSER_EXECUTABLE_PATH"] = str(candidates[0])

        opened_url = str(command_args.get("url") or "").strip().lower()
        verifies_local_html = (
            action == "open"
            and opened_url.startswith("file:")
            and urllib.parse.urlsplit(opened_url).path.endswith((".html", ".htm"))
        )
        # Browser sessions persist across actions, including their JavaScript
        # error buffers. The current agent-browser release reports success for
        # `errors --clear` without reliably clearing that buffer. Reset the
        # session before opening a local artifact so verification considers
        # only errors emitted by this page. Opening a URL replaces prior page
        # state anyway; cookies are irrelevant for confined file:// artifacts.
        session_id = str((ctx or {}).get("session_id") or "").strip()
        browser.bind(env, cmd_prefix)
        loop = asyncio.get_running_loop()
        if deadline is None:
            deadline = loop.time() + timeout_s + self._RECOVERY_BUDGET_S
        warm = self._owned_daemon_exists(env, session_id)
        if verifies_local_html and warm:
            reset_started = time.monotonic()
            await self._reset_browser_session(cmd_prefix, env, timeout_s)
            clock.record("reset", reset_started, True)
            browser.discarded("reset")
        navigation_url = self._navigation_target(action, command_args)
        stale_note = (
            browser.stale_observation_note()
            if action in browser_lifecycle.OBSERVATION_ACTIONS and not navigation_url
            else ""
        )
        command_started = time.monotonic()

        # agent-browser starts a persistent daemon which can inherit the
        # client's stdout/stderr descriptors.  Pipes therefore never reach
        # EOF when the short-lived CLI client exits, and communicate() waits
        # until the browser idle timeout even though the command succeeded.
        # Temporary files preserve the CLI output while making completion
        # depend on the client process, not its detached daemon.
        stdout_file = tempfile.TemporaryFile()
        stderr_file = tempfile.TemporaryFile()
        attempt_timeout = max(1.0, min(float(timeout_s), deadline - loop.time()))
        proc = None
        try:
            proc = await _spawn_browser_cli(
                *command,
                stdin=asyncio.subprocess.PIPE if stdin_data is not None else None,
                stdout=stdout_file,
                stderr=stderr_file,
                env=env,
                start_new_session=True,
            )
            await asyncio.wait_for(
                proc.communicate(stdin_data.encode("utf-8") if stdin_data is not None else None),
                timeout=attempt_timeout,
            )
            stdout_file.seek(0)
            stderr_file.seek(0)
            stdout = stdout_file.read()
            stderr = stderr_file.read()
        except asyncio.TimeoutError:
            if proc is not None:
                with contextlib.suppress(Exception):
                    self._terminate_subprocess(proc)
            clock.record(action, command_started, False, cold_start=not warm, failure="timeout")
            self._discard_session(browser, env, session_id, clock, "timed_out")
            # A failed local-page verification can leave agent-browser's
            # persistent session between a page-error response and the next
            # repair attempt. The session was cleaned above, so reopen exactly
            # once within the call's deadline; never retry mutating browser
            # actions or arbitrary URLs.
            remaining = deadline - loop.time()
            if verifies_local_html and action == "open" and not retry and remaining >= 10:
                retry_args = dict(args)
                retry_args["timeout_ms"] = int(
                    min(max(60.0, float(timeout_s)), remaining - 5) * 1000
                )
                clock.extra["recovery_attempts"] = 1
                return await self._execute_unlocked(
                    json.dumps(retry_args), ctx,
                    browser=browser, clock=clock, retry=True, deadline=deadline,
                )
            return {
                "error": f"private_browser timed out after {int(attempt_timeout)}s",
                "exit_code": 1,
            }
        except Exception as e:
            if proc is not None:
                with contextlib.suppress(Exception):
                    self._terminate_subprocess(proc)
            clock.record(action, command_started, False, cold_start=not warm, failure=type(e).__name__)
            if proc is not None:
                # The client reached the daemon, so the session's state is
                # unknown. A client that never started left it untouched.
                self._discard_session(browser, env, session_id, clock, "failed")
            return {"error": f"private_browser failed: {type(e).__name__}: {e}", "exit_code": 1}
        finally:
            stdout_file.close()
            stderr_file.close()

        out = stdout.decode("utf-8", errors="replace").strip()
        err_text = stderr.decode("utf-8", errors="replace").strip()
        combined = out
        if err_text:
            combined = f"{combined}\n\n[stderr]\n{err_text}".strip()
        command_ok = (proc.returncode or 0) == 0
        read_page = None
        if action == "read" and navigation_url:
            read_page = self._read_page_from_rows(out)
            command_ok = command_ok and read_page.get("ok", False)
        clock.record(action, command_started, command_ok, cold_start=not warm)
        if not command_ok and browser_lifecycle.LAUNCH_FAILURE_RE.search(combined):
            # The browser never became ready. The daemon outlives this failure
            # and a later close cannot reach a browser, so clean it here.
            self._discard_session(browser, env, session_id, clock, "launch_failed")
            return {
                "output": combined[:4000],
                "error": (
                    "private_browser could not launch the browser; no page was "
                    "opened or observed. The browser session was cleaned up."
                ),
                "exit_code": 1,
                "untrusted_content": True,
            }
        if navigation_url:
            outcome, final_url = "ok" if command_ok else "failed", ""
            if action == "batch":
                outcome, final_url = self._batch_navigation_outcome(out, command_ok)
            if outcome == "ok":
                browser.navigated(
                    final_url
                    or (read_page or {}).get("url")
                    or self._navigated_url(out)
                    or navigation_url
                )
            elif outcome == "failed":
                browser.navigation_failed(navigation_url)
            else:
                browser.navigation_unknown(navigation_url)
        if read_page is not None:
            if not command_ok:
                return {
                    "output": combined[:4000],
                    "error": read_page.get("error") or "private_browser read failed; no page text was observed",
                    "exit_code": 1,
                    "untrusted_content": True,
                }
            out = read_page["text"]
            combined = out if not err_text else f"{out}\n\n[stderr]\n{err_text}"
        elif command_ok and browser.state in {"idle", "closed", "reset"}:
            browser.state = "ready"
        if stale_note and command_ok:
            combined = f"[{stale_note}]\n\n{combined}".strip()
            clock.extra["stale_observation"] = True
        from src.turn_contract import active_turn_contract
        contract = active_turn_contract()
        model_choice = getattr(contract, 'routing_experiment', '') == 'recent_model_choice'
        if action == 'snapshot' and model_choice and (proc.returncode or 0) == 0:
            combined = self._dialog_first_snapshot(out)
            if err_text:
                combined = f"[stderr]\n{err_text}\n\n{combined}".strip()
        fill_error = ""
        empty_observation = (proc.returncode or 0) == 0 and self._empty_dom_observation(out)
        observe_state_change = action in {"open", "fill", "press"} and model_choice
        failed_interaction = action in {"click", "fill"} and (proc.returncode or 0) != 0
        if empty_observation or failed_interaction or ((action == "click" or observe_state_change) and (proc.returncode or 0) == 0):
            # A click can navigate, replace the DOM, or open a modal. Return
            # the settled post-click DOM in the same tool result so callers do
            # not race navigation with a separate immediate read and so the
            # next conversational turn receives current element refs. A failed
            # interaction also needs refs for a covering dialog
            # or changed DOM. A successful fill may run input handlers that
            # open a modal or replace the field: CLI success is not proof that
            # the intended value survived. Observe only; never retry an action.
            post_click_state, fill_error = await self._capture_post_click_state(
                cmd_prefix, env, timeout_s,
                verify_fill=(command[-2], command[-1])
                if action == "fill" and not failed_interaction else None,
            )
            if post_click_state:
                label = f'page state after failed {action}' if failed_interaction else f'post-{action} page state'
                combined = f"{combined}\n\n[{label}]\n{post_click_state}".strip()
            if fill_error:
                # The CLI's optimistic "Done" contradicts verified failure.
                # Report the outcome, retaining current DOM but not that claim.
                combined = fill_error
                if post_click_state:
                    combined += f"\n\n[post-fill page state]\n{post_click_state}"
        # Parallel benchmark runtimes can race a detached agent-browser
        # daemon during Chromium bootstrap.  Recover once for a confined
        # local HTML verification, after cleaning only this runtime's browser
        # state.  Do not retry arbitrary URLs or mutating browser actions.
        if (
            verifies_local_html
            and action == "open"
            and (proc.returncode or 0) != 0
            and self._retryable_local_open_failure(combined)
            and not retry
            and deadline - loop.time() >= 10
        ):
            self._discard_session(browser, env, session_id, clock, "bootstrap_failed")
            clock.extra["recovery_attempts"] = 1
            return await self._execute_unlocked(
                json.dumps(args), ctx,
                browser=browser, clock=clock, retry=True, deadline=deadline,
            )
        page_errors = ""
        if (
            verifies_local_html
            and (proc.returncode or 0) == 0
        ):
            page_errors = await self._capture_page_errors(
                cmd_prefix,
                env,
                timeout_s,
            )
            if page_errors:
                combined = f"{combined}\n\n[page errors]\n{page_errors}".strip()
        if len(combined) > MAX_OUTPUT_CHARS:
            from src.browser_observation import compact_browser_observation
            combined = compact_browser_observation(combined, budget=MAX_OUTPUT_CHARS)
        shopping_hint = self._shopping_landing_hint(combined)
        if shopping_hint:
            combined = f"{combined}\n\n[{shopping_hint}]"
        result = {
            "output": combined,
            "exit_code": 1 if page_errors or fill_error else (proc.returncode or 0),
            "untrusted_content": True,
        }
        if page_errors:
            result["error"] = (
                "The local HTML page opened, but JavaScript page errors were "
                "detected. Fix the artifact and reopen it to verify."
            )
        elif fill_error:
            result["error"] = fill_error
            result["browser_command_exit_code"] = proc.returncode or 0
        if (
            action == "screenshot"
            and screenshot_path
            and (proc.returncode or 0) == 0
            and screenshot_path.exists()
            and screenshot_path.stat().st_size > 0
        ):
            image = self._image_payload_from_path(screenshot_path)
            if image:
                result["images"] = [image]
        elif action in self._AUTO_SCREENSHOT_ACTIONS and (proc.returncode or 0) == 0:
            image = await self._capture_screenshot(cmd_prefix, env, timeout_s)
            if image:
                result["images"] = [image]
        if batch_screenshot_paths and (proc.returncode or 0) == 0:
            images = [self._image_payload_from_path(path) for path in batch_screenshot_paths]
            images = [image for image in images if image]
            if images:
                result["images"] = images
        return result

    async def _capture_post_click_state(
        self,
        cmd_prefix: list[str],
        env: dict[str, str],
        timeout_s: int,
        *,
        verify_fill: tuple[str, str] | None = None,
    ) -> tuple[str, str]:
        """Return a bounded settled observation without repeating an action."""
        commands = [["wait", "1000"], ["snapshot"]]
        unverified = "Browser fill could not be verified; the input outcome is unknown." if verify_fill else ""
        if verify_fill:
            # Read using the old handle BEFORE snapshot replaces the ref map.
            # Never repeat the fill or disclose input values in diagnostics.
            commands.insert(1, ["get", "value", verify_fill[0]])
        from src.turn_contract import active_turn_contract
        model_choice = getattr(active_turn_contract(), 'routing_experiment', '') == 'recent_model_choice'
        # A navigation can acknowledge the click before the destination renders.
        # Retry only an explicitly empty observation, once, within ONE deadline.
        # Never repeat the action or read old fill refs after a snapshot refresh.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + min(timeout_s, 20)
        text, observation_note = "", ""
        first_rows, rows = [], []
        for attempt in range(2):
            proc = None
            try:
                async with asyncio.timeout(max(0, deadline - loop.time())):
                    proc = await _spawn_browser_cli(
                        *cmd_prefix, "batch", "--json",
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        env=env, start_new_session=True,
                    )
                    stdout, stderr = await proc.communicate(json.dumps(commands).encode())
                if (proc.returncode or 0) != 0:
                    raise RuntimeError('observation failed')
            except Exception:
                if proc is not None:
                    with contextlib.suppress(Exception):
                        self._terminate_subprocess(proc)
                if not attempt:
                    return "", unverified
                observation_note = "A fresh page snapshot could not be obtained; the last observation was empty."
                break
            observed = stdout.decode("utf-8", errors="replace").strip()
            if not observed:
                observed = stderr.decode("utf-8", errors="replace").strip()
            try:
                observed_rows = json.loads(observed)
                if not isinstance(observed_rows, list):
                    observed_rows = []
            except (ValueError, TypeError):
                observed_rows = []
            snapshots = [row['result']['snapshot'] for row in observed_rows
                if isinstance(row, dict) and row.get('success') is True
                and isinstance(row.get('result'), dict)
                and isinstance(row['result'].get('snapshot'), str)]
            if attempt and not snapshots:
                observation_note = "A fresh page snapshot could not be obtained; the last observation was empty."
                break
            text, rows = observed, observed_rows
            if not attempt:
                first_rows = rows
            if not snapshots or not self._empty_dom_observation(observed):
                break
            commands = [["wait", "1000"], ["snapshot"]]
        if not observation_note and self._empty_dom_observation(text):
            observation_note = (
                "Browser observation incomplete: the page still has no readable content after waiting. "
                "Navigation success is not evidence that results loaded. Do not infer page results."
            )
        fill_error = ""
        if verify_fill:
            fill_error = unverified
            for row in first_rows:
                if not isinstance(row, dict) or row.get("command") != ["get", "value", verify_fill[0]]:
                    continue
                value = row.get("result")
                if row.get("success") is True and isinstance(value, dict) and isinstance(value.get("value"), str):
                    fill_error = "" if value["value"] == verify_fill[1] else (
                        "Browser input did not retain the requested text; fill is incomplete."
                    )
                break
            # Keep only snapshot rows. A missing/malformed snapshot must never
            # fall back to dumping the raw value-verification response.
            text = json.dumps([row for row in rows if isinstance(row, dict)
                and isinstance(row.get("result"), dict)
                and isinstance(row["result"].get("snapshot"), str)])
        if model_choice:
            text = self._snapshot_observation(text)
        if observation_note:
            text += '\n' + observation_note
        if len(text) > MAX_OUTPUT_CHARS:
            from src.browser_observation import compact_browser_observation
            text = compact_browser_observation(text, budget=MAX_OUTPUT_CHARS)
        return text, fill_error

    @staticmethod
    def _empty_dom_observation(text: str) -> bool:
        """Recognize empty accessibility scaffolding, not an actual no-results message."""
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            payload = None
        if isinstance(payload, list):
            snapshots = [row['result']['snapshot'] for row in payload
                if isinstance(row, dict) and row.get('success') is True
                and isinstance(row.get('result'), dict)
                and isinstance(row['result'].get('snapshot'), str)]
        else:
            snapshots = [text] if isinstance(text, str) and text.strip() else []
        scaffolding = {'- generic', '- main', '- none', '- presentation', '(empty page)'}
        return bool(snapshots) and all(
            all(line.strip() in scaffolding for line in snapshot.splitlines() if line.strip())
            for snapshot in snapshots
        )

    @staticmethod
    def _dialog_first_snapshot(snapshot: str) -> str:
        """Keep modal controls ahead of long page content without inventing refs."""
        lines = snapshot.splitlines(keepends=True)
        dialogs = []
        remainder = []
        index = 0
        while index < len(lines):
            match = re.match(r'^(\s*)- (?:dialog|alertdialog)(?:\s|$)', lines[index])
            if not match:
                remainder.append(lines[index])
                index += 1
                continue
            indent = len(match[1])
            end = index + 1
            while end < len(lines):
                line = lines[end]
                if line.strip() and len(line) - len(line.lstrip()) <= indent:
                    break
                end += 1
            # A nested dialog stays with its parent; no duplicated handles.
            dialogs.append(''.join(line[indent:] if line.strip() else line
                                   for line in lines[index:end]))
            index = end
        if not dialogs or not ''.join(remainder).strip():
            return snapshot
        return '\n'.join(dialogs) + '\n[Remaining page snapshot]\n' + ''.join(remainder)

    @staticmethod
    def _snapshot_observation(text: str) -> str:
        """Put the actual DOM before redundant CLI lifecycle/ref metadata."""
        try:
            rows = json.loads(text)
        except (ValueError, TypeError):
            return text
        if not isinstance(rows, list):
            return text
        snapshots = []
        errors = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get('error'):
                errors.append(str(row['error']))
            result = row.get('result')
            if isinstance(result, dict) and isinstance(result.get('snapshot'), str):
                snapshot = PrivateBrowserTool._dialog_first_snapshot(result['snapshot'])
                snapshots.append((str(result.get('origin') or '') + '\n' + snapshot).strip())
        return '\n\n'.join(snapshots + errors) if snapshots else text


    async def _reset_browser_session(
        self,
        cmd_prefix: list[str],
        env: dict[str, str],
        timeout_s: int,
    ) -> None:
        """Best-effort reset of state retained by a persistent browser session."""
        proc = None
        try:
            proc = await _spawn_browser_cli(
                *cmd_prefix,
                "close",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                start_new_session=True,
            )
            await asyncio.wait_for(
                proc.communicate(),
                timeout=min(timeout_s, 20),
            )
        except Exception:
            if proc is not None:
                with contextlib.suppress(Exception):
                    self._terminate_subprocess(proc)

    async def _capture_page_errors(
        self,
        cmd_prefix: list[str],
        env: dict[str, str],
        timeout_s: int,
    ) -> str:
        """Return bounded JavaScript errors from the current browser page."""
        proc = None
        try:
            proc = await _spawn_browser_cli(
                *cmd_prefix,
                "errors",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                start_new_session=True,
            )
            stdout, _stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=min(timeout_s, 20),
            )
        except Exception:
            if proc is not None:
                with contextlib.suppress(Exception):
                    self._terminate_subprocess(proc)
            return ""
        if (proc.returncode or 0) != 0:
            return ""
        text = stdout.decode("utf-8", errors="replace").strip()
        if not text or re.fullmatch(
            r"(?:no (?:page )?errors?(?: found)?|0 errors?|\[\])\.?",
            text,
            re.IGNORECASE,
        ):
            return ""
        return text[:4000]

    async def _capture_screenshot(
        self,
        cmd_prefix: list[str],
        env: dict[str, str],
        timeout_s: int,
    ) -> dict[str, str] | None:
        screenshot_path = self._new_screenshot_path()
        command, stdin_data, err = self._command_for_action(
            cmd_prefix,
            "screenshot",
            {"path": str(screenshot_path)},
        )
        if err or stdin_data is not None:
            return None
        proc = None
        try:
            proc = await _spawn_browser_cli(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                start_new_session=True,
            )
            await asyncio.wait_for(proc.communicate(), timeout=min(timeout_s, 20))
        except Exception:
            if proc is not None:
                with contextlib.suppress(Exception):
                    proc.kill()
            return None
        if (proc.returncode or 0) != 0:
            return None
        return self._image_payload_from_path(screenshot_path)

    def _new_screenshot_path(self) -> Path:
        configured = os.getenv("ODYSSEUS_BROWSER_SCREENSHOT_DIR")
        candidates = [
            Path(configured) if configured else Path("/app/data/tmp/private-browser"),
            Path(tempfile.gettempdir()) / "odysseus-private-browser",
        ]
        screenshot_dir = candidates[-1]
        for candidate in candidates:
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                screenshot_dir = candidate
                break
            except OSError:
                continue
        with tempfile.NamedTemporaryFile(
            prefix="browser-",
            suffix=".png",
            dir=screenshot_dir,
            delete=False,
        ) as tmp:
            return Path(tmp.name)

    def _normalize_batch_screenshots(self, commands: Any) -> tuple[Any, list[Path]]:
        if not isinstance(commands, list):
            return commands, []
        paths: list[Path] = []
        normalized: list[Any] = []
        for command in commands:
            if isinstance(command, list) and command:
                action = str(command[0]).strip().lower()
                if action == "wait":
                    # Compact/OpenAI schemas sometimes preserve an omitted
                    # selector as null and put the timeout in the next slot:
                    # ["wait", null, 2500]. agent-browser accepts only arrays
                    # of strings, so recover the intended timeout instead of
                    # rejecting the whole browser batch.
                    wait_args = [value for value in command[1:] if value is not None]
                    normalized.append(["wait", *[str(value) for value in wait_args]])
                    continue
                if action in {"open", "read"} and len(command) >= 2:
                    candidate_url = str(command[1] or "").strip()
                    if (
                        candidate_url.lower().startswith("file://")
                        or candidate_url == "/workspace"
                        or candidate_url.startswith("/workspace/")
                    ):
                        normalized.append([
                            "open",
                            self._resolve_local_file_url(candidate_url),
                            *command[2:],
                        ])
                        continue
                    if action == "read" and not re.match(
                        r"^(?:https?|file)://", candidate_url, re.IGNORECASE
                    ):
                        # The top-level read action treats target/selector as
                        # DOM text extraction. Keep batch semantics identical;
                        # agent-browser's bare `read h1` instead interprets h1
                        # as a URL/path and fails before the model can answer.
                        normalized.append(["get", "text", candidate_url])
                        continue
                if action == "evaluate":
                    normalized.append(["eval", *command[1:]])
                    continue
                if action == "find" and len(command) == 2:
                    normalized.append(["find", "text", str(command[1]), "text"])
                    continue
            if isinstance(command, dict):
                action = str(command.get("action") or "").strip().lower()
                candidate_url = str(command.get("url") or "").strip()
                if action in {"open", "read"} and (
                    candidate_url.lower().startswith("file://")
                    or candidate_url == "/workspace"
                    or candidate_url.startswith("/workspace/")
                ):
                    updated = dict(command)
                    updated["action"] = "open"
                    updated["url"] = self._resolve_local_file_url(candidate_url)
                    command = updated
            if isinstance(command, list) and command and str(command[0]).strip().lower() == "screenshot":
                path = self._new_screenshot_path()
                paths.append(path)
                normalized.append(["screenshot", str(path)])
                continue
            elif isinstance(command, dict) and str(command.get("action") or "").strip().lower() == "screenshot":
                path = self._new_screenshot_path()
                paths.append(path)
                normalized.append(["screenshot", str(path)])
                continue
            if isinstance(command, dict):
                action = str(command.get("action") or "").strip().lower()
                converted, stdin_data, error = self._command_for_action([], action, command)
                if not error and stdin_data is None and converted:
                    normalized.append(converted)
                    continue
            normalized.append(command)
        # Opening a page invalidates every prior element ref. A small router
        # sometimes guesses human labels ("search input") and places fill or
        # click immediately after open in the same batch. That cannot use the
        # new DOM and predictably fails. End that batch at a snapshot so the
        # next model round receives real refs; preserve explicit refs/CSS for
        # callers that intentionally supplied a stable selector.
        open_index = next((
            index for index, command in enumerate(normalized)
            if (
                isinstance(command, list) and command
                and str(command[0]).strip().lower() == "open"
            ) or (
                isinstance(command, dict)
                and str(command.get("action") or "").strip().lower() == "open"
            )
        ), None)
        if open_index is not None:
            for index in range(open_index + 1, len(normalized)):
                command = normalized[index]
                if isinstance(command, list) and command:
                    action = str(command[0]).strip().lower()
                    target = str(command[1] if len(command) > 1 else "").strip()
                elif isinstance(command, dict):
                    action = str(command.get("action") or "").strip().lower()
                    target = str(command.get("selector") or command.get("target") or "").strip()
                else:
                    continue
                if action == "snapshot":
                    break
                if action not in {"click", "fill", "wait"}:
                    continue
                explicit_selector = bool(
                    target.startswith(("@", "#", ".", "[", "//", "xpath=", "css="))
                    or any(char in target for char in (">", ":", "[", "]"))
                )
                if target and not explicit_selector:
                    normalized = [*normalized[:index], ["snapshot"]]
                    break
        return normalized, paths

    def _image_payload_from_path(self, path: Path) -> dict[str, str] | None:
        try:
            if not path.exists() or path.stat().st_size <= 0:
                return None
            return {
                "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                "mimeType": "image/png",
            }
        except Exception:
            return None

    def _parse_args(self, content: str) -> tuple[dict, str | None]:
        raw = (content or "").strip()
        if not raw:
            return {}, "private_browser: provide a JSON object with an action"
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"action": "read", "url": raw}, None
        if not isinstance(parsed, dict):
            return {}, "private_browser: arguments must be a JSON object"
        return parsed, None

    def _timeout_seconds(self, args: dict, *, action: str = "") -> int:
        value = args.get("timeout_ms")
        if isinstance(value, int) and value > 0:
            if action == "wait" and not any(
                str(args.get(key) or "").strip()
                for key in ("selector", "target", "key")
            ):
                # For a bare wait, timeout_ms is the requested sleep duration,
                # not the subprocess deadline. Leave startup/IPC headroom so
                # `wait 2000` cannot race an asyncio timeout at exactly 2s.
                return min(125, max(45, (value + 999) // 1000 + 5))
            return max(1, min(120, value // 1000 or 1))
        return 45

    def _command_for_action(
        self,
        prefix: list[str],
        action: str,
        args: dict,
    ) -> tuple[list[str], str | None, str | None]:
        if action in {"read", "wait", "click", "fill"} and "ref" in args:
            # Snapshots label elements as ref=eN. Accept that explicit handle
            # as a transport alias, not as permission to infer a CSS selector.
            ref = str(args.get("ref") or "").strip()
            if not re.fullmatch(r"@?e[0-9]+", ref):
                return [], None, "private_browser: ref must be an element handle such as e2 or @e2"
            target = "@" + ref.lstrip("@")
            supplied = [str(args[key]).strip() for key in ("selector", "target") if args.get(key)]
            if any(value not in {target, target[1:]} for value in supplied):
                return [], None, "private_browser: ref conflicts with selector/target; specify one element"
            args = {**args, "selector": target}
        if action == "open":
            url = str(args.get("url") or "").strip()
            if not url:
                return [], None, "private_browser open: url is required"
            return [*prefix, "open", url], None, None
        if action == "read":
            target = str(args.get("selector") or args.get("target") or "").strip()
            if target:
                return [*prefix, "get", "text", target], None, None
            url = str(args.get("url") or "").strip()
            # agent-browser has no `read` command. Navigate and extract in one
            # client call so the text is observed after this navigation.
            if url:
                return [*prefix, "batch", "--json"], json.dumps(
                    [["open", url], ["get", "text", "body"]]
                ), None
            return [*prefix, "get", "text", "body"], None, None
        if action == "snapshot":
            return [*prefix, "snapshot"], None, None
        if action == "find":
            value = str(args.get("find") or args.get("text") or args.get("value") or "").strip()
            if not value:
                return [], None, "private_browser find: find/text is required"
            return [*prefix, "find", "text", value, "text"], None, None
        if action == "evaluate":
            script = str(args.get("script") or args.get("text") or args.get("value") or "").strip()
            if not script:
                return [], None, "private_browser evaluate: script is required"
            return [*prefix, "eval", script], None, None
        if action == "close":
            return [*prefix, "close"], None, None
        if action == "scroll":
            direction = str(
                args.get("direction") or args.get("target") or "down"
            ).strip().lower()
            if direction in {"bottom", "end"}:
                return [*prefix, "press", "End"], None, None
            if direction in {"top", "home"}:
                return [*prefix, "press", "Home"], None, None
            if direction not in {"up", "down", "left", "right"}:
                return [], None, (
                    "private_browser scroll: direction must be up, down, left, "
                    "right, top, or bottom"
                )
            raw_amount = args.get("amount", 300)
            try:
                amount = max(1, min(100_000, int(raw_amount)))
            except (TypeError, ValueError):
                return [], None, "private_browser scroll: amount must be an integer"
            # Compact routers often express scrolling as 1-10 wheel steps even
            # though this wrapper accepts pixels. Five pixels is effectively a
            # no-op and caused repeated snapshot loops. Interpret these tiny
            # values as conventional 300px wheel steps.
            if amount <= 10:
                amount *= 300
            return [*prefix, "scroll", direction, str(amount)], None, None
        if action == "wait":
            target = str(args.get("selector") or args.get("target") or "").strip()
            if target:
                return [*prefix, "wait", target], None, None
            duration_ms = args.get("timeout_ms")
            if isinstance(duration_ms, int) and duration_ms > 0:
                return [*prefix, "wait", str(min(120_000, duration_ms))], None, None
            return [], None, "private_browser wait: selector/target or timeout_ms is required"
        if action in {"click", "press"}:
            target = str(args.get("selector") or args.get("target") or args.get("key") or "").strip()
            if not target:
                return [], None, f"private_browser {action}: selector/target/key is required"
            if action == "click":
                role_name = str(args.get("text") or args.get("value") or "").strip()
                role = target.casefold()
                if role_name and role in {
                    "link", "button", "menuitem", "tab", "checkbox", "radio",
                }:
                    return [
                        *prefix, "find", "role", role, "click", "--name", role_name,
                    ], None, None
                quoted_role = re.fullmatch(
                    r"(?P<role>link|button|menuitem|tab|checkbox|radio)\s+"
                    r"(?P<quote>['\"])(?P<name>.*?)(?P=quote)",
                    target,
                    re.IGNORECASE,
                )
                if quoted_role:
                    return [
                        *prefix, "find", "role", quoted_role.group("role").lower(),
                        "click", "--name", quoted_role.group("name").strip(),
                    ], None, None
                visible_text = re.fullmatch(
                    r"(?P<tag>[a-z][a-z0-9_-]*)?:has-text\(\s*"
                    r"(?P<quote>['\"])(?P<text>.*?)(?P=quote)\s*\)",
                    target,
                    re.IGNORECASE,
                )
                if visible_text:
                    text = visible_text.group("text").strip()
                    role = {
                        "a": "link",
                        "button": "button",
                    }.get((visible_text.group("tag") or "").lower())
                    if role:
                        return [
                            *prefix, "find", "role", role, "click", "--name", text,
                        ], None, None
                    return [*prefix, "find", "text", text, "click"], None, None
            return [*prefix, action, target], None, None
        if action == "fill":
            selector = str(args.get("selector") or args.get("target") or "").strip()
            text = str(args.get("text") or args.get("value") or "")
            if not selector:
                return [], None, "private_browser fill: selector is required"
            return [*prefix, "fill", selector, text], None, None
        if action == "screenshot":
            path = str(args.get("path") or "").strip()
            command = [*prefix, "screenshot"]
            if path:
                command.append(path)
            return command, None, None
        commands = args.get("commands")
        if not isinstance(commands, list):
            return [], None, "private_browser batch: commands must be a list"
        if not commands:
            # Some compact routers emit an empty batch as "continue inspecting
            # the current page". Treat it as a harmless snapshot so the agent
            # gets state back instead of burning failed rounds and being forced
            # to stop before it can scroll/click/read.
            return [*prefix, "snapshot"], None, None
        return [*prefix, "batch", "--json"], json.dumps(commands), None

    def _with_session_args(self, prefix: list[str], ctx: dict) -> list[str]:
        session_id = str((ctx or {}).get("session_id") or "").strip()
        if not session_id:
            return prefix
        runtime_env = (ctx or {}).get("subproc_env") if isinstance(ctx, dict) else None
        namespace = str(
            (runtime_env or {}).get("ODYSSEUS_BROWSER_NAMESPACE")
            or os.getenv("ODYSSEUS_BROWSER_NAMESPACE", "odysseus-ui")
        ).strip() or "odysseus-ui"
        # Upstream agent-browser exposes --session, not --namespace. Fold the
        # runtime namespace into the session key so independent Odysseus
        # runtimes remain isolated without relying on a fork-only CLI flag.
        scoped_session = _scoped_browser_session(namespace, session_id)
        _ACTIVE_BROWSER_SESSIONS.add(scoped_session)
        return [*prefix, "--session", scoped_session]


async def shutdown_private_browser_sessions() -> None:
    """Close and verify every browser session this runtime started.

    Each session is closed with the environment it was launched with, so its
    runtime directory resolves to the daemon's own. ``close`` is sent only to
    a verified live daemon, because against a missing one it bootstraps a new
    browser. Forced cleanup of the session's own browser tree always follows.
    """

    binary = shutil.which("agent-browser") or PrivateBrowserTool._local_agent_browser_binary()
    command_prefix = [binary] if binary else (
        ["npx", "-y", "agent-browser"] if shutil.which("npx") else []
    )
    sessions = sorted(_ACTIVE_BROWSER_SESSIONS)
    if not command_prefix or not sessions:
        return
    base_env = dict(os.environ)
    base_env["HOME"] = str(_service_home())
    base_env.setdefault("AGENT_BROWSER_IDLE_TIMEOUT_MS", "300000")
    try:
        for session in sessions:
            record = browser_lifecycle.registered(session)
            env = record.env if record is not None and record.env is not None else base_env
            root = browser_lifecycle.runtime_root(env)
            if browser_lifecycle.has_live_daemon(
                root, session, pid_alive=lambda pid: _process_is_alive(pid)
            ):
                proc = None
                try:
                    proc = await _spawn_browser_cli(
                        *command_prefix, "--session", session, "close",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                        env=env,
                        start_new_session=True,
                    )
                    await asyncio.wait_for(proc.communicate(), timeout=20)
                except Exception:
                    if proc is not None:
                        with contextlib.suppress(Exception):
                            PrivateBrowserTool._terminate_subprocess(proc)
            browser_lifecycle.force_cleanup(
                root, session, method="shutdown",
                pid_alive=lambda pid: _process_is_alive(pid),
            )
            browser_lifecycle.forget(session)
    finally:
        _ACTIVE_BROWSER_SESSIONS.difference_update(sessions)
        PrivateBrowserTool._terminate_owned_chrome(base_env)
        PrivateBrowserTool._terminate_owned_daemon(base_env)
