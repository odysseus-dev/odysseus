"""Read-Only Controlled Intelligence Gateway for OdyAI (CP-3B).

Provides authenticated, scope-enforced federated search across:
  - data/private_ocr_index.db (ocr_pages_fts + ocr_documents)
  - data/stream_media_catalog.db (transcript_segments_fts + media_items)
  - data/drive_inventory.db (provenance metadata lookup)

All database connections open via SQLite read-only URIs (?mode=ro) with
PRAGMA query_only = ON. No databases are merged or mutated.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

DEFAULT_APP_DB = DATA_DIR / "app.db"
DEFAULT_OCR_DB = DATA_DIR / "private_ocr_index.db"
DEFAULT_MEDIA_DB = DATA_DIR / "stream_media_catalog.db"
DEFAULT_INVENTORY_DB = DATA_DIR / "drive_inventory.db"

SENSITIVITY_ORDER: dict[str, int] = {
    "general-rag": 1,
    "business-rag": 2,
    "personal-private": 3,
    "restricted-financial": 4,
    "restricted-pii": 5,
}

# OCR-tolerant physical access credential patterns ? triggers full-snippet
# suppression so surrounding facility/unit details are never exposed alongside
# a redacted code.
ACCESS_CREDENTIAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Explicit delimiter (: = #) followed by a 3-20 char secret token
    re.compile(
        r"(?i)\b(gate\s*c[o0]de|access\s*c[o0]de|security\s*c[o0]de|pin(?:\s*c[o0]de)?|passc[o0]de|passw[o0]rd)"
        r"\s*[:=#]\s*[*#?]?[A-Z0-9!@$%^&*_-]{3,20}[*#]?"
    ),
    # Whitespace-separated secret token (requires at least one digit or special symbol so ordinary words like 'password policy' are not suppressed)
    re.compile(
        r"(?i)\b(gate\s*c[o0]de|access\s*c[o0]de|security\s*c[o0]de|pin(?:\s*c[o0]de)?|passc[o0]de|passw[o0]rd)"
        r"\s+(?=[A-Z0-9!@$%^&*_-]*[\d*#!?@$%^&_-])[*#?]?[A-Z0-9!@$%^&*_-]{3,20}[*#]?"
    ),
    # Standalone keypad-delimited numeric codes (*451963#, ?999999#, #884422*)
    re.compile(r"[*#?]\d{4,10}[*#]"),
)

# OCR-tolerant contextual credential patterns ? masks account/routing/SSN/email
# identifiers while preserving surrounding non-secret context, dates, and amounts.
CONTEXTUAL_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)\b(ac+ount\s*(?:number|#|no\.?)?|acc[o0]unt\s*(?:number|#|no\.?)?|acct\s*(?:number|#|no\.?)?|routing\s*(?:number|#|no\.?)?|ssn|social\s*security)"
        r"\s*[:=#-]?\s*[A-Z0-9-]{4,20}"
    ),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
)


@dataclass(frozen=True)
class AuthContext:
    """Verified caller identity and derived authorization permissions."""

    owner: str
    scopes: frozenset[str]
    auth_method: str  # "session" | "api_token"
    session_id: str | None = None
    token_id: str | None = None


def ro_connect(db_path: Path) -> sqlite3.Connection:
    """Open an existing SQLite database in strict read-only, query-only mode."""
    resolved = Path(db_path).resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Database not found: {resolved}")
    uri = f"file:{resolved.as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=5.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = ON;")
    return con


def _parse_iso_utc(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def resolve_auth_from_session(
    session_id: str,
    *,
    app_db: Path = DEFAULT_APP_DB,
    max_idle_hours: int = 24,
    now_utc: datetime | None = None,
) -> AuthContext:
    """Validate an active, non-archived, non-expired session in app.db."""
    if not session_id or not session_id.strip():
        raise PermissionError("Missing session identifier.")

    con = ro_connect(app_db)
    try:
        row = con.execute(
            """
            SELECT id, owner, archived, updated_at, created_at
            FROM sessions
            WHERE id = ?
            """,
            (session_id.strip(),),
        ).fetchone()
    finally:
        con.close()

    if row is None:
        raise PermissionError("Invalid or unknown session.")
    if int(row["archived"] or 0) != 0:
        raise PermissionError("Session is archived/revoked.")

    owner = (row["owner"] or "").strip()
    if not owner:
        raise PermissionError("Session has no authenticated owner.")

    ref_now = now_utc or datetime.now(timezone.utc)
    last_ts = _parse_iso_utc(row["updated_at"]) or _parse_iso_utc(row["created_at"])
    if last_ts is None:
        raise PermissionError("Session timestamp invalid; failing closed.")
    if ref_now - last_ts > timedelta(hours=max_idle_hours):
        raise PermissionError("Session has expired.")

    # Interactive authenticated session derives owner-scoped read permissions
    derived_scopes = frozenset({
        "docs:read",
        "media:read",
        "personal:read",
        "business:read",
        "tier:restricted-pii",
    })
    return AuthContext(
        owner=owner,
        scopes=derived_scopes,
        auth_method="session",
        session_id=row["id"],
    )


def resolve_auth_from_token(
    raw_token: str,
    *,
    app_db: Path = DEFAULT_APP_DB,
) -> AuthContext:
    """Validate an active API token in app.db using constant-time SHA-256 comparison."""
    if not raw_token or not raw_token.strip():
        raise PermissionError("Missing API token.")

    candidate = raw_token.strip()
    candidate_hash = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
    prefix = candidate[:8]

    con = ro_connect(app_db)
    try:
        rows = con.execute(
            """
            SELECT id, owner, token_hash, token_prefix, scopes, is_active
            FROM api_tokens
            WHERE token_prefix = ? OR is_active = 1
            """,
            (prefix,),
        ).fetchall()
    finally:
        con.close()

    matched = None
    for r in rows:
        stored_hash = (r["token_hash"] or "").strip()
        if stored_hash and hmac.compare_digest(stored_hash, candidate_hash):
            matched = r
            break

    if matched is None:
        raise PermissionError("Invalid API token.")
    if int(matched["is_active"] or 0) != 1:
        raise PermissionError("API token is revoked/inactive.")

    owner = (matched["owner"] or "").strip()
    if not owner:
        raise PermissionError("API token has no authenticated owner.")

    raw_scopes = matched["scopes"] or ""
    parsed_scopes = frozenset(
        s.strip() for s in re.split(r"[,\s]+", raw_scopes) if s.strip()
    )
    return AuthContext(
        owner=owner,
        scopes=parsed_scopes,
        auth_method="api_token",
        token_id=matched["id"],
    )


def _authorize_request(
    auth: AuthContext,
    *,
    requested_domains: Iterable[str],
    requested_owner_scopes: Iterable[str],
    max_sensitivity: str,
    expected_workstation_owner: str | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Enforce domain, owner_scope, and sensitivity_tier permissions before querying."""
    if not isinstance(auth, AuthContext) or not auth.owner:
        raise PermissionError("Unauthenticated caller rejected.")

    if expected_workstation_owner and auth.owner != expected_workstation_owner:
        raise PermissionError("Caller owner does not match resource owner.")

    domains = tuple(dict.fromkeys(d.strip().lower() for d in requested_domains if d.strip()))
    if not domains:
        raise ValueError("At least one search domain ('docs' or 'media') is required.")
    for d in domains:
        if d not in ("docs", "media"):
            raise ValueError(f"Unsupported domain: {d!r}")
        if f"{d}:read" not in auth.scopes:
            raise PermissionError(f"Caller lacks required scope '{d}:read'.")

    owner_scopes = tuple(dict.fromkeys(s.strip().lower() for s in requested_owner_scopes if s.strip()))
    if not owner_scopes:
        raise ValueError("At least one owner_scope ('personal' or 'business') is required.")
    for os_name in owner_scopes:
        if os_name not in ("personal", "business"):
            raise ValueError(f"Unsupported owner_scope: {os_name!r}")
        if f"{os_name}:read" not in auth.scopes:
            raise PermissionError(f"Caller lacks required scope '{os_name}:read'.")

    if max_sensitivity not in SENSITIVITY_ORDER:
        raise ValueError(f"Unsupported sensitivity_tier: {max_sensitivity!r}")

    # Determine highest sensitivity tier allowed by caller's scopes
    caller_max_rank = 2  # default ceiling: business-rag
    if "tier:restricted-pii" in auth.scopes:
        caller_max_rank = 5
    elif "tier:restricted-financial" in auth.scopes:
        caller_max_rank = 4
    elif "tier:personal-private" in auth.scopes:
        caller_max_rank = 3

    requested_rank = SENSITIVITY_ORDER[max_sensitivity]
    effective_rank = min(caller_max_rank, requested_rank)
    allowed_tiers = tuple(
        tier for tier, rank in SENSITIVITY_ORDER.items() if rank <= effective_rank
    )
    return domains, owner_scopes, allowed_tiers


