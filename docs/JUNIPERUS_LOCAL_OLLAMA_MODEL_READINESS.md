# JUNIPERUS LOCAL OLLAMA MODEL READINESS

_The Local Ollama layer for Juniperus / Gnexus Operations Console._

---

## Detection Order (No Secrets Stored)

1. `http://127.0.0.1:11434/api/tags` (HTTP, 2.5s timeout)
2. `http://localhost:11434/api/tags` (HTTP fallback, 2.5s)
3. `ollama list` (CLI, 6s timeout if both HTTP fail)

All detection is **local-only**. No credentials are sent. No API tokens stored.

---

## Endpoint Registration

| Field | Value |
|---|---|
| **name** | `Local Ollama (All Models)` |
| **base_url** | `http://127.0.0.1:11434/v1` |
| **is_enabled** | `true` (if Ollama is running and at least one model exists) |
| **model_type** | `llm` |
| **cached_models** | All discovered model names (e.g. `["llama3.2:latest", "qwen2.5:7b"]`) |
| **supports_tools** | `null` (unknown) — auto-detected later at use time |

**Policy:** One endpoint registers for all models, not per-model endpoints. This simplifies ops and lets the picker list all models under "Local Ollama".

---

## Capability Classification (Heuristic)

Each discovered model is tagged with zero or more capability hints:

| Class | Pattern Triggers |
|---|---|
| **fast chat candidate** | `mini`, `small`, `1b`, `2b`, `3b`, `phi`, `gemma2` |
| **coding candidate** | `coder`, `code`, `deepseek-coder`, `starcoder`, `codellama` |
| **reasoning candidate** | `r1`, `reason`, `think`, `qwq`, `deepseek-r1` |
| **long-context candidate** | `128k`, `1m`, `long`, `llama3.1`, `llama3.2`, `qwen2.5` |

**Tool-capable:** Models with `llama3.1`, `llama3.2`, `qwen2.5`, `mistral`, `command-r`, `firefunction`, `hermes` are likely tool-capable. Others: unknown.

---

## Fallback Model Selection

The registry marks one model as `fallback: true`:
1. Prefer a **fast chat candidate**.
2. Else, use the **first discovered model**.

This is the default choice for the smoke test and quick chat initiation.

---

## Smoke Test (Local-Only)

- **Prompt:** "Say OK"
- **Max tokens:** 8
- **Endpoint:** `http://127.0.0.1:11434/api/generate`
- **Records:** `ok`, `latencyMs`, `responsePreview`
- **No cloud calls:** If Ollama is unreachable, smoke test is skipped with a clear error record.

---

## Registry Location

`data/gnexus/ollama/ollama-model-registry.json`

Structure:

```json
{
  "schema": "gnexus.ollama.registry.v1",
  "ollama": { "running": true, "source": "http-127" },
  "endpoint": {
    "name": "Local Ollama (All Models)",
    "base_url": "http://127.0.0.1:11434/v1",
    "cached_models": ["llama3.2:latest"],
    "is_enabled": true,
    "registered_in_picker": true
  },
  "modelCount": 1,
  "fallbackModel": "llama3.2:latest",
  "models": [
    { "name": "...", "family": "...", "parameter_size": "...",
      "capabilities": ["fast chat candidate"], "tool_capable": null, "fallback": true }
  ]
}
```

---

## Import Script

```powershell
.\venv\Scripts\python.exe .\scripts\gnexus\Import-LocalOllamaModels.py
```

- Idempotent: Updates existing endpoint if already registered.
- Writes an import receipt to `data/gnexus/receipts/ollama-import-receipt.json`.
- Runs smoke test automatically (unless `--no-smoke` passed).

---

*Workspace root: `C:\Users\iamcy\CymaticsDev`*  
*Local-first. Human approval required. No endless loading.*
