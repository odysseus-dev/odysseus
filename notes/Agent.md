# Agentic Mode

Odysseus has a built-in Agent mode that gives the model access to tools like `read_file`, `write_file`, `bash`, web search, and more. The challenge with local models is that tool-use support has to be explicitly enabled.

## Why local models often don't work in agent mode

By default, Odysseus assumes local models **don't support native function calling** (`src/agent_loop.py:1539-1549`). Specifically:

- Ollama native endpoints (`/api/chat`) are treated as text-only by default, even for capable models, because of a known issue where they emit one tool token then stop (issue #1567).
- Ollama's OpenAI-compat path (`/v1/chat/completions`) is also text-only by default.

## How to enable it

1. Go to **Settings → Model Endpoints** in the Odysseus UI.
2. Find your local model endpoint (Ollama, LM Studio, vLLM, etc.).
3. Toggle **"Supports Tools"** to **ON** for that endpoint.

This sets `supports_tools = True` in the database, which overrides all default heuristics and forces native function calling on.

## Using Agent mode

- In the chat UI, switch the mode selector from **Chat** to **Agent**.
- Set a **Workspace** folder path — this confines `read_file` / `write_file` / `bash` to that directory and tells the model "this folder IS the project, start exploring it."

## Local models known to work well

The following models are explicitly recognized as tool-capable in `src/agent_loop.py:1511-1523`:

- `qwen2.5`, `qwen3`
- `llama-3.1`, `llama-3.2`, `llama-3.3`, `llama-4`
- `mistral`, `mixtral`
- `gemma` (via API path)
- `hermes` variants
- `phi-3`, `phi-4`

For best results, serve your model via **vLLM with `--enable-auto-tool-choice`**, or use Ollama's OpenAI-compat endpoint (`/v1/`) and enable the "Supports Tools" toggle in endpoint settings.
