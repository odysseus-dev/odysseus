"""Fugassa database layer."""

from titan.fugassa.db import asset_repository, migrations, seed, sqlite_store

__all__ = [
    "asset_repository",
    "migrations",
    "seed",
    "sqlite_store",
    "schema_path",
]


def schema_path() -> str:
    import os

    return os.path.join(os.path.dirname(__file__), "schema.sql")
