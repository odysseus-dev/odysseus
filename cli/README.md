# Odysseus CLI

A local-first, terminal **coding agent** — like Claude Code / Gemini CLI, but
powered entirely by your own local models. It reuses the Odysseus agent loop
(`src/agent_loop.py`) and its tool suite (bash, python, read/write files, web
search, …), running them on **your machine in the current project directory**
with a Claude-Code-style approval gate.

> Status: **Phase 1 MVP.** Interactive REPL + one-shot mode, streaming output,
> tool execution with approval prompts. See "Roadmap" below for what's next.

## How it works

```
┌────────────┐   prompt    ┌──────────────────────┐  /v1/chat  ┌──────────┐
│  your      │ ──────────▶ │ Odysseus CLI         │ ─────────▶ │  Ollama  │
│  terminal  │ ◀────────── │  • native agent loop │ ◀───────── │  (local) │
└────────────┘  rendered   │  • 6 coding tools    │            └──────────┘
                           │  • tools in $PWD     │
                           │  • approval gate     │
                           └──────────────────────┘
```

**Default: native loop (`nativeagent.py`).** A tight, "Claude Code-style" agent
loop with a fixed 6-tool set — `read_file`, `write_file`, `edit_file`,
`list_dir`, `grep`, `bash` — talking directly to the endpoint with clean
tool-turn threading. This is what makes *local* models behave as agents: small
models get confused by large tool surfaces, and Ollama doesn't lift their tool
calls into the native `tool_calls` field (they arrive as JSON in the message
content), so the loop parses and threads them itself, with a loop-guard that
forces a final answer when a model repeats itself.

**Legacy loop (`--legacy`).** Routes through the full Odysseus server agent loop
(`stream_agent_loop`, 56 tools incl. email/calendar/MCP). More capable on strong
API models, but tends to overwhelm small local models — use the native loop for
local work.

Either way, tools run in your shell / filesystem — **not** inside the Odysseus
Docker container — which is what makes it usable for real coding work.

## Requirements

- **Python 3.11+** (the agent core needs it; macOS system Python 3.9 won't work).
- The Odysseus repo's Python dependencies importable on `PYTHONPATH`
  (`pip install -r ../requirements.txt`). Optional deps (fastembed, pyotp,
  chromadb) are not required — the CLI runs fine without them.
- A local model server. By default: **Ollama** at `http://localhost:11434/v1`.

Recommended model for coding (24 GB Apple Silicon):

```bash
ollama pull qwen2.5-coder:7b
```

## Usage

```bash
# from inside the project you want to work on:
python -m odysseus_cli                       # interactive REPL
python -m odysseus_cli "explain src/app.py"  # one-shot, then exit
python -m odysseus_cli --tui                 # full-screen TUI (see below)

# pick a model / endpoint explicitly:
python -m odysseus_cli --model qwen2.5-coder:7b --endpoint http://localhost:11434/v1
```

### Full-screen TUI (`--tui`)

A Claude-Code-style full-screen interface: a scrollable transcript pane, a
pinned status footer (model · tokens · context %), an input box, and modal
tool-approval prompts. Requires Textual:

```bash
pip install textual
python -m odysseus_cli --tui
```

The line-based REPL remains the default; `--tui` is opt-in and uses the native
loop (not compatible with `--legacy`).

### Approval policy (safety)

The CLI runs an agent that can execute shell commands and edit files. Mutating
tools (`bash`, `python`, `write_file`, `edit_document`) are gated:

| Flag / setting        | Behavior                                            |
|-----------------------|-----------------------------------------------------|
| `--approval ask` (default) | Prompt `[y]es / [n]o / [a]lways` before each mutation |
| `--approval auto` / `--yolo` | Never prompt (use only in throwaway dirs)     |
| `--approval deny` / `--read-only` | Block all mutations (read-only agent)    |

Read-only tools (`read_file`, `web_search`, `list_*`) never prompt.

### REPL commands

```
/help            show help
/model <name>    switch model
/approval <m>    set policy: ask | auto | deny
/clear           clear conversation history
/exit            quit
```

## Configuration

Optional `~/.odysseus/cli.toml`:

```toml
model = "qwen2.5-coder:7b"
endpoint = "http://localhost:11434/v1"
approval = "ask"
temperature = 0.2
context_length = 32768
```

Environment overrides: `ODYSSEUS_CLI_MODEL`, `ODYSSEUS_CLI_ENDPOINT`,
`ODYSSEUS_CLI_APPROVAL`, `ODYSSEUS_CLI_API_KEY`, `ODYSSEUS_CLI_OWNER`,
`ODYSSEUS_CLI_DEBUG=1` (restore full logging).

## Tests

```bash
cd cli && python -m pytest tests/ -q
```

## Roadmap

- **Phase 2 — coding UX:** project sandbox (restrict file tools to repo root),
  repo-map injection, colored diffs with apply/reject, session persistence,
  more slash commands.
- **Phase 3 — power:** MCP servers, connect to the running Odysseus memory for
  cross-project recall, planner/executor model split.
- **Phase 4 — packaging:** `pipx install`, shell completion, richer metrics.
