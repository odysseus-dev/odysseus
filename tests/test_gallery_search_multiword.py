"""Regression for #2123 (gallery part): library search must match multi-word
queries in any order and treat % / _ literally.

Old code built a single ``%{search}%`` LIKE over prompt/tags/ai_tags, so
"red car" missed "a car that is red", and a literal "%" matched every row.
"""

import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import GalleryImage
import routes.gallery_routes as gallery_routes


def _client_with_gallery(monkeypatch, tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'gallery_search.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(gallery_routes, "SessionLocal", session_factory)

    db = session_factory()
    try:
        db.add_all(
            [
                GalleryImage(
                    id="img-red-car",
                    filename=f"{uuid.uuid4().hex}.png",
                    prompt="a car that is red",
                    model="model-a",
                    tags="",
                    ai_tags="",
                    is_active=True,
                    file_size=10,
                ),
                GalleryImage(
                    id="img-boat",
                    filename=f"{uuid.uuid4().hex}.png",
                    prompt="blue boat on the lake",
                    model="model-a",
                    tags="",
                    ai_tags="",
                    is_active=True,
                    file_size=10,
                ),
                GalleryImage(
                    id="img-percent",
                    filename=f"{uuid.uuid4().hex}.png",
                    prompt="100% cotton shirt",
                    model="model-a",
                    tags="sale_2024",
                    ai_tags="",
                    is_active=True,
                    file_size=10,
                ),
                GalleryImage(
                    id="img-backslash",
                    filename=f"{uuid.uuid4().hex}.png",
                    prompt=r"C:\photos\sunset",
                    model="model-a",
                    tags="",
                    ai_tags="",
                    is_active=True,
                    file_size=10,
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    app = FastAPI()
    app.include_router(gallery_routes.setup_gallery_routes())
    return TestClient(app)


def test_gallery_search_matches_words_in_any_order(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    client = _client_with_gallery(monkeypatch, tmp_path)

    body = client.get("/api/gallery/library", params={"search": "red car"}).json()
    ids = {item["id"] for item in body["items"]}
    assert "img-red-car" in ids
    assert "img-boat" not in ids


def test_gallery_search_escapes_percent_wildcard(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    client = _client_with_gallery(monkeypatch, tmp_path)

    body = client.get("/api/gallery/library", params={"search": "%"}).json()
    ids = {item["id"] for item in body["items"]}
    assert ids == {"img-percent"}


def test_gallery_search_escapes_underscore_wildcard(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    client = _client_with_gallery(monkeypatch, tmp_path)

    # Only img-percent has a literal underscore (in tags "sale_2024").
    # Unescaped, "_" matches any single char so every row matches.
    body = client.get("/api/gallery/library", params={"search": "_"}).json()
    ids = {item["id"] for item in body["items"]}
    assert ids == {"img-percent"}


def test_gallery_search_escapes_backslash(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    client = _client_with_gallery(monkeypatch, tmp_path)

    # Only img-backslash has a literal backslash. The escape char itself
    # must survive escaping instead of escaping the surrounding wildcards.
    body = client.get("/api/gallery/library", params={"search": "\\"}).json()
    ids = {item["id"] for item in body["items"]}
    assert ids == {"img-backslash"}


def test_gallery_tag_filter_escapes_wildcards(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    client = _client_with_gallery(monkeypatch, tmp_path)

    # Unescaped, "%" / "_" match every row; escaped, only literal matches.
    assert client.get("/api/gallery/library", params={"tag": "%"}).json()["items"] == []

    body = client.get("/api/gallery/library", params={"tag": "_"}).json()
    assert {item["id"] for item in body["items"]} == {"img-percent"}
