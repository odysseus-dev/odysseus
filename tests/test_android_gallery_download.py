from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GALLERY_JS = (ROOT / "static/js/gallery.js").read_text(encoding="utf-8")
GALLERY_EDITOR_JS = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
MAIN_ACTIVITY = (ROOT / "android/app/src/main/java/com/odysseus/simplesignal/MainActivity.java").read_text(encoding="utf-8")


def test_gallery_downloads_use_android_native_save_bridge():
    assert "async function _downloadBlob(blob, filename)" in GALLERY_JS
    assert "bridge.saveDownload(dataUrl, filename, blob.type || 'application/octet-stream')" in GALLERY_JS
    assert "async function _downloadUrl(url, filename)" in GALLERY_JS
    assert "await _downloadUrl(url, filename)" in GALLERY_JS
    assert "await _downloadUrl(img.url, filename)" in GALLERY_JS
    assert "await _downloadBlob(blob, 'gallery-photos.zip')" in GALLERY_JS


def test_android_bridge_saves_downloads_to_public_downloads_folder():
    assert "public void saveDownload(String dataUrl, String filename, String mimeType)" in MAIN_ACTIVITY
    assert "saveDownloadToDownloads(dataUrl, filename, mimeType)" in MAIN_ACTIVITY
    assert "MediaStore.Downloads.EXTERNAL_CONTENT_URI" in MAIN_ACTIVITY
    assert 'Environment.DIRECTORY_DOWNLOADS + "/Odysseus"' in MAIN_ACTIVITY
    assert "decodeDownloadDataUrl" in MAIN_ACTIVITY
    assert "Base64.decode(payload, Base64.DEFAULT)" in MAIN_ACTIVITY
    assert "safeDownloadFileName" in MAIN_ACTIVITY


def test_gallery_editor_png_download_uses_same_android_bridge():
    assert "bridge.saveDownload(dataUrl, 'edited-image.png', 'image/png')" in GALLERY_EDITOR_JS
