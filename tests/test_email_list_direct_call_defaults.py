"""/api/email/list must have plain Python defaults, not Query(...) objects.

Regression test: GET /api/codex/emails calls the /api/email/list endpoint
function directly, not through FastAPI. Any argument it omitted got its
Query(...) default object, which is truthy, so date_from hit
datetime.strptime(Query(None), ...) and raised TypeError. The API-token auth
middleware then reported that as 401 "Invalid API token".
"""
import inspect

from fastapi.params import Param

from routes.codex_routes import _find_endpoint
from routes.email.email_routes import setup_email_routes


def test_email_list_defaults_are_plain_values():
    endpoint = _find_endpoint(setup_email_routes(), "GET", "/api/email/list")
    assert endpoint is not None

    leaked = sorted(
        name
        for name, param in inspect.signature(endpoint).parameters.items()
        if isinstance(param.default, Param)
    )
    assert leaked == []
