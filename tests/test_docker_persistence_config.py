from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_docker_compose_persists_cookbook_cache_and_user_installs():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "./data/huggingface:/app/.cache/huggingface" in compose
    assert "./data/local:/app/.local" in compose
    assert "HOME=/app" in compose
    assert "HF_HOME=/app/.cache/huggingface" in compose
    assert "HUGGINGFACE_HUB_CACHE=/app/.cache/huggingface/hub" in compose
    assert "PYTHONUSERBASE=/app/.local" in compose


def test_docker_entrypoint_exports_persistent_home_before_setup():
    entrypoint = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")

    home_idx = entrypoint.index('export HOME="${ODYSSEUS_HOME:-/app}"')
    setup_idx = entrypoint.index("python /app/setup.py")
    assert home_idx < setup_idx
    assert 'export HF_HOME="${HF_HOME:-/app/.cache/huggingface}"' in entrypoint
    assert 'export PYTHONUSERBASE="${PYTHONUSERBASE:-/app/.local}"' in entrypoint


def test_cuda_overlay_keeps_persistent_home_env():
    overlay = (ROOT / "docker" / "gpu.nvidia.llamacpp.yml").read_text(encoding="utf-8")

    assert "HOME=/app" in overlay
    assert "HF_HOME=/app/.cache/huggingface" in overlay
    assert "PYTHONUSERBASE=/app/.local" in overlay


def test_cached_model_scanner_uses_explicit_hf_cache_envs():
    source = (ROOT / "routes" / "cookbook_helpers.py").read_text(encoding="utf-8")

    assert "os.environ.get('HUGGINGFACE_HUB_CACHE')" in source
    assert "os.environ.get('HF_HOME')" in source
    assert "for _hf_root in hf_cache_roots(): scan_hf(_hf_root)" in source
