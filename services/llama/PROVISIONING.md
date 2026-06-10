# llama.cpp provisioning — source, version, license

Nothing in this directory ships a prebuilt binary or a model. The llama-server
binary and all GGUF models are provisioned at **runtime** into `data/llama/`
(gitignored), so **no third-party binary/static asset is vendored into the
repo or the diff**.

## llama-server binary
- **Source:** https://github.com/ggml-org/llama.cpp (official GitHub releases)
- **Version:** pinned — `LLAMA_RELEASE_TAG = "b9444"` in `manager.py`
- **Artifacts:** `llama-b9444-bin-win-cuda-13.3-x64.zip` (NVIDIA) /
  `llama-b9444-bin-win-cpu-x64.zip` (CPU fallback) / `cudart-llama-*.zip`
- **License:** MIT (llama.cpp)
- **Stored at:** `data/llama/bin/` (gitignored) — or the user's own
  `llama-server` already on PATH

## GGUF models
- **Source:** HuggingFace, via `huggingface_hub.hf_hub_download`
- **License:** each model carries its own per-repo license; none is
  redistributed by this project
- **Stored at:** `data/llama/models/` (gitignored)

The staged-review concern — *"no bundled static third-party assets unless
licensing/versioning is documented"* — is satisfied here by (a) provisioning
at runtime, (b) gitignoring the artifacts, and (c) this file documenting
source + version + license for the record.
