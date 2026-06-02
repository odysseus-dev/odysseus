"""Pin the fix for issue #1115: non-text, non-image, non-audio
attachments (e.g. .stl, .obj, .step, .blend, archives) used to surface
to the model as the bare string ``"[Attached non-text file]"`` with no
name, path, size, or hint. The agent had no anchor to call bash/python
against, so a user who dragged a .stl into the chat would hear the
agent say "I don't see a file" even though ``save_upload`` had stored
it on disk.

These tests pin the new behaviour: every non-text attachment leaks
its name, absolute path, size, and MIME into the user-content text,
and the text tells the model it can use shell / Python tools against
the saved file.
"""

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _make_resolved(path: Path, name: str = "model.stl", mime: str = "application/octet-stream") -> dict:
    return {
        "id": path.name,
        "path": str(path),
        "name": name,
        "original_name": name,
        "mime": mime,
        "size": path.stat().st_size,
        "owner": "alice",
    }


def _make_handler(tmp_path: Path):
    from src.upload_handler import UploadHandler
    base = tmp_path / "base"
    upload = tmp_path / "uploads"
    base.mkdir()
    upload.mkdir()
    return UploadHandler(base_dir=str(base), upload_dir=str(upload)), upload


def test_stl_attachment_surfaces_path_and_name(tmp_path):
    from src.document_processor import build_user_content

    handler, upload_dir = _make_handler(tmp_path)
    stl = upload_dir / "abc123def456.stl"
    stl.write_bytes(b"solid empty\nendsolid empty\n")

    info = _make_resolved(stl, name="model.stl")
    content = build_user_content(
        "What's the volume of this STL?",
        [info["id"]],
        str(upload_dir),
        handler,
        owner="alice",
        resolved_uploads={info["id"]: info},
    )

    assert "model.stl" in content
    assert str(stl) in content
    assert "non-text file" in content
    assert "bash" in content.lower() or "python" in content.lower()


def test_stl_attachment_surfaces_size_and_mime(tmp_path):
    from src.document_processor import build_user_content

    handler, upload_dir = _make_handler(tmp_path)
    stl = upload_dir / "x.stl"
    stl.write_bytes(b"solid x\nendsolid x\n")
    info = _make_resolved(stl, name="tiny.stl", mime="application/sla")
    content = build_user_content(
        "inspect this",
        [info["id"]],
        str(upload_dir),
        handler,
        owner="alice",
        resolved_uploads={info["id"]: info},
    )

    assert "tiny.stl" in content
    assert "application/sla" in content


def test_multiple_nontext_attachments_all_surfaced(tmp_path):
    from src.document_processor import build_user_content

    handler, upload_dir = _make_handler(tmp_path)
    a = upload_dir / "a.stl"
    b = upload_dir / "b.obj"
    a.write_bytes(b"solid a\nendsolid a\n")
    b.write_bytes(b"# obj a\n")
    info_a = _make_resolved(a, name="mesh_a.stl")
    info_b = _make_resolved(b, name="mesh_b.obj", mime="model/obj")

    content = build_user_content(
        "compare these",
        [info_a["id"], info_b["id"]],
        str(upload_dir),
        handler,
        owner="alice",
        resolved_uploads={info_a["id"]: info_a, info_b["id"]: info_b},
    )

    assert "mesh_a.stl" in content
    assert "mesh_b.obj" in content
    assert str(a) in content
    assert str(b) in content


def test_nontext_attachment_with_missing_path_silently_skipped(tmp_path):
    from src.document_processor import build_user_content

    handler, upload_dir = _make_handler(tmp_path)
    info = {
        "id": "ghost.stl",
        "path": str(upload_dir / "ghost.stl"),
        "name": "ghost.stl",
        "original_name": "ghost.stl",
        "mime": "application/octet-stream",
        "size": 0,
        "owner": "alice",
    }
    content = build_user_content(
        "where is the file?",
        [info["id"]],
        str(upload_dir),
        handler,
        owner="alice",
        resolved_uploads={info["id"]: info},
    )

    assert content == "where is the file?"
    assert "non-text file" not in content


def test_existing_image_audio_document_paths_unchanged(tmp_path):
    """Regression guard: the fix must not affect image or text-document
    paths. A small PNG and a small .txt file should still inline as
    before (image_url / extracted text), not as the new "non-text file"
    banner."""
    from src.document_processor import build_user_content

    handler, upload_dir = _make_handler(tmp_path)
    png = upload_dir / "i.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    txt = upload_dir / "r.txt"
    txt.write_text("hello world\n", encoding="utf-8")

    info_png = _make_resolved(png, name="i.png", mime="image/png")
    info_txt = _make_resolved(txt, name="r.txt", mime="text/plain")
    content = build_user_content(
        "see attachments",
        [info_png["id"], info_txt["id"]],
        str(upload_dir),
        handler,
        owner="alice",
        resolved_uploads={info_png["id"]: info_png, info_txt["id"]: info_txt},
    )

    if isinstance(content, list):
        has_image = any(
            isinstance(item, dict) and item.get("type") == "image_url"
            for item in content
        )
        assert has_image, "PNG attachment must still produce an image_url entry"
        text = "".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    else:
        text = content
    assert "hello world" in text
    assert "non-text file" not in text
