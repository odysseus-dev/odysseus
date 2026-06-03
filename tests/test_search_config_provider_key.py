from services.search import core, providers

PROVIDER_ENV_KEYS = (
    "DATA_BRAVE_API_KEY",
    "GOOGLE_API_KEY",
    "TAVILY_API_KEY",
    "SERPER_API_KEY",
)


def _config(monkeypatch, settings):
    for env_name in PROVIDER_ENV_KEYS:
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setattr(core, "_get_search_settings", lambda: settings)
    monkeypatch.setattr(providers, "_get_search_settings", lambda: settings)
    return core.get_search_config()


def test_search_config_detects_active_provider_specific_key(monkeypatch):
    config = _config(monkeypatch, {
        "search_provider": "tavily",
        "tavily_api_key": "tavily-key",
    })

    assert config["has_api_key"] is True


def test_search_config_ignores_key_for_different_provider(monkeypatch):
    config = _config(monkeypatch, {
        "search_provider": "brave",
        "tavily_api_key": "tavily-key",
    })

    assert config["has_api_key"] is False


def test_search_config_keeps_legacy_shared_key_fallback(monkeypatch):
    config = _config(monkeypatch, {
        "search_provider": "serper",
        "search_api_key": "legacy-key",
    })

    assert config["has_api_key"] is True


def test_search_config_detects_provider_env_key(monkeypatch):
    settings = {"search_provider": "tavily"}
    for env_name in PROVIDER_ENV_KEYS:
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", "env-key")
    monkeypatch.setattr(core, "_get_search_settings", lambda: settings)
    monkeypatch.setattr(providers, "_get_search_settings", lambda: settings)

    assert core.get_search_config()["has_api_key"] is True
    assert providers._get_provider_key("tavily") == "env-key"
