import sys
import types
# conftest.py stubs src.database with a fake module; webhook_manager imports
# from it, so drop the stub here to load the real module under test.
for name in ("src.database", "src.webhook_manager"):
    sys.modules.pop(name, None)

core_db = sys.modules.get("core.database")
if core_db is not None:
    module_file = getattr(core_db, "__file__", None)
    module_all = getattr(core_db, "__all__", [])
    if (
        not isinstance(core_db, types.ModuleType)
        or not isinstance(module_file, str)
        or not isinstance(module_all, (list, tuple, set))
        or not all(isinstance(item, str) for item in module_all)
    ):
        sys.modules.pop("core.database", None)
        if "core" in sys.modules and hasattr(sys.modules["core"], "database"):
            delattr(sys.modules["core"], "database")
        if "core" in sys.modules and not getattr(sys.modules["core"], "__file__", None):
            sys.modules.pop("core", None)

import pytest
from src.webhook_manager import validate_webhook_url


def test_webhook_url_ssrf_mitigation():
    # SSRF bypasses that must be rejected, including IPv6 unspecified and
    # IPv4-mapped IPv6 (loopback + cloud metadata).
    private_urls = [
        "http://[::]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[::ffff:169.254.169.254]/",
        "http://127.0.0.1/",
        "http://0.0.0.0/",
    ]
    for url in private_urls:
        with pytest.raises(ValueError) as exc:
            validate_webhook_url(url)
        assert "private/internal addresses" in str(exc.value)

    # A clearly public IP literal must still be accepted.
    public_url = "http://93.184.216.34/"
    assert validate_webhook_url(public_url) == public_url
