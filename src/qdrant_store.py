import logging
import uuid
from typing import Any, Dict, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class QdrantSettings(BaseSettings):
    url: str | None = None
    host: str | None = None
    port: int | None = None
    grpc_port: int | None = None
    prefer_grpc: bool = False
    https: bool | None = None
    api_key: str | None = None
    prefix: str | None = None
    timeout: int | None = None
    path: str | None = None

    model_config = SettingsConfigDict(env_prefix="QDRANT_", extra="ignore")


logger = logging.getLogger(__name__)

_qdrant_client = None
_collection_cache: Dict[str, "QdrantCollection"] = {}


def get_qdrant_client():
    global _qdrant_client
    if _qdrant_client is not None:
        return _qdrant_client

    try:
        from qdrant_client import QdrantClient
    except ImportError as e:
        raise RuntimeError(
            "qdrant-client not installed: pip install qdrant-client"
        ) from e

    settings = QdrantSettings()
    client = QdrantClient(**settings.model_dump(exclude_none=True))

    client.get_collections()

    _qdrant_client = client
    logger.info("Qdrant connected")

    return _qdrant_client


def reset_qdrant_client():
    global _qdrant_client
    _qdrant_client = None
    _collection_cache.clear()


def _uid(s: str) -> str:
    # Qdrant only allows UUIDs and +ve IDs as point IDs.
    # Ref: https://qdrant.tech/documentation/manage-data/points/#point-ids
    # So, we create a deterministic UUID from an arbitrary string ID.
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, s))


def _to_filter(where: Optional[Dict]):
    # Translate a Chroma-style where dict to a Qdrant Filter.
    if not where:
        return None
    from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

    conditions = []
    # All filters used are plain single-key equality.
    for k, v in where.items():
        if k.startswith("$"):
            continue
        if isinstance(v, dict):
            if "$eq" in v:
                conditions.append(
                    FieldCondition(key=k, match=MatchValue(value=v["$eq"]))
                )
            elif "$in" in v:
                conditions.append(FieldCondition(key=k, match=MatchAny(any=v["$in"])))
        else:
            conditions.append(FieldCondition(key=k, match=MatchValue(value=v)))
    return Filter(must=conditions) if conditions else None


def _unpack(points) -> Dict[str, Any]:
    ids, docs, metas = [], [], []
    for p in points:
        pl = p.payload or {}
        ids.append(pl.get("_id", str(p.id)))
        docs.append(pl.get("_document", ""))
        metas.append({k: v for k, v in pl.items() if k not in ("_id", "_document")})
    return {"ids": ids, "documents": docs, "metadatas": metas}


class QdrantCollection:
    def __init__(self, client, name: str):
        self._c = client
        self._name = name
        self._dim: Optional[int] = None

    def _ensure(self, dim: int) -> None:
        """Create the collection if it doesn't exist yet."""
        if self._dim is not None:
            return
        from qdrant_client.models import Distance, VectorParams

        if not self._c.collection_exists(self._name):
            self._c.create_collection(
                collection_name=self._name,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )
            logger.info(
                f"Qdrant: created collection '{self._name}' with dimensions={dim}"
            )
        self._dim = dim

    def count(self) -> int:
        try:
            return self._c.count(self._name, exact=True).count
        except Exception:
            return 0

    def get(self, ids=None, where=None, include=None) -> Dict[str, Any]:
        SCROLL_LIMIT = 250

        empty = {"ids": [], "documents": [], "metadatas": []}
        try:
            if ids is not None:
                if not ids:
                    return empty
                return _unpack(
                    self._c.retrieve(
                        self._name,
                        ids=[_uid(i) for i in ids],
                        with_payload=True,
                        with_vectors=False,
                    )
                )

            pts, offset = [], None
            while True:
                batch, offset = self._c.scroll(
                    self._name,
                    scroll_filter=_to_filter(where),
                    with_payload=True,
                    with_vectors=False,
                    limit=SCROLL_LIMIT,
                    offset=offset,
                )
                pts.extend(batch)
                if offset is None:
                    break
            return _unpack(pts)
        except Exception as e:
            if "404" not in str(e):
                logger.error(f"Qdrant get '{self._name}': {e}")
            return empty

    def add(self, ids, embeddings, documents, metadatas) -> None:
        if not ids:
            return
        from qdrant_client.models import PointStruct

        self._ensure(len(embeddings[0]))
        self._c.upsert(
            self._name,
            points=[
                PointStruct(
                    id=_uid(ids[i]),
                    vector=embeddings[i],
                    payload={"_id": ids[i], "_document": documents[i], **metadatas[i]},
                )
                for i in range(len(ids))
            ],
        )

    def upsert(self, ids, embeddings, documents, metadatas) -> None:
        self.add(ids, embeddings, documents, metadatas)

    def delete(self, ids=None) -> None:
        if not ids:
            return
        try:
            self._c.delete(
                self._name,
                points_selector=[_uid(i) for i in ids],
            )
        except Exception as e:
            logger.error(f"Qdrant delete '{self._name}': {e}")

    def query(
        self, query_embeddings, n_results=10, where=None, include=None
    ) -> Dict[str, Any]:
        f = _to_filter(where)
        rows = []
        for emb in query_embeddings:
            try:
                hits = self._c.query_points(
                    collection_name=self._name,
                    query=emb,
                    query_filter=f,
                    limit=n_results,
                    with_payload=True,
                ).points
                row = _unpack(hits)
                row["distances"] = [1.0 - h.score for h in hits]
                rows.append(row)
            except Exception as e:
                if "404" not in str(e):
                    logger.error(f"Qdrant search '{self._name}': {e}")
                rows.append(
                    {"ids": [], "documents": [], "metadatas": [], "distances": []}
                )
        return {
            k: [r[k] for r in rows]
            for k in ("ids", "documents", "metadatas", "distances")
        }


def get_qdrant_collection(name: str) -> QdrantCollection:
    if name not in _collection_cache:
        _collection_cache[name] = QdrantCollection(get_qdrant_client(), name)
    return _collection_cache[name]


def delete_qdrant_collection(name: str) -> None:
    _collection_cache.pop(name, None)
    get_qdrant_client().delete_collection(collection_name=name)
