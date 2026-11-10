from src.agent_loop import _native_unattended_workspace_read_floor


def test_native_unattended_workspace_keeps_minimal_read_surface():
    context = {
        "surface": "odysseus-native",
        "unattended_mode": True,
        "workspace": "/workspace/run",
    }

    assert _native_unattended_workspace_read_floor(
        context, "/workspace/run", [], set(), set()
    ) == {"get_workspace", "ls", "read_file"}


def test_workspace_floor_respects_contract_and_denials():
    context = {
        "surface": "odysseus-native",
        "interaction_mode": "cook",
    }

    assert _native_unattended_workspace_read_floor(
        context, "/workspace/run", [], {"ls"}, {"read_file"}
    ) == {"get_workspace"}
    assert _native_unattended_workspace_read_floor(
        context,
        "/workspace/run",
        [{"function": {"name": "declared_tool"}}],
        set(),
        set(),
    ) == set()


def test_interactive_or_missing_workspace_has_no_read_floor():
    assert _native_unattended_workspace_read_floor(
        {"surface": "odysseus-native"}, "/workspace/run", [], set(), set()
    ) == set()
    assert _native_unattended_workspace_read_floor(
        {"surface": "odysseus-native", "unattended_mode": True},
        None,
        [],
        set(),
        set(),
    ) == set()
