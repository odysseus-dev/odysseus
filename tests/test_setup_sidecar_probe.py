import importlib


def test_setup_has_check_sidecars_function():
    setup = importlib.import_module("setup")
    assert hasattr(setup, "check_sidecars")
    assert callable(setup.check_sidecars)
