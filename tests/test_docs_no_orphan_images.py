"""Repository asset ownership guards for issues #1335 and #6175.

Public Markdown belongs in docs/, the GitHub Pages bundle belongs in website/,
and shared README/packaging imagery belongs in assets/branding/. Images in the
documentation or branding roots must be referenced by tracked text, and every
tracked website video must be referenced by the site's entry point.
"""
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
VIDEO_EXTS = {".webm", ".mp4", ".mov", ".m4v"}
# Files a referenced image name could legitimately appear in.
TEXT_EXTS = {".md", ".html", ".htm", ".js", ".ts", ".css", ".py", ".sh",
             ".json", ".yml", ".yaml", ".txt"}


def _tracked(*paths_under):
    """Git-tracked files under paths, or None if git isn't available."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "--", *paths_under],
            cwd=REPO, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return [REPO / line for line in out.stdout.splitlines() if line.strip()]


def test_no_orphan_documentation_or_branding_images():
    managed_files = _tracked("docs", "assets/branding")
    if managed_files is None:
        pytest.skip("not a git checkout")
    managed_images = [p for p in managed_files if p.suffix.lower() in IMAGE_EXTS]
    assert any("assets/branding" in p.as_posix() for p in managed_images), (
        "expected assets/branding/ to contain the shared project imagery"
    )

    # All tracked text we might reference an image from.
    all_tracked = _tracked(".") or []
    haystack = []
    for p in all_tracked:
        if p.suffix.lower() not in TEXT_EXTS:
            continue
        try:
            haystack.append(p.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    blob = "\n".join(haystack)

    orphans = [
        str(img.relative_to(REPO))
        for img in managed_images
        if img.name not in blob
    ]
    assert not orphans, (
        "unreferenced image(s) committed under docs/ or assets/branding/ "
        f"(see #1335 and #6175): {orphans}"
    )


def test_pages_site_owns_its_entrypoint_and_media():
    docs_files = _tracked("docs")
    website_files = _tracked("website")
    if docs_files is None or website_files is None:
        pytest.skip("not a git checkout")

    assert REPO / "website/index.html" in website_files
    assert REPO / "docs/index.html" not in docs_files
    assert not [p for p in docs_files if p.suffix.lower() in VIDEO_EXTS]

    website_videos = [p for p in website_files if p.suffix.lower() in VIDEO_EXTS]
    assert website_videos, "expected website/ to contain the landing-page videos"

    entrypoint = (REPO / "website/index.html").read_text(encoding="utf-8")
    unreferenced = [
        str(video.relative_to(REPO))
        for video in website_videos
        if video.name not in entrypoint
    ]
    assert not unreferenced, f"unreferenced website video(s): {unreferenced}"
