from pathlib import Path

import pytest
from packaging.requirements import Requirement


ROOT = Path(__file__).resolve().parents[1]


def _optional_requirement(name):
    lines = (ROOT / "requirements-optional.txt").read_text(encoding="utf-8").splitlines()
    return Requirement(next(line for line in lines if line.startswith(name)))


def test_kokoro_optional_dependency_pins_the_verified_release():
    kokoro = _optional_requirement("kokoro")

    assert str(kokoro.specifier) == "==0.9.4"


@pytest.mark.parametrize(
    ("python_version", "selected"),
    (("3.11", True), ("3.12", True), ("3.13", False), ("3.14", False)),
)
def test_kokoro_feature_markers_match_supported_python_range(python_version, selected):
    for name in ("kokoro", "soundfile"):
        requirement = _optional_requirement(name)
        assert requirement.marker is not None
        assert requirement.marker.evaluate({"python_version": python_version}) is selected


def test_setup_documents_container_constraint_and_install_command():
    setup = (ROOT / "website" / "setup.md").read_text(encoding="utf-8")

    assert "pip install -r requirements-optional.txt" in setup
    assert "default Docker image currently uses Python 3.14" in setup
    assert "Python `>=3.10,<3.13`" in setup
    assert "native Python 3.11 or 3.12" in setup
    assert "skipped on Python 3.13+" in setup
    assert "torch build has CUDA" in setup
