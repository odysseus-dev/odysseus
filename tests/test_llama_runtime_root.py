"""The managed llama.cpp runtime location follows ODYSSEUS_DATA_DIR; unset keeps the default."""
from pathlib import Path

import pytest

from src import llama_runtime as lr
from src.runtime_paths import get_default_data_dir


def test_runtime_root_follows_odysseus_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ODYSSEUS_DATA_DIR", str(tmp_path / "isolated"))
    root = tmp_path / "isolated" / "runtimes" / "llama.cpp"
    assert lr.llama_runtime_root() == root
    assert lr.package_cache_dir().parent == root
    assert lr.llama_install_dir("b1-cuda").parent == root


@pytest.mark.parametrize("value", [None, "", "   "])
def test_runtime_root_keeps_default_when_data_dir_unset(value, monkeypatch):
    if value is None:
        monkeypatch.delenv("ODYSSEUS_DATA_DIR", raising=False)
    else:
        monkeypatch.setenv("ODYSSEUS_DATA_DIR", value)
    assert lr.llama_runtime_root() == Path(get_default_data_dir()) / "runtimes" / "llama.cpp"
