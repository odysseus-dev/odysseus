"""Set-but-empty env overrides must fall back to the DATA_DIR defaults.

docker-compose.yml injects FASTEMBED_CACHE_PATH=${FASTEMBED_CACHE_PATH:-},
which becomes an empty string whenever the var is unset in .env (the
fresh-install default). os.getenv("FASTEMBED_CACHE_PATH", default) returns ""
in that case (set-but-empty wins over the default), FastEmbed init then fails
on os.makedirs(''), and every stock Docker install runs with memory/RAG
silently degraded. Same trap for ODYSSEUS_MAIL_ATTACHMENTS_DIR on the
adjacent line.
"""
import importlib
import os


def _reload_constants():
    import src.constants as constants
    return importlib.reload(constants)


def test_empty_fastembed_cache_path_falls_back(monkeypatch):
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", "")
    try:
        c = _reload_constants()
        assert c.FASTEMBED_CACHE_DIR
        assert os.path.basename(c.FASTEMBED_CACHE_DIR) == "fastembed_cache"
    finally:
        monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
        _reload_constants()


def test_empty_mail_attachments_dir_falls_back(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_MAIL_ATTACHMENTS_DIR", "")
    try:
        c = _reload_constants()
        assert c.MAIL_ATTACHMENTS_DIR
        assert os.path.basename(c.MAIL_ATTACHMENTS_DIR) == "mail-attachments"
    finally:
        monkeypatch.delenv("ODYSSEUS_MAIL_ATTACHMENTS_DIR", raising=False)
        _reload_constants()


def test_explicit_fastembed_cache_path_still_wins(monkeypatch, tmp_path):
    custom = str(tmp_path / "fe-cache")
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", custom)
    try:
        c = _reload_constants()
        assert c.FASTEMBED_CACHE_DIR == custom
    finally:
        monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
        _reload_constants()
