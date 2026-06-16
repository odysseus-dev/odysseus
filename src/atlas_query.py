"""Atlas — the Bases query engine.

Evaluates a *structured* query (the canonical form the UI filter-builder, the
agent tool, and the MCP server all produce) against a vault's notes. No text
query language to parse or secure: a query is JSON.

Query shape (all keys optional except where noted)::

    {
      "from":   "notes" | "sections",   # default "notes"
      "where":  {"join": "and"|"or",
                 "filters": [{"field": "prop.status",
                              "op": "eq", "value": "open"}, ...]},
      "select": ["file.title", "prop.status"],   # columns; omit for defaults
      "computed": {"age_days": "days_since(prop.date)"},  # safe expressions
      "sort":   [{"field": "file.mtime", "dir": "desc"}],
      "group_by": "prop.status",         # for the board/Kanban view
      "view":   "table" | "cards" | "board",
      "limit":  500
    }

Fields:
  file.path | file.name | file.title | file.folder | file.mtime | file.tags
  prop.<name>                          (frontmatter property)
  section.heading | section.body | section.level   (when from == "sections")

Operators:
  eq ne contains startswith endswith regex gt lt gte lte exists empty in

Computed columns use a SMALL, SAFE expression evaluator (AST allowlist — no
``eval`` of arbitrary Python, no attribute access, no imports). This is the
documented boundary vs. Obsidian's full formula language.
"""

from __future__ import annotations

import ast
import os
import re
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from src.atlas_frontmatter import parse_frontmatter, extract_sections
from src.atlas_links import parse_links, note_title

MAX_REGEX_LEN = 200
MAX_ROWS = 2000

VALID_OPS = {
    "eq", "ne", "contains", "startswith", "endswith", "regex",
    "gt", "lt", "gte", "lte", "exists", "empty", "in",
}


# ---------------------------------------------------------------------------
# Record building
# ---------------------------------------------------------------------------

def _tags_for(props: Dict[str, Any], body: str) -> List[str]:
    """Frontmatter tags (list or comma/space string) plus inline #tags."""
    out: List[str] = []
    raw = props.get("tags")
    if isinstance(raw, str):
        out.extend(t.strip().lstrip("#") for t in re.split(r"[,\s]+", raw) if t.strip())
    elif isinstance(raw, list):
        out.extend(str(t).strip().lstrip("#") for t in raw if str(t).strip())
    for t in parse_links(body).get("tags", []):
        if t not in out:
            out.append(t)
    return out


def _base_record(note: Dict[str, Any]) -> Dict[str, Any]:
    """Per-note fields: file.* + prop.* (no section.* yet)."""
    path = note["path"]
    md = note.get("content", "") or ""
    props, body = parse_frontmatter(md)
    rec: Dict[str, Any] = {
        "file.path": path,
        "file.name": os.path.splitext(os.path.basename(path))[0],
        "file.title": note.get("title") or note_title(path, md),
        "file.folder": os.path.dirname(path),
        "file.mtime": note.get("mtime", 0),
        "file.tags": _tags_for(props, body),
        "_body": body,
    }
    for k, v in props.items():
        rec[f"prop.{k}"] = v
    return rec


