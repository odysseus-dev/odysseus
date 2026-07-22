"""Odyssey life-data substrate.

The canonical file store for life artifacts (routines, recipes, weekly plans,
…). Human-readable files under ``data/life/`` are the ONE source of truth
[LAW:one-source-of-truth]. Two derived projections are kept in sync on every
write, and never treated as authoritative:

  - ``registry.json`` — a manifest enumerating every domain + artifact so the
    agent always knows *what exists*. It is a pure scan of the tree; a rebuild
    always reproduces it exactly, so it can never diverge from the files.
  - the Chroma/RAG vector index — so a saved artifact is retrievable by
    vector + keyword search months later. Indexing is delegated to a
    ``LifeIndexer`` seam [LAW:effects-at-boundaries] so the store composes with
    a fake in tests and with the real RAG in production.

Files = truth; registry + vectors = derived index.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional, Protocol

from src.constants import LIFE_DIR

logger = logging.getLogger(__name__)

REGISTRY_VERSION = 1
REGISTRY_FILENAME = "registry.json"

# Seed domains from docs/odyssey-plan.md #1. The set is open — any slug-valid
# domain is allowed — but these are created up front so the store is legible.
SEED_DOMAINS = ("routines", "recipes", "weekly")

ALLOWED_EXTENSIONS = (".md", ".html")

_DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_NAME_STEM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_MD_TITLE_RE = re.compile(r"^\s{0,3}#\s+(.+?)\s*$", re.MULTILINE)
_HTML_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_HTML_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


# ---------------------------------------------------------------------------
# Types — a legal state is exactly a domain + artifact under the store root.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Artifact:
    """One life file. ``path`` is relative to the store root so the manifest
    is portable across machines; the store resolves it to an absolute path."""

    domain: str
    name: str          # filename incl. extension, e.g. "tacos.md"
    path: str          # relative to store root, e.g. "recipes/tacos.md"
    type: str          # extension, e.g. ".md"
    title: str
    size: int
    modified: float    # mtime, epoch seconds


@dataclass(frozen=True)
class Registry:
    version: int
    domains: Dict[str, List[Artifact]]


# ---------------------------------------------------------------------------
# The indexing seam — the only coupling to Chroma/RAG lives behind this.
# ---------------------------------------------------------------------------


class LifeIndexer(Protocol):
    def index(self, *, text: str, source: str, metadata: Dict[str, object]) -> int:
        """Index ``text`` under ``source`` (a stable id). Re-indexing the same
        source replaces prior content. Returns the number of chunks written."""

    def remove(self, *, source: str) -> int:
        """Remove all indexed content for ``source``. Returns chunks removed."""


class RagLifeIndexer:
    """Default ``LifeIndexer`` backed by the existing Odysseus VectorRAG.

    When ChromaDB is unreachable the RAG manager is ``None``; indexing is a
    derived best-effort projection, so we log loudly and return 0 rather than
    failing the caller's save [LAW:no-silent-failure] — the file itself is the
    source of truth and is already persisted."""

    def index(self, *, text: str, source: str, metadata: Dict[str, object]) -> int:
        from src.rag_singleton import get_rag_manager

        rag = get_rag_manager()
        if rag is None or not getattr(rag, "healthy", False):
            logger.warning(
                "life index skipped for %s: RAG unavailable (keyword-only until it recovers)",
                source,
            )
            return 0

        rag.delete_by_source(source)
        chunks = rag._split_into_chunks(text)
        written = 0
        for chunk_id, chunk in enumerate(chunks):
            if rag.add_document(chunk, {**metadata, "source": source, "chunk_id": chunk_id}):
                written += 1
        return written

    def remove(self, *, source: str) -> int:
        from src.rag_singleton import get_rag_manager

        rag = get_rag_manager()
        if rag is None or not getattr(rag, "healthy", False):
            return 0
        return rag.delete_by_source(source)


# ---------------------------------------------------------------------------
# Pure helpers — validation, title derivation, (de)serialization.
# ---------------------------------------------------------------------------


def _validate(domain: str, name: str) -> str:
    """Validate caller-supplied identifiers at the trust boundary and return
    the file extension. Raises ValueError on anything unsafe."""

    if not _DOMAIN_RE.match(domain):
        raise ValueError(f"invalid domain {domain!r}: expected [a-z0-9_-]")
    stem, ext = os.path.splitext(name)
    if ext.lower() not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"invalid artifact name {name!r}: extension must be one of {ALLOWED_EXTENSIONS}"
        )
    if not _NAME_STEM_RE.match(stem) or ".." in name:
        raise ValueError(f"invalid artifact name {name!r}: expected [A-Za-z0-9._-]")
    return ext.lower()


def _derive_title(name: str, ext: str, content: str) -> str:
    """First heading in the content, else the filename stem."""

    if ext == ".md":
        m = _MD_TITLE_RE.search(content)
        if m:
            return m.group(1).strip()
    elif ext == ".html":
        for pattern in (_HTML_H1_RE, _HTML_TITLE_RE):
            m = pattern.search(content)
            if m:
                text = _HTML_TAG_RE.sub("", m.group(1)).strip()
                if text:
                    return text
    return os.path.splitext(name)[0]


def _artifact_to_json(a: Artifact) -> Dict[str, object]:
    return asdict(a)


def _artifact_from_json(domain: str, raw: Dict[str, object]) -> Artifact:
    return Artifact(
        domain=domain,
        name=str(raw["name"]),
        path=str(raw["path"]),
        type=str(raw["type"]),
        title=str(raw.get("title", "")),
        size=int(raw.get("size", 0)),
        modified=float(raw.get("modified", 0.0)),
    )


# ---------------------------------------------------------------------------
# The store.
# ---------------------------------------------------------------------------


class LifeStore:
    """Owns ``data/life/``. Single writer for the tree, its ``registry.json``
    manifest, and the on-save vector index [LAW:no-shared-mutable-globals]."""

    def __init__(self, root: Optional[str] = None, indexer: Optional[LifeIndexer] = None):
        self.root = root or LIFE_DIR
        self.indexer: LifeIndexer = indexer if indexer is not None else RagLifeIndexer()
        os.makedirs(self.root, exist_ok=True)
        for domain in SEED_DOMAINS:
            os.makedirs(os.path.join(self.root, domain), exist_ok=True)

    # -- paths -------------------------------------------------------------

    @property
    def registry_path(self) -> str:
        return os.path.join(self.root, REGISTRY_FILENAME)

    def _abs(self, domain: str, name: str) -> str:
        return os.path.join(self.root, domain, name)

    def _relpath(self, domain: str, name: str) -> str:
        return f"{domain}/{name}"

    # -- write path --------------------------------------------------------

    def save(self, domain: str, name: str, content: str, *, owner: Optional[str] = None) -> Artifact:
        """Persist a life artifact (creating the domain dir on demand), index
        it into the RAG, and refresh the registry manifest. The file is the
        source of truth; both projections are rebuilt from it."""

        ext = _validate(domain, name)
        abs_path = self._abs(domain, name)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)

        source = self._relpath(domain, name)
        metadata: Dict[str, object] = {
            "kind": "life",
            "life_domain": domain,
            "life_artifact": name,
            "filename": name,
            "directory": domain,
            "type": ext,
        }
        if owner:
            metadata["owner"] = owner
        self.indexer.index(text=content, source=source, metadata=metadata)

        self.rebuild_registry()
        return self._scan_artifact(domain, name)

    def delete(self, domain: str, name: str) -> None:
        """Remove an artifact and its derived index entries, then refresh the
        manifest. Missing files are a no-op — the desired end state (absent) is
        already true."""

        _validate(domain, name)
        abs_path = self._abs(domain, name)
        if os.path.exists(abs_path):
            os.remove(abs_path)
        self.indexer.remove(source=self._relpath(domain, name))
        self.rebuild_registry()

    # -- read path ---------------------------------------------------------

    def read(self, domain: str, name: str) -> str:
        _validate(domain, name)
        with open(self._abs(domain, name), "r", encoding="utf-8") as f:
            return f.read()

    def list_artifacts(self, domain: Optional[str] = None) -> List[Artifact]:
        registry = self.load_registry()
        if domain is None:
            return [a for arts in registry.domains.values() for a in arts]
        return list(registry.domains.get(domain, []))

    # -- registry (derived manifest) --------------------------------------

    def load_registry(self) -> Registry:
        """Read ``registry.json``; rebuild it from the tree if absent or
        unreadable so a caller always gets a truthful manifest."""

        try:
            with open(self.registry_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return self.rebuild_registry()

        domains = {
            domain: [_artifact_from_json(domain, item) for item in items]
            for domain, items in raw.get("domains", {}).items()
        }
        return Registry(version=int(raw.get("version", REGISTRY_VERSION)), domains=domains)

    def rebuild_registry(self) -> Registry:
        """Scan the tree and write ``registry.json``. This is the definition of
        the manifest — every save/delete calls it, so the manifest is always a
        faithful projection of the files [LAW:one-source-of-truth]."""

        domains: Dict[str, List[Artifact]] = {}
        for domain in sorted(self._existing_domains()):
            artifacts = []
            domain_dir = os.path.join(self.root, domain)
            for name in sorted(os.listdir(domain_dir)):
                if os.path.splitext(name)[1].lower() not in ALLOWED_EXTENSIONS:
                    continue
                if not os.path.isfile(os.path.join(domain_dir, name)):
                    continue
                artifacts.append(self._scan_artifact(domain, name))
            domains[domain] = artifacts

        registry = Registry(version=REGISTRY_VERSION, domains=domains)
        payload = {
            "version": registry.version,
            "domains": {
                domain: [_artifact_to_json(a) for a in arts]
                for domain, arts in registry.domains.items()
            },
        }
        with open(self.registry_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
        return registry

    # -- internal scan -----------------------------------------------------

    def _existing_domains(self) -> List[str]:
        return [
            entry
            for entry in os.listdir(self.root)
            if _DOMAIN_RE.match(entry) and os.path.isdir(os.path.join(self.root, entry))
        ]

    def _scan_artifact(self, domain: str, name: str) -> Artifact:
        abs_path = self._abs(domain, name)
        ext = os.path.splitext(name)[1].lower()
        stat = os.stat(abs_path)
        with open(abs_path, "r", encoding="utf-8") as f:
            content = f.read()
        return Artifact(
            domain=domain,
            name=name,
            path=self._relpath(domain, name),
            type=ext,
            title=_derive_title(name, ext, content),
            size=stat.st_size,
            modified=stat.st_mtime,
        )
