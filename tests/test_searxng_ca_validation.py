"""A network failure must not be mistaken for rejection of an incident chain."""

import asyncio
import importlib.util
import io
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


ROOT = Path(__file__).resolve().parents[1]
INCIDENT = json.loads((ROOT / "tests/fixtures/ca-trust/incident.json").read_text())


@pytest.fixture
def validator():
    checker = ModuleType("check_ca_trust")
    checker.check_bundle = lambda path: None
    spec = importlib.util.spec_from_file_location(
        "searxng_validation", ROOT / "docker/searxng/validate_image.py"
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"check_ca_trust": checker}):
        spec.loader.exec_module(module)
    module.__file__ = str(ROOT / "tests/fixtures/ca-trust/validate_image.py")
    return module


class RequestFailure(Exception):
    def __init__(self, code):
        super().__init__(f"curl error {code}")
        self.code = code


@pytest.mark.parametrize("code", [60, 6, 28, 35, None])
def test_live_probe_requires_certificate_errors_for_every_host(validator, code):
    hosts = []

    async def get(url, **kwargs):
        if url == "https://pypi.org/simple/certifi/":
            return SimpleNamespace(status_code=200)
        hosts.append(url.removeprefix("https://").removesuffix("/"))
        assert kwargs["allow_redirects"] is False
        if code is not None:
            raise RequestFailure(code)
        return SimpleNamespace(status_code=200)

    client = SimpleNamespace(get=get, aclose=AsyncMock())
    factory = ModuleType("searx.network.client")

    def new_client(**kwargs):
        assert kwargs["verify"] is True
        return client

    factory.new_client = new_client
    errors = ModuleType("curl_cffi.requests.exceptions")
    errors.RequestException = RequestFailure
    modules = {"searx.network.client": factory, "curl_cffi.requests.exceptions": errors}
    with patch.dict(sys.modules, modules), patch.object(sys, "path", list(sys.path)):
        if code == 60:
            asyncio.run(validator.check_https())
            assert set(hosts) == set(INCIDENT["chains"])
        else:
            expected = "unexpectedly accepted" if code is None else "inconclusive"
            with pytest.raises(RuntimeError, match=expected):
                asyncio.run(validator.check_https())
    client.aclose.assert_awaited_once()


def test_offline_probe_rejects_openssl_execution_failure(validator):
    result = SimpleNamespace(returncode=1, stderr="openssl: unknown option", stdout="")
    with patch.dict(validator.os.environ, {"SSL_CERT_FILE": "/unused.pem", "SSL_CERT_DIR": "/unused"}), \
            patch.object(validator.subprocess, "run", return_value=result):
        with pytest.raises(RuntimeError, match="not a certificate trust rejection"):
            validator.check_chains()


@pytest.mark.parametrize("results", [[{"title": "ordinary result"}], []])
def test_running_probe_checks_existing_service_without_repair_or_second_boot(validator, tmp_path, results):
    certificate = tmp_path / "01234567.0"
    certificate.write_text("certificate double")
    (tmp_path / "unrelated").write_text("ignored")
    health = io.BytesIO(b"OK")
    health.status = 200
    search = io.BytesIO(json.dumps({"results": results}).encode())
    with patch.dict(validator.os.environ, {"SSL_CERT_DIR": str(tmp_path), "GRANIAN_PORT": "8080"}), \
            patch.object(validator.urllib.request, "build_opener") as opener, \
            patch.object(validator, "check_chains") as chains, \
            patch.object(validator, "check_bundle") as bundle, \
            patch.object(validator, "check_https", new_callable=AsyncMock) as https, \
            patch.object(validator.subprocess, "Popen") as boot:
        opener.return_value.open.side_effect = [health, search]
        if results:
            validator.check_running(live=True)
        else:
            with pytest.raises(RuntimeError, match="no results"):
                validator.check_running(live=True)
        assert opener.return_value.open.call_args_list[0].args == ("http://127.0.0.1:8080/healthz",)
        chains.assert_called_once_with()
        bundle.assert_called_once_with(certificate)
        https.assert_awaited_once()
        boot.assert_not_called()