def _records(query: Dict[str, Any], notes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    src = (query.get("from") or "notes").lower()
    recs: List[Dict[str, Any]] = []
    for note in notes:
        base = _base_record(note)
        if src == "sections":
            for sec in extract_sections(note.get("content", "") or ""):
                rec = dict(base)
                rec["section.heading"] = sec["heading"]
                rec["section.body"] = sec["body"]
                rec["section.level"] = sec["level"]
                recs.append(rec)
        else:
            recs.append(base)
    return recs


# ---------------------------------------------------------------------------
# Coercion + operators
# ---------------------------------------------------------------------------

def _as_number(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def _as_date(v: Any) -> Optional[datetime]:
    if isinstance(v, datetime):
        return v
    # PyYAML parses a bare `2026-06-10` as datetime.date — promote to datetime.
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    if isinstance(v, (int, float)):
        try:
            return datetime.fromtimestamp(v, tz=timezone.utc)
        except (OSError, ValueError, OverflowError):
            return None
    if isinstance(v, str):
        from dateutil import parser as _dp
        try:
            return _dp.parse(v.strip())
        except (ValueError, OverflowError, TypeError):
            return None
    return None


def _cmp(a: Any, b: Any) -> Optional[int]:
    """Three-way compare, trying numbers then dates then strings. None if N/A."""
    an, bn = _as_number(a), _as_number(b)
    if an is not None and bn is not None:
        return (an > bn) - (an < bn)
    ad, bd = _as_date(a), _as_date(b)
    if ad is not None and bd is not None:
        if ad.tzinfo and not bd.tzinfo:
            bd = bd.replace(tzinfo=ad.tzinfo)
        elif bd.tzinfo and not ad.tzinfo:
            ad = ad.replace(tzinfo=bd.tzinfo)
        return (ad > bd) - (ad < bd)
    sa, sb = str(a).lower(), str(b).lower()
    return (sa > sb) - (sa < sb)


def _match(field_val: Any, op: str, value: Any) -> bool:
    if op == "exists":
        return field_val not in (None, "", [], {})
    if op == "empty":
        return field_val in (None, "", [], {})

    if op in ("eq", "ne"):
        if isinstance(field_val, list):
            hit = any(str(x).lower() == str(value).lower() for x in field_val)
        else:
            hit = str(field_val).lower() == str(value).lower()
        return hit if op == "eq" else not hit

    if op == "contains":
        if isinstance(field_val, list):
            return any(str(value).lower() == str(x).lower() for x in field_val)
        return str(value).lower() in str(field_val or "").lower()
    if op == "startswith":
        return str(field_val or "").lower().startswith(str(value).lower())
    if op == "endswith":
        return str(field_val or "").lower().endswith(str(value).lower())

    if op == "regex":
        pat = str(value)
        if len(pat) > MAX_REGEX_LEN:
            return False
        try:
            return re.search(pat, str(field_val or ""), re.IGNORECASE) is not None
        except re.error:
            return False

    if op == "in":
        opts = value if isinstance(value, list) else [value]
        opts = [str(x).lower() for x in opts]
        if isinstance(field_val, list):
            return any(str(x).lower() in opts for x in field_val)
        return str(field_val).lower() in opts

    if op in ("gt", "lt", "gte", "lte"):
        c = _cmp(field_val, value)
        if c is None:
            return False
        return {"gt": c > 0, "lt": c < 0, "gte": c >= 0, "lte": c <= 0}[op]

    return False


def _passes(rec: Dict[str, Any], where: Dict[str, Any]) -> bool:
    filters = (where or {}).get("filters") or []
    if not filters:
        return True
    join = (where.get("join") or "and").lower()
    results = []
    for f in filters:
        op = f.get("op")
        if op not in VALID_OPS:
            results.append(False)
            continue
        results.append(_match(rec.get(f.get("field")), op, f.get("value")))
    return all(results) if join == "and" else any(results)


# ---------------------------------------------------------------------------
# Safe computed-column expressions
# ---------------------------------------------------------------------------

_ALLOWED_NODES = (
    ast.Expression, ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Compare,
    ast.IfExp, ast.Call, ast.Name, ast.Load, ast.Constant, ast.Subscript,
    ast.Index if hasattr(ast, "Index") else ast.Constant,
    ast.And, ast.Or, ast.Not, ast.USub,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
)

_FIELD_TOKEN = re.compile(r"\b(file|prop|section)\.([A-Za-z0-9_]+)")


def _expr_funcs() -> Dict[str, Any]:
    def days_since(x):
        d = _as_date(x)
        if d is None:
            return None
        now = datetime.now(d.tzinfo) if d.tzinfo else datetime.now()
        return (now - d).days
    return {
        "lower": lambda x: str(x).lower(),
        "upper": lambda x: str(x).upper(),
        "len": lambda x: len(x) if x is not None else 0,
        "str": lambda x: "" if x is None else str(x),
        "int": lambda x: int(_as_number(x) or 0),
        "round": lambda x, n=0: round(_as_number(x) or 0, n),
        "days_since": days_since,
    }


def _compile_expr(expr: str):
    """Validate a computed-column expression and return an AST, or raise."""
    # Rewrite dotted field tokens into subscripts on the record dict `f`.
    safe = _FIELD_TOKEN.sub(r'f["\1.\2"]', expr)
    tree = ast.parse(safe, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ValueError(f"disallowed expression element: {type(node).__name__}")
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name)
                                               and node.func.id in _expr_funcs()):
            raise ValueError("only allowlisted functions may be called")
        if isinstance(node, ast.Subscript) and not (
            isinstance(node.value, ast.Name) and node.value.id == "f"
        ):
            raise ValueError("subscript only allowed on the record")
    return tree


def _eval_computed(tree, rec: Dict[str, Any]):
    try:
        return eval(  # noqa: S307 — AST allowlisted in _compile_expr; no builtins
            compile(tree, "<atlas-computed>", "eval"),
            {"__builtins__": {}},
            {"f": rec, **_expr_funcs()},
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def _default_columns(query: Dict[str, Any], rows: List[Dict[str, Any]]) -> List[str]:
    src = (query.get("from") or "notes").lower()
    cols = ["file.title"]
    if src == "sections":
        cols.append("section.heading")
    prop_keys = set()
    for r in rows:
        for k in r:
            if k.startswith("prop."):
                prop_keys.add(k)
    cols.extend(sorted(prop_keys))
    return cols


def run_query(query: Dict[str, Any], notes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Run a structured query against ``notes`` (list of {path,title,mtime,content}).

    Returns ``{columns, rows, view, group_by, count}`` where each row is a dict
    of selected/auto columns plus any computed columns. Always includes
    ``file.path`` so the UI can open the underlying note.
    """
    query = query or {}
    recs = _records(query, notes)

    # Filter.
    matched = [r for r in recs if _passes(r, query.get("where") or {})]

    # Compile computed expressions once.
    computed = {}
    for name, expr in (query.get("computed") or {}).items():
        try:
            computed[name] = _compile_expr(str(expr))
        except (ValueError, SyntaxError):
            computed[name] = None  # invalid expr → column of None, not a crash

    # Sort (stable, multi-key, last key applied first).
    for s in reversed(query.get("sort") or []):
        field = s.get("field")
        rev = (s.get("dir") or "asc").lower() == "desc"
        matched.sort(key=lambda r, f=field: _sort_key(r.get(f)), reverse=rev)

    limit = min(int(query.get("limit", MAX_ROWS) or MAX_ROWS), MAX_ROWS)
    matched = matched[:limit]

    select = query.get("select")
    columns = list(select) if select else _default_columns(query, matched)
    for name in computed:
        if name not in columns:
            columns.append(name)

    rows = []
    for r in matched:
        row = {"file.path": r.get("file.path")}
        for c in columns:
            row[c] = r.get(c)
        for name, tree in computed.items():
            row[name] = _eval_computed(tree, r) if tree is not None else None
        rows.append(row)

    return {
        "columns": columns,
        "rows": rows,
        "count": len(rows),
        "view": (query.get("view") or "table"),
        "group_by": query.get("group_by"),
    }


def _sort_key(v: Any):
    """Total-order key: numbers/dates sort numerically, else case-folded string."""
    n = _as_number(v)
    if n is not None:
        return (0, n)
    d = _as_date(v)
    if d is not None:
        return (1, d.timestamp())
    return (2, str(v if v is not None else "").lower())
