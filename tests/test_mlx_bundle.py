"""MLX folder-bundle handling: serve-path normalization + post-download check.

An MLX model is a directory whose manifest is config.json, not a single file.
Both halves here exist so that mistake is caught with a clear message instead of
surfacing as an opaque mlx-lm load failure minutes later.

The post-download check is deliberately narrow: mlx-community hosts far more
than transformers text bundles (whisper, diffusers-style image pipelines), and
those are complete and loadable in shapes that have no tokenizer.json and no
safetensors at top level. Flipping a finished download to "error" is only ever
allowed for a listing positively identified as an MLX LLM text bundle.
"""

import types

import pytest

from routes.cookbook_helpers import _normalize_mlx_model_path
from routes import cookbook_output
from routes.cookbook_output import (
    looks_like_mlx_llm_bundle,
    looks_like_mlx_repo,
    missing_mlx_bundle_files,
    mlx_bundle_diagnosis,
    mlx_bundle_gap,
)

# A real mlx-community text-model listing (the shape mlx-lm loads).
_FULL_BUNDLE = [
    "config.json",
    "generation_config.json",
    "model.safetensors",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
]
_REPO = "mlx-community/Qwen3-4B-4bit"


# ── serve-command normalization ──

@pytest.mark.parametrize("given, expected", [
    (
        "python3 -m mlx_lm.server --model /m/Qwen3-4bit/config.json --port 8080",
        "python3 -m mlx_lm.server --model /m/Qwen3-4bit --port 8080",
    ),
    (
        "python3 -m mlx_lm.server --model /m/Qwen3-4bit/model.safetensors --port 8080",
        "python3 -m mlx_lm.server --model /m/Qwen3-4bit --port 8080",
    ),
    (
        "python3 -m mlx_lm.server --model /m/Q/model-00001-of-00002.safetensors --port 8080",
        "python3 -m mlx_lm.server --model /m/Q --port 8080",
    ),
    (
        "omlx serve --model-dir /m/Qwen3-4bit/config.json --port 8000",
        "omlx serve --model-dir /m/Qwen3-4bit --port 8000",
    ),
])
def test_bundle_file_normalizes_to_its_directory(given, expected):
    assert _normalize_mlx_model_path(given) == expected


def test_quoted_bundle_path_keeps_its_quotes():
    out = _normalize_mlx_model_path("mlx_lm.server --model '/m/My Model/config.json' --port 8080")
    assert out == "mlx_lm.server --model '/m/My Model' --port 8080"


def test_directory_model_path_is_left_alone():
    cmd = "python3 -m mlx_lm.server --model /m/Qwen3-4bit --port 8080"
    assert _normalize_mlx_model_path(cmd) == cmd


def test_non_mlx_commands_are_never_rewritten():
    # vLLM takes a repo/dir too, but this rewrite is MLX's contract, not its own.
    cmd = "vllm serve /m/model/config.json --port 8000"
    assert _normalize_mlx_model_path(cmd) == cmd


def test_empty_cmd_passes_through():
    assert _normalize_mlx_model_path("") == ""
    assert _normalize_mlx_model_path(None) is None


# ── post-download bundle verification ──

def test_complete_bundle_has_no_gaps():
    assert missing_mlx_bundle_files(_FULL_BUNDLE) == []
    assert mlx_bundle_diagnosis(_REPO, _FULL_BUNDLE) is None


def test_tokenizer_config_alone_satisfies_the_tokenizer_requirement():
    assert missing_mlx_bundle_files(
        ["config.json", "model.safetensors", "tokenizer_config.json"]
    ) == []


@pytest.mark.parametrize("dropped, needle", [
    ("config.json", "config.json"),
    ("model.safetensors", "safetensors"),
    ("tokenizer.json", "tokenizer"),
])
def test_missing_member_is_named_in_the_error(dropped, needle):
    names = [n for n in _FULL_BUNDLE if n not in (dropped, "tokenizer_config.json")]
    diag = mlx_bundle_diagnosis(_REPO, names)
    assert diag is not None
    assert needle in diag["message"]
    assert _REPO in diag["message"]


def test_empty_listing_is_not_reported_as_a_bundle_gap():
    # "nothing downloaded" is already covered by the download markers; claiming
    # a broken MLX bundle there would mislabel an unrelated failure.
    assert missing_mlx_bundle_files([]) == []
    assert mlx_bundle_diagnosis(_REPO, None) is None


# ── the check only ever fires on MLX *LLM text* bundles ──

def test_npz_is_a_valid_weight_container():
    # mlx-examples-era bundles ship weights.npz instead of safetensors shards.
    names = ["config.json", "weights.npz", "tokenizer.json", "tokenizer_config.json"]
    assert missing_mlx_bundle_files(names) == []


def test_whisper_style_bundle_is_left_alone():
    """mlx-community/whisper-large-v3-mlx is config.json + weights.npz and no
    tokenizer at all — complete and loadable. It must not be called broken."""
    names = ["config.json", "weights.npz"]
    assert looks_like_mlx_llm_bundle(names) is False
    assert missing_mlx_bundle_files(names) == []
    assert mlx_bundle_diagnosis("mlx-community/whisper-large-v3-mlx", names) is None


