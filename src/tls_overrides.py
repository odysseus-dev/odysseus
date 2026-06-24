"""Extended TLS trust store for private-CA outbound endpoints.

Some endpoints Odysseus talks to are served over TLS certificates signed by a
private root CA that is not part of the standard system / certifi bundle:

  - LLM providers (`LLM_CA_BUNDLE`):
      - GigaChat (Sber) uses the Russian Trusted Root CA, not bundled with
        OpenSSL / certifi / system trust on most non-Russian installs. The
        chain looks self-signed to Python and the endpoint is marked offline
        with `CERTIFICATE_VERIFY_FAILED: self-signed certificate in
        certificate chain` (see issue #722).
      - On-premise enterprise LLM gateways often present a corporate CA that
        has not been imported into the runtime's trust store.
  - Self-hosted SearXNG (`SEARXNG_CA_BUNDLE`):
      - A LAN SearXNG behind a reverse proxy (Caddy / nginx) terminating TLS
        with a self-signed or private-CA cert. `SEARXNG_INSTANCE` is meant to
        point at exactly this, but httpx's default context rejects it with
        `CERTIFICATE_VERIFY_FAILED` (see issue #4782).

Operators point the relevant env var at a PEM file containing the extra CA
cert(s) (for a bare self-signed cert, the cert file itself works). The default
system / certifi trust store is loaded first, then the operator's PEM is
layered on top, so verification still happens — the trust set just gets
larger. We deliberately do not provide a "verify=off" knob: weakening
verification globally (or per-host) would expose those endpoints to MITM, and
the operator-supplied bundle is the correct fix for legitimate private-CA
endpoints.

Example (GigaChat):
    # Sber publishes the chain at
    # https://www.gosuslugi.ru/crt/rootca_ssl_rsa2022.cer
    # Convert to PEM and point the env var at it.
    LLM_CA_BUNDLE=/etc/odysseus/ca/russian-trusted-root.pem

Example (self-hosted SearXNG behind a private CA):
    SEARXNG_CA_BUNDLE=/etc/odysseus/ca/homelab-root.pem

Scope:
    Each helper is consumed by a small, fixed set of call sites that reach the
    matching endpoint, and nothing else:
      - `llm_verify()`     -> the shared async client in `src/llm_core.py` and
                              the endpoint probes in `routes/model_routes.py`.
      - `searxng_verify()` -> the self-hosted SearXNG requests in
                              `services/search/providers.py`.
    Neither override is threaded into web_fetch, the other (public) search
    providers, gallery downloads, embeddings, webhook delivery, or anything
    else that hits arbitrary URLs, and neither affects the app's own
    browser-facing TLS. Those boundaries are pinned by
    `tests/test_tls_overrides_scope.py` — extending either requires updating
    the allowlist there with a written justification.
"""

import logging
import os
import ssl
from typing import Optional

logger = logging.getLogger(__name__)


def _bundle_path_from_env(var: str) -> Optional[str]:
    """Read an optional CA-bundle path from an env var (blank/unset -> None)."""
    return (os.environ.get(var) or "").strip() or None


def _build_ssl_context(bundle_path: Optional[str], env_name: str) -> Optional[ssl.SSLContext]:
    """Build an SSLContext that uses the default trust store and ALSO trusts
    the operator-supplied PEM bundle at ``bundle_path``. Returns None when no
    bundle is configured (or it cannot be loaded), so callers fall through to
    httpx's default verify=True. ``env_name`` is used only for log messages."""
    if not bundle_path:
        return None
    if not os.path.isfile(bundle_path):
        logger.warning(
            "%s points at %r but the file does not exist; "
            "falling back to the default trust store.",
            env_name, bundle_path,
        )
        return None
    ctx = ssl.create_default_context()
    try:
        ctx.load_verify_locations(cafile=bundle_path)
    except (ssl.SSLError, OSError) as e:
        logger.warning(
            "%s=%r failed to load (%s); falling back to the "
            "default trust store.",
            env_name, bundle_path, e,
        )
        return None
    logger.info(
        "Loaded extra CA bundle %r (%s) on top of the default trust store.",
        bundle_path, env_name,
    )
    return ctx


# Resolved once at import time. The httpx clients that consume these are
# long-lived (process-wide), so editing the env vars requires a restart —
# matching the existing semantics of LLM_HOST, SEARXNG_INSTANCE, etc.
_LLM_SSL_CONTEXT: Optional[ssl.SSLContext] = _build_ssl_context(
    _bundle_path_from_env("LLM_CA_BUNDLE"), "LLM_CA_BUNDLE"
)
_SEARXNG_SSL_CONTEXT: Optional[ssl.SSLContext] = _build_ssl_context(
    _bundle_path_from_env("SEARXNG_CA_BUNDLE"), "SEARXNG_CA_BUNDLE"
)


def llm_verify():
    """Return the value to pass as `verify=` on httpx.get / httpx.Client /
    httpx.AsyncClient for LLM provider requests. Returns the extended-trust
    SSLContext when LLM_CA_BUNDLE is set and loaded; otherwise True (httpx
    default — system / certifi bundle, verification fully on)."""
    return _LLM_SSL_CONTEXT if _LLM_SSL_CONTEXT is not None else True


def searxng_verify():
    """Return the value to pass as `verify=` on httpx requests to a self-hosted
    SearXNG instance. Returns the extended-trust SSLContext when
    SEARXNG_CA_BUNDLE is set and loaded; otherwise True (httpx default).

    Scoped to self-hosted SearXNG (`services/search/providers.py`); the other
    search providers (Brave / DuckDuckGo / Google PSE) are public HTTPS with
    valid certs and keep the default trust store. Like LLM_CA_BUNDLE this only
    EXTENDS trust — there is no verify=off switch."""
    return _SEARXNG_SSL_CONTEXT if _SEARXNG_SSL_CONTEXT is not None else True
