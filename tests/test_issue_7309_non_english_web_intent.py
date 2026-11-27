import pytest

from src.agent_runtime.authority import create_request_authority
from src.turn_contract import has_multilingual_web_intent, requested_capabilities


@pytest.mark.parametrize(
    "prompt",
    [
        "Busca en internet las \u00faltimas noticias sobre inteligencia artificial",
        "\u00bfCu\u00e1l es el pron\u00f3stico del tiempo en Madrid hoy?",
        "Suche im Internet nach den neuesten Nachrichten \u00fcber Python",
        "Wie wird das Wetter morgen in Berlin?",
        "Recherche sur le web les derni\u00e8res actualit\u00e9s technologiques",
        "Quelle est la m\u00e9t\u00e9o \u00e0 Paris aujourd'hui ?",
        "Pesquise na internet as \u00faltimas not\u00edcias sobre energia solar",
        "Cerca sul web le ultime notizie di oggi",
        "Zoek op internet naar het laatste nieuws",
        "\u041d\u0430\u0439\u0434\u0438 \u0432 \u0438\u043d\u0442\u0435\u0440\u043d\u0435\u0442\u0435 \u043f\u043e\u0441\u043b\u0435\u0434\u043d\u0438\u0435 \u043d\u043e\u0432\u043e\u0441\u0442\u0438 \u043e \u043a\u043e\u0441\u043c\u043e\u0441\u0435",
        "\u8bf7\u5e2e\u6211\u5728\u7f51\u4e0a\u641c\u7d22\u6700\u65b0\u7684AI\u65b0\u95fb",
        "\u6771\u4eac\u306e\u660e\u65e5\u306e\u5929\u6c17\u4e88\u5831\u3092\u8abf\u3079\u3066\u304f\u3060\u3055\u3044",
        "\uc624\ub298 \uc11c\uc6b8 \ub0a0\uc528 \uc608\ubcf4\ub97c \uac80\uc0c9\ud574 \uc918",
        "\u0627\u0628\u062d\u062b \u0641\u064a \u0627\u0644\u0625\u0646\u062a\u0631\u0646\u062a \u0639\u0646 \u0622\u062e\u0631 \u0627\u0644\u0623\u062e\u0628\u0627\u0631 \u0627\u0644\u062a\u0642\u0646\u064a\u0629",
    ],
)
def test_non_english_web_search_grants_search_browser_and_web_search_authority(prompt: str) -> None:
    assert has_multilingual_web_intent(prompt) is True
    caps = requested_capabilities(prompt)
    assert "search_browser" in caps

    authority = create_request_authority(prompt, history=())
    assert any(grant.tool == "web_search" for grant in authority.grants)


@pytest.mark.parametrize(
    "prompt",
    [
        "Hola, \u00bfc\u00f3mo est\u00e1s hoy?",
        "Hallo, danke f\u00fcr deine Hilfe!",
        "Bonjour, peux-tu m'aider \u00e0 \u00e9crire un po\u00e8me ?",
        "\u4f60\u597d\uff0c\u8bf7\u5e2e\u6211\u5199\u4e00\u9996\u5173\u4e8e\u6625\u5929\u7684\u8bd7",
        "\u3053\u3093\u306b\u3061\u306f\u3001\u5143\u6c17\u3067\u3059\u304b\uff1f",
        "\uc548\ub155\ud558\uc138\uc694, \uc624\ub298 \uae30\ubd84\uc774 \uc5b4\ub54c\uc694?",
    ],
)
def test_non_english_plain_chat_does_not_falsely_trigger_web_intent(prompt: str) -> None:
    assert has_multilingual_web_intent(prompt) is False
    assert "search_browser" not in requested_capabilities(prompt)
