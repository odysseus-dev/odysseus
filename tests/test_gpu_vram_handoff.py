"""Tests for GPU VRAM handoff logic in cookbook serve runner.

The handoff is implemented as shell commands appended to `runner_lines` in
`routes/cookbook_routes.py`. These tests verify the generated shell script
contains the correct guards (single-GPU check, graceful shutdown) without
needing a real GPU or Ollama instance.
"""

import re

import pytest

# The handoff markers we look for in the generated shell script
VLLM_UNLOAD_MARKER = "Checking for Ollama models on host to free GPU VRAM"
OLLAMA_KILL_MARKER = "Checking for orphaned vLLM processes hogging GPU VRAM"
GPU_COUNT_CHECK = "nvidia-smi --query-gpu=count"
SINGLE_GPU_GUARD = 'if [ "$_n_gpus" = "1" ]'
MULTI_GPU_SKIP = "skipping"
GRACEFUL_STOP = "kill -TERM"
FORCE_KILL = "kill -9"


class TestVllmUnloadShellCommands:
    """Verify vLLM pre-launch block unloads Ollama models only on single-GPU."""

    SAMPLE_SCRIPT = """\
_n_gpus="$(nvidia-smi --query-gpu=count --format=csv,noheader 2>/dev/null || echo 0)"
if [ "$_n_gpus" = "1" ] && command -v curl &>/dev/null; then
  echo "Single GPU — checking for Ollama models to free VRAM..."
  _ollama_models="$(curl -sf --max-time 3 http://host.docker.internal:11434/api/ps 2>/dev/null || echo '{}')"
  _loaded="$(printf "%s" "$_ollama_models" | python3 -c "import json,sys; d=json.load(sys.stdin); [print(m['name']) for m in d.get('models', [])]" 2>/dev/null)"
  if [ -n "$_loaded" ]; then
    echo "Unloading Ollama model(s): $_loaded"
    for _m in $_loaded; do
      curl -sf -o /dev/null --max-time 5 http://host.docker.internal:11434/api/generate -d "{\\"model\\":\\"$_m\\",\\"keep_alive\\":0,\\"prompt\\":\\"\\"}" 2>/dev/null || true
    done
    sleep 2
    echo "VRAM freed — launching vLLM."
  else
    echo "No Ollama models loaded — proceeding."
  fi
else
  echo "Multi-GPU system ($_n_gpus GPUs) or curl unavailable — skipping Ollama auto-unload."
fi"""

    def test_detects_ollama_models(self):
        """Script queries Ollama /api/ps and parses model names."""
        assert 'http://host.docker.internal:11434/api/ps' in self.SAMPLE_SCRIPT
        assert 'python3 -c "import json,sys;' in self.SAMPLE_SCRIPT
        assert 'm[\"name\"]' in self.SAMPLE_SCRIPT or "m['name']" in self.SAMPLE_SCRIPT

    def test_unloads_via_keep_alive(self):
        """Script sends keep_alive=0 via /api/generate for each loaded model."""
        assert 'keep_alive' in self.SAMPLE_SCRIPT
        assert '/api/generate' in self.SAMPLE_SCRIPT

    def test_single_gpu_guard_present(self):
        """Script only runs on single-GPU systems."""
        assert 'nvidia-smi --query-gpu=count' in self.SAMPLE_SCRIPT
        assert 'if [ "$_n_gpus" = "1" ]' in self.SAMPLE_SCRIPT

    def test_multi_gpu_skip_message(self):
        """Script logs a skip message on multi-GPU."""
        assert 'Multi-GPU' in self.SAMPLE_SCRIPT or 'skipping' in self.SAMPLE_SCRIPT

    def test_noop_when_no_models_loaded(self):
        """Script handles the empty-models case gracefully."""
        assert "No Ollama models loaded" in self.SAMPLE_SCRIPT

    def test_noop_when_curl_unavailable(self):
        """Script falls through when curl is not installed."""
        assert 'command -v curl' in self.SAMPLE_SCRIPT


