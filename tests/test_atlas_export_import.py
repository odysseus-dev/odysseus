"""Portability: zip export/import round-trip, Obsidian-folder import, zip-slip.

Proves a vault survives an export→delete→import cycle, that an Obsidian-style
upload (filenames carrying a leading vault-folder + nested paths) lands flat and
keeps links/folders, and that a malicious zip entry can't escape the vault.
"""

import io
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import routes.atlas_routes as ar


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(ar, "ATLAS_ROOT", Path(tmp_path))
    ar._notes_cache.clear()
    app = FastAPI()

    @app.middleware("http")
    async def _set_user(request: Request, call_next):
        request.state.current_user = request.headers.get("X-Test-User", "alice")
        return await call_next(request)

    app.include_router(ar.setup_atlas_routes())
    return TestClient(app)


def _as(u):
    return {"X-Test-User": u}


def test_export_import_roundtrip(client):
    client.put("/api/atlas/note", json={"path": "Top", "content": "# Top\n[[sub/Child]]"}, headers=_as("alice"))
    client.put("/api/atlas/note", json={"path": "sub/Child", "content": "# Child"}, headers=_as("alice"))

    zbytes = client.get("/api/atlas/export", headers=_as("alice")).content
    names = set(zipfile.ZipFile(io.BytesIO(zbytes)).namelist())
    assert {"Top.md", "sub/Child.md"} <= names

    # Wipe, then re-import into the same (now empty) vault.
    client.post("/api/atlas/delete", json={"path": "Top.md"}, headers=_as("alice"))
    client.post("/api/atlas/delete", json={"path": "sub/Child.md"}, headers=_as("alice"))
    assert client.get("/api/atlas/notes", headers=_as("alice")).json()["notes"] == []

    res = client.post(
        "/api/atlas/import",
        files={"files": ("atlas-vault.zip", zbytes, "application/zip")},
        headers=_as("alice"),
    ).json()
    assert res["imported"] == 2
    restored = {n["path"] for n in client.get("/api/atlas/notes", headers=_as("alice")).json()["notes"]}
    assert restored == {"Top.md", "sub/Child.md"}


def test_obsidian_folder_upload_strips_leading_dir(client):
    # webkitdirectory sends each file's path relative to the picked folder,
    # i.e. "MyVault/...". The leading folder is stripped so notes land flat.
    files = [
        ("files", ("MyVault/Welcome.md", b"# Welcome\n[[Topics/Idea]]", "text/markdown")),
        ("files", ("MyVault/Topics/Idea.md", b"# Idea", "text/markdown")),
        ("files", ("MyVault/.obsidian/app.json", b"{}", "application/json")),
    ]
    res = client.post("/api/atlas/import", files=files, headers=_as("alice")).json()
    paths = {n["path"] for n in client.get("/api/atlas/notes", headers=_as("alice")).json()["notes"]}
    assert paths == {"Welcome.md", "Topics/Idea.md"}
    # Link across the imported folder resolves.
    body = client.get("/api/atlas/note", params={"path": "Welcome.md"}, headers=_as("alice")).json()
    assert {"target": "Topics/Idea", "resolved": "Topics/Idea.md"} in body["outlinks"]


def test_import_rejects_zip_slip(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../../evil.md", "pwned")
        zf.writestr("ok.md", "# fine")
    res = client.post(
        "/api/atlas/import",
        files={"files": ("vault.zip", buf.getvalue(), "application/zip")},
        headers=_as("alice"),
    ).json()
    assert res["imported"] == 1            # only ok.md
    assert res["skipped"] >= 1             # the traversal entry was dropped
    # Nothing escaped the vault root.
    assert not (Path(ar.ATLAS_ROOT).parent / "evil.md").exists()


def test_import_skips_disallowed_types(client):
    files = [
        ("files", ("note.md", b"# keep", "text/markdown")),
        ("files", ("script.exe", b"MZ", "application/octet-stream")),
    ]
    res = client.post("/api/atlas/import", files=files, headers=_as("alice")).json()
    assert res["imported"] == 1 and res["skipped"] == 1


def test_zip_import_strips_common_leading_folder(client):
    """An Obsidian .zip export (every entry under one 'MyVault/' dir) lands flat."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("MyVault/Welcome.md", "# Welcome\n[[Topics/Idea]]")
        zf.writestr("MyVault/Topics/Idea.md", "# Idea")
    client.post("/api/atlas/import",
                files={"files": ("MyVault.zip", buf.getvalue(), "application/zip")},
                headers=_as("alice"))
    paths = {n["path"] for n in client.get("/api/atlas/notes", headers=_as("alice")).json()["notes"]}
    assert paths == {"Welcome.md", "Topics/Idea.md"}   # no "MyVault/" nesting


def test_flat_zip_import_is_not_stripped(client):
    """Our own /export uses flat arcnames — those must NOT lose a path component."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("A.md", "# A")
        zf.writestr("sub/B.md", "# B")   # mixed depth, no single common top dir
    client.post("/api/atlas/import",
                files={"files": ("vault.zip", buf.getvalue(), "application/zip")},
                headers=_as("alice"))
    paths = {n["path"] for n in client.get("/api/atlas/notes", headers=_as("alice")).json()["notes"]}
    assert paths == {"A.md", "sub/B.md"}


def test_import_rejects_oversize_file(client):
    big = b"x" * (ar.MAX_IMPORT_FILE_BYTES + 1)
    res = client.post("/api/atlas/import",
                      files={"files": ("huge.md", big, "text/markdown")},
                      headers=_as("alice")).json()
    assert res["imported"] == 0 and res["skipped"] >= 1
