"""
graph_store.py

User knowledge graph backed by Kuzu (embedded, file-based, openCypher).

Stores typed entities (Person, Place, Organization, Concept, Goal, Project)
and relationships extracted from chat conversations. Lives alongside the flat
memory.json store — facts go to both places: text to memory.json for fuzzy
recall, typed triples here for structured traversal ("who do I know in Paris?").

Persists to {data_dir}/graph/. The whole module degrades gracefully: if kuzu
is missing or the DB is unhealthy, every call becomes a no-op and `.healthy`
reads False. Callers should not see exceptions.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


ENTITY_LABELS = ("User", "Person", "Place", "Organization", "Concept", "Goal", "Project")

# Relationship name -> (allowed source labels, allowed target labels).
# Kuzu requires REL TABLE definitions to enumerate the FROM/TO pairs, so this
# also drives schema creation. Keep the set small and well-typed — free-form
# edge labels are a Phase 2 concern.
RELATIONS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "KNOWS":         (("User", "Person"),       ("Person",)),
    "LIVES_IN":      (("User", "Person"),       ("Place",)),
    "WORKS_AT":      (("User", "Person"),       ("Organization",)),
    "INTERESTED_IN": (("User",),                ("Concept",)),
    "HAS_GOAL":      (("User",),                ("Goal",)),
    "WORKING_ON":    (("User", "Person"),       ("Project",)),
    "RELATED_TO":    (("Person",),              ("Person",)),
}


def _norm_key(name: str) -> str:
    """Stable lookup key for an entity name — lowercased, whitespace-collapsed,
    punctuation-stripped. Two memories saying 'John' and 'john.' resolve to
    the same Person node."""
    s = re.sub(r"\s+", " ", (name or "").strip().lower())
    return re.sub(r"[^\w\s\-']", "", s)


class GraphStore:
    """Embedded Kuzu-backed graph of typed user-context entities.

    Thread-safe via a single coarse lock — the Kuzu Python connection is not
    safe for concurrent writes, and Phase 1 traffic is low (one extraction per
    chat turn). If concurrency ever matters here, swap to a connection pool.
    """

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.db_path = os.path.join(data_dir, "graph")
        self._lock = threading.Lock()
        self._db = None
        self._conn = None
        self.healthy = False
        self._init()

    def _init(self) -> None:
        try:
            import kuzu  # type: ignore
        except ImportError:
            logger.warning("GraphStore DEGRADED: kuzu not installed (pip install kuzu)")
            return

        try:
            os.makedirs(self.db_path, exist_ok=True)
            self._db = kuzu.Database(self.db_path)
            self._conn = kuzu.Connection(self._db)
            self._ensure_schema()
            self.healthy = True
            logger.info("GraphStore initialized at %s", self.db_path)
        except Exception as e:
            logger.warning("GraphStore DEGRADED: %s", e)
            self._db = None
            self._conn = None
            self.healthy = False

    def _ensure_schema(self) -> None:
        """Create node + relationship tables if missing.

        Kuzu raises on duplicate CREATE; the cheapest portable check is to wrap
        each statement in try/except and swallow the 'already exists' branch.
        IF NOT EXISTS syntax availability varies by Kuzu version.
        """
        assert self._conn is not None

        node_defs = [
            # Every entity carries `owner` for multi-user isolation. Without it
            # one user's "John" would silently merge into another user's graph.
            ("User",         "id STRING, owner STRING, name STRING, PRIMARY KEY(id)"),
            ("Person",       "id STRING, owner STRING, name STRING, email STRING, PRIMARY KEY(id)"),
            ("Place",        "id STRING, owner STRING, name STRING, kind STRING, PRIMARY KEY(id)"),
            ("Organization", "id STRING, owner STRING, name STRING, PRIMARY KEY(id)"),
            ("Concept",      "id STRING, owner STRING, name STRING, PRIMARY KEY(id)"),
            ("Goal",         "id STRING, owner STRING, text STRING, deadline STRING, PRIMARY KEY(id)"),
            ("Project",      "id STRING, owner STRING, name STRING, PRIMARY KEY(id)"),
        ]
        for label, cols in node_defs:
            self._safe_exec(f"CREATE NODE TABLE {label}({cols})")

        # `source_memory_id` ties every edge back to the memory.json entry that
        # introduced it — used for provenance, deletion cascade, and audit.
        for rel, (froms, tos) in RELATIONS.items():
            pairs = ", ".join(f"FROM {f} TO {t}" for f in froms for t in tos)
            self._safe_exec(
                f"CREATE REL TABLE {rel}({pairs}, source_memory_id STRING, ts INT64)"
            )

    def _safe_exec(self, cypher: str, params: Optional[dict] = None):
        try:
            return self._conn.execute(cypher, parameters=params or {})
        except Exception as e:
            msg = str(e).lower()
            if "already exists" in msg or "duplicate" in msg:
                return None
            # Bubble for real errors — the caller decides whether to log/swallow.
            raise

    # ---- public API ---------------------------------------------------------

    def upsert_entity(
        self,
        label: str,
        name: str,
        owner: Optional[str],
        extra: Optional[dict] = None,
    ) -> Optional[str]:
        """Insert-or-fetch an entity by (label, owner, normalized name).

        Returns the entity id, or None on degraded/invalid input. Name-based
        resolution is Phase 1 — Phase 2 will add embedding similarity.
        """
        if not self.healthy or label not in ENTITY_LABELS:
            return None
        clean = (name or "").strip()
        if not clean:
            return None

        owner_val = owner or ""
        key = _norm_key(clean)
        if not key:
            return None

        with self._lock:
            try:
                # Look up by normalized name + owner. Kuzu doesn't have a
                # case-insensitive index, so we filter in Cypher with lower().
                # The graph is small (hundreds-thousands of nodes), so a scan
                # per upsert is fine for Phase 1.
                name_field = "text" if label == "Goal" else "name"
                result = self._conn.execute(
                    f"MATCH (n:{label}) WHERE n.owner = $owner "
                    f"AND lower(n.{name_field}) = $key RETURN n.id LIMIT 1",
                    parameters={"owner": owner_val, "key": key},
                )
                if result.has_next():
                    return str(result.get_next()[0])

                # Insert new.
                eid = str(uuid.uuid4())
                if label == "Goal":
                    deadline = (extra or {}).get("deadline") or ""
                    self._conn.execute(
                        "CREATE (n:Goal {id: $id, owner: $owner, text: $text, deadline: $deadline})",
                        parameters={"id": eid, "owner": owner_val, "text": clean, "deadline": deadline},
                    )
                elif label == "Place":
                    kind = (extra or {}).get("kind") or ""
                    self._conn.execute(
                        "CREATE (n:Place {id: $id, owner: $owner, name: $name, kind: $kind})",
                        parameters={"id": eid, "owner": owner_val, "name": clean, "kind": kind},
                    )
                elif label == "Person":
                    email = (extra or {}).get("email") or ""
                    self._conn.execute(
                        "CREATE (n:Person {id: $id, owner: $owner, name: $name, email: $email})",
                        parameters={"id": eid, "owner": owner_val, "name": clean, "email": email},
                    )
                else:
                    self._conn.execute(
                        f"CREATE (n:{label} {{id: $id, owner: $owner, name: $name}})",
                        parameters={"id": eid, "owner": owner_val, "name": clean},
                    )
                return eid
            except Exception as e:
                logger.warning("upsert_entity(%s, %r) failed: %s", label, name, e)
                return None

    def upsert_user(self, owner: Optional[str]) -> Optional[str]:
        """Ensure the (User) node exists for this owner; returns its id.

        The graph is rooted at the User node — every fact about the user is an
        outgoing edge from here.
        """
        if not self.healthy:
            return None
        owner_val = owner or ""
        with self._lock:
            try:
                result = self._conn.execute(
                    "MATCH (u:User) WHERE u.owner = $owner RETURN u.id LIMIT 1",
                    parameters={"owner": owner_val},
                )
                if result.has_next():
                    return str(result.get_next()[0])
                uid = str(uuid.uuid4())
                self._conn.execute(
                    "CREATE (u:User {id: $id, owner: $owner, name: $name})",
                    parameters={"id": uid, "owner": owner_val, "name": owner_val or "user"},
                )
                return uid
            except Exception as e:
                logger.warning("upsert_user(%r) failed: %s", owner, e)
                return None

    def add_relation(
        self,
        rel: str,
        from_id: str,
        from_label: str,
        to_id: str,
        to_label: str,
        source_memory_id: Optional[str] = None,
    ) -> bool:
        """Add a relationship between two existing entities.

        Idempotent on (rel, from_id, to_id) — duplicate edges are filtered out
        so re-running extraction over the same memory doesn't fan out the
        graph. Returns True on success.
        """
        if not self.healthy or rel not in RELATIONS:
            return False
        froms, tos = RELATIONS[rel]
        if from_label not in froms or to_label not in tos:
            logger.debug("add_relation: type mismatch %s(%s -> %s)", rel, from_label, to_label)
            return False
        if not from_id or not to_id:
            return False

        with self._lock:
            try:
                exists = self._conn.execute(
                    f"MATCH (a:{from_label})-[r:{rel}]->(b:{to_label}) "
                    f"WHERE a.id = $a AND b.id = $b RETURN r LIMIT 1",
                    parameters={"a": from_id, "b": to_id},
                )
                if exists.has_next():
                    return True

                self._conn.execute(
                    f"MATCH (a:{from_label}), (b:{to_label}) "
                    f"WHERE a.id = $a AND b.id = $b "
                    f"CREATE (a)-[:{rel} {{source_memory_id: $mid, ts: $ts}}]->(b)",
                    parameters={
                        "a": from_id,
                        "b": to_id,
                        "mid": source_memory_id or "",
                        "ts": int(time.time()),
                    },
                )
                return True
            except Exception as e:
                logger.warning("add_relation(%s) failed: %s", rel, e)
                return False

    def ingest_triple(
        self,
        owner: Optional[str],
        subject_label: str,
        subject_name: str,
        rel: str,
        object_label: str,
        object_name: str,
        source_memory_id: Optional[str] = None,
        extras: Optional[dict] = None,
    ) -> bool:
        """High-level convenience: upsert both endpoints, then connect them.

        `extras` may carry per-node fields like {'place_kind': 'city',
        'goal_deadline': '2026-12-01', 'person_email': '...'}. Unknown keys are
        ignored.
        """
        if not self.healthy:
            return False
        if rel not in RELATIONS:
            logger.debug("ingest_triple: unknown relation %s", rel)
            return False
        if subject_label not in ENTITY_LABELS or object_label not in ENTITY_LABELS:
            return False

        # Anchor the User node — subjects of "User" type always resolve to the
        # single user-rooted node, regardless of what name the LLM chose.
        if subject_label == "User":
            from_id = self.upsert_user(owner)
        else:
            sub_extra = self._extract_node_extras(subject_label, extras)
            from_id = self.upsert_entity(subject_label, subject_name, owner, sub_extra)

        if object_label == "User":
            to_id = self.upsert_user(owner)
        else:
            obj_extra = self._extract_node_extras(object_label, extras)
            to_id = self.upsert_entity(object_label, object_name, owner, obj_extra)

        if not from_id or not to_id:
            return False
        return self.add_relation(rel, from_id, subject_label, to_id, object_label, source_memory_id)

    @staticmethod
    def _extract_node_extras(label: str, extras: Optional[dict]) -> dict:
        if not extras:
            return {}
        if label == "Place":
            return {"kind": extras.get("place_kind", "")}
        if label == "Goal":
            return {"deadline": extras.get("goal_deadline", "")}
        if label == "Person":
            return {"email": extras.get("person_email", "")}
        return {}

    # ---- queries -----------------------------------------------------------

    def neighborhood(self, owner: Optional[str], depth: int = 1, limit: int = 50) -> list[dict]:
        """Return facts as triples surrounding the User node.

        Output: [{"subject": {label, name}, "rel": str, "object": {label, name}}].
        This is the form that gets serialized into the chat context prompt.
        Depth > 1 expands to friend-of-friend relationships.
        """
        if not self.healthy:
            return []
        depth = max(1, min(int(depth), 3))
        owner_val = owner or ""
        triples: list[dict] = []

        with self._lock:
            try:
                result = self._conn.execute(
                    f"MATCH (u:User)-[r*1..{depth}]->(n) "
                    f"WHERE u.owner = $owner "
                    f"RETURN u, r, n LIMIT $limit",
                    parameters={"owner": owner_val, "limit": limit},
                )
                while result.has_next():
                    row = result.get_next()
                    # row = [User node, list of rels, terminal node]
                    # Kuzu returns nodes as dicts with `_label`, properties, etc.
                    rels = row[1] if isinstance(row[1], list) else [row[1]]
                    # We only surface the immediate User -> X edge for the
                    # prompt; deeper paths are reachable by re-running with
                    # the neighbor as anchor. Keeping the projection simple
                    # here avoids ambiguous "rel chain" rendering.
                    first_rel = rels[0] if rels else None
                    if not first_rel:
                        continue
                    target = row[2]
                    triples.append({
                        "subject": {"label": "User", "name": owner_val or "user"},
                        "rel": first_rel.get("_label") or first_rel.get("label") or "",
                        "object": {
                            "label": target.get("_label") or "",
                            "name": target.get("name") or target.get("text") or "",
                        },
                    })
            except Exception as e:
                logger.warning("neighborhood query failed: %s", e)
                return []

        # Dedup: the depth>1 MATCH can return the same User->X edge multiple
        # times once per longer path that traverses it.
        seen = set()
        deduped = []
        for t in triples:
            key = (t["rel"], t["object"]["label"], t["object"]["name"].lower())
            if key in seen:
                continue
            seen.add(key)
            deduped.append(t)
        return deduped

    def entities(self, owner: Optional[str], label: Optional[str] = None, limit: int = 200) -> list[dict]:
        """List entities for an owner, optionally filtered by label."""
        if not self.healthy:
            return []
        owner_val = owner or ""
        labels = [label] if label in ENTITY_LABELS else list(ENTITY_LABELS)
        out: list[dict] = []
        with self._lock:
            for lbl in labels:
                try:
                    name_field = "text" if lbl == "Goal" else "name"
                    result = self._conn.execute(
                        f"MATCH (n:{lbl}) WHERE n.owner = $owner "
                        f"RETURN n.id, n.{name_field} LIMIT $limit",
                        parameters={"owner": owner_val, "limit": limit},
                    )
                    while result.has_next():
                        row = result.get_next()
                        out.append({"label": lbl, "id": str(row[0]), "name": str(row[1] or "")})
                except Exception as e:
                    logger.warning("entities(%s) failed: %s", lbl, e)
        return out[:limit]

    def stats(self, owner: Optional[str] = None) -> dict:
        """Counts per node label and per relationship — useful for the UI / debug."""
        if not self.healthy:
            return {"healthy": False}
        owner_val = owner or ""
        nodes: dict[str, int] = {}
        rels: dict[str, int] = {}
        with self._lock:
            for lbl in ENTITY_LABELS:
                try:
                    result = self._conn.execute(
                        f"MATCH (n:{lbl}) WHERE n.owner = $owner RETURN count(n)",
                        parameters={"owner": owner_val},
                    )
                    if result.has_next():
                        nodes[lbl] = int(result.get_next()[0])
                except Exception:
                    nodes[lbl] = 0
            for rel in RELATIONS:
                try:
                    # Count edges anchored on any node owned by this user.
                    result = self._conn.execute(
                        f"MATCH (a)-[r:{rel}]->(b) WHERE a.owner = $owner RETURN count(r)",
                        parameters={"owner": owner_val},
                    )
                    if result.has_next():
                        rels[rel] = int(result.get_next()[0])
                except Exception:
                    rels[rel] = 0
        return {"healthy": True, "nodes": nodes, "relationships": rels}

    def delete_by_memory(self, source_memory_id: str) -> int:
        """Drop all edges that were introduced by a single memory entry.

        Called when the originating memory is deleted from memory.json so the
        graph stays consistent. Orphan nodes (no remaining edges) are NOT
        pruned in Phase 1 — they're cheap and may get re-attached later.

        Kuzu's openCypher does not support RETURN after DELETE in one query,
        so we count first, then delete.
        """
        if not self.healthy or not source_memory_id:
            return 0
        removed = 0
        with self._lock:
            for rel in RELATIONS:
                try:
                    count_result = self._conn.execute(
                        f"MATCH ()-[r:{rel}]->() WHERE r.source_memory_id = $mid RETURN count(r)",
                        parameters={"mid": source_memory_id},
                    )
                    n = 0
                    if count_result.has_next():
                        n = int(count_result.get_next()[0] or 0)
                    if n == 0:
                        continue
                    self._conn.execute(
                        f"MATCH ()-[r:{rel}]->() WHERE r.source_memory_id = $mid DELETE r",
                        parameters={"mid": source_memory_id},
                    )
                    removed += n
                except Exception as e:
                    logger.debug("delete_by_memory(%s) on %s failed: %s", source_memory_id, rel, e)
        return removed

    def close(self) -> None:
        with self._lock:
            self._conn = None
            self._db = None
            self.healthy = False