def sanitize_snippet(
    text: str,
    *,
    sensitivity_tier: str | None = None,
    suppress_restricted_pii: bool = True,
    redact_sensitive: bool = True,
) -> tuple[str, bool]:
    """Suppress restricted-PII and physical-access-credential snippets; redact OCR-tolerant account tokens."""
    if suppress_restricted_pii and sensitivity_tier == "restricted-pii":
        return (
            "[RESTRICTED-PII SNIPPET SUPPRESSED ? AUTHORIZED OWNER MUST INSPECT SOURCE FILE DIRECTLY]",
            True,
        )

    if not text:
        return "", False

    if redact_sensitive:
        for access_pat in ACCESS_CREDENTIAL_PATTERNS:
            if access_pat.search(text):
                return (
                    "[CREDENTIAL-BEARING SNIPPET SUPPRESSED ? AUTHORIZED OWNER MUST INSPECT SOURCE FILE DIRECTLY]",
                    True,
                )

    cleaned = text
    was_redacted = False
    if redact_sensitive:
        for pat in CONTEXTUAL_SECRET_PATTERNS:
            new_text, count = pat.subn(
                lambda m: f"{m.group(1)}: [REDACTED]" if m.lastindex else "[REDACTED]",
                cleaned,
            )
            if count > 0:
                was_redacted = True
                cleaned = new_text

    return cleaned, was_redacted


