"""The /setup guide must offer a llama.cpp (llama-server) local example.

Without it, the port-8080 "llama.cpp" provider label (src/llm_core.py
_provider_label) is never reachable from first-run setup — a user pasting a
local endpoint only saw the Ollama and generic examples. Both the static-HTML
and the streamed-blocks renderings of the setup guide must carry the example.
"""
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "static" / "js" / "slashCommands.js"


def test_setup_guide_offers_llamacpp_local_example():
    src = _SRC.read_text(encoding="utf-8")
    # The example URL appears in both the HTML-string and streamed renderings.
    assert src.count("http://localhost:8080/v1") >= 2
    assert "llama.cpp (llama-server)" in src
