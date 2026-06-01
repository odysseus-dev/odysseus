"""Odysseus CLI — a local-first, terminal coding agent.

Drives the Odysseus agent loop (src.agent_loop.stream_agent_loop) against a
local OpenAI-compatible model server (Ollama / vLLM / llama.cpp), executing
file and shell tools in the current working directory with an approval gate.
"""

__version__ = "0.1.0"
