"""Extended TLS trust store for private-CA LLM providers.

Some upstream LLM providers serve their API over TLS certificates that are
signed by a private root CA which is not part of the standard system bundle:

  - GigaChat (Sber) uses the Russian Trusted Root CA, not bundled with
    OpenSSL / certifi / system trust on most non-Russian installs. The
    chain looks self-signed to Python and the endpoint is marked offline
    with `CERTIFICATE_VERIFY_FAILED: self-signed certificate in
    certificate chain` (see issue #722).
  - On-premise enterprise LLM gateways often present a corporate CA that
    has not been imported into the runtime's trust store.

Operators point `LLM_CA_BUNDLE` at a PEM file containing the extra CA
cert(s). The default system / certifi trust store is loaded first, then
the operator's PEM is layered on top, so verification still happens —
the trust set just gets larger. We deliberately do not provide a
"verify=off" knob: weakening verification globally (or per-host) would
expose those endpoints to MITM, and the operator-supplied bundle is the
correct fix for legitimate private-CA providers.

Example (GigaChat):
    # Sber publishes the chain at
    # https://www.gosuslugi.ru/crt/rootca_ssl_rsa2022.cer
    # Convert to PEM and point the env var at it.
    LLM_CA_BUNDLE=/etc/odysseus/ca/russian-trusted-root.pem

Scope:
    `llm_verify()` is intentionally consumed by only two call sites — the
    shared async client in `src/llm_core.py` and the endpoint probes in
    `routes/model_routes.py`. Both reach LLM provider URLs. The override
    is NOT threaded into web_fetch, search providers, gallery downloads,
    embeddings, webhook delivery, or anything else that hits arbitrary
    URLs, and it does NOT affect the app's own browser-facing TLS. That
    boundary is pinned by `tests/test_tls_overrides_scope.py` — extending
    it requires updating the allowlist there with a written justification.
"""

import logging
import os
import ssl
from typing import Optional

logger = logging.getLogger(__name__)


_extra_bundle_path: Optional[str] = (os.environ.get("LLM_CA_BUNDLE") or "").strip() or None


def _build_ssl_context() -> Optional[ssl.SSLContext]:
    """Build an SSLContext that uses the default trust store and ALSO trusts
    the operator-supplied PEM bundle. Returns None when no extra bundle is
    configured, so callers fall through to httpx's default verify=True."""
    if not _extra_bundle_path:
        return None
    if not os.path.isfile(_extra_bundle_path):
        logger.warning(
            "LLM_CA_BUNDLE points at %r but the file does not exist; "
            "falling back to the default trust store.",
            _extra_bundle_path,
        )
        return None
    ctx = ssl.create_default_context()
    try:
        ctx.load_verify_locations(cafile=_extra_bundle_path)
    except (ssl.SSLError, OSError) as e:
        logger.warning(
            "LLM_CA_BUNDLE=%r failed to load (%s); falling back to the "
            "default trust store.",
            _extra_bundle_path, e,
        )
        return None
    logger.info(
        "Loaded extra CA bundle %r on top of the default trust store.",
        _extra_bundle_path,
    )
    return ctx


# Resolved once at import time. The httpx clients in src/llm_core.py are
# long-lived (process-wide), so editing LLM_CA_BUNDLE requires a restart —
# matching the existing semantics of LLM_HOST, SEARXNG_INSTANCE, etc.
_SHARED_SSL_CONTEXT: Optional[ssl.SSLContext] = _build_ssl_context()


def llm_verify():
    """Return the value to pass as `verify=` on httpx.get / httpx.Client /
    httpx.AsyncClient. Returns the extended-trust SSLContext when
    LLM_CA_BUNDLE is set and loaded; otherwise True (httpx default — system
    / certifi bundle, verification fully on)."""
    return _SHARED_SSL_CONTEXT if _SHARED_SSL_CONTEXT is not None else True
