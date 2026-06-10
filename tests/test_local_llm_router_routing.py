from unittest.mock import MagicMock, patch

import pytest

from src.local_llm_router_routing import (
    LocalLlmRouterNotReady,
    LocalLlmRouterResolution,
    build_model_pool,
    check_local_llm_router_ready,
    describe_local_llm_router_status,
    is_local_llm_router_auto_model,
    resolve_local_llm_router,
    resolve_model_on_endpoint,
)
from src.constants import LOCAL_LLM_ROUTER_AUTO_MODEL_ID as CONST_ID
from src.local_llm_router_runtime import local_llm_router_available


def test_is_local_llm_router_auto_model():
    assert is_local_llm_router_auto_model(CONST_ID)
    assert not is_local_llm_router_auto_model("qwen3:8b")


@patch("src.local_llm_router_routing.resolve_model_on_endpoint")
@patch("src.local_llm_router_routing._configure_llr_from_installed")
@patch("src.local_llm_router_routing.load_local_llm_router")
def test_resolve_local_llm_router_passes_mode_and_reasons(mock_load_ss, mock_configure, mock_resolve):
    mock_configure.return_value = {"pool": ["gemma4:e4b", "qwen3:8b"]}
    decision = MagicMock()
    decision.tier = MagicMock(value="simple")
    decision.model = "gemma4:e4b"
    decision.reasons = ("mode=agent", "keyword/heuristic scoring → tier simple")
    router = MagicMock()
    router.explain.return_value = decision
    mock_load_ss.return_value = router
    mock_resolve.return_value = ("http://127.0.0.1:11434/api/chat", "gemma4:e4b", {})

    res = resolve_local_llm_router(
        prompt="Reply with exactly: pong",
        endpoint_url="http://127.0.0.1:11434",
        headers={},
        mode="agent",
    )

    mock_configure.assert_called_once()
    router.explain.assert_called_once_with("Reply with exactly: pong", mode="agent")
    assert isinstance(res, LocalLlmRouterResolution)
    assert res.model == "gemma4:e4b"
    assert res.route_reasons == ("mode=agent", "keyword/heuristic scoring → tier simple")


@patch("src.local_llm_router_routing._load_endpoint")
def test_resolve_model_on_endpoint_scoped(mock_load):
    ep = MagicMock()
    ep.base_url = "http://127.0.0.1:11434"
    ep.name = "Ollama"
    ep.api_key = ""
    mock_load.return_value = ep
    with patch("src.local_llm_router_routing._endpoint_enabled_models", return_value=["gemma4:e4b", "qwen3:8b"]):
        with patch("src.local_llm_router_routing.build_chat_url", return_value="http://127.0.0.1:11434/api/chat"):
            with patch("src.local_llm_router_routing.build_headers", return_value={"Authorization": "Bearer x"}):
                url, model, headers = resolve_model_on_endpoint(
                    "qwen3:8b",
                    endpoint_url="http://127.0.0.1:11434",
                    headers={"X-Test": "1"},
                    owner=None,
                )
    assert model == "qwen3:8b"
    assert "11434" in url
    assert headers.get("X-Test") == "1"


@patch("src.local_llm_router_routing.get_setting")
@patch("src.local_llm_router_routing.load_local_llm_router")
@patch("src.local_llm_router_routing.installed_tags_for_endpoint")
def test_build_model_pool_intersection(mock_installed, mock_load_ss, mock_setting):
    mock_installed.return_value = ["gemma4:e4b", "qwen3:8b", "qwen3:14b"]
    mock_setting.side_effect = lambda key, default=None: {
        "local_llm_router_vram_gb": 16,
        "local_llm_router_quant": "qat",
        "local_llm_router_models": [],
    }.get(key, default)
    ss = MagicMock()
    ss.recommended_models.return_value = ["gemma4:e4b", "qwen3:8b", "qwen3:14b", "deepseek-r1:8b"]
    mock_load_ss.return_value = ss
    pool = build_model_pool("http://127.0.0.1:11434")
    assert "gemma4:e4b" in pool
    assert len(pool) >= 2


