from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Iterable

import numpy as np

from odysseus_desktop_backend.storage import Database, utc_ms


DEFAULT_EMBEDDING_MODEL = "local-hash-v1"
DEFAULT_DIMENSIONS = 384
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_\-']*", re.IGNORECASE)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EmbeddingResult:
    content_hash: str
    model: str
    vector: np.ndarray
    from_cache: bool


class LocalHashEmbeddingProvider:
    """Deterministic local embedding provider for the no-service MVP path.

    This is intentionally lightweight and dependency-free beyond NumPy. It is
    lexical rather than model-semantic, but it proves the cache/vector pipeline
    without adding a new runtime service before Milestone 2 is stable.
    """

    def __init__(self, dimensions: int = DEFAULT_DIMENSIONS):
        self.dimensions = dimensions
        self.calls = 0

    def embed(self, texts: list[str]) -> list[np.ndarray]:
        self.calls += len(texts)
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> np.ndarray:
        vector = np.zeros(self.dimensions, dtype=np.float32)
        for token in TOKEN_RE.findall(text.lower()):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = np.linalg.norm(vector)
        if norm > 0:
            vector = vector / norm
        return vector.astype(np.float32)


class EmbeddingService:
    def __init__(
        self,
        db: Database,
        model: str = DEFAULT_EMBEDDING_MODEL,
        provider: LocalHashEmbeddingProvider | None = None,
    ):
        self.db = db
        self.model = model
        self.provider = provider or LocalHashEmbeddingProvider()

    def embed_texts(self, texts: Iterable[str]) -> list[EmbeddingResult]:
        items = list(texts)
        hashes = [content_hash(text) for text in items]
        cached = self._load_cached(hashes)

        missing_texts: list[str] = []
        missing_hashes: list[str] = []
        for text, digest in zip(items, hashes):
            if digest not in cached:
                missing_texts.append(text)
                missing_hashes.append(digest)

        if missing_texts:
            vectors = self.provider.embed(missing_texts)
            now = utc_ms()
            for digest, vector in zip(missing_hashes, vectors):
                self.db.conn.execute(
                    """
                    INSERT INTO embedding_cache(
                        content_hash, embedding_model, vector_blob, dimensions, created_at, last_used_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(content_hash, embedding_model) DO UPDATE SET
                        vector_blob = excluded.vector_blob,
                        dimensions = excluded.dimensions,
                        last_used_at = excluded.last_used_at
                    """,
                    (
                        digest,
                        self.model,
                        vector.astype(np.float32).tobytes(),
                        int(vector.shape[0]),
                        now,
                        now,
                    ),
                )
                cached[digest] = vector.astype(np.float32)
            self.db.conn.commit()

        now = utc_ms()
        for digest in hashes:
            self.db.conn.execute(
                """
                UPDATE embedding_cache
                SET last_used_at = ?
                WHERE content_hash = ? AND embedding_model = ?
                """,
                (now, digest, self.model),
            )
        self.db.conn.commit()

        return [
            EmbeddingResult(
                content_hash=digest,
                model=self.model,
                vector=cached[digest],
                from_cache=digest not in missing_hashes,
            )
            for digest in hashes
        ]

    def embed_query(self, query: str) -> np.ndarray:
        return self.provider.embed([query])[0]

    def _load_cached(self, hashes: list[str]) -> dict[str, np.ndarray]:
        if not hashes:
            return {}
        placeholders = ",".join("?" for _ in hashes)
        rows = self.db.conn.execute(
            f"""
            SELECT content_hash, vector_blob, dimensions
            FROM embedding_cache
            WHERE embedding_model = ? AND content_hash IN ({placeholders})
            """,
            [self.model, *hashes],
        ).fetchall()
        cached: dict[str, np.ndarray] = {}
        for row in rows:
            vector = np.frombuffer(row["vector_blob"], dtype=np.float32).copy()
            if vector.shape[0] == row["dimensions"]:
                cached[row["content_hash"]] = vector
        return cached
