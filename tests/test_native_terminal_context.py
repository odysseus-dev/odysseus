from routes.chat_routes import _parse_client_runtime_context


def test_native_terminal_bypasses_chat_preemptive_shortcuts():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "src" / "agent_loop.py").read_text()
    assert source.count("and not _native_terminal_runtime") >= 3
    assert '"fallback": "preemptive_explicit_admin_session"' in source


def test_native_terminal_context_keeps_safe_artifact_contract_only():
    parsed = _parse_client_runtime_context({
        "surface": "odysseus-native",
        "terminal_agent": True,
        "artifact_recovery_enabled": True,
        "input_files": [
            "/workspace/fixtures/video.mp4",
            "/workspace/../escape.mp4",
        ],
        "completion_requirements": {
            "required_artifacts": [
                "/workspace/index.html",
                "/workspace/images/craft_1.png",
                "/workspace/../escape",
            ],
            "verifier_required": True,
            "verifier_commands": ["rm -rf /"],
        },
        "host_shell_bridge": {"url": "http://127.0.0.1:1/run", "token": "x"},
    })

    assert parsed == {
        "surface": "odysseus-native",
        "terminal_agent": True,
        "artifact_recovery_enabled": True,
        "input_files": ["/workspace/fixtures/video.mp4"],
        "completion_requirements": {
            "required_artifacts": [
                "/workspace/index.html",
                "/workspace/images/craft_1.png",
            ],
            "verifier_required": False,
            "executable_verifier_available": False,
            "verifier_commands": [],
        },
    }


def test_native_input_files_are_visible_as_bounded_runtime_facts():
    from src.agent_loop import _client_runtime_context_message

    message = _client_runtime_context_message({
        "surface": "odysseus-native",
        "terminal_agent": True,
        "input_files": ["/workspace/fixtures/Dm3nyBNhkp8.mp4"],
    })
    assert message is not None
    assert "input_files=/workspace/fixtures/Dm3nyBNhkp8.mp4" in message["content"]
    assert "already exist in the active workspace" in message["content"]
