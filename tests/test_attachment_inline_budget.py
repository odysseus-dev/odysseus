"""Regression test for issue #1492 — many file attachments were each inlined
(capped per-file at ~15-30k) with no cap on the TOTAL, so a handful of files
blew the model's context and later ones were silently dropped ("more than ~5
files not recognized").

build_user_content now enforces a total inline budget; beyond it, remaining
attachments are listed with an "ask me to read it" marker instead of inlined.
"""
import types

from src.document_processor import build_user_content, _MAX_TOTAL_INLINE_CHARS


def _fake_handler(resolved):
    return types.SimpleNamespace(
        is_image_file=lambda n, m=None: False,
        is_audio_file=lambda n, m=None: False,
        is_document_file=lambda n, m=None: str(n).endswith(".txt"),
        _inside_upload_dir=lambda p: True,
        resolve_upload=lambda fid, owner=None: resolved.get(fid),
    )


def test_total_attachment_inline_is_bounded(tmp_path):
    resolved = {}
    for i in range(4):  # 4 × 20k = 80k >> 48k budget
        p = tmp_path / f"f{i}.txt"
        p.write_text("X" * 20000 + f"FILE{i}END", encoding="utf-8")
        resolved[f"id{i}"] = {"path": str(p), "name": f"f{i}.txt", "mime": "text/plain"}

    out = build_user_content("hi", list(resolved.keys()), str(tmp_path),
                             _fake_handler(resolved), resolved_uploads=resolved)

    assert isinstance(out, str)
    # The combined inlined text is bounded (not the full 80k).
    assert len(out) <= _MAX_TOTAL_INLINE_CHARS + 2000, len(out)
    # The first file's content is fully present...
    assert "FILE0END" in out
    # ...and the budget marker appears once the cap is hit.
    assert "attachment-context budget" in out


def test_small_attachments_not_truncated(tmp_path):
    # A couple of small files stay well under budget — no marker, full content.
    resolved = {}
    for i in range(2):
        p = tmp_path / f"s{i}.txt"
        p.write_text(f"small file {i} content END{i}", encoding="utf-8")
        resolved[f"id{i}"] = {"path": str(p), "name": f"s{i}.txt", "mime": "text/plain"}

    out = build_user_content("hi", list(resolved.keys()), str(tmp_path),
                             _fake_handler(resolved), resolved_uploads=resolved)
    assert "END0" in out and "END1" in out
    assert "attachment-context budget" not in out
