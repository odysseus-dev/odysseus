"""Verify incident distrust and startup in the unmodified upstream image."""

import hashlib
import asyncio
import json
import os
from pathlib import Path
import re
import signal
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request

import certifi

from check_ca_trust import check_bundle


def check_chains():
    incident = json.loads(Path(__file__).with_name("incident.json").read_text())
    system = Path(os.environ["SSL_CERT_FILE"])
    capath = os.environ["SSL_CERT_DIR"]
    stores = [["-CAfile", str(system), "-no-CApath"],
              ["-CAfile", certifi.where(), "-no-CApath"],
              ["-no-CAfile", "-CApath", capath]]
    check_bundle(system)
    check_bundle(Path(certifi.where()))
    with tempfile.TemporaryDirectory() as directory:
        directory = Path(directory)
        for fingerprint, pem in incident["certificates"].items():
            if hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest() != fingerprint:
                raise ValueError(f"incident fixture fingerprint mismatch: {fingerprint}")
            (directory / fingerprint).write_text(pem)
        positive = directory / "affected.pem"
        positive.write_text("\n".join(incident["certificates"][fp] for fp in incident["roots"]))
        for hostname, chain in incident["chains"].items():
            untrusted = directory / "untrusted.pem"
            untrusted.write_text("\n".join(incident["certificates"][fp] for fp in chain[1:]))
            base = ["openssl", "verify", "-no-CAstore", "-attime", str(incident["verification_time"]),
                    "-purpose", "sslserver", "-verify_hostname", hostname,
                    "-x509_strict", "-partial_chain", "-trusted_first", "-untrusted", str(untrusted)]
            expectations = [(selection, False) for selection in stores]
            for selection, expected in expectations + [
                    (["-CAfile", str(positive), "-no-CApath"], True)]:
                result = subprocess.run(base + selection + [str(directory / chain[0])],
                                        capture_output=True, text=True)
                if (result.returncode == 0) != expected:
                    raise RuntimeError(f"unexpected chain result: {hostname}: {selection}: {result.stderr}")
                # Accept issuer lookup, self-signed and explicitly untrusted
                # certificate errors, rather than tool or fixture failures.
                if not expected and not re.search(r"error (?:2|18|19|20|21|27) at \d+ depth lookup", result.stderr):
                    raise RuntimeError(f"not a certificate trust rejection: {hostname}: {result.stderr}")
    print(f"{len(incident['chains'])}/{len(incident['chains'])} incident chains rejected by system PEM, hashed directory and certifi; affected controls accepted")


def check_boot(live=False):
    with tempfile.TemporaryDirectory() as directory:
        settings = Path(directory) / "settings.yml"
        settings.write_text("use_default_settings: true\nserver:\n  secret_key: offline-build-validation-only\n  limiter: false\nsearch:\n  formats: [html, json]\n")
        cache = Path(directory) / "cache"
        cache.mkdir()
        env = dict(os.environ, SEARXNG_SETTINGS_PATH=str(settings),
                   __SEARXNG_CONFIG_PATH=directory, __SEARXNG_DATA_PATH=str(cache),
                   GRANIAN_HOST="127.0.0.1", GRANIAN_PORT="18888")
        # Do not expose a port or issue an outbound search during offline builds.
        with tempfile.TemporaryFile() as log:
            process = subprocess.Popen(["/usr/local/searxng/entrypoint.sh"],
                                       cwd="/usr/local/searxng", env=env, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                healthy = False
                for _ in range(60):
                    if process.poll() is not None:
                        break
                    try:
                        with opener.open("http://127.0.0.1:18888/healthz", timeout=1) as response:
                            healthy = response.status == 200
                    except OSError:
                        pass
                    if healthy:
                        break
                    time.sleep(0.5)
                if not healthy:
                    log.seek(0)
                    raise RuntimeError("SearXNG failed boot smoke: " + log.read().decode(errors="replace")[-6000:])
                print("SearXNG loopback healthcheck passed")
                # Its root entrypoint regenerates system trust. Repeat the
                # behavioral checks after that real startup path completes.
                check_running(live=live, port=18888)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


def check_running(live=False, port=None):
    """Verify an existing service without repairing it or starting another one."""
    port = int(os.environ.get("GRANIAN_PORT", "8080")) if port is None else port
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{port}/healthz", timeout=5) as response:
        if response.status != 200:
            raise RuntimeError("SearXNG running-service healthcheck failed")
    check_chains()
    for path in Path(os.environ["SSL_CERT_DIR"]).iterdir():
        if re.fullmatch(r"[0-9a-fA-F]{8}\.[0-9]+", path.name):
            check_bundle(path)
    if live:
        asyncio.run(check_https())
        with opener.open(f"http://127.0.0.1:{port}/search?q=python+documentation&format=json", timeout=90) as search:
            payload = json.load(search)
        results = payload.get("results", [])
        if not results:
            raise RuntimeError("live search returned no results")
        print(f"SearXNG live JSON search passed: {len(results)} results")


async def check_https():
    # Exercise the actual pinned SearXNG client factory with its default CA
    # selection, rather than a different HTTP library's trust configuration.
    sys.path.insert(0, "/usr/local/searxng")
    from curl_cffi.requests.exceptions import RequestException
    from searx.network.client import new_client

    client = new_client(enable_http=False, verify=True, enable_http2=True,
                        enable_http3=False, max_connections=2, proxies={},
                        local_address=None, max_redirects=3)
    try:
        response = await client.get("https://pypi.org/simple/certifi/", timeout=30)
        if response.status_code != 200:
            raise RuntimeError(f"ordinary HTTPS failed: {response.status_code}")
        print("SearXNG actual curl client ordinary HTTPS passed with verification enabled")
        incident = json.loads(Path(__file__).with_name("incident.json").read_text())
        for hostname in incident["chains"]:
            try:
                await client.get(f"https://{hostname}/", timeout=30, allow_redirects=False)
            except RequestException as exc:
                # libcurl code 60 is peer certificate verification failure.
                # DNS failures, timeouts and handshake EOF do not prove distrust.
                if exc.code != 60:
                    raise RuntimeError(f"inconclusive incident TLS failure: {hostname}: {exc}") from exc
            else:
                raise RuntimeError(f"incident HTTPS unexpectedly accepted: {hostname}")
        print(f"SearXNG actual curl client rejects {len(incident['chains'])}/{len(incident['chains'])} incident hosts with certificate errors after startup")
    finally:
        await client.aclose()


if __name__ == "__main__":
    live = "--live" in sys.argv
    if "--running" in sys.argv:
        check_running(live=live)
    else:
        check_chains()
        check_boot(live=live)
