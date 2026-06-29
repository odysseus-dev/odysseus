"""Regression: compare vote timestamps must not call datetime.utcnow() (#1116)."""

import inspect

import routes.compare_routes as cr


def test_compare_routes_does_not_reference_utcnow():
    source = inspect.getsource(cr)
    assert "datetime.utcnow()" not in source