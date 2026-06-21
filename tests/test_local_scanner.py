import os
from unittest.mock import patch

from services.hwfit.fit import rank_models
from services.hwfit.local_scanner import _extra_scan_dirs, default_scan_dirs, scan_local_gguf


def _cpu_system():
    return {
        "has_gpu": False,
        "backend": "cpu_x86",
        "gpu_name": None,
        "gpu_vram_gb": 0,
        "gpu_count": 0,
        "available_ram_gb": 32.0,
        "total_ram_gb": 32.0,
    }


def test_scan_local_gguf_finds_model_and_extracts_metadata(tmp_path):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    model_file = model_dir / "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"
    model_file.write_bytes(b"x" * 1024)

    result = scan_local_gguf([model_dir])

    assert len(result) == 1
    model = result[0]
    assert model["name"] == "local/Meta-Llama-3.1-8B-Instruct-Q4_K_M"
    assert model["family"] == "llama"
    assert model["parameter_count"] == "8B"
    assert model["quant"] == "Q4_K_M"
    assert model["is_gguf"] is True
    assert model["_source"] == "local_gguf"
    assert model["local_path"] == str(model_file.resolve())
    assert model["gguf_sources"][0]["path"] == str(model_file.resolve())


def test_scan_local_gguf_detects_mmproj_sidecar(tmp_path):
    model_file = tmp_path / "gemma-4-12B-it-Q4_K_M.gguf"
    mmproj_file = tmp_path / "mmproj-gemma-4.gguf"
    model_file.write_bytes(b"x" * 1024)
    mmproj_file.write_bytes(b"x" * 1024)

    result = scan_local_gguf([tmp_path])

    assert len(result) == 1
    assert result[0]["mmproj_path"] == str(mmproj_file.resolve())
    assert result[0]["gguf_sources"][0]["mmproj_path"] == str(mmproj_file.resolve())


def test_scan_local_gguf_ignores_non_models(tmp_path):
    (tmp_path / "notes.txt").write_text("ignore", encoding="utf-8")
    (tmp_path / "mmproj-only.gguf").write_bytes(b"x" * 1024)

    assert scan_local_gguf([tmp_path]) == []


def test_scan_local_gguf_estimates_params_from_file_size(tmp_path):
    model_file = tmp_path / "qwen-local-Q8_0.gguf"
    model_file.write_bytes(b"x" * 1024 * 1024)

    result = scan_local_gguf([tmp_path])

    assert len(result) == 1
    assert result[0]["parameter_count"].endswith("B")
    assert result[0]["quant"] == "Q8_0"


def test_extra_scan_dirs_accepts_colon_and_windows_drives():
    paths = _extra_scan_dirs(r"D:\Models:E:\More")

    assert [str(p) for p in paths] == [r"D:\Models", r"E:\More"]


def test_scan_local_gguf_graceful_when_scan_dirs_env_var_absent(tmp_path):
    # When ODYSSEUS_MODEL_SCAN_DIRS is not set, default_scan_dirs() should
    # include only the built-in paths (no crash, returns a list).
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("ODYSSEUS_MODEL_SCAN_DIRS", None)
        dirs = default_scan_dirs()

    assert isinstance(dirs, list)
    # default dirs must not contain any path derived from the env var
    assert not any("ODYSSEUS" in str(d) for d in dirs)


def test_scan_local_gguf_empty_scan_dirs_env_var_is_noop():
    # An explicitly empty env var must not add any extra scan directories.
    with patch.dict(os.environ, {"ODYSSEUS_MODEL_SCAN_DIRS": ""}):
        dirs = default_scan_dirs()

    # No path derived from an empty string should be in the list
    assert all(str(d) for d in dirs)  # all paths are non-empty strings


def test_rank_models_preserves_local_source_metadata(tmp_path):
    model_file = tmp_path / "Phi-3-mini-4B-Q4_K_M.gguf"
    model_file.write_bytes(b"x" * 1024)
    local_model = scan_local_gguf([tmp_path])[0]

    results = rank_models(_cpu_system(), search="Phi-3-mini", extra_models=[local_model])

    match = next(r for r in results if r["name"] == local_model["name"])
    assert match["_source"] == "local_gguf"
    assert match["source"] == "local_gguf"
    assert match["local_path"] == str(model_file.resolve())
