import hashlib
import importlib.util
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[2] / "scripts" / "pdv_build_source_archive.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("pdv_build_source_archive_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_corresponding_source_archive_is_deterministic_complete_and_secret_safe(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "dev"], cwd=repo, check=True, capture_output=True)
    (repo / "app.py").write_text("print('source')\n", encoding="utf-8")
    (repo / "LICENSE").write_text("GNU AFFERO GENERAL PUBLIC LICENSE\n", encoding="utf-8")
    (repo / "service" / "data").mkdir(parents=True)
    (repo / "service" / "data" / "required.json").write_text("{}\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture"], cwd=repo, check=True, capture_output=True)
    (repo / "PDV_INTEGRATION_BOUNDARY.md").write_text("integration boundary\n", encoding="utf-8")
    (repo / "new_integration_module.py").write_text("print('untracked integration source')\n", encoding="utf-8")
    (repo / "confidential-notes.md").write_text("private but not secret-pattern-matching\n", encoding="utf-8")
    (repo / "local.env").write_text("TOKEN=must-not-ship\n", encoding="utf-8")
    (repo / "private.pem").write_text("must-not-ship\n", encoding="utf-8")
    (repo / "unrelated.bin").write_bytes(b"unrelated local material")
    (repo / "data" / "pdv-integration-v1").mkdir(parents=True)
    (repo / "data" / "pdv-integration-v1" / "adapter.key").write_text("must-not-ship", encoding="utf-8")
    (repo / ".venv").mkdir()
    (repo / ".venv" / "secret.env").write_text("must-not-ship", encoding="utf-8")
    output = repo / "data" / "pdv-integration-v1" / "source" / "source.zip"

    command = [shutil.which("python") or "python", str(SCRIPT), "--repository-root", str(repo), "--output", str(output), "--include", "PDV_INTEGRATION_BOUNDARY.md", "--json"]
    first = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    assert first.returncode == 0, first.stderr or first.stdout
    first_receipt = json.loads(first.stdout)
    assert first_receipt["schemaVersion"] == 2
    assert first_receipt["sourceInventoryMode"] == "tracked-plus-explicit-integration-files"
    first_bytes = output.read_bytes()
    second = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    assert second.returncode == 0, second.stderr or second.stdout
    assert output.read_bytes() == first_bytes
    assert json.loads(second.stdout)["archiveSha256"] == hashlib.sha256(first_bytes).hexdigest() == first_receipt["archiveSha256"]

    with zipfile.ZipFile(output) as archive:
      names = archive.namelist()
      assert names == sorted(names)
      assert "app.py" in names and "LICENSE" in names and "PDV_INTEGRATION_BOUNDARY.md" in names
      assert "new_integration_module.py" not in names
      assert "confidential-notes.md" not in names
      assert "service/data/required.json" in names
      assert "CORRESPONDING_SOURCE_MANIFEST.json" in names
      manifest = json.loads(archive.read("CORRESPONDING_SOURCE_MANIFEST.json"))
      assert manifest["schemaVersion"] == 2
      assert manifest["sourceInventoryMode"] == "tracked-plus-explicit-integration-files"
      assert manifest["excludedUntrackedCount"] == 6
      assert len(manifest["excludedUntrackedPathsSha256"]) == 64
      assert manifest["integrationBranch"] == "dev"
      assert manifest["licenseSha256"] == hashlib.sha256((repo / "LICENSE").read_bytes()).hexdigest()
      assert "new_integration_module.py" not in manifest["files"]
      assert all(".venv" not in name and not name.startswith("data/") and "adapter.key" not in name and not name.endswith((".env", ".pem")) for name in names)
      assert b"must-not-ship" not in first_bytes
      assert b"unrelated local material" not in first_bytes


def test_corresponding_source_builder_rejects_include_outside_repository(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir(); subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    outside = tmp_path / "outside.py"; outside.write_text("secret", encoding="utf-8")
    result = subprocess.run([shutil.which("python") or "python", str(SCRIPT), "--repository-root", str(repo), "--output", str(repo / "data" / "source.zip"), "--include", "../outside.py", "--json"], capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode != 0
    assert not (repo / "data" / "source.zip").exists()


def test_corresponding_source_builder_fails_closed_on_secret_content_in_innocent_path(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir(); subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    (repo / "LICENSE").write_text("GNU AFFERO GENERAL PUBLIC LICENSE\n", encoding="utf-8")
    synthetic_secret = "sk-proj-" + "abcdefghijklmnopqrstuvwxyz0123456789"
    (repo / "settings.json").write_text(json.dumps({"endpointCredential": synthetic_secret}) + "\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    output = repo / "data" / "pdv-integration-v1" / "source" / "source.zip"
    result = subprocess.run([shutil.which("python") or "python", str(SCRIPT), "--repository-root", str(repo), "--output", str(output), "--json"], capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode != 0
    assert json.loads(result.stdout) == {"ok": False, "error": "ValueError"}
    assert not output.exists()


def test_failed_rebuild_invalidates_old_archive_and_detects_integration_secrets(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir(); subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    (repo / "LICENSE").write_text("GNU AFFERO GENERAL PUBLIC LICENSE\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture"], cwd=repo, check=True, capture_output=True)
    key_dir = repo / "data" / "pdv-integration-v1"; key_dir.mkdir(parents=True)
    adapter_key = ("a1" * 32).encode()
    (key_dir / "adapter.key").write_bytes(adapter_key)
    output = key_dir / "source" / "source.zip"
    command = [shutil.which("python") or "python", str(SCRIPT), "--repository-root", str(repo), "--output", str(output), "--json"]
    assert subprocess.run(command, capture_output=True, text=True, timeout=30, check=False).returncode == 0
    assert output.exists() and output.with_suffix(".zip.json").exists()

    innocent = repo / "innocent.txt"
    innocent.write_text(adapter_key.decode() + "\n", encoding="utf-8")
    failed = subprocess.run([*command[:-1], "--include", "innocent.txt", "--json"], capture_output=True, text=True, timeout=30, check=False)
    assert failed.returncode != 0
    assert not output.exists()
    assert not output.with_suffix(".zip.json").exists()

    innocent.unlink()
    assert subprocess.run(command, capture_output=True, text=True, timeout=30, check=False).returncode == 0
    owner_token = "ody_" + "Ab9x" * 8
    innocent.write_text(owner_token + "\n", encoding="utf-8")
    assert subprocess.run([*command[:-1], "--include", "innocent.txt", "--json"], capture_output=True, text=True, timeout=30, check=False).returncode != 0
    assert not output.exists() and not output.with_suffix(".zip.json").exists()


def test_failed_output_invalidation_removes_sidecar_and_temp_even_when_archive_is_locked(tmp_path, monkeypatch):
    module = _load_builder()
    output = tmp_path / "source.zip"
    sidecar = output.with_suffix(".zip.json")
    temporary = output.with_suffix(".zip.tmp")
    for path in (output, sidecar, temporary):
        path.write_bytes(b"stale")
    original_unlink = Path.unlink

    def locked_archive_unlink(path, *args, **kwargs):
        if path == output:
            raise PermissionError("synthetic archive lock")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked_archive_unlink)
    assert module._invalidate_failed_output(tmp_path, output) is False
    assert output.exists()
    assert not sidecar.exists()
    assert not temporary.exists()
