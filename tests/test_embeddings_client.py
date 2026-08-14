import httpx
import pytest

from src.embeddings import EmbeddingClient


class _FakeEmbeddingHttpClient:
    def __init__(self, handler):
        self.handler = handler
        self.headers = []

    def post(self, url, headers=None, json=None):
        self.headers.append(headers or {})
        request = httpx.Request("POST", url)
        status, body = self.handler(json)
        return httpx.Response(status, request=request, json=body)


def test_embedding_400_batch_retry_falls_back_to_single_inputs(monkeypatch):
    monkeypatch.setenv("EMBEDDING_BATCH_SIZE", "8")
    calls = []

    def handler(payload):
        texts = payload["input"]
        calls.append(list(texts))
        if len(texts) > 1:
            return 400, {"error": "batch too large"}
        text = texts[0]
        return 200, {"data": [{"index": 0, "embedding": [float(len(text)), 1.0]}]}

    client = EmbeddingClient(url="http://embeddings.test/v1/embeddings", model="embed-test")
    client._client = _FakeEmbeddingHttpClient(handler)

    vecs = client.encode(["a", "bbbb"], normalize_embeddings=False)

    assert calls == [["a", "bbbb"], ["a"], ["bbbb"]]
    assert vecs.tolist() == [[1.0, 1.0], [4.0, 1.0]]


def test_embedding_400_single_input_retries_with_truncated_text(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MAX_CHARS", "200")
    lengths = []

    def handler(payload):
        text = payload["input"][0]
        lengths.append(len(text))
        if len(text) > 200:
            return 400, {"error": "context length exceeded"}
        return 200, {"data": [{"index": 0, "embedding": [2.0, 0.0]}]}

    client = EmbeddingClient(url="http://embeddings.test/v1/embeddings", model="embed-test")
    client._client = _FakeEmbeddingHttpClient(handler)

    vecs = client.encode(["x" * 250], normalize_embeddings=False)

    assert lengths == [250, 200]
    assert vecs.tolist() == [[2.0, 0.0]]


def test_embedding_non_400_errors_are_not_retried_or_swallowed():
    calls = 0

    def handler(payload):
        nonlocal calls
        calls += 1
        return 500, {"error": "server error"}

    client = EmbeddingClient(url="http://embeddings.test/v1/embeddings", model="embed-test")
    client._client = _FakeEmbeddingHttpClient(handler)

    with pytest.raises(httpx.HTTPStatusError):
        client.encode(["a"], normalize_embeddings=False)

    assert calls == 1


def test_embedding_retry_path_preserves_api_key_header():
    seen_headers = []

    def handler(payload):
        return 200, {"data": [{"index": 0, "embedding": [1.0, 0.0]}]}

    client = EmbeddingClient(
        url="http://embeddings.test/v1/embeddings",
        model="embed-test",
        api_key="secret-key",
    )
    fake = _FakeEmbeddingHttpClient(handler)
    client._client = fake

    vecs = client.encode(["a"], normalize_embeddings=False)
    seen_headers.extend(fake.headers)

    assert vecs.tolist() == [[1.0, 0.0]]
    assert seen_headers == [{"Authorization": "Bearer secret-key"}]


# ---------------------------------------------------------------- GPU providers

from src.embeddings import select_gpu_providers


def test_gpu_provider_selection_nvidia_tensorrt_first():
    """NVIDIA hosts with TensorRT get the optimized path, CUDA fallback, CPU last."""
    provs = ["CPUExecutionProvider", "CUDAExecutionProvider",
             "TensorrtExecutionProvider"]
    assert select_gpu_providers(provs) == [
        "TensorrtExecutionProvider", "CUDAExecutionProvider",
        "CPUExecutionProvider"]


def test_gpu_provider_selection_nvidia_cuda_only():
    """NVIDIA hosts without TensorRT fall back to CUDA, then CPU."""
    provs = ["CPUExecutionProvider", "CUDAExecutionProvider"]
    assert select_gpu_providers(provs) == [
        "CUDAExecutionProvider", "CPUExecutionProvider"]


def test_gpu_provider_selection_amd_migraphx():
    """AMD hosts use MIGraphX (the current provider, ORT >= 1.23)."""
    provs = ["CPUExecutionProvider", "MIGraphXExecutionProvider"]
    assert select_gpu_providers(provs) == [
        "MIGraphXExecutionProvider", "CPUExecutionProvider"]


def test_gpu_provider_selection_amd_prefers_migraphx_over_legacy_rocm():
    """When both AMD providers are present, MIGraphX wins over legacy ROCm."""
    provs = ["CPUExecutionProvider", "ROCmExecutionProvider",
             "MIGraphXExecutionProvider"]
    assert select_gpu_providers(provs) == [
        "MIGraphXExecutionProvider", "ROCmExecutionProvider",
        "CPUExecutionProvider"]


def test_gpu_provider_selection_amd_legacy_rocm():
    """Older AMD installs (ORT < 1.23) still use ROCmExecutionProvider."""
    provs = ["CPUExecutionProvider", "ROCmExecutionProvider"]
    assert select_gpu_providers(provs) == [
        "ROCmExecutionProvider", "CPUExecutionProvider"]


def test_gpu_provider_selection_apple_coreml():
    """Apple Silicon uses CoreML."""
    provs = ["CPUExecutionProvider", "CoreMLExecutionProvider"]
    assert select_gpu_providers(provs) == [
        "CoreMLExecutionProvider", "CPUExecutionProvider"]


def test_gpu_provider_selection_windows_directml():
    """Windows with any GPU uses DirectML."""
    provs = ["CPUExecutionProvider", "DirectMLExecutionProvider"]
    assert select_gpu_providers(provs) == [
        "DirectMLExecutionProvider", "CPUExecutionProvider"]


def test_gpu_provider_selection_no_gpu_cpu_only():
    """GPU-less hosts degrade to CPU only — never crash."""
    assert select_gpu_providers(["CPUExecutionProvider"]) == [
        "CPUExecutionProvider"]
