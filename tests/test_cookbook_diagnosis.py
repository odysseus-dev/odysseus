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
