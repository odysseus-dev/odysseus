"""Regression: do_api_call must not crash when the LLM emits non-dict JSON.

do_api_call parses its tool-block `content` with json.loads and then does
args.get("integration"). json.loads succeeds on a valid JSON array or scalar
(the model emits `["GET","/x"]` or `5`), leaving args a list/int/str and
crashing every .get() with AttributeError. The guard coerces non-dict values
to {} so the call falls through to the clean "no integration matching ''"
error path instead.
"""
import asyncio
from unittest.mock import patch

import pytest

from src.tools.system import do_api_call


def _run(content):
    with patch("src.integrations.load_integrations", return_value=[]):
        return asyncio.run(do_api_call(content))


@pytest.mark.parametrize("content", ['[1, 2, 3]', '5', 'true', '"hello"', 'null'])
def test_non_dict_json_does_not_crash(content):
    # Before the guard these raised AttributeError/TypeError on args.get(...).
    result = _run(content)
    assert isinstance(result, dict)
    assert result.get("exit_code") == 1
    assert "No integration matching" in result.get("error", "")


def test_dict_json_still_resolves_integration():
    intg = {"id": "mini", "name": "Miniflux", "enabled": True}
    with (
        patch("src.integrations.load_integrations", return_value=[intg]),
        patch("src.integrations.execute_api_call",
              return_value={"exit_code": 0, "output": "ok"}) as ex,
    ):
        result = asyncio.run(do_api_call('{"integration": "mini", "method": "GET", "path": "/v1/feeds"}'))
    assert result["exit_code"] == 0
    ex.assert_called_once()
    assert ex.call_args.args[0] == "mini"


def test_line_based_fallback_still_works():
    intg = {"id": "mini", "name": "Miniflux", "enabled": True}
    with (
        patch("src.integrations.load_integrations", return_value=[intg]),
        patch("src.integrations.execute_api_call",
              return_value={"exit_code": 0, "output": "ok"}) as ex,
    ):
        # Not JSON -> line-based parse: integration on line 1, "METHOD path" on line 2.
        result = asyncio.run(do_api_call("Miniflux\nGET /v1/feeds"))
    assert result["exit_code"] == 0
    assert ex.call_args.args[1] == "GET"
    assert ex.call_args.args[2] == "/v1/feeds"
