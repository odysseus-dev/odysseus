from routes.chat_routes import (
    _clean_v3_route_for_model,
    _native_runtime_requires_local_browser,
    _successful_session_tool_names,
    _turn_contract_enabled,
)


def test_preheretic_model_owns_clean_route_independent_of_endpoint_alias():
    assert _clean_v3_route_for_model("odysseus-qwen3.5-tools-pre-heretic")


def test_trial55_base_uses_same_stable_compact_tool_runtime():
    assert _clean_v3_route_for_model("odysseus-qwen3.5-heretic-trial55-base")


def test_ajax_c375_uses_same_stable_compact_tool_runtime():
    assert _clean_v3_route_for_model("ajax_c375")
    assert _clean_v3_route_for_model("openai/ajax_c375")


def test_any_odysseus_or_ajax_token_uses_clean_tool_runtime():
    assert _clean_v3_route_for_model("Odysseus")
    assert _clean_v3_route_for_model("lab/my-odysseus-experiment")
    assert _clean_v3_route_for_model("lab/AJAX-trial-99")


def test_partial_name_matches_do_not_capture_regular_models():
    assert not _clean_v3_route_for_model("ajaxian-model")
    assert not _clean_v3_route_for_model("myodysseusfoo")


def test_explicit_schema_setting_overrides_model_name_default():
    assert not _clean_v3_route_for_model("Odysseus-trial55", "full")
    assert not _clean_v3_route_for_model("Ajax_c375", "none")
    assert _clean_v3_route_for_model("gpt-5.5", "compact")
    assert _clean_v3_route_for_model("gpt-5.5", "odysseus_compact")


def test_other_models_keep_regular_harness():
    assert not _clean_v3_route_for_model("qwen35-9b-base")
    assert not _clean_v3_route_for_model("")


def test_clean_v3_keeps_contract_ownership_on_native_workspace():
    assert _turn_contract_enabled(
        exact_tool_approval=None,
        runtime_surface="odysseus-native",
        native_workspace_contract=True,
        clean_v3_route=True,
    )


def test_other_models_keep_separate_native_workspace_contract():
    assert not _turn_contract_enabled(
        exact_tool_approval=None,
        runtime_surface="odysseus-native",
        native_workspace_contract=True,
        clean_v3_route=False,
    )


def test_explicit_full_schema_route_bypasses_capability_contract():
    assert not _turn_contract_enabled(
        exact_tool_approval=None,
        runtime_surface="",
        native_workspace_contract=False,
        clean_v3_route=False,
        full_schema_route=True,
    )


def test_successfully_used_tools_stay_warm_for_the_session():
    class Message:
        def __init__(self, metadata):
            self.metadata = metadata

    class Session:
        history = [
            Message({"tool_events": [
                {"tool": "manage_calendar", "exit_code": 0},
                {"tool": "web_fetch", "status": "done"},
            ]}),
            Message({"tool_events": [
                {"tool": "manage_notes", "error": True},
                {"tool": "private_browser", "status": "failed"},
            ]}),
        ]

    assert _successful_session_tool_names(Session()) == {
        "manage_calendar", "web_fetch",
    }


def test_tui_and_exact_approval_still_bypass_routed_contract():
    assert not _turn_contract_enabled(
        exact_tool_approval=None,
        runtime_surface="odysseus-tui",
        native_workspace_contract=False,
        clean_v3_route=True,
    )


def test_native_html_artifact_requires_local_browser_without_public_web():
    context = {
        "surface": "odysseus-native",
        "terminal_agent": True,
        "unattended_mode": True,
        "completion_requirements": {
            "required_artifacts": ["/workspace/output.html"],
        },
    }
    assert _native_runtime_requires_local_browser(context)
    context["completion_requirements"]["required_artifacts"] = ["/workspace/output.txt"]
    assert not _native_runtime_requires_local_browser(context)
    assert not _turn_contract_enabled(
        exact_tool_approval=object(),
        runtime_surface="",
        native_workspace_contract=False,
        clean_v3_route=True,
    )
