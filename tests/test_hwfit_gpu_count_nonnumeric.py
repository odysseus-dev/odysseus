"""GET /api/hwfit/models must not 500 on a non-numeric gpu_count.

The handler did `n = int(gpu_count)` with no guard, so `?gpu_count=abc` (or any
non-integer) raised ValueError -> HTTP 500. A malformed count is now ignored,
matching how the neighbouring gpu_group param is already parsed.
"""
from routes.hwfit_routes import setup_hwfit_routes


def _get_models():
    router = setup_hwfit_routes()
    for route in router.routes:
        if getattr(route, "path", "").endswith("/models") and "GET" in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError("hwfit /models route not found")


def test_non_numeric_gpu_count_does_not_raise():
    handler = _get_models()
    # Previously raised ValueError (HTTP 500); now degrades to a normal ranking.
    result = handler(gpu_count="abc")
    assert isinstance(result, dict)


def test_numeric_gpu_count_still_accepted():
    handler = _get_models()
    result = handler(gpu_count="0")
    assert isinstance(result, dict)
