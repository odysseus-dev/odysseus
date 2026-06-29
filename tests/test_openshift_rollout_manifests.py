from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
OPENSHIFT = REPO / "deploy" / "openshift"


def _read(relative: str) -> str:
    return (OPENSHIFT / relative).read_text(encoding="utf-8")


def test_openshift_manifests_keep_llm_ca_scoped():
    """OpenShift deploys must not make private LLM CA trust process-wide."""
    deploy_text = "\n".join(path.read_text(encoding="utf-8") for path in OPENSHIFT.rglob("*.yaml"))

    assert "SSL_CERT_FILE" not in deploy_text
    assert "REQUESTS_CA_BUNDLE" not in deploy_text

    example_patch = _read("overlays/example/odysseus-example-env.patch.yaml")
    assert "LLM_CA_BUNDLE" in example_patch
    assert "value: /etc/odysseus/ca/router-ca.crt" in example_patch
    assert "key: router-ca.crt" in example_patch
    assert "path: router-ca.crt" in example_patch


def test_openshift_base_wires_readiness_and_search():
    odysseus = _read("base/odysseus.yaml")
    searxng = _read("base/searxng.yaml")

    assert "path: /api/ready" in odysseus
    assert "name: SEARXNG_INSTANCE" in odysseus
    assert "value: http://searxng:8080" in odysseus
    assert "docker.io/searxng/searxng:2026.5.31-7159b8aed" in searxng
    assert "formats:" in searxng
    assert "- json" in searxng


def test_openshift_example_overlay_uses_placeholders():
    rendered_inputs = "\n".join(
        _read(path.relative_to(OPENSHIFT).as_posix())
        for path in (OPENSHIFT / "overlays" / "example").glob("*.yaml")
    )

    assert "odysseus-example" in rendered_inputs
    assert "odysseus.apps.example.com" in rendered_inputs
    assert "replace-me-rwo-storage-class" in rendered_inputs
    assert "router-ca.crt" in rendered_inputs


def test_readiness_endpoint_is_auth_exempt():
    app_py = (REPO / "app.py").read_text(encoding="utf-8")

    exact_start = app_py.index("AUTH_EXEMPT_EXACT = {")
    exact_end = app_py.index("}", exact_start)
    exact_block = app_py[exact_start:exact_end]
    assert '"/api/ready"' in exact_block
