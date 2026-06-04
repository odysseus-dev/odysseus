# Draft: Issue + PR for Windows Cookbook GGUF-only Filter

## Step 1 — Open the Issue (use the Bug Report form)

Go to: https://github.com/pewdiepie-archdaemon/odysseus/issues/new?template=bug_report.yml

**Title:** `Cookbook recommends unservable AWQ/GPTQ/FP8 models on Windows`

**Fill in the form fields:**

- **Prerequisites:** Check all three boxes
- **Install Method:** Windows native (launch-windows.ps1) OR Manual Python install
- **Operating System:** Windows
- **Steps to Reproduce:**
  ```
  1. Run Odysseus on Windows with an NVIDIA GPU (e.g., RTX 4060/4090)
  2. Open the Cookbook and let it scan hardware
  3. Observe the recommended models list
  4. Note that AWQ/GPTQ/FP8 models (e.g., Qwen/Qwen2.5-3B-Instruct-AWQ) appear in recommendations
  5. Attempt to download and serve one of these AWQ models
  6. The serve fails because vLLM is blocked on Windows and llama.cpp cannot serve AWQ safetensors
  ```
- **Expected Behaviour:**
  ```
  On Windows, the Cookbook should only recommend models that have a servable GGUF source (or are themselves GGUF), exactly as it already does for Apple Silicon and consumer AMD RDNA. AWQ/GPTQ/FP8 safetensors models should be hidden unless they have a verified GGUF alternate.
  ```
- **Actual Behaviour:**
  ```
  AWQ/GPTQ/FP8 safetensors models are recommended on Windows even though they cannot be served. Users download these models only to find they cannot launch them because:
  - vLLM and SGLang are explicitly blocked on Windows
  - llama.cpp (the only supported backend on Windows) requires GGUF files, not AWQ/GPTQ/FP8 safetensors
  ```
- **Logs / Screenshots:** (Attach logs if you have them, or mention the related issues)
- **Model / Backend:** Any AWQ/GPTQ/FP8 model (e.g., Qwen/Qwen2.5-3B-Instruct-AWQ) + llama.cpp on Windows
- **Are you willing to submit a fix?** Yes — I can open a PR
- **Additional Information:**
  ```
  This is the root cause behind several existing user reports:
  - #122 — User downloaded Qwen3-VL-30B-A3B-Thinking-AWQ from Cookbook but could not serve it
  - #614 — Download of cyankiwi/gemma-4-26B-A4B-it-AWQ-4bit crashed on Windows
  - #191 — Ministral-3-8B-Reasoning-2512-AWQ-4bit failed to serve via vLLM

  Root cause analysis:
  1. _detect_windows() in services/hwfit/hardware.py does not include a "platform" key
  2. rank_models() in services/hwfit/fit.py has no way to know the host is Windows
  3. The existing GGUF-only filters for Apple Silicon and consumer AMD RDNA do not apply to Windows
  ```

---

## Step 2 — Open the Pull Request

Issue number: **#2526**

```bash
git push origin fix/windows-cookbook-gguf-only
```

**Base branch:** `dev` (not `main`)
Go to: https://github.com/pewdiepie-archdaemon/odysseus/compare/dev...fix/windows-cookbook-gguf-only

**Title:** `fix(hwfit): filter non-GGUF models on Windows`

**Body (copy-paste this exactly):**

```markdown
## Summary

Odysseus only supports llama.cpp on Windows (vLLM and SGLang are explicitly blocked). llama.cpp requires GGUF files, but the Cookbook hardware-fit scan was still recommending AWQ/GPTQ/FP8 safetensors models to Windows users — models they cannot actually serve. This fix adds a "platform": "windows" flag to hardware detection and extends the existing GGUF-only filter (used for Apple Silicon and consumer AMD RDNA) to also apply to Windows hosts.

## Target branch

- [x] This PR targets **`dev`**, not `main`. All PRs land in `dev`; `main` is curated by the maintainer at each release. If your PR is on `main` by accident, click "Edit" on this PR and change the base.

## Linked Issue

Fixes #2526

## Type of Change

- [x] Bug fix (non-breaking — fixes a confirmed issue)
- [ ] New feature (non-breaking — adds new behaviour)
- [ ] Breaking change (changes or removes existing behaviour)
- [ ] Refactor / cleanup (behaviour unchanged)
- [ ] Documentation only
- [ ] CI / tooling / configuration

## Checklist

- [x] I searched [open issues](https://github.com/pewdiepie-archdaemon/odysseus/issues) and [open PRs](https://github.com/pewdiepie-archdaemon/odysseus/pulls) — this is not a duplicate.
- [ ] This PR targets `main`
- [x] My changes are limited to the scope described above — no unrelated refactors or whitespace changes mixed in.
- [ ] I actually ran the app (`docker compose up` or `uvicorn app:app`) and verified the change works end-to-end. Type-checks and unit tests are not enough.

## How to Test

1. On a Windows machine with an NVIDIA GPU, run Odysseus
2. Open the Cookbook and click "Rescan" to refresh hardware detection
3. Search for "Qwen/Qwen2.5-3B-Instruct-AWQ" — it should NOT appear in recommendations
4. Search for "Qwen/Qwen2.5-3B-Instruct" (the base model) — it SHOULD appear (has GGUF source)
5. On a Linux/CUDA machine, verify that the AWQ model still appears in recommendations (regression guard)

Or run the automated tests:


python -m pytest tests/test_hwfit_windows.py tests/test_hwfit_amd.py tests/test_hwfit_macos.py tests/test_hwfit_manual_backend.py -v


Expected: 32 passed (1 pre-existing unrelated failure on Windows: `test_detect_system_propagates_unified_memory` asserts `backend == "metal"` but on a Windows host `detect_system()` returns `"cuda"` — this is unrelated to this change).

## Visual / UI changes — REQUIRED if you touched anything that renders

This change does not affect the UI. It only affects which models are recommended by the hardware-fit backend.

- [ ] **Screenshot or short clip** of the change in the running app, attached below. Mobile screenshot too if the change affects mobile.
- [ ] **Style match**: the change uses Odysseus's existing visual language. Specifically:
  - Reuse existing CSS variables (`--red`, `--fg`, `--bg`, `--card`, `--border`, etc.) — do not introduce new color values, font sizes, or spacing units.
  - Reuse existing button/input/card/border classes. Don't invent parallel styling.
  - **No Unicode emoji in UI or code.** Use inline SVG (matching the monochrome icon style already in `static/index.html`) or plain text.
  - Monospaced font (`Fira Code`) for primary UI text. Don't override.
  - Dark theme is the default; any light-mode work must be wired through the existing theme system, not hard-coded.
- [ ] **No new component patterns.** If a similar widget already exists in the app, extend it instead of writing a parallel one.
- [x] **I am not an LLM agent submitting a bulk PR.** If you are, please open an issue describing the problem first — bulk auto-generated PRs that don't match the project's visual style are closed on sight, even when the underlying fix is correct.

### Screenshots / clips

N/A — no UI changes
```