def _sanitize_fts_query(raw_query: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9_'-]+", raw_query or "")
    if not tokens:
        raise ValueError("Search query must contain at least one searchable term.")
    return " ".join(f'"{tok}"' for tok in tokens)


def _lookup_inventory_provenance(
    sha256_list: list[str],
    *,
    inventory_db: Path = DEFAULT_INVENTORY_DB,
) -> dict[str, dict[str, Any]]:
    if not sha256_list or not Path(inventory_db).exists():
        return {}
    placeholders = ", ".join("?" for _ in sha256_list)
    con = ro_connect(inventory_db)
    try:
        rows = con.execute(
            f"""
            SELECT sha256, status, catalog_category, proposed_category, scanned_at
            FROM inventory_files
            WHERE sha256 IN ({placeholders})
            ORDER BY id DESC
            """,
            tuple(sha256_list),
        ).fetchall()
    finally:
        con.close()

    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        digest = r["sha256"]
        if digest and digest not in out:
            out[digest] = {
                "inventory_status": r["status"],
                "category": r["catalog_category"] or r["proposed_category"],
                "scanned_at": r["scanned_at"],
            }
    return out


def _search_ocr_docs(
    fts_query: str,
    *,
    ocr_db: Path,
    inventory_db: Path,
    owner_scopes: tuple[str, ...],
    allowed_tiers: tuple[str, ...],
    fetch_limit: int,
    suppress_restricted_pii: bool,
    redact_sensitive: bool,
) -> list[dict[str, Any]]:
    scope_ph = ", ".join("?" for _ in owner_scopes)
    tier_ph = ", ".join("?" for _ in allowed_tiers)

    con = ro_connect(ocr_db)
    try:
        rows = con.execute(
            f"""
            SELECT
                p.sha256 AS sha256,
                d.source_path AS source_path,
                d.filename AS filename,
                d.owner_scope AS owner_scope,
                d.sensitivity_tier AS sensitivity_tier,
                d.category AS category,
                p.page_num AS page_num,
                p.extracted_text AS extracted_text,
                bm25(ocr_pages_fts) AS raw_bm25
            FROM ocr_pages_fts
            JOIN ocr_documents d ON d.sha256 = ocr_pages_fts.sha256
            JOIN ocr_pages p ON p.sha256 = ocr_pages_fts.sha256 AND p.page_num = ocr_pages_fts.page_num
            WHERE ocr_pages_fts MATCH ?
              AND d.status = 'completed'
              AND d.owner_scope IN ({scope_ph})
              AND d.sensitivity_tier IN ({tier_ph})
            ORDER BY bm25(ocr_pages_fts) ASC
            LIMIT ?
            """,
            (fts_query, *owner_scopes, *allowed_tiers, fetch_limit),
        ).fetchall()
    finally:
        con.close()

    prov_map = _lookup_inventory_provenance(
        [r["sha256"] for r in rows if r["sha256"]],
        inventory_db=inventory_db,
    )

    results: list[dict[str, Any]] = []
    for rank, r in enumerate(rows, start=1):
        raw_excerpt = (r["extracted_text"] or "").strip()[:360]
        safe_snippet, redacted = sanitize_snippet(
            raw_excerpt,
            sensitivity_tier=r["sensitivity_tier"],
            suppress_restricted_pii=suppress_restricted_pii,
            redact_sensitive=redact_sensitive,
        )
        results.append({
            "domain": "docs",
            "domain_rank": rank,
            "sha256": r["sha256"],
            "source_path": r["source_path"],
            "filename": r["filename"],
            "owner_scope": r["owner_scope"],
            "sensitivity_tier": r["sensitivity_tier"],
            "category": r["category"],
            "location": {"page_num": r["page_num"]},
            "snippet": safe_snippet,
            "redacted": redacted,
            "provenance": prov_map.get(r["sha256"], {}),
        })
    return results


def _search_media_transcripts(
    fts_query: str,
    *,
    media_db: Path,
    fetch_limit: int,
    redact_sensitive: bool,
) -> list[dict[str, Any]]:
    con = ro_connect(media_db)
    try:
        rows = con.execute(
            """
            SELECT
                m.id AS media_id,
                m.source_path AS source_path,
                m.filename AS filename,
                m.scope AS storage_bucket,
                m.media_role AS media_role,
                m.duration_sec AS duration_sec,
                m.transcript_srt_path AS transcript_srt_path,
                m.transcript_json_path AS transcript_json_path,
                transcript_segments_fts.start_sec AS start_sec,
                transcript_segments_fts.end_sec AS end_sec,
                transcript_segments_fts.segment_text AS segment_text,
                bm25(transcript_segments_fts) AS raw_bm25
            FROM transcript_segments_fts
            JOIN media_items m ON CAST(m.id AS TEXT) = CAST(transcript_segments_fts.media_id AS TEXT)
            WHERE transcript_segments_fts MATCH ?
              AND m.is_duplicate = 0
              AND m.transcript_status = 'completed'
            ORDER BY bm25(transcript_segments_fts) ASC
            LIMIT ?
            """,
            (fts_query, fetch_limit),
        ).fetchall()
    finally:
        con.close()

    results: list[dict[str, Any]] = []
    for rank, r in enumerate(rows, start=1):
        safe_snippet, redacted = sanitize_snippet(
            (r["segment_text"] or "").strip(),
            sensitivity_tier=None,
            suppress_restricted_pii=False,
            redact_sensitive=redact_sensitive,
        )
        results.append({
            "domain": "media",
            "domain_rank": rank,
            "media_id": r["media_id"],
            "source_path": r["source_path"],
            "filename": r["filename"],
            "storage_bucket": r["storage_bucket"],
            "media_role": r["media_role"],
            "location": {
                "start_sec": float(r["start_sec"]) if r["start_sec"] is not None else None,
                "end_sec": float(r["end_sec"]) if r["end_sec"] is not None else None,
                "srt_path": r["transcript_srt_path"],
                "json_path": r["transcript_json_path"],
            },
            "snippet": safe_snippet,
            "redacted": redacted,
        })
    return results


def fuse_rrf(
    ranked_lists: Iterable[list[dict[str, Any]]],
    *,
    k: int = 60,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Combine ranked result lists using Reciprocal Rank Fusion: score = sum(1 / (k + rank))."""
    fused: list[dict[str, Any]] = []
    for rlist in ranked_lists:
        for item in rlist:
            rank = int(item["domain_rank"])
            rrf_score = round(1.0 / (k + rank), 6)
            enriched = dict(item)
            enriched["rrf_score"] = rrf_score
            fused.append(enriched)

    fused.sort(key=lambda x: (-x["rrf_score"], x["domain_rank"], x["filename"]))
    return fused[:limit]


def search_local_intelligence(
    query: str,
    *,
    auth: AuthContext,
    domains: Iterable[str] = ("docs", "media"),
    owner_scopes: Iterable[str] = ("personal", "business"),
    max_sensitivity: str = "restricted-pii",
    limit: int = 10,
    rrf_k: int = 60,
    suppress_restricted_pii: bool = True,
    redact_sensitive: bool = True,
    expected_workstation_owner: str | None = None,
    ocr_db: Path = DEFAULT_OCR_DB,
    media_db: Path = DEFAULT_MEDIA_DB,
    inventory_db: Path = DEFAULT_INVENTORY_DB,
) -> dict[str, Any]:
    """Execute an authenticated, read-only, RRF-ranked local knowledge search."""
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100.")

    allowed_domains, allowed_owner_scopes, allowed_tiers = _authorize_request(
        auth,
        requested_domains=domains,
        requested_owner_scopes=owner_scopes,
        max_sensitivity=max_sensitivity,
        expected_workstation_owner=expected_workstation_owner,
    )
    fts_query = _sanitize_fts_query(query)
    per_domain_limit = max(limit * 2, 20)

    ranked_lists: list[list[dict[str, Any]]] = []
    if "docs" in allowed_domains:
        ranked_lists.append(
            _search_ocr_docs(
                fts_query,
                ocr_db=ocr_db,
                inventory_db=inventory_db,
                owner_scopes=allowed_owner_scopes,
                allowed_tiers=allowed_tiers,
                fetch_limit=per_domain_limit,
                suppress_restricted_pii=suppress_restricted_pii,
                redact_sensitive=redact_sensitive,
            )
        )
    if "media" in allowed_domains:
        ranked_lists.append(
            _search_media_transcripts(
                fts_query,
                media_db=media_db,
                fetch_limit=per_domain_limit,
                redact_sensitive=redact_sensitive,
            )
        )

    results = fuse_rrf(ranked_lists, k=rrf_k, limit=limit)
    return {
        "query": query,
        "authenticated_owner": auth.owner,
        "auth_method": auth.auth_method,
        "domains": list(allowed_domains),
        "owner_scopes": list(allowed_owner_scopes),
        "allowed_sensitivity_tiers": list(allowed_tiers),
        "ranking": {"method": "rrf", "k": rrf_k},
        "result_count": len(results),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-Only Controlled Intelligence Gateway (interactive auth only; no secret CLI flags)."
    )
    parser.add_argument("--query", required=True, help="Search query string.")
    parser.add_argument("--auth-type", choices=("session", "token"), required=True, help="Authentication mode.")
    parser.add_argument("--domains", nargs="+", default=["docs", "media"], help="Domains to query: docs media")
    parser.add_argument("--owner-scopes", nargs="+", default=["personal", "business"], help="Owner scopes: personal business")
    parser.add_argument("--limit", type=int, default=5, help="Max fused results to return.")
    args = parser.parse_args()

    secret = getpass.getpass(f"Enter {args.auth_type} credential (hidden): ")
    if args.auth_type == "session":
        auth = resolve_auth_from_session(secret)
    else:
        auth = resolve_auth_from_token(secret)

    response = search_local_intelligence(
        args.query,
        auth=auth,
        domains=args.domains,
        owner_scopes=args.owner_scopes,
        limit=args.limit,
    )
    print(json.dumps(response, indent=2))


if __name__ == "__main__":
    main()
