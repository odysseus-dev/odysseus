from routes import cookbook_routes


def test_ollama_download_missing_leads_with_ollama_not_installed_when_docker_blocked():
    lines: list[str] = []
    cookbook_routes._append_local_ollama_download_command_lines(
        lines,
        "ollama pull qwen2.5-coder:14b",
        docker_fallback_available=False,
        docker_fallback_blocked=True,
    )
    rendered = "\n".join(lines)

    assert "ERROR: Ollama is not installed in this environment." in rendered
    assert "llama.cpp" in rendered
    assert "remote Ollama server" in rendered
    assert "docker/host-docker.yml" in rendered
    assert rendered.index("Ollama is not installed in this environment") < rendered.index(
        "Local Docker daemon access is disabled"
    )
