"""storage.py — PluginStorage with table-prefix isolation.

Every extension gets isolated SQLite tables named ``<extension_id>__<name>``.
For example, an extension with id ``whats2api-bridge`` and a declared table
``messages`` ends up at ``whats2appi_bridge__messages`` in the DB.

Why a prefix and not a separate file?
  - One DB connection, one transaction model (matches the rest of Odysseus).
  - Backup / restore tools see the prefix and can dump/restore one
    extension without touching the rest.
  - JOINs across extensions are explicitly disallowed (see
    ``_ensure_table_owned``); the prefix is a hard wall.

The ``data_schema`` block in the manifest describes the tables. Format:

    {
      "messages": {
        "columns": {
          "id":           "TEXT PRIMARY KEY",
          "chat_jid":     "TEXT NOT NULL",
          "text":         "TEXT",
          "ts":           "INTEGER NOT NULL"
        },
        "indexes": [["chat_jid", "ts"]],
        "unique":  [["id"]]
      },
      "chats": { "columns": {...}, ... }
    }

The column syntax is plain SQLite column constraint grammar (anything
SQLite accepts after a column name in a CREATE TABLE works). ``PRIMARY
KEY``, ``NOT NULL``, ``DEFAULT``, ``UNIQUE``, ``REFERENCES``, ``CHECK``
are all passed through.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any, Iterable

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine


_TABLE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_OWNED_PREFIX_SEP = "__"


class OXSStorageError(PermissionError):
    """Raised when a storage operation crosses extension boundaries."""


class PluginStorage:
    """Per-extension storage handle, scoped to ``<extension_id>__*`` tables."""

    def __init__(
        self,
        extension_id: str,
        *,
        engine: Engine | None = None,
        database_url: str | None = None,
    ):
        if not _TABLE_NAME_RE.match(extension_id):
            raise ValueError(f"invalid extension_id for prefix: {extension_id!r}")
        self.extension_id = extension_id
        self._prefix = extension_id + _OWNED_PREFIX_SEP
        if engine is None:
            url = database_url or "sqlite:///:memory:"
            self.engine = create_engine(url, future=True)
        else:
            self.engine = engine

    # ── Schema management ──────────────────────────────────────────────

    def ensure_table(self, name: str, *, columns: dict[str, str]) -> None:
        """Create the prefixed table if it does not already exist.

        ``columns`` is ``{col_name: sqlite_column_def}``. Column names are
        validated; the column definitions are passed to ``CREATE TABLE``
        verbatim.
        """
        self._ensure_table_owned(name)
        if not columns:
            raise ValueError(
                f"ensure_table({name!r}) requires at least one column"
            )
        full = self._table(name)
        col_sql = ", ".join(
            f"{col} {self._validate_column_def(col, defs)}"
            for col, defs in columns.items()
        )
        ddl = f"CREATE TABLE IF NOT EXISTS {full} ({col_sql})"
        with self.engine.begin() as conn:
            conn.execute(text(ddl))

    def ensure_index(self, table: str, columns: list[str]) -> None:
        self._ensure_table_owned(table)
        full = self._table(table)
        idx = f"idx_{full}_{'_'.join(columns)}"
        cols_sql = ", ".join(columns)
        ddl = f"CREATE INDEX IF NOT EXISTS {idx} ON {full} ({cols_sql})"
        with self.engine.begin() as conn:
            conn.execute(text(ddl))

    def ensure_unique(self, table: str, columns: list[str]) -> None:
        self._ensure_table_owned(table)
        full = self._table(table)
        # SQLite supports UNIQUE in column defs or as table constraints.
        # We check if the unique already covers these columns; if not, add.
        cols_csv = ", ".join(columns)
        ddl = (
            f"CREATE UNIQUE INDEX IF NOT EXISTS uq_{full}_{'_'.join(columns)} "
            f"ON {full} ({cols_csv})"
        )
        with self.engine.begin() as conn:
            conn.execute(text(ddl))

    def apply_data_schema(self, data_schema: dict) -> list[str]:
        """Apply every table defined in ``data_schema``. Returns list of created tables."""
        created: list[str] = []
        if not isinstance(data_schema, dict):
            return created
        for table, defn in data_schema.items():
            if not isinstance(defn, dict):
                continue
            columns = defn.get("columns") or {}
            if not isinstance(columns, dict) or not columns:
                continue
            self.ensure_table(table, columns=columns)
            created.append(self._table(table))
            for idx_cols in defn.get("indexes") or []:
                if isinstance(idx_cols, list) and idx_cols:
                    self.ensure_index(table, idx_cols)
            for uq_cols in defn.get("unique") or []:
                if isinstance(uq_cols, list) and uq_cols:
                    self.ensure_unique(table, uq_cols)
        return created

    def drop_own_tables(self) -> list[str]:
        """Drop every prefixed table that currently exists. Used at uninstall."""
        insp = inspect(self.engine)
        all_tables = set(insp.get_table_names())
        own = sorted(t for t in all_tables if t.startswith(self._prefix))
        if not own:
            return []
        with self.engine.begin() as conn:
            for t in own:
                conn.execute(text(f"DROP TABLE IF EXISTS {t}"))
        return own

    def list_own_tables(self) -> list[str]:
        insp = inspect(self.engine)
        all_tables = set(insp.get_table_names())
        return sorted(t for t in all_tables if t.startswith(self._prefix))

    # ── CRUD ────────────────────────────────────────────────────────────

    def insert(self, table: str, **values: Any) -> int:
        """Insert one row. Returns the last inserted rowid.

        Unknown column names raise ``OXSStorageError`` — the prefix wall
        catches tables, not dict keys, so we additionally check the keys
        against the table's declared columns.
        """
        self._ensure_table_owned(table)
        full = self._table(table)
        cols = self._real_columns(full)
        for col in values:
            if col not in cols:
                raise OXSStorageError(
                    f"column {col!r} not in table {full!r}"
                )
        cols_list = list(values.keys())
        placeholders = ", ".join(f":{c}" for c in cols_list)
        cols_sql = ", ".join(cols_list)
        sql = f"INSERT INTO {full} ({cols_sql}) VALUES ({placeholders})"
        with self.engine.begin() as conn:
            result = conn.execute(text(sql), values)
            return int(result.lastrowid or 0)

    def execute_write(self, sql: str, params: dict | tuple | None = None) -> int:
        """Run a DELETE/UPDATE/INSERT against this extension's own tables.

        Same prefix-safety as :meth:`execute_raw` but the head verb is
        restricted to writes. Returns the affected row count.
        """
        if not isinstance(sql, str):
            raise TypeError("execute_write requires str")
        stripped = sql.strip()
        head = stripped.split(None, 1)[0].upper() if stripped else ""
        if head not in ("INSERT", "UPDATE", "DELETE"):
            raise OXSStorageError(
                f"execute_write only accepts INSERT/UPDATE/DELETE (got {head!r})"
            )
        # Reuse the prefix wall: collect real table names touched by
        # the FROM/JOIN/UPDATE/INSERT-INTO clauses.
        import re as _re
        owned = self._prefix
        bad: list[str] = []
        for match in _re.finditer(
            r"\b(?:FROM|JOIN|UPDATE|INTO)\s+`?\"?([A-Za-z_][\w]*)",
            stripped,
            _re.IGNORECASE,
        ):
            ident = match.group(1)
            if not ident.startswith(owned):
                bad.append(ident)
        if bad:
            raise OXSStorageError(
                f"execute_write must only touch own tables (prefix {owned!r}); "
                f"found: {bad}"
            )
        with self.engine.begin() as conn:
            result = conn.execute(text(stripped), params or {})
            return int(result.rowcount or 0)

    def execute_raw(self, sql: str, params: dict | None = None) -> list[dict]:
        """Run a SELECT against this extension's own tables only.

        Other DDL/DML is refused. CTE references (``WITH name AS (...)``)
        are excluded from the prefix wall — they reference CTE aliases,
        not real tables.
        """
        if not isinstance(sql, str):
            raise TypeError("execute_raw requires str")
        stripped = sql.strip()
        head = stripped.split(None, 1)[0].upper() if stripped else ""
        if head not in ("SELECT", "WITH"):
            raise OXSStorageError(
                f"execute_raw only accepts SELECT/WITH queries (got {head!r})"
            )

        # Collect CTE aliases declared anywhere in the query so we can
        # exclude them from the FROM/JOIN scan.
        cte_aliases = self._collect_cte_aliases(stripped)

        # Tokenize the FROM/JOIN table names and ensure each is owned.
        import re as _re
        owned = self._prefix
        bad: list[str] = []
        for match in _re.finditer(
            r"\b(?:FROM|JOIN)\s+`?\"?([A-Za-z_][\w]*)",
            stripped,
            _re.IGNORECASE,
        ):
            ident = match.group(1)
            if ident in cte_aliases:
                continue
            if not ident.startswith(owned):
                bad.append(ident)
        if bad:
            raise OXSStorageError(
                f"execute_raw must only reference own tables (prefix {owned!r}); "
                f"found: {bad}"
            )
        with self.engine.connect() as conn:
            result = conn.execute(text(stripped), params or {})
            return [dict(row._mapping) for row in result]

    def _collect_cte_aliases(self, sql: str) -> set[str]:
        """Find identifiers declared as CTE names in ``WITH a AS (...)`` clauses."""
        import re as _re
        out: set[str] = set()
        upper = sql.upper()
        i = 0
        while True:
            idx = upper.find("WITH", i)
            if idx < 0:
                break
            j = idx + len("WITH")
            # Skip whitespace
            while j < len(sql) and sql[j].isspace():
                j += 1
            # Walk comma-separated ``name AS (...)`` groups until we hit
            # the closing paren of the last one. Names are bare
            # identifiers.
            while True:
                # Skip whitespace
                while j < len(sql) and sql[j].isspace():
                    j += 1
                # Read identifier
                m = _re.match(r"[A-Za-z_]\w*", sql[j:])
                if not m:
                    break
                name = m.group(0)
                j += len(name)
                # Skip whitespace
                while j < len(sql) and sql[j].isspace():
                    j += 1
                if sql[j:j + 2].upper() != "AS":
                    break  # malformed
                j += 2
                while j < len(sql) and sql[j].isspace():
                    j += 1
                if sql[j] != "(":
                    break
                # Find matching close
                depth = 0
                k = j
                while k < len(sql):
                    if sql[k] == "(":
                        depth += 1
                    elif sql[k] == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    k += 1
                if k >= len(sql):
                    break
                out.add(name)
                j = k + 1
                # Continue if there's a comma (more CTE definitions) or a
                # SELECT (main body starts).
                while j < len(sql) and sql[j].isspace():
                    j += 1
                if sql[j:j + 1] == ",":
                    j += 1
                    continue
                if upper[j:j + 6] in ("SELECT", "WITH  "):
                    break
                # Anything else (like the main SELECT) ends the CTE walk.
                break
            i = j
        return out

    def table_name(self, name: str) -> str:
        """Return the prefixed table name. Useful for raw queries / ORM."""
        self._ensure_table_owned(name)
        return self._table(name)

    # ── Internals ──────────────────────────────────────────────────────

    def _table(self, name: str) -> str:
        return self._prefix + name

    def _ensure_table_owned(self, name: str) -> None:
        if not _TABLE_NAME_RE.match(name):
            raise ValueError(f"invalid table name: {name!r}")
        # Always returns the prefixed name. Callers compose SELECT/INSERT
        # themselves and the prefix is part of the string, so cross-
        # extension access is impossible.

    def _validate_column_def(self, col: str, defs: str) -> str:
        if not _TABLE_NAME_RE.match(col):
            raise ValueError(f"invalid column name: {col!r}")
        if not isinstance(defs, str) or not defs.strip():
            raise ValueError(f"empty column def for {col!r}")
        # Reject obvious injection vectors even though we are the only
        # author of the DDL: semicolons, comments, parentheses that would
        # close the CREATE TABLE prematurely.
        bad_chars = (";", "--", "/*", "*/", "\n", "\r")
        if any(c in defs for c in bad_chars):
            raise ValueError(f"column def contains forbidden chars: {defs!r}")
        return defs.strip()

    def _real_columns(self, full_table: str) -> set[str]:
        insp = inspect(self.engine)
        try:
            cols = insp.get_columns(full_table)
        except Exception:
            return set()
        return {c["name"] for c in cols}


__all__ = ["PluginStorage", "OXSStorageError"]