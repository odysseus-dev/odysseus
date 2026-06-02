import sys
# conftest.py stubs src.database with a fake module; webhook_manager imports
# from it, so drop the stub here to load the real module under test.
for mod_name in ["src.database", "core.database"]:
    _mod = sys.modules.get(mod_name)
    if _mod is not None and not getattr(_mod, "__file__", None):
        sys.modules.pop(mod_name, None)

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
