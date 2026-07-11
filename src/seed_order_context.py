"""Runtime loader for the canonical Misumi Seed Order context."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterable

from src.settings import get_setting

logger = logging.getLogger(__name__)

SETTING_KEY = "misumi_seed_order_root"
ENV_KEYS = (
    "MISUMI_SOURCE_ROOT",
    "MISUMI_SEED_ORDER_ROOT",
    "MISUMI_CANONICAL_ROOT",
    "FLAT_KNOWLEDGEBASE_ROOT",
)

_LOAD_ORDER = (
    "docs/core/misumi-seed-order-v0.1.md",
    "docs/core/agent-personality-registry-v0.1.md",
    "docs/core/agent-personality-registry-v0.2.md",
    "docs/core/aoteru-routing-contract-v0.1.md",
    "protocols/register.md",
    "templates/change-log-entry.md",
    "agents/core/emperor-aoteru-misumi.md",
    "agents/core/operator-lelouch-lamperouge.md",
    "agents/core/archivist-makise-kurisu.md",
    "docs/repository-boundaries.md",
    "docs/odysseus-contract.md",
)


def _candidate_roots() -> Iterable[Path]:
    for key in ENV_KEYS:
        value = (os.getenv(key) or "").strip()
        if value:
            yield Path(value)

    configured = str(get_setting(SETTING_KEY, "") or "").strip()
    if configured:
        yield Path(configured)

    home = Path.home()
    yield home / "Documents" / "flat-knowledgebase"
    yield home / "Documents" / "misumi"
    yield home / "Documents" / "Claude" / "Overflow" / "flat-knowledgebase-fcc"


def _resolve_seed_root() -> Path | None:
    seen: set[Path] = set()
    for candidate in _candidate_roots():
        try:
            root = candidate.expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        if root in seen:
            continue
        seen.add(root)
        if (root / _LOAD_ORDER[0]).is_file():
            return root
    return None


def build_seed_order_context(root: str | os.PathLike[str] | None = None) -> str | None:
    """Read canonical Misumi seed-order files as one trusted runtime block.

    Missing roots or missing files are non-fatal. The runtime must never create
    or mutate the canonical knowledgebase while building a prompt.
    """
    try:
        seed_root = Path(root).expanduser().resolve() if root else _resolve_seed_root()
        if not seed_root:
            return None

        sections: list[str] = []
        for rel in _LOAD_ORDER:
            path = (seed_root / rel).resolve()
            try:
                path.relative_to(seed_root)
            except ValueError:
                logger.warning("Misumi seed-order path escaped root: %s", path)
                return None
            if not path.is_file():
                logger.debug("Misumi seed-order file absent: %s", path)
                return None
            content = path.read_text(encoding="utf-8").strip()
            sections.append(f"### {rel}\n{content}")

        return (
            "## Misumi Seed Order Runtime Context\n"
            "Loaded read-only from the configured canonical Misumi repository.\n\n"
            "The ratified seed documents below govern Misumi runtime behavior "
            "before persona flavor, tools, or routing instructions. Treat the "
            "repo persona as the Misumi Seed Order core plan: preserve raw "
            "actuality before organizing it, preserve uncertainty, distinguish "
            "candidate from ratified, and follow Observe -> Propose -> Review -> "
            "Ratify -> Implement -> Log. Expose only Emperor, Operator, and "
            "Archivist as visible roles. Specialist personas remain dormant "
            "unless repeated need is evidenced through the Agent Evolution "
            "Protocol. Every behavior-affecting output must carry exactly one "
            "approved status label: raw, observed, reported, inferred, "
            "candidate_pattern, candidate_mechanism, proposed, ratified, "
            "rejected, or archived. Level 5 and Level 6 changes must remain "
            "proposed until ratified by the user.\n\n"
            + "\n\n---\n\n".join(sections)
        )
    except Exception as exc:
        logger.debug("Misumi seed-order context unavailable: %s", exc, exc_info=True)
        return None
