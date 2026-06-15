from pathlib import Path


def test_memory_list_implementations_do_not_truncate_results():
    for path in ("mcp_servers/memory_server.py", "src/ai_interaction.py"):
        source = Path(path).read_text()
        assert "memories[:100]" not in source
