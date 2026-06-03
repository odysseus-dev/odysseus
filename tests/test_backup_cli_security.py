import importlib.machinery
import importlib.util
import io
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_backup_cli():
    path = Path(__file__).resolve().parent.parent / "scripts" / "odysseus-backup"
    loader = importlib.machinery.SourceFileLoader("odysseus_backup_under_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _patch_repo(module, monkeypatch, root: Path):
    monkeypatch.setattr(module, "_REPO_ROOT", root)
    monkeypatch.setattr(module, "_DATA_DIR", root / "data")


def _restore_args(path: Path):
    return SimpleNamespace(path=str(path), yes=True, pretty=False)


def _verify_args(path: Path):
    return SimpleNamespace(path=str(path), pretty=False)


def test_snapshot_rejects_output_inside_data_dir(tmp_path, monkeypatch):
    backup = _load_backup_cli()
    repo = tmp_path / "repo"
    data = repo / "data"
    data.mkdir(parents=True)
    _patch_repo(backup, monkeypatch, repo)

    with pytest.raises(SystemExit):
        backup._reject_output_inside_data(data / "self.tar.gz")


def test_restore_rejects_symlink_escape(tmp_path, monkeypatch):
    backup = _load_backup_cli()
    repo = tmp_path / "repo"
    data = repo / "data"
    outside = tmp_path / "outside"
    data.mkdir(parents=True)
    outside.mkdir()
    (data / "keep.txt").write_text("still here", encoding="utf-8")
    _patch_repo(backup, monkeypatch, repo)

    tar_path = tmp_path / "malicious.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        data_dir = tarfile.TarInfo("data")
        data_dir.type = tarfile.DIRTYPE
        tar.addfile(data_dir)

        link = tarfile.TarInfo("data/link")
        link.type = tarfile.SYMTYPE
        link.linkname = str(outside)
        tar.addfile(link)

        payload = b"escaped"
        escaped = tarfile.TarInfo("data/link/pwned.txt")
        escaped.size = len(payload)
        tar.addfile(escaped, io.BytesIO(payload))

    with pytest.raises(SystemExit):
        backup.cmd_restore(_restore_args(tar_path))

    assert not (outside / "pwned.txt").exists()
    assert (data / "keep.txt").read_text(encoding="utf-8") == "still here"


def test_verify_rejects_symlink_escape(tmp_path):
    backup = _load_backup_cli()

    tar_path = tmp_path / "malicious.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        link = tarfile.TarInfo("data/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/tmp"
        tar.addfile(link)

    with pytest.raises(SystemExit):
        backup.cmd_verify(_verify_args(tar_path))


def test_restore_rejects_hardlink_entries(tmp_path, monkeypatch):
    backup = _load_backup_cli()
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    _patch_repo(backup, monkeypatch, repo)

    tar_path = tmp_path / "hardlink.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        link = tarfile.TarInfo("data/hardlink")
        link.type = tarfile.LNKTYPE
        link.linkname = "../outside.txt"
        tar.addfile(link)

    with pytest.raises(SystemExit):
        backup.cmd_restore(_restore_args(tar_path))


def test_restore_extracts_regular_files_without_extractall(tmp_path, monkeypatch):
    backup = _load_backup_cli()
    repo = tmp_path / "repo"
    data = repo / "data"
    data.mkdir(parents=True)
    (data / "old.txt").write_text("old", encoding="utf-8")
    _patch_repo(backup, monkeypatch, repo)

    tar_path = tmp_path / "valid.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        folder = tarfile.TarInfo("data/nested")
        folder.type = tarfile.DIRTYPE
        tar.addfile(folder)

        payload = b"new"
        item = tarfile.TarInfo("data/nested/new.txt")
        item.size = len(payload)
        tar.addfile(item, io.BytesIO(payload))

    backup.cmd_restore(_restore_args(tar_path))

    assert (repo / "data" / "nested" / "new.txt").read_text(encoding="utf-8") == "new"
    assert not (repo / "data" / "old.txt").exists()
    assert list(repo.glob("data.before-restore-*"))