@pytest.mark.parametrize("names", [
    # diffusers pipeline: manifest at top level, everything else in subdirs.
    ["model_index.json", "scheduler", "text_encoder", "tokenizer", "transformer", "vae"],
    # same layout without the manifest — the component dirs still give it away.
    ["text_encoder", "tokenizer", "transformer", "vae"],
])
def test_diffusers_style_image_bundle_is_left_alone(names):
    assert looks_like_mlx_llm_bundle(names) is False
    assert missing_mlx_bundle_files(names) == []
    assert mlx_bundle_diagnosis("mlx-community/FLUX.1-schnell-4bit", names) is None


def test_unfamiliar_layout_is_left_alone():
    # No text-model marker anywhere → not something this check understands.
    names = ["config.json", "pytorch_model.bin", "README.md"]
    assert looks_like_mlx_llm_bundle(names) is False
    assert missing_mlx_bundle_files(names) == []


@pytest.mark.parametrize("names", [
    ["config.json", "model.safetensors", "tokenizer_config.json"],
    ["config.json", "model.safetensors", "generation_config.json"],
    ["config.json", "model.safetensors.index.json", "model-00001-of-00002.safetensors"],
])
def test_text_model_markers_put_a_listing_in_scope(names):
    assert looks_like_mlx_llm_bundle(names) is True


def test_llm_bundle_with_no_weights_at_all_is_flagged():
    names = ["config.json", "generation_config.json", "tokenizer.json", "tokenizer_config.json"]
    diag = mlx_bundle_diagnosis(_REPO, names)
    assert diag is not None
    assert "weights" in diag["message"]


@pytest.mark.parametrize("repo", [
    "mlx-community/Qwen3-4B-4bit",
    "someone/Qwen3-4B-mlx",
    "someone/qwen3_mlx_4bit",
])
def test_mlx_repos_are_in_scope(repo):
    assert looks_like_mlx_repo(repo)


@pytest.mark.parametrize("repo", [
    "unsloth/Qwen3-8B-GGUF",
    "Qwen/Qwen3-8B",
    "",
])
def test_non_mlx_repos_are_out_of_scope(repo):
    assert not looks_like_mlx_repo(repo)


# ── probe memoization (per download session, bounded, failures re-tried) ──

@pytest.fixture(autouse=True)
def _clear_bundle_memo():
    cookbook_output._mlx_bundle_checks.clear()
    yield
    cookbook_output._mlx_bundle_checks.clear()


def _fake_probe(monkeypatch, listings, returncode=0):
    """Patch the snapshot probe to return successive listings; count the calls."""
    calls = []

    def _run(cmd, **kwargs):
        calls.append(cmd)
        names = listings[min(len(calls) - 1, len(listings) - 1)]
        return types.SimpleNamespace(
            returncode=returncode, stdout="\n".join(names), stderr="",
        )

    monkeypatch.setattr(cookbook_output.subprocess, "run", _run)
    return calls


def test_a_finished_session_is_probed_once(monkeypatch):
    broken = [n for n in _FULL_BUNDLE if n != "model.safetensors"]
    calls = _fake_probe(monkeypatch, [broken])
    first = mlx_bundle_gap("cookbook-aaaa1111", _REPO)
    second = mlx_bundle_gap("cookbook-aaaa1111", _REPO)
    assert first is not None and second is first
    assert len(calls) == 1


def test_a_re_download_re_probes_instead_of_replaying_the_old_gap(monkeypatch):
    """The user fixes the repo and downloads again. The new download gets a new
    session id, so the stale 'broken bundle' verdict must not follow it."""
    broken = [n for n in _FULL_BUNDLE if n != "model.safetensors"]
    calls = _fake_probe(monkeypatch, [broken, _FULL_BUNDLE])
    assert mlx_bundle_gap("cookbook-aaaa1111", _REPO) is not None
    assert mlx_bundle_gap("cookbook-bbbb2222", _REPO) is None
    assert len(calls) == 2


def test_probe_failure_is_memoized_then_retried(monkeypatch):
    """An unreachable remote host must not cost a 12s SSH on every status poll,
    but the memo has to expire so a host that comes back is picked up."""
    calls = _fake_probe(monkeypatch, [[]], returncode=255)
    assert mlx_bundle_gap("cookbook-cccc3333", _REPO, remote_host="mac.local") is None
    assert mlx_bundle_gap("cookbook-cccc3333", _REPO, remote_host="mac.local") is None
    assert len(calls) == 1

    (key, (expires_at, gap)), = cookbook_output._mlx_bundle_checks.items()
    assert gap is None
    # Failures expire; successes never do.
    assert expires_at != float("inf")
    cookbook_output._mlx_bundle_checks[key] = (0.0, None)
    mlx_bundle_gap("cookbook-cccc3333", _REPO, remote_host="mac.local")
    assert len(calls) == 2


def test_memo_stays_bounded(monkeypatch):
    _fake_probe(monkeypatch, [_FULL_BUNDLE])
    for i in range(cookbook_output._MLX_BUNDLE_CHECK_MAX * 2):
        mlx_bundle_gap(f"cookbook-{i:08x}", _REPO)
    assert len(cookbook_output._mlx_bundle_checks) <= cookbook_output._MLX_BUNDLE_CHECK_MAX


def test_non_mlx_repo_is_never_probed(monkeypatch):
    calls = _fake_probe(monkeypatch, [_FULL_BUNDLE])
    assert mlx_bundle_gap("cookbook-dddd4444", "unsloth/Qwen3-8B-GGUF") is None
    assert calls == []
