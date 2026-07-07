from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DIAGNOSIS_JS = ROOT / "static" / "js" / "cookbook-diagnosis.js"


def test_repair_kernels_pip_spec_is_shell_quoted():
    source = DIAGNOSIS_JS.read_text(encoding="utf-8")

    assert '"kernels<0.15"' in source
    assert " --break-system-packages kernels<0.15" not in source


def test_sglang_native_dependency_diagnosis_is_exposed_to_browser():
    source = DIAGNOSIS_JS.read_text(encoding="utf-8")

    assert r"Python\.h" in source
    assert r"libnuma\.so\.1" in source
    assert "SGLang native kernel/runtime" in source
    assert "libnuma-dev python3.12-dev build-essential" in source
    assert "sglang-kernel" in source
