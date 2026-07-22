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


def test_diagnose_missing_pip_module():
    output = """
    [odysseus] HF token: NOT SET
    /usr/bin/python3: No module named pip

    === Process exited with code 1 ===
    """

    diagnosis = _diagnose_serve_output(output)

    assert diagnosis is not None
    assert "no pip module" in diagnosis["message"]
    assert "venv" in diagnosis["suggestions"][0]["label"]


def test_diagnose_pep668_externally_managed():
    output = """
    error: externally-managed-environment
    x This environment is externally managed
    hint: See PEP 668 for the detailed specification.
    """

    diagnosis = _diagnose_serve_output(output)

    assert diagnosis is not None
    assert "PEP 668" in diagnosis["message"]
    assert "venv" in diagnosis["suggestions"][0]["label"]


def test_diagnose_nvcc_missing_during_pip_build():
    output = """
      File "/usr/lib/python3.14/subprocess.py", line 1990, in _execute_child
          raise child_exception_type(errno_num, err_msg, err_filename)
      FileNotFoundError: [Errno 2] No such file or directory: 'nvcc'
      [end of output]
    error: metadata-generation-failed
    x Encountered error while generating package metadata.
    |-> flashinfer_python
    """

    diagnosis = _diagnose_serve_output(output)

    assert diagnosis is not None
    assert "nvcc" in diagnosis["message"]
    assert "/opt/cuda" in diagnosis["suggestions"][0]["label"]
