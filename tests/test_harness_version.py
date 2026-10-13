from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def test_harness_version_is_canonical_semver() -> None:
    version = (ROOT / "HARNESS_VERSION").read_text(encoding="utf-8").strip()

    assert re.fullmatch(r"\d+\.\d+\.\d+", version)


def test_runtime_build_version_uses_canonical_harness_version() -> None:
    from core.constants import APP_BUILD_VERSION

    version = (ROOT / "HARNESS_VERSION").read_text(encoding="utf-8").strip()
    assert APP_BUILD_VERSION == version


def test_original_harness_does_not_ship_benchmark_adapter() -> None:
    assert not (ROOT / "odysseus_native_harbor_agent.py").exists()
