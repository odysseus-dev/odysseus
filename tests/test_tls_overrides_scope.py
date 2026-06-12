"""Scope tests for src/tls_overrides.

#722 / PR #769 added an opt-in extra CA bundle (LLM_CA_BUNDLE) for
private-CA LLM providers. The whole point is that the override stays
SCOPED — it must extend trust for the intended outbound LLM provider
requests only, and never:

  - touch arbitrary URL fetching (web_fetch, document downloads, generic
    httpx.get from any other module),
  - touch browser-facing TLS (anything our app serves over HTTPS),
  - weaken httpx's process-wide defaults,
  - silently disable certificate verification.

These tests prove that. They enumerate the call sites of `llm_verify()`
in the source tree and assert they match an allowlist; they verify the
override module itself never reaches for the well-known "skip TLS
verification" knobs; and they pin the safe default (verify=True) when
LLM_CA_BUNDLE is unset.

If a future change threads `llm_verify()` into a non-LLM HTTP path, the
first test fails and the contributor either has to justify the new
caller (and add it to ALLOWED_CALLERS with a comment) or revert. That
keeps the security-sensitive helper hard to misuse.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


# Files that legitimately need llm_verify() applied to their outbound
# httpx calls because the URL is an LLM provider's API. Every caller here
# is a discrete LLM HTTP entry point and intentional. Any addition must
# come with its own justification in code review.
ALLOWED_CALLERS = frozenset({
    "src/llm_core.py",          # shared AsyncClient used by stream_llm
    "routes/model_routes.py",   # _probe_endpoint + _ping_endpoint
})


def _grep_files(pattern: str) -> set[str]:
    """Return the set of repo-relative .py file paths whose body matches
    `pattern`. Skips tests, the override module itself, and worktree
    scratch dirs."""
    rx = re.compile(pattern)
    hits: set[str] = set()
    for path in REPO.rglob("*.py"):
        rel = path.relative_to(REPO).as_posix()
        if rel.startswith("tests/"):
            continue
        if rel == "src/tls_overrides.py":  # definition site, not a caller
            continue
        if rel.startswith(".claude/") or "/.claude/" in rel:
            continue
        try:
            body = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if rx.search(body):
            hits.add(rel)
    return hits


def test_llm_verify_only_used_in_allowlisted_files():
    """llm_verify() must only be consumed by the LLM provider HTTP path.

    The extra CA bundle is scoped to the two known LLM HTTP entry points.
    If a future PR threads llm_verify() into web_fetch, search providers,
    embeddings, gallery downloads, webhook delivery, or any other
    arbitrary-URL caller, that's a scope expansion and a security review.
    Adding a file to ALLOWED_CALLERS requires a written justification.
    """
    callers = _grep_files(r"\bllm_verify\s*\(")
    unexpected = callers - ALLOWED_CALLERS
    missing = ALLOWED_CALLERS - callers
    assert not unexpected, (
        f"llm_verify() called from unexpected file(s): {sorted(unexpected)}. "
        f"Expected scope: {sorted(ALLOWED_CALLERS)}. If the new caller is an "
        "LLM provider HTTP entry point, add it to ALLOWED_CALLERS with a "
        "comment; if it's not, do not thread the extra CA bundle into it."
    )
    assert not missing, (
        f"llm_verify() no longer called from {sorted(missing)} — the "
        "extra CA bundle integration regressed or the allowlist is stale."
    )


def test_tls_overrides_does_not_weaken_global_tls():
    """src/tls_overrides must never reach for a TLS-weakening knob.

    Several common ways to silently weaken TLS in Python:
      - ssl._create_default_https_context = ssl._create_unverified_context
      - ssl._create_unverified_context (used as a default)
      - urllib3.disable_warnings(...)
      - httpx.AsyncClient(verify=False) (anywhere — must stay verify=True
        or an SSLContext)
      - requests.packages.urllib3.disable_warnings(...)

    The override module must only EXTEND trust by loading an additional
    bundle into an ssl.SSLContext built on top of the system default. It
    must never silently disable verification.
    """
    body = (REPO / "src" / "tls_overrides.py").read_text(encoding="utf-8")
    forbidden = [
        r"_create_default_https_context\s*=",
        r"_create_unverified_context",
        r"disable_warnings",
        r"verify\s*=\s*False",
    ]
    for pat in forbidden:
        assert not re.search(pat, body), (
            f"src/tls_overrides.py contains forbidden pattern {pat!r}. "
            "The extra CA bundle must only ADD trust, never weaken it."
        )


def test_llm_verify_default_is_true_when_env_unset():
    """When LLM_CA_BUNDLE is unset, llm_verify() must return True so httpx
    falls through to its built-in trust store. This is the safe default —
    operators have to opt in to get any change at all."""
    os.environ.pop("LLM_CA_BUNDLE", None)
    import importlib

    import src.tls_overrides as mod
    importlib.reload(mod)
    assert mod.llm_verify() is True, (
        f"Default llm_verify() must be True (httpx built-in trust store); "
        f"got {mod.llm_verify()!r}. An accidental non-True default would "
        "turn an opt-in extension into a process-wide change."
    )


def test_llm_verify_falls_back_to_true_for_missing_bundle_file():
    """Pointing LLM_CA_BUNDLE at a non-existent path must NOT raise and
    must fall back to verify=True (system trust). A misconfigured env var
    on a deploy box should never produce a silently TLS-disabled process."""
    os.environ["LLM_CA_BUNDLE"] = "/nonexistent/path/extra-roots.pem"
    try:
        import importlib

        import src.tls_overrides as mod
        importlib.reload(mod)
        assert mod.llm_verify() is True
    finally:
        os.environ.pop("LLM_CA_BUNDLE", None)