@patch("src.local_llm_router_routing.get_setting")
@patch("src.local_llm_router_routing.load_local_llm_router")
@patch("src.local_llm_router_routing.installed_tags_for_endpoint")
def test_build_model_pool_falls_back_to_installed_only(mock_installed, mock_load_ss, mock_setting):
    mock_installed.return_value = ["qwen3:8b", "qwen3:14b", "llama3.2:3b"]
    mock_setting.side_effect = lambda key, default=None: {
        "local_llm_router_vram_gb": 16,
        "local_llm_router_quant": "qat",
        "local_llm_router_models": [],
    }.get(key, default)
    ss = MagicMock()
    ss.recommended_models.return_value = ["gemma4:e4b", "qwen3:8b"]
    mock_load_ss.return_value = ss
    pool = build_model_pool("http://127.0.0.1:11434")
    assert set(pool) == {"qwen3:8b", "qwen3:14b", "llama3.2:3b"}
    assert len(pool) == 3


@patch("src.local_llm_router_routing.installed_tags_for_endpoint")
def test_check_local_llm_router_ready_no_models(mock_installed):
    mock_installed.return_value = []
    with pytest.raises(LocalLlmRouterNotReady) as exc:
        check_local_llm_router_ready("http://127.0.0.1:11434/v1")
    assert exc.value.code == "no_models"
    assert "Cookbook" in str(exc.value)


@patch("src.local_llm_router_routing.installed_tags_for_endpoint")
def test_check_local_llm_router_ready_one_model(mock_installed):
    mock_installed.return_value = ["qwen3:8b"]
    with pytest.raises(LocalLlmRouterNotReady) as exc:
        check_local_llm_router_ready("http://127.0.0.1:11434/v1")
    assert exc.value.code == "insufficient_models"
    assert "qwen3:8b" in str(exc.value)


def test_check_local_llm_router_ready_no_endpoint():
    with pytest.raises(LocalLlmRouterNotReady) as exc:
        check_local_llm_router_ready("")
    assert exc.value.code == "no_endpoint"


@patch("src.local_llm_router_runtime.local_llm_router_available", return_value=True)
@patch("src.local_llm_router_routing.get_setting")
@patch("src.local_llm_router_routing.load_local_llm_router")
@patch("src.local_llm_router_routing.installed_tags_for_endpoint")
@patch("src.local_llm_router_routing.build_model_pool")
def test_describe_local_llm_router_status_ready(
    mock_pool, mock_installed, mock_load_ss, mock_setting, _mock_avail,
):
    mock_installed.return_value = ["gemma4:e4b", "qwen3:8b", "qwen3:14b"]
    mock_pool.return_value = ["gemma4:e4b", "qwen3:8b"]
    mock_setting.side_effect = lambda key, default=None: {
        "local_llm_router_vram_gb": 16,
        "local_llm_router_quant": "qat",
        "local_llm_router_models": [],
    }.get(key, default)
    ss = MagicMock()
    ss.profile_for_vram_gb.return_value = "medium"
    ss.recommended_models.return_value = ["gemma4:e4b", "qwen3:8b", "deepseek-r1:8b"]
    mock_load_ss.return_value = ss

    status = describe_local_llm_router_status("http://127.0.0.1:11434")

    assert status["ready"] is True
    assert status["vram_gb"] == 16
    assert status["profile"] == "medium"
    assert status["pool"] == ["gemma4:e4b", "qwen3:8b"]
    assert "deepseek-r1:8b" in status["missing_pulls"]


@patch("src.local_llm_router_runtime.local_llm_router_available", return_value=False)
def test_describe_local_llm_router_status_router_missing(_mock_avail):
    status = describe_local_llm_router_status("http://127.0.0.1:11434")
    assert status["ready"] is False
    assert status["code"] == "router_missing"


@patch("src.local_llm_router_runtime.local_llm_router_available", return_value=True)
@patch("src.local_llm_router_routing.get_setting")
@patch("src.local_llm_router_routing.load_local_llm_router")
@patch("src.local_llm_router_routing.installed_tags_for_endpoint")
def test_describe_local_llm_router_status_insufficient_models(
    mock_installed, mock_load_ss, mock_setting, _mock_avail,
):
    mock_installed.return_value = ["qwen3:8b"]
    mock_setting.side_effect = lambda key, default=None: {
        "local_llm_router_vram_gb": 16,
        "local_llm_router_quant": "qat",
        "local_llm_router_models": [],
    }.get(key, default)
    ss = MagicMock()
    ss.profile_for_vram_gb.return_value = "medium"
    ss.recommended_models.return_value = ["gemma4:e4b", "qwen3:8b"]
    mock_load_ss.return_value = ss

    status = describe_local_llm_router_status("http://127.0.0.1:11434")

    assert status["ready"] is False
    assert status["code"] == "insufficient_models"
    assert status["installed"] == ["qwen3:8b"]


