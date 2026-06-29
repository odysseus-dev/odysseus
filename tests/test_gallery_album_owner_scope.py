"""Issue #2754 — gallery owner-scoping.

`patch_gallery_image` must validate that the *target album* belongs to the caller
before moving an image into it (otherwise user B can file B's image into user A's
album), and `list_albums` must owner-scope the per-album count + cover-fallback
queries. The gallery route handlers are closures, so — matching the AST-assertion
convention of test_gallery_image_privileges.py — we assert the guards are present
in the source.
"""
import ast
from pathlib import Path


def _function_sources():
    source = Path("routes/gallery/gallery_routes.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    return {
        node.name: ast.get_source_segment(source, node) or ""
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_patch_validates_target_album_ownership():
    fns = _function_sources()
    body = fns["patch_gallery_image"]
    assert "req.album_id" in body
    # The target album must be ownership-validated (via the same helper the
    # sibling mutators use) before the image is reassigned to it.
    assert "_get_or_404_album(db, req.album_id, user)" in body


def test_upload_validates_target_album_ownership():
    fns = _function_sources()
    body = fns["gallery_upload"]
    assert "album_id" in body
    assert "_get_or_404_album(db, album_id, user)" in body


def test_list_albums_count_and_cover_are_owner_scoped():
    fns = _function_sources()
    body = fns["list_albums"]
    # The album list, per-album image count, explicit cover, and cover-fallback
    # queries should all share the same gallery owner policy.
    assert "q = _owner_filter(q, user, GalleryAlbum)" in body
    assert "_count_q = _owner_filter(_count_q, user)" in body
    assert "cover = _owner_filter(cover_q, user).first()" in body
    assert "_cover_q = _owner_filter(_cover_q, user)" in body


def test_delete_album_cleanup_is_owner_scoped():
    fns = _function_sources()
    body = fns["delete_album"]
    assert "GalleryImage.album_id == album_id" in body
    assert "GalleryImage.owner == user" in body
    assert 'q.update({"album_id": None}' in body


def test_get_or_404_album_enforces_owner():
    # Guard the precedent we rely on: the helper rejects another user's album.
    fns = _function_sources()
    helper = fns["_get_or_404_album"]
    assert "album.owner != user" in helper
