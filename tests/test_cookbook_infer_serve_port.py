r"""Regression: _infer_serve_port must actually parse --port / OLLAMA_HOST.

Both regexes were written with doubled backslashes inside raw-string literals
(r"--port\\s+(\\d+)", r"OLLAMA_HOST=[^\\s]*?:(\\d+)"), so the regex engine
received literal backslashes (\\s, \\d) and never matched a real serve command.
A command like "... --port 9000" silently fell through to the 8080/11434
default, so the inferred endpoint port was wrong. The patterns are now correct
single-escape (\s, \d).
"""
from src.tools.cookbook import _infer_serve_port


def test_port_flag_is_parsed():
    assert _infer_serve_port("python -m vllm.entrypoints.openai.api_server --port 9000") == 9000


def test_ollama_host_port_is_parsed():
    assert _infer_serve_port("OLLAMA_HOST=127.0.0.1:11500 ollama serve") == 11500


def test_ollama_default_without_host_override():
    assert _infer_serve_port("ollama serve") == 11434


def test_empty_and_plain_command_defaults():
    assert _infer_serve_port("") == 8080
    assert _infer_serve_port("some other server") == 8080