def test_vram_gb_from_hwfit_uses_per_gpu_not_total():
    from src.local_llm_router_routing import _vram_gb_from_hwfit

    # 2x 8 GB: total 16 but Ollama uses one card → 8 GB tier
    system = {
        "gpu_vram_gb": 16.0,
        "gpu_groups": [
            {"vram_each": 8.0, "count": 2, "vram_total": 16.0},
        ],
    }
    assert _vram_gb_from_hwfit(system) == 8.0


def test_match_tag_no_fuzzy_cross_model():
    from src.local_llm_router_routing import _match_tag

    assert _match_tag("gemma4:e4b", ["qwen3:8b", "qwen3:14b"]) is None
    assert _match_tag("qwen3:8b", ["qwen3:8b", "qwen3:14b"]) == "qwen3:8b"


@patch("src.local_llm_router_routing.resolve_local_llm_router")
def test_resolve_task_endpoint_concrete_auto_stack(mock_resolve_stack):
    from src.local_llm_router_routing import LocalLlmRouterResolution
    from src.constants import LOCAL_LLM_ROUTER_AUTO_MODEL_ID
    from src.task_endpoint import _resolve_local_llm_router_fallback

    mock_resolve_stack.return_value = LocalLlmRouterResolution(
        endpoint_url="http://127.0.0.1:11434/api/chat",
        model="gemma4:e4b",
        headers={},
        tier="simple",
        route_reasons=("mode=chat",),
        pool=("gemma4:e4b", "qwen3:8b"),
    )
    url, model, headers = _resolve_local_llm_router_fallback(
        "http://127.0.0.1:11434/v1",
        LOCAL_LLM_ROUTER_AUTO_MODEL_ID,
        {},
    )
    assert model == "gemma4:e4b"
    assert "11434" in url


def test_resolve_task_endpoint_passthrough_non_auto():
    from src.task_endpoint import _resolve_local_llm_router_fallback

    url, model, headers = _resolve_local_llm_router_fallback(
        "http://127.0.0.1:11434/v1",
        "qwen3:8b",
        {"X": "1"},
    )
    assert model == "qwen3:8b"
    assert headers == {"X": "1"}


@pytest.mark.skipif(not local_llm_router_available(), reason="local-llm-router package not installed")
def test_build_llr_session_16gb_preset_tiers_and_pool_order():
    from src.local_llm_router_routing import _build_llr_session

    installed = [
        "gemma4:e4b",
        "qwen3.5:9b",
        "qwen3.6:35b-a3b",
        "qwen3:14b",
        "qwen2.5-coder:14b",
        "deepseek-r1:8b",
    ]
    ctx = _build_llr_session(installed, vram_gb=16, quant="qat")

    assert ctx["profile"] == "workstation_16gb"
    assert ctx["pool"] == [
        "gemma4:e4b",
        "qwen3.5:9b",
        "qwen3.6:35b-a3b",
        "qwen3:14b",
        "qwen2.5-coder:14b",
        "deepseek-r1:8b",
    ]
    assert ctx["tiers"].complex == "qwen3.6:35b-a3b"
    assert ctx["tiers"].complex_alt == "qwen3:14b"
    assert ctx["tiers"].code == "qwen2.5-coder:14b"


@patch("src.local_llm_router_routing.get_setting")
@patch("src.local_llm_router_routing.installed_tags_for_endpoint")
@pytest.mark.skipif(not local_llm_router_available(), reason="local-llm-router package not installed")
def test_configure_uses_llr_preset_complex_and_complex_alt(mock_installed, mock_setting):
    from src.local_llm_router_routing import _configure_llr_from_installed
    from src.local_llm_router_runtime import load_local_llm_router

    mock_setting.side_effect = lambda key, default=None: {
        "local_llm_router_vram_gb": 16,
        "local_llm_router_quant": "qat",
        "local_llm_router_models": [],
    }.get(key, default)
    installed = [
        "gemma4:e4b",
        "qwen3.5:9b",
        "qwen3.6:35b-a3b",
        "qwen3:14b",
        "qwen2.5-coder:14b",
        "deepseek-r1:8b",
    ]
    mock_installed.return_value = installed

    _configure_llr_from_installed(installed)
    session = load_local_llm_router().get_session()
    assert session.tiers.complex == "qwen3.6:35b-a3b"
    assert session.tiers.complex_alt == "qwen3:14b"

    router = load_local_llm_router()
    agent = router.explain("architecture tradeoffs", hint="design", mode="agent")
    chat = router.explain("architecture tradeoffs", hint="design", mode="chat")
    assert agent.model == "qwen3.6:35b-a3b"
    assert chat.model == "qwen3:14b"
