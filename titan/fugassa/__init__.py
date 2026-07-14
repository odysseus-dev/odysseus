"""Fugassa AI RPG — Titan integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI


def register_fugassa(app: "FastAPI") -> None:
    from titan.fugassa.routes import router
    from titan.fugassa import save_store

    save_store.ensure_layout()
    app.include_router(router)
