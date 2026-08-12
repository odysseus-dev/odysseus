"""CPU-local semantic intent hints for request routing.

The router is deliberately advisory. It never grants permissions and it does
not replace deterministic policy checks. The first rollout stage is disabled
by default and can be enabled in shadow mode to measure quality and latency
without changing request behaviour.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, replace
from typing import Callable, Protocol, Sequence

import numpy as np

from src.tool_security import plan_mode_disabled_tools

logger = logging.getLogger(__name__)

INTENT_ROUTER_MODE_ENV = "ODYSSEUS_INTENT_ROUTER_MODE"
INTENT_ROUTER_TIMEOUT_MS_ENV = "ODYSSEUS_INTENT_ROUTER_TIMEOUT_MS"
INTENT_ROUTER_MIN_SCORE_ENV = "ODYSSEUS_INTENT_ROUTER_MIN_SCORE"
INTENT_ROUTER_ACTIVE_MIN_SCORE_ENV = "ODYSSEUS_INTENT_ROUTER_ACTIVE_MIN_SCORE"
INTENT_ROUTER_ACTIVE_MIN_MARGIN_ENV = "ODYSSEUS_INTENT_ROUTER_ACTIVE_MIN_MARGIN"

_DEFAULT_TIMEOUT_MS = 75
_DEFAULT_MIN_SCORE = 0.35
_DEFAULT_ACTIVE_MIN_SCORE = 0.62
_DEFAULT_ACTIVE_MIN_MARGIN = 0.08
_VALID_MODES = frozenset({"off", "shadow", "active"})


class IntentEncoder(Protocol):
    """Small surface shared by FastEmbedClient and deterministic test doubles."""

    def encode(
        self, texts: list[str], normalize_embeddings: bool = True
    ) -> np.ndarray: ...


@dataclass(frozen=True)
class IntentDefinition:
    """One stable route label and the prototypes used to recognize it."""

    name: str
    needs_tools: bool
    domains: tuple[str, ...]
    examples: tuple[str, ...]


@dataclass(frozen=True)
class IntentScore:
    """A ranked semantic label returned to a routing consumer."""

    name: str
    score: float
    needs_tools: bool
    domains: tuple[str, ...]


@dataclass(frozen=True)
class IntentRoute:
    """Compact request-level signal that intentionally excludes prompt text."""

    top_intents: tuple[IntentScore, ...] = ()
    domains: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    needs_tools: bool = False
    source: str = "unavailable"
    rollout_mode: str = "direct"
    elapsed_ms: float = 0.0
    error: str = ""

    def log_fields(
        self,
        *,
        deterministic_needs_tools: bool | None = None,
        deterministic_domains: Sequence[str] = (),
    ) -> dict[str, object]:
        """Return aggregate shadow fields that are safe to log.

        User text is never stored on the route and therefore cannot enter this
        representation accidentally.
        """

        fields: dict[str, object] = {
            "intents": [
                {"name": item.name, "score": round(item.score, 4)}
                for item in self.top_intents
            ],
            "domains": list(self.domains),
            "constraints": list(self.constraints),
            "needs_tools": self.needs_tools,
            "source": self.source,
            "rollout_mode": self.rollout_mode,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "error": self.error,
        }
        if deterministic_needs_tools is not None:
            baseline_domains = tuple(sorted(set(deterministic_domains)))
            semantic_domains = set(self.domains)
            fields["comparison"] = {
                "deterministic_needs_tools": deterministic_needs_tools,
                "needs_tools_disagrees": self.needs_tools
                != deterministic_needs_tools,
                "deterministic_domains": list(baseline_domains),
                "semantic_only_domains": sorted(
                    semantic_domains.difference(baseline_domains)
                ),
                "deterministic_only_domains": sorted(
                    set(baseline_domains).difference(semantic_domains)
                ),
            }
        return fields


@dataclass(frozen=True)
class ActiveIntentSelection:
    """High-confidence additive signals that may affect active routing."""

    intents: tuple[IntentScore, ...] = ()
    domains: tuple[str, ...] = ()
    needs_tools: bool = False
    reason: str = "inactive"


INTENT_DEFINITIONS: tuple[IntentDefinition, ...] = (
    IntentDefinition(
        "chat.explain",
        False,
        (),
        (
            "Explain how deployment works without doing it",
            "How do calendar reminders work?",
            "What does this shell command mean?",
            "Describe the architecture and available options",
        ),
    ),
    IntentDefinition(
        "chat.converse",
        False,
        (),
        (
            "Hello, how are you?",
            "Help me brainstorm some ideas",
            "What do you think about this approach?",
            "Summarize what we have discussed so far",
        ),
    ),
    IntentDefinition(
        "workspace.inspect",
        True,
        ("files",),
        (
            "Inspect the repository and explain the failure",
            "Read the source file and review the diff",
            "Look at the logs and traceback",
            "Find where this function is implemented",
        ),
    ),
    IntentDefinition(
        "workspace.modify",
        True,
        ("files",),
        (
            "Fix the bug in this repository",
            "Implement the requested code change",
            "Refactor this module and update the code",
            "Patch the application to handle this case",
        ),
    ),
    IntentDefinition(
        "workspace.test",
        True,
        ("files",),
        (
            "Run the tests for this project",
            "Execute pytest and fix the failing tests",
            "Lint and type-check the codebase",
            "Benchmark this implementation",
        ),
    ),
    IntentDefinition(
        "git.commit",
        True,
        ("files",),
        (
            "Create a git commit for these changes",
            "Commit the current patch",
            "Stage the files and make a commit",
            "Save this work in version control",
        ),
    ),
    IntentDefinition(
        "git.publish",
        True,
        ("files",),
        (
            "Push the branch and open a pull request",
            "Publish these commits to the remote repository",
            "Create a pull request for this branch",
            "Ship the change through git",
        ),
    ),
    IntentDefinition(
        "web.search",
        True,
        ("web",),
        (
            "Search the web for current information",
            "Look up the latest price online",
            "Check today's weather and news",
            "Trova online il prezzo attuale",
        ),
    ),
    IntentDefinition(
        "research.run",
        True,
        ("web",),
        (
            "Research this topic using several sources",
            "Investigate the options and compare the evidence",
            "Do a deep dive and prepare a cited report",
            "Recherchez ce sujet et comparez plusieurs sources",
        ),
    ),
    IntentDefinition(
        "email.read",
        True,
        ("email",),
        (
            "Check my inbox for unread email",
            "Find the message from my colleague",
            "Summarize my recent emails",
            "Busca el último correo de Ana",
        ),
    ),
    IntentDefinition(
        "email.write",
        True,
        ("email",),
        (
            "Reply to that email",
            "Write and send a message to my colleague",
            "Archive these messages",
            "Responde a ese correo electrónico",
        ),
    ),
    IntentDefinition(
        "calendar.read",
        True,
        ("notes_calendar_tasks",),
        (
            "What is on my calendar tomorrow?",
            "Find my next meeting",
            "Show this week's appointments",
            "Welche Termine habe ich morgen?",
        ),
    ),
    IntentDefinition(
        "calendar.write",
        True,
        ("notes_calendar_tasks",),
        (
            "Schedule a meeting for tomorrow",
            "Add this appointment to my calendar",
            "Reschedule the event to Friday",
            "Ajoute un rendez-vous demain matin",
        ),
    ),
    IntentDefinition(
        "notes.read",
        True,
        ("notes_calendar_tasks",),
        (
            "Find my note about the project",
            "Show the tasks on my todo list",
            "Read my saved checklist",
            "Busca mi nota sobre el proyecto",
        ),
    ),
    IntentDefinition(
        "notes.write",
        True,
        ("notes_calendar_tasks",),
        (
            "Add milk to my todo list",
            "Take a note about this decision",
            "Set a reminder for this afternoon",
            "Aggiungi questa voce alla lista delle cose da fare",
        ),
    ),
    IntentDefinition(
        "document.read",
        True,
        ("documents",),
        (
            "Search my documents for this phrase",
            "Read the uploaded PDF and summarize it",
            "Find the relevant passage in my files",
            "Résume le document que j'ai importé",
        ),
    ),
    IntentDefinition(
        "document.write",
        True,
        ("documents",),
        (
            "Create a document from these notes",
            "Edit the open document",
            "Rewrite this report in the document editor",
            "Aggiorna il documento aperto",
        ),
    ),
    IntentDefinition(
        "ui.control",
        True,
        ("ui",),
        (
            "Open the calendar panel",
            "Show my settings",
            "Turn on the browser control",
            "Switch the interface to another view",
        ),
    ),
    IntentDefinition(
        "system.execute",
        True,
        ("files",),
        (
            "Run this command in the terminal",
            "Restart the local service",
            "Install the package on this machine",
            "Execute the deployment script",
        ),
    ),
)


_CONSTRAINT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "git.no_push",
        re.compile(
            r"\b(?:(?:do\s+not|don['’]?t|never)\s+(?:push|publish)|"
            r"without\s+(?:pushing|publishing)|"
            r"no\s+(?:push(?:ing)?|publish(?:ing)?))\b",
            re.I,
        ),
    ),
    (
        "git.no_commit",
        re.compile(
            r"\b(?:(?:do\s+not|don['’]?t|never)\s+commit|"
            r"without\s+committing|no\s+commits?)\b",
            re.I,
        ),
    ),
    (
        "web.no_browse",
        re.compile(
            r"\b(?:(?:do\s+not|don['’]?t|never)\s+(?:use\s+)?"
            r"(?:browse|search(?:\s+the)?\s+web|web\s+search|look\s+online)|"
            r"without\s+(?:browsing|web\s+search)|no\s+(?:browsing|web\s+search))\b",
            re.I,
        ),
    ),
    (
        "workspace.read_only",
        re.compile(
            r"\b(?:read[- ]only|(?:do\s+not|don['’]?t|never)\s+"
            r"(?:change|modify|edit|write|delete)|without\s+"
            r"(?:changing|modifying|editing|writing|deleting))\b",
            re.I,
        ),
    ),
    (
        "system.no_execute",
        re.compile(
            r"\b(?:(?:do\s+not|don['’]?t|never)\s+"
            r"(?:run|execute|start|restart|deploy|install)|without\s+"
            r"(?:running|executing|starting|restarting|deploying|installing))\b",
            re.I,
        ),
    ),
)

_CONSTRAINT_INTENT_BLOCKS: dict[str, frozenset[str]] = {
    "git.no_push": frozenset({"git.publish"}),
    "git.no_commit": frozenset({"git.commit"}),
    "web.no_browse": frozenset({"web.search", "research.run"}),
    "workspace.read_only": frozenset(
        {"workspace.modify", "git.commit", "git.publish", "document.write"}
    ),
    "system.no_execute": frozenset({"system.execute", "workspace.test"}),
}

_CONSTRAINT_TOOL_BLOCKS: dict[str, frozenset[str]] = {
    "web.no_browse": frozenset(
        {
            "web_search",
            "web_fetch",
            "builtin_browser",
            "trigger_research",
            "manage_research",
        }
    ),
    "system.no_execute": frozenset({"bash", "python", "manage_bg_jobs"}),
}


def get_intent_router_mode(value: str | None = None) -> str:
    """Return a validated rollout mode, defaulting invalid values to off."""

    raw = value if value is not None else os.getenv(INTENT_ROUTER_MODE_ENV, "off")
    mode = raw.strip().lower()
    if mode not in _VALID_MODES:
        logger.warning("Invalid %s=%r; using off", INTENT_ROUTER_MODE_ENV, raw)
        return "off"
    return mode


def extract_intent_constraints(text: str) -> tuple[str, ...]:
    """Extract negative constraints without interpreting them as authority."""

    if not text:
        return ()
    return tuple(name for name, pattern in _CONSTRAINT_PATTERNS if pattern.search(text))


def _bounded_env_float(name: str, default: float, low: float, high: float) -> float:
    try:
        return min(high, max(low, float(os.getenv(name, str(default)))))
    except (TypeError, ValueError):
        logger.warning("Invalid %s; using %s", name, default)
        return default


def select_active_intents(
    route: IntentRoute,
    *,
    min_score: float | None = None,
    min_margin: float | None = None,
) -> ActiveIntentSelection:
    """Select additive active-mode signals without weakening hard routing.

    Operational labels must clear an absolute confidence threshold and beat
    the strongest explanation/conversation label by a margin. Explicit
    negative constraints remove conflicting labels before domains are exposed.
    """

    if route.rollout_mode != "active":
        return ActiveIntentSelection(reason="inactive")
    if route.source != "semantic":
        return ActiveIntentSelection(reason=route.source)

    score_threshold = (
        min_score
        if min_score is not None
        else _bounded_env_float(
            INTENT_ROUTER_ACTIVE_MIN_SCORE_ENV,
            _DEFAULT_ACTIVE_MIN_SCORE,
            -1.0,
            1.0,
        )
    )
    margin_threshold = (
        min_margin
        if min_margin is not None
        else _bounded_env_float(
            INTENT_ROUTER_ACTIVE_MIN_MARGIN_ENV,
            _DEFAULT_ACTIVE_MIN_MARGIN,
            0.0,
            2.0,
        )
    )
    strongest_non_tool = max(
        (intent.score for intent in route.top_intents if not intent.needs_tools),
        default=-1.0,
    )
    blocked_names = {
        name
        for constraint in route.constraints
        for name in _CONSTRAINT_INTENT_BLOCKS.get(constraint, ())
    }
    eligible = tuple(
        intent
        for intent in route.top_intents
        if intent.needs_tools
        and intent.name not in blocked_names
        and intent.score >= score_threshold
        and intent.score >= strongest_non_tool + margin_threshold
    )
    if not eligible:
        scored_tools = tuple(
            intent
            for intent in route.top_intents
            if intent.needs_tools and intent.score >= score_threshold
        )
        if scored_tools and all(intent.name in blocked_names for intent in scored_tools):
            reason = "constrained"
        elif scored_tools and strongest_non_tool > -1.0:
            reason = "conflicting"
        else:
            reason = "low_confidence"
        return ActiveIntentSelection(reason=reason)

    domains = tuple(
        sorted({domain for intent in eligible for domain in intent.domains})
    )
    return ActiveIntentSelection(
        intents=eligible,
        domains=domains,
        needs_tools=True,
        reason="selected",
    )


def active_constraint_disabled_tools(route: IntentRoute) -> set[str]:
    """Return tool names denied by constraints, active mode only."""

    if route.rollout_mode != "active":
        return set()
    disabled = {
        tool
        for constraint in route.constraints
        for tool in _CONSTRAINT_TOOL_BLOCKS.get(constraint, ())
    }
    if "workspace.read_only" in route.constraints:
        disabled.update(plan_mode_disabled_tools())
    return disabled


def _normalise_rows(values: np.ndarray, *, expected_rows: int) -> np.ndarray:
    array = np.asarray(values, dtype="float32")
    if array.ndim != 2 or array.shape[0] != expected_rows or array.shape[1] == 0:
        raise ValueError("intent encoder returned an invalid matrix shape")
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    return array / norms


class IntentRouter:
    """Lazy prototype scorer backed by the local FastEmbed encoder."""

    def __init__(
        self,
        *,
        encoder_factory: Callable[[], IntentEncoder] | None = None,
        definitions: Sequence[IntentDefinition] = INTENT_DEFINITIONS,
        min_score: float | None = None,
    ):
        if not definitions:
            raise ValueError("at least one intent definition is required")
        if any(not definition.examples for definition in definitions):
            raise ValueError("every intent definition needs at least one example")
        names = [definition.name for definition in definitions]
        if len(names) != len(set(names)):
            raise ValueError("intent definition names must be unique")

        self._encoder_factory = encoder_factory or self._default_encoder_factory
        self._definitions = tuple(definitions)
        self._min_score = (
            min_score
            if min_score is not None
            else _bounded_env_float(
                INTENT_ROUTER_MIN_SCORE_ENV, _DEFAULT_MIN_SCORE, -1.0, 1.0
            )
        )
        self._encoder: IntentEncoder | None = None
        self._prototype_matrix: np.ndarray | None = None
        self._prototype_owners: tuple[int, ...] = ()
        self._init_lock = threading.Lock()
        self._encode_lock = threading.Lock()

    @staticmethod
    def _default_encoder_factory() -> IntentEncoder:
        from src.embeddings import FastEmbedClient

        return FastEmbedClient()

    def _ensure_ready(self) -> tuple[IntentEncoder, np.ndarray, tuple[int, ...]]:
        if self._encoder is not None and self._prototype_matrix is not None:
            return self._encoder, self._prototype_matrix, self._prototype_owners

        with self._init_lock:
            if self._encoder is None or self._prototype_matrix is None:
                encoder = self._encoder_factory()
                prototypes: list[str] = []
                owners: list[int] = []
                for index, definition in enumerate(self._definitions):
                    prototypes.extend(definition.examples)
                    owners.extend([index] * len(definition.examples))
                with self._encode_lock:
                    encoded = encoder.encode(prototypes, normalize_embeddings=True)
                matrix = _normalise_rows(encoded, expected_rows=len(prototypes))
                self._encoder = encoder
                self._prototype_matrix = matrix
                self._prototype_owners = tuple(owners)

        return self._encoder, self._prototype_matrix, self._prototype_owners

    def classify(self, text: str, *, top_k: int = 3) -> IntentRoute:
        """Rank intent labels for one prompt without retaining the prompt."""

        started = time.perf_counter()
        constraints = extract_intent_constraints(text)
        if not text or not text.strip():
            return IntentRoute(
                constraints=constraints,
                source="empty",
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )

        try:
            encoder, prototypes, owners = self._ensure_ready()
            with self._encode_lock:
                encoded_query = encoder.encode([text], normalize_embeddings=True)
            query = _normalise_rows(encoded_query, expected_rows=1)[0]
            similarities = prototypes @ query

            best_scores = np.full(len(self._definitions), -1.0, dtype="float32")
            for prototype_index, definition_index in enumerate(owners):
                best_scores[definition_index] = max(
                    best_scores[definition_index], similarities[prototype_index]
                )

            ranked = sorted(
                (
                    (definition, float(best_scores[index]))
                    for index, definition in enumerate(self._definitions)
                    if float(best_scores[index]) >= self._min_score
                ),
                key=lambda item: (-item[1], item[0].name),
            )[: max(1, top_k)]
            top_intents = tuple(
                IntentScore(
                    name=definition.name,
                    score=score,
                    needs_tools=definition.needs_tools,
                    domains=definition.domains,
                )
                for definition, score in ranked
            )
            domains = tuple(
                sorted(
                    {
                        domain
                        for intent in top_intents
                        if intent.needs_tools
                        for domain in intent.domains
                    }
                )
            )
            return IntentRoute(
                top_intents=top_intents,
                domains=domains,
                constraints=constraints,
                needs_tools=any(intent.needs_tools for intent in top_intents),
                source="semantic",
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )
        except Exception as exc:
            return IntentRoute(
                constraints=constraints,
                source="unavailable",
                elapsed_ms=(time.perf_counter() - started) * 1000,
                error=type(exc).__name__,
            )


_router: IntentRouter | None = None
_router_lock = threading.Lock()
_executor: concurrent.futures.ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()
_inflight_lock = threading.Lock()


def get_intent_router() -> IntentRouter:
    """Return the process-wide lazy router."""

    global _router
    if _router is None:
        with _router_lock:
            if _router is None:
                _router = IntentRouter()
    return _router


def _get_intent_router_executor() -> concurrent.futures.ThreadPoolExecutor:
    """Return a single-worker executor so timed-out calls cannot fan out."""

    global _executor
    if _executor is None:
        with _executor_lock:
            if _executor is None:
                _executor = concurrent.futures.ThreadPoolExecutor(
                    max_workers=1,
                    thread_name_prefix="intent-router",
                )
    return _executor


def _classify_claimed(
    router: IntentRouter,
    text: str,
    *,
    top_k: int,
) -> IntentRoute:
    """Classify one claimed request and release the process-wide slot."""

    try:
        return router.classify(text, top_k=top_k)
    finally:
        _inflight_lock.release()


async def classify_intent_route(
    text: str,
    *,
    router: IntentRouter | None = None,
    mode: str | None = None,
    top_k: int = 3,
    timeout_ms: float | None = None,
) -> IntentRoute:
    """Classify off the event loop with a bounded request-time wait."""

    selected_mode = get_intent_router_mode(mode)
    if selected_mode == "off":
        return IntentRoute(source="disabled", rollout_mode="off")

    timeout = (
        timeout_ms
        if timeout_ms is not None
        else _bounded_env_float(
            INTENT_ROUTER_TIMEOUT_MS_ENV, _DEFAULT_TIMEOUT_MS, 1.0, 5000.0
        )
    )
    started = time.perf_counter()
    if not _inflight_lock.acquire(blocking=False):
        return IntentRoute(
            constraints=extract_intent_constraints(text),
            source="busy",
            rollout_mode=selected_mode,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            error="IntentRouterBusy",
        )

    try:
        try:
            selected_router = router or get_intent_router()
            loop = asyncio.get_running_loop()
            future = loop.run_in_executor(
                _get_intent_router_executor(),
                functools.partial(
                    _classify_claimed,
                    selected_router,
                    text,
                    top_k=top_k,
                ),
            )
        except Exception:
            _inflight_lock.release()
            raise
        # Keep the worker future alive after the request-time timeout so the
        # claimed slot is always released by _classify_claimed. Cancelling a
        # queued executor future before it starts would otherwise strand the
        # process-wide gate permanently.
        route = await asyncio.wait_for(asyncio.shield(future), timeout=timeout / 1000)
        return replace(route, rollout_mode=selected_mode)
    except TimeoutError:
        return IntentRoute(
            constraints=extract_intent_constraints(text),
            source="timeout",
            rollout_mode=selected_mode,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            error="TimeoutError",
        )
    except Exception as exc:
        return IntentRoute(
            constraints=extract_intent_constraints(text),
            source="unavailable",
            rollout_mode=selected_mode,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            error=type(exc).__name__,
        )
