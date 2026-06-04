import subprocess

from routes.cookbook_routes import _hf_download_verify_bash


def test_hf_download_verifier_rejects_empty_snapshot(tmp_path):
    script = _hf_download_verify_bash("org/model", None, str(tmp_path))

    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)

    assert result.returncode == 42
    assert "no non-empty model payload found" in result.stdout


def test_hf_download_verifier_accepts_matching_payload(tmp_path):
    model_file = tmp_path / "model.Q4_K_M.gguf"
    model_file.write_bytes(b"payload")
    script = _hf_download_verify_bash("org/model", "*Q4_K_M*.gguf", str(tmp_path))

    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)

    assert result.returncode == 0
    assert "verified 1 model payload file" in result.stdout
