from routes.cookbook_helpers import _diagnose_serve_output


def test_diagnose_vllm_modelopt_lm_head_error():
    output = """
    ValueError: There is no module or parameter named 'lm_head.input_scale'
    Engine core initialization failed.
    """

    diagnosis = _diagnose_serve_output(output)

    assert diagnosis is not None
    assert "ModelOpt LM-head" in diagnosis["message"]
    assert diagnosis["suggestions"][0]["op"] == "manual"
    assert "provides this CLI" in diagnosis["suggestions"][0]["label"]


def test_diagnose_sglang_native_dependency_errors():
    output = """
    /tmp/cuda_utils.c:7:10: fatal error: Python.h: No such file or directory
    ImportError:
    [sgl_kernel] CRITICAL: Could not load any common_ops library!
    Please ensure sgl_kernel is properly installed with:
    pip install --upgrade sglang-kernel
    Error details from previous import attempts:
    - ImportError: libnuma.so.1: cannot open shared object file
    """

    diagnosis = _diagnose_serve_output(output)

    assert diagnosis is not None
    assert "SGLang native kernel/runtime" in diagnosis["message"]
    labels = [suggestion["label"] for suggestion in diagnosis["suggestions"]]
    assert any("libnuma-dev" in label for label in labels)
    assert any("python3.12-dev" in label for label in labels)
    assert any("sglang-kernel" in label for label in labels)
