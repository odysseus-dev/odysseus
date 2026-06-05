"""Skill URL importer — GitHub path parsing."""
import pytest

from services.memory.skill_importer import (
    ResolvedSource,
    SkillImportError,
    _assert_github_url,
    _fetch_bytes,
    _list_github_dir,
    parse_skill_source,
)


def test_parse_github_blob_skill_md():
    src = parse_skill_source(
        "https://github.com/anthropics/skills/blob/main/skills/pdf/SKILL.md"
    )
    assert src.owner == "anthropics"
    assert src.repo == "skills"
    assert src.ref == "main"
    assert src.path.endswith("skills/pdf/SKILL.md")


def test_parse_github_tree_directory():
    src = parse_skill_source(
        "https://github.com/example/my-skills/tree/develop/caveman-skill"
    )
    assert src.owner == "example"
    assert src.repo == "my-skills"
    assert src.ref == "develop"
    assert src.path == "caveman-skill"


def test_parse_raw_github():
    src = parse_skill_source(
        "https://raw.githubusercontent.com/o/r/main/path/SKILL.md"
    )
    assert src.owner == "o"
    assert src.repo == "r"
    assert src.ref == "main"
    assert src.path == "path/SKILL.md"


def test_rejects_non_github():
    with pytest.raises(SkillImportError):
        parse_skill_source("https://example.com/skill.md")


def test_fetch_bytes_rejects_cross_host_redirect(monkeypatch):
    class _Resp:
        url = "https://evil.example/secret"
        content = b"x"

        def raise_for_status(self):
            return None

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers=None):
            return _Resp()

    monkeypatch.setattr("services.memory.skill_importer.httpx.Client", _Client)
    monkeypatch.setattr(
        "services.memory.skill_importer.check_outbound_url",
        lambda url: (True, ""),
    )
    with pytest.raises(SkillImportError, match="redirect target"):
        _fetch_bytes("https://raw.githubusercontent.com/o/r/main/SKILL.md")


def test_assert_github_url_allows_api_host():
    _assert_github_url(
        "https://api.github.com/repos/o/r/contents?ref=main",
        context="redirect target",
    )


def test_list_github_dir_accepts_api_github_response(monkeypatch):
    monkeypatch.setattr(
        "services.memory.skill_importer._fetch_text",
        lambda url: "# skill\n",
    )
    monkeypatch.setattr(
        "services.memory.skill_importer.check_outbound_url",
        lambda url: (True, ""),
    )

    class _Resp:
        url = "https://api.github.com/repos/o/r/contents?ref=main"
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return [{
                "name": "SKILL.md",
                "type": "file",
                "download_url": "https://raw.githubusercontent.com/o/r/main/SKILL.md",
            }]

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers=None):
            return _Resp()

    monkeypatch.setattr("services.memory.skill_importer.httpx.Client", _Client)

    out = {}
    src = ResolvedSource(owner="o", repo="r", ref="main", path="")
    _list_github_dir(src, "", out)
    assert "SKILL.md" in out
