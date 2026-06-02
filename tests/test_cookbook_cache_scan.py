"""Cached-model scanner: Ollama parsing.

The /api/model/cached scanner shells out to `ollama list`. These lock in that
its table output parses into the same {repo_id, size_bytes, backend} shape the
Serve picker consumes. Each test runs the generated standalone scan script
against a fake `ollama` binary on PATH, with HOME pointed at a tmp dir so the
real HF cache and ollama install can't leak in.
"""

import json
import os
import socket
import subprocess
import sys

import pytest

from routes.cookbook_helpers import _cached_model_scan_script


def _run_cached_scan(tmp_path, fake_ollama_body):
    """Run _cached_model_scan_script() with a fake `ollama` binary on PATH.

    fake_ollama_body is the stdout the fake prints (pass None to simulate a
    missing/failing CLI via a non-zero exit). Returns the parsed model list.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "ollama"
    if fake_ollama_body is None:
        fake.write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(1)\n", encoding="utf-8")
    else:
        # repr() keeps the multi-line table intact inside the generated source.
        fake.write_text(
            "#!/usr/bin/env python3\nimport sys\nsys.stdout.write(%r)\n" % fake_ollama_body,
            encoding="utf-8",
        )
    fake.chmod(0o755)

    scan_py = tmp_path / "scan_cache.py"
    scan_py.write_text(_cached_model_scan_script(), encoding="utf-8")

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["HOME"] = str(tmp_path)          # no real HF hub cache under here
    env.pop("HF_HOME", None)
    proc = subprocess.run(
        [sys.executable, str(scan_py)],
        check=True, capture_output=True, text=True, env=env,
    )
    return json.loads(proc.stdout)


def _ollama_api_reachable():
    """True if something answers on the Ollama default port — the scanner also
    probes the HTTP API, so a live server would add models even when the CLI
    fails. Used to keep the failing-CLI test flake-proof on dev boxes."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", 11434)) == 0


@pytest.mark.skipif(os.name == "nt", reason="fake-binary-on-PATH test is POSIX-only")
def test_cached_scan_parses_ollama_list(tmp_path):
    table = (
        "NAME                 ID              SIZE      MODIFIED\n"
        "qwen2.5:0.5b         a8b0c5157701    397 MB    2 days ago\n"
        "llama3.2:latest      baf6a787fdff    2.0 GB    3 days ago\n"
    )
    models = _run_cached_scan(tmp_path, table)
    by_id = {m["repo_id"]: m for m in models}

    assert "qwen2.5:0.5b" in by_id
    assert "llama3.2:latest" in by_id

    qwen = by_id["qwen2.5:0.5b"]
    assert qwen["backend"] == "ollama"
    assert qwen["is_ollama"] is True
    assert qwen["path"] == "ollama"
    assert qwen["nb_files"] == 1
    assert qwen["has_incomplete"] is False
    assert qwen["size_bytes"] == int(397 * 1024 ** 2)   # "397 MB" -> bytes
    assert by_id["llama3.2:latest"]["size_bytes"] == int(2.0 * 1024 ** 3)  # "2.0 GB"


@pytest.mark.skipif(os.name == "nt", reason="fake-binary-on-PATH test is POSIX-only")
def test_cached_scan_skips_ollama_header_and_short_rows(tmp_path):
    """The first line (column header) and any malformed/short row must be
    skipped — only well-formed `name id size unit` rows become models."""
    table = (
        "NAME                 ID              SIZE      MODIFIED\n"
        "gemma3:1b            c0ffee00         815 MB    1 hour ago\n"
        "garbage-row\n"                       # too few columns -> skipped
        "\n"                                  # blank -> skipped
    )
    models = _run_cached_scan(tmp_path, table)
    ids = {m["repo_id"] for m in models}
    assert "NAME" not in ids                  # header never becomes a model
    assert "garbage-row" not in ids
    assert "gemma3:1b" in ids


@pytest.mark.skipif(os.name == "nt", reason="fake-binary-on-PATH test is POSIX-only")
def test_cached_scan_tolerates_failing_ollama_cli(tmp_path):
    """A non-zero `ollama list` (daemon down, etc.) must not crash the scan —
    it simply contributes no Ollama models from the CLI path."""
    models = _run_cached_scan(tmp_path, None)   # check=True: a crash would raise
    assert isinstance(models, list)
    if not _ollama_api_reachable():
        # No live API either, so there's no other Ollama source — the failing
        # CLI must have produced exactly zero Ollama models.
        assert [m for m in models if m.get("is_ollama")] == []
