# Tools manifest (index only)

> Scripts remain in root [`tools/`](../../tools/), [`scripts/`](../../scripts/), and [`mcp_servers/`](../../mcp_servers/). This file is the agent index.

## Root `tools/` — memory stack & sidecars

| Tool | Path | One-liner |
|------|------|-----------|
| Unified memory API | `tools/unified_memory_api.py` | Merges PixelRAG visual + agent memory + MemPalace notes on `:40001` |
| PixelRAG serve | `tools/pixelrag_serve_quiet.py` | Serves FAISS index on `:30001` |
| PixelRAG pipeline | `tools/pixelrag_pipeline.py` | Export → embed → index pipeline |
| Screenpipe export | `tools/export_screenpipe.py` | Export Screenpipe OCR frames to tiles |
| Build tiles | `tools/build_tiles.py` | Tile builder for archivist/PixelRAG |
| Agent memory | `tools/agent_memory.py` | Agent session memory helpers |
| MemPalace search | `tools/mempalace_search.py` | MemPalace KG search wrapper |
| Clicky worker API | `tools/clicky_worker_api.py` | Clicky overlay worker HTTP API |
| Memory stack env | `tools/memory_stack_env.py` | Loads `memory_stack.env` for sidecars |

## `scripts/` — Odysseus CLI & ops

| Tool | Path | One-liner |
|------|------|-----------|
| Odysseus CLI | `scripts/odysseus` | Main CLI entry |
| MCP helper | `scripts/odysseus-mcp` | MCP server management |
| Memory CLI | `scripts/odysseus-memory` | Memory read/write from shell |
| Research CLI | `scripts/odysseus-research` | Research pipeline helper |
| Sessions CLI | `scripts/odysseus-sessions` | Session catalog ops |
| Tasks CLI | `scripts/odysseus-tasks` | Todo/task management |
| Mail CLI | `scripts/odysseus-mail` | Email helpers |
| Notes CLI | `scripts/odysseus-notes` | Notes management |
| Test endpoints | `scripts/test-endpoints.py` | API smoke probe |
| Probe imports | `scripts/_probe_imports.py` | Import alignment check |
| Handoff relay | `scripts/handoff-relay-watcher.ps1` | Watches handoff inbox |

## `mcp_servers/` — built-in MCP

| Tool | Path | One-liner |
|------|------|-----------|
| Email server | `mcp_servers/email_server.py` | Email MCP tools |
| Memory server | `mcp_servers/memory_server.py` | Memory MCP tools |
| RAG server | `mcp_servers/rag_server.py` | Document RAG MCP |
| Image gen server | `mcp_servers/image_gen_server.py` | Image generation MCP |

## Sidecar launch (deploy)

| Script | Path | One-liner |
|--------|------|-----------|
| Start Screenpipe | `deploy/scripts/start-screenpipe.ps1` | Screen OCR sidecar `:3030` |
| Start PixelRAG | `deploy/scripts/start_pixelrag_local.ps1` | Local PixelRAG stack |
| Start Clicky | `deploy/scripts/start-clicky.ps1` | Clicky overlay worker |
| Start Archivist | `deploy/scripts/start-archivist.ps1` | Archivist sidecar |

## Integration skill scripts

| Tool | Path | One-liner |
|------|------|-----------|
| Odysseus API (Claude) | `integrations/claude/skills/odysseus/scripts/odysseus_api.py` | Scoped Odysseus agent API |
| Handoff API (Claude) | `integrations/claude/skills/handoff/scripts/handoff_api.py` | Handoff inbox writer |
| Odysseus API (Codex) | `integrations/codex/skills/odysseus/scripts/odysseus_api.py` | Codex Odysseus API |