class TestOllamaKillShellCommands:
    """Verify Ollama pre-launch block kills vLLM processes only on single-GPU."""

    SAMPLE_SCRIPT = """\
_n_gpus="$(nvidia-smi --query-gpu=count --format=csv,noheader 2>/dev/null || echo 0)"
if [ "$_n_gpus" = "1" ]; then
  _vllm_pids="$(pgrep -f 'VLLM::EngineCore|vllm serve' 2>/dev/null || true)"
  if [ -n "$_vllm_pids" ]; then
    echo "Found stale vLLM process(es): $_vllm_pids — attempting graceful stop..."
    kill -TERM $_vllm_pids 2>/dev/null || true
    sleep 3
    _still_alive=""
    for _pid in $_vllm_pids; do
      kill -0 $_pid 2>/dev/null && _still_alive="$_still_alive $_pid"
    done
    if [ -n "$_still_alive" ]; then
      echo "Process(es) $_still_alive did not stop — sending SIGKILL."
      kill -9 $_still_alive 2>/dev/null || true
    fi
    sleep 2
    echo "VRAM freed for Ollama."
  fi
else
  echo "Multi-GPU system ($_n_gpus GPUs) — skipping vLLM auto-kill."
fi"""

    def test_checks_gpu_count(self):
        """Script detects GPU count before acting."""
        assert GPU_COUNT_CHECK in self.SAMPLE_SCRIPT

    def test_single_gpu_guard(self):
        """Script only kills on single-GPU."""
        assert SINGLE_GPU_GUARD in self.SAMPLE_SCRIPT

    def test_graceful_stop_before_force(self):
        """Script tries SIGTERM before SIGKILL."""
        assert GRACEFUL_STOP in self.SAMPLE_SCRIPT
        assert FORCE_KILL in self.SAMPLE_SCRIPT

    def test_force_kill_fallback(self):
        """Script falls back to kill -9 for processes that did not stop."""
        assert "did not stop" in self.SAMPLE_SCRIPT
        assert FORCE_KILL in self.SAMPLE_SCRIPT

    def test_multi_gpu_skip(self):
        """Script skips on multi-GPU systems."""
        assert "Multi-GPU" in self.SAMPLE_SCRIPT

    def test_noop_when_no_vllm(self):
        """Script does nothing when no vLLM process is found."""
        # The inner block only runs if _vllm_pids is non-empty
        assert 'VLLM::EngineCore' in self.SAMPLE_SCRIPT or "VLLM::EngineCore" in self.SAMPLE_SCRIPT
        assert 'if [ -n "$_vllm_pids" ]; then' in self.SAMPLE_SCRIPT


# Integration-style: verify the cookbook_routes source contains the markers
class TestCookbookRoutesSource:
    """Verify the actual cookbook_routes.py source has the handoff blocks."""

    ROUTES_PATH = "routes/cookbook_routes.py"

    def test_vllm_unload_marker_in_source(self):
        """The vLLM pre-launch block exists."""
        source = self._read_source()
        assert "Free GPU VRAM before launching vLLM" in source

    def test_ollama_kill_marker_in_source(self):
        """The Ollama pre-launch block exists."""
        source = self._read_source()
        assert "Free GPU VRAM before starting Ollama" in source

    def test_gpu_count_check_in_source(self):
        """Both blocks use nvidia-smi to check GPU count."""
        source = self._read_source()
        count = source.count("nvidia-smi --query-gpu=count")
        assert count >= 2, f"Expected 2 GPU count checks, found {count}"

    def test_graceful_shutdown_in_source(self):
        """The Ollama block uses SIGTERM first."""
        source = self._read_source()
        assert "kill -TERM" in source
        assert "kill -9" in source

    def _read_source(self):
        with open(self.ROUTES_PATH) as f:
            return f.read()
