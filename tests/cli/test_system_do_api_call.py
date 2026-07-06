import asyncio
import sys
import types

from src.tools.system import do_api_call


def test_do_api_call_non_dict_json(monkeypatch):
    integrations = types.ModuleType("src.integrations")
    integrations.load_integrations = lambda: []
    integrations.execute_api_call = lambda *args, **kwargs: None

    monkeypatch.setitem(sys.modules, "src.integrations", integrations)

    result = asyncio.run(do_api_call("[1, 2, 3]"))

    assert result == {
        "error": "No integration matching ''. Available: none configured",
        "exit_code": 1,
    }

