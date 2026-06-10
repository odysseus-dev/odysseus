import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_module():
    path = ROOT / "scripts" / "hf_download.py"
    spec = importlib.util.spec_from_file_location("hf_download", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_download_max_workers_falls_back_from_invalid_values():
    mod = _load_module()

    assert mod._download_max_workers("4") == 4
    assert mod._download_max_workers("bad") == 8
    assert mod._download_max_workers(None) == 8
    assert mod._download_max_workers("0") == 8
    assert mod._download_max_workers("-2") == 8
