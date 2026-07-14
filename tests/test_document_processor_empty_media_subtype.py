"""Regression: extensionless image/audio uploads must get a valid MIME subtype.

The data-URL subtype was derived only from the stored file's extension
(`image_format = ext[1:]`). A pasted screenshot or any file whose stored id
carries no extension yields `ext == ""`, so the emitted URL was
`data:image/;base64,...` — an empty MIME subtype (invalid per RFC 2046) that
vision/audio endpoints reject, silently dropping the attachment. When the
extension is missing, fall back to the resolved MIME subtype. Extensions that
are present are unchanged.
"""


class _Handler:
    def __init__(self, uploads, image=False, audio=False):
        self.uploads = uploads
        self._image = image
        self._audio = audio

    def resolve_upload(self, fid, owner=None):
        return self.uploads.get(fid)

    def _inside_upload_dir(self, path):
        return True

    def is_image_file(self, name, mime):
        return self._image and (mime or "").startswith("image/")

    def is_audio_file(self, name, mime):
        return self._audio and (mime or "").startswith("audio/")

    def is_document_file(self, name, mime):
        return False


def _blocks(content, block_type):
    return [b for b in content if isinstance(b, dict) and b.get("type") == block_type]


def test_extensionless_image_uses_mime_subtype(tmp_path):
    import src.document_processor as dp

    p = tmp_path / ("a" * 32)          # bare id, no extension
    p.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    uploads = {"img": {"path": str(p), "name": "screenshot", "mime": "image/png"}}

    content = dp.build_user_content("look", ["img"], str(tmp_path), _Handler(uploads, image=True), owner="t")
    imgs = _blocks(content, "image_url")
    assert imgs, content
    assert imgs[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_extensionless_audio_uses_mime_subtype(tmp_path):
    import src.document_processor as dp

    p = tmp_path / ("b" * 32)
    p.write_bytes(b"fakeaudio")
    uploads = {"aud": {"path": str(p), "name": "recording", "mime": "audio/mpeg"}}

    content = dp.build_user_content("listen", ["aud"], str(tmp_path), _Handler(uploads, audio=True), owner="t")
    auds = _blocks(content, "audio")
    assert auds, content
    assert auds[0]["audio"]["url"].startswith("data:audio/mpeg;base64,")


def test_extension_present_is_unchanged(tmp_path):
    import src.document_processor as dp

    p = tmp_path / "pic.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n")
    uploads = {"img": {"path": str(p), "name": "pic.png", "mime": "image/png"}}

    content = dp.build_user_content("look", ["img"], str(tmp_path), _Handler(uploads, image=True), owner="t")
    imgs = _blocks(content, "image_url")
    assert imgs[0]["image_url"]["url"].startswith("data:image/png;base64,")
