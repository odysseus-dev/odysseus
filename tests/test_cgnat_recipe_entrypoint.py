"""Regression tests for SSRF / privilege-boundary fixes.

1. The agent web-fetch blocklist (src/outbound_fetch.py) must reject RFC 6598
   shared address space (100.64.0.0/10): Tailscale nodes and Alibaba Cloud's
   metadata endpoint 100.100.100.200 live there.
2. /api/cookbook/vllm-recipe must not let `..` segments in `repo` steer the
   raw.githubusercontent.com fetch to another repository, and its per-repo
   cache must stay bounded.
3. docker/entrypoint.sh chowns /app/.cache and /app/.cache/huggingface as
   root. /app is app-user owned, so those paths can be replaced by symlinks;
   the root chown must never follow them, including in the recursive
   `find | xargs chown` repairs.
"""
import asyncio
import ipaddress
import os
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

import routes.cookbook_routes as cookbook_routes
from src.outbound_fetch import _is_private_address, _public_http_url

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "docker" / "entrypoint.sh"


# ── 1. CGNAT / shared address space in the outbound fetch blocklist ─────────

@pytest.mark.parametrize(
    "addr",
    ["100.64.1.1", "100.100.100.200", "100.64.0.0", "100.127.255.255", "::ffff:100.100.100.200"],
)
def test_shared_address_space_is_private(addr):
    assert _is_private_address(ipaddress.ip_address(addr)) is True


@pytest.mark.parametrize("addr", ["100.63.255.255", "100.128.0.0", "8.8.8.8"])
def test_neighbouring_public_addresses_stay_allowed(addr):
    assert _is_private_address(ipaddress.ip_address(addr)) is False


def test_public_http_url_rejects_host_resolving_to_alibaba_metadata():
    def resolver(_host):
        return [ipaddress.ip_address("100.100.100.200")]

    assert _public_http_url("http://innocent.example/latest/meta-data", resolver=resolver) is False


# ── 2. vllm-recipe path steering and cache bound ────────────────────────────

class _FakeHttpxClient:
    calls: list[str] = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        type(self).calls.append(url)
        return SimpleNamespace(status_code=404, text="Not Found")


@pytest.fixture
def recipe_app(monkeypatch):
    _FakeHttpxClient.calls = []
    # The handler does `import httpx as _httpx` and `_httpx.Client(...)`.
    monkeypatch.setattr(httpx, "Client", _FakeHttpxClient)
    router = cookbook_routes.setup_cookbook_routes()
    app = FastAPI()
    app.include_router(router)
    endpoint = next(
        r.endpoint for r in router.routes if getattr(r, "path", "") == "/api/cookbook/vllm-recipe"
    )
    closure = dict(zip(endpoint.__code__.co_freevars, (c.cell_contents for c in endpoint.__closure__)))
    return app, closure["_vllm_recipe_cache"]


async def _get_many(app, repos, after_each=None):
    transport = httpx.ASGITransport(app=app)
    out = []
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for repo in repos:
            resp = await client.get("/api/cookbook/vllm-recipe", params={"repo": repo})
            assert resp.status_code == 200
            out.append(resp.json())
            if after_each:
                after_each()
    return out


@pytest.mark.parametrize(
    "repo",
    [
        "x/../../../../../attacker/repo/main/payload",
        "org/../other",
        "org/model/../../x",
        "a/b/c",
        "../org/model",
        "org/..",
    ],
)
def test_vllm_recipe_rejects_path_steering_without_fetching(recipe_app, repo):
    app, cache = recipe_app
    (body,) = asyncio.run(_get_many(app, [repo]))
    assert body["exists"] is False
    assert _FakeHttpxClient.calls == []
    assert cache == {}


def test_vllm_recipe_valid_repo_still_fetches_upstream(recipe_app):
    app, cache = recipe_app
    (body,) = asyncio.run(_get_many(app, ["MiniMaxAI/MiniMax-M2"]))
    assert body == {"exists": False}
    assert _FakeHttpxClient.calls == [
        "https://raw.githubusercontent.com/vllm-project/recipes/main/models/MiniMaxAI/MiniMax-M2.yaml"
    ]
    assert "MiniMaxAI/MiniMax-M2" in cache


def test_vllm_recipe_cache_is_bounded(recipe_app):
    app, cache = recipe_app
    bound = 256
    repos = []
    for i in range(400):
        repos.append(f"org{i}/model-{i}")  # valid id, upstream 404 -> cached
        repos.append(f"org{i}/../evil-{i}")  # invalid id, never cached
    sizes = []
    asyncio.run(_get_many(app, repos, after_each=lambda: sizes.append(len(cache))))
    assert max(sizes) <= bound
    assert len(cache) == bound
    assert "org399/model-399" in cache  # newest kept
    assert "org0/model-0" not in cache  # oldest evicted
    assert all(".." not in key for key in cache)
    assert len(_FakeHttpxClient.calls) == 400


# ── 3. entrypoint chown must not follow symlinks ────────────────────────────

def _entrypoint_text():
    return ENTRYPOINT.read_text(encoding="utf-8")


def _function_source(name):
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", _entrypoint_text(), re.M | re.S)
    assert match, f"docker/entrypoint.sh must define {name}()"
    return match.group(0)


def _helper_source():
    return _function_source("chown_path_no_follow")


def _run_helper(target, tmp_path, *, puid, pgid, shim_chown):
    return _run_function("chown_path_no_follow", target, tmp_path, puid=puid, pgid=pgid, shim_chown=shim_chown)


def _run_function(name, target, tmp_path, *, puid, pgid, shim_chown, app_root=None):
    source = _function_source(name)
    if app_root is not None:
        # repair_app_tree_ownership hard-codes /app; point it at a sandbox.
        source = source.replace("/app", str(app_root))
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(PUID=str(puid), PGID=str(pgid))
    if shim_chown:
        shim_dir = tmp_path / "shim"
        shim_dir.mkdir(exist_ok=True)
        log = tmp_path / "chown.log"
        shim = shim_dir / "chown"
        shim.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CHOWN_LOG"\n', encoding="utf-8")
        shim.chmod(0o755)
        env.update(PATH=f"{shim_dir}{os.pathsep}{env.get('PATH', '')}", CHOWN_LOG=str(log))
    script = source + f'{name} "$1"\n'
    return subprocess.run(
        ["sh", "-c", script, "sh", str(target)],
        env=env, capture_output=True, text=True, timeout=10, check=True,
    )


def _shim_calls(tmp_path):
    log = tmp_path / "chown.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def test_entrypoint_is_valid_posix_sh():
    assert _entrypoint_text().startswith("#!/bin/sh\n")
    subprocess.run(["sh", "-n", str(ENTRYPOINT)], check=True, timeout=10)


def test_entrypoint_direct_chowns_go_through_no_follow_helper():
    text = _entrypoint_text()
    assert re.search(r"^chown_path_no_follow /app/\.cache$", text, re.M)
    assert re.search(r"^chown_path_no_follow /app/\.cache/huggingface$", text, re.M)
    assert re.search(r"^\s+chown_path_no_follow \"\$dir\"$", text, re.M)
    direct = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("chown ")]
    assert direct == ['chown -h "$PUID:$PGID" "$target" 2>/dev/null || true']
    helper = _helper_source()
    assert '[ -L "$target" ]' in helper
    assert '[ -L "${target%/*}" ]' in helper


def test_helper_skips_symlinked_path(tmp_path):
    (tmp_path / "etc").mkdir()
    link = tmp_path / "cache"
    link.symlink_to(tmp_path / "etc")
    result = _run_helper(link, tmp_path, puid=4242, pgid=4242, shim_chown=True)
    assert _shim_calls(tmp_path) == []
    assert "symlink" in result.stderr


def test_helper_skips_path_under_symlinked_parent(tmp_path):
    (tmp_path / "etc" / "huggingface").mkdir(parents=True)
    (tmp_path / "cache").symlink_to(tmp_path / "etc")
    result = _run_helper(tmp_path / "cache" / "huggingface", tmp_path, puid=4242, pgid=4242, shim_chown=True)
    assert _shim_calls(tmp_path) == []
    assert "symlink" in result.stderr


def test_helper_chowns_real_directory_without_following(tmp_path):
    real = tmp_path / "cache"
    real.mkdir()
    _run_helper(real, tmp_path, puid=4242, pgid=4343, shim_chown=True)
    assert _shim_calls(tmp_path) == [f"-h 4242:4343 {real}"]


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() != 0, reason="needs root to chown")
def test_helper_as_root_never_changes_symlink_target_ownership(tmp_path):
    victim = tmp_path / "etc"
    (victim / "huggingface").mkdir(parents=True)
    os.chown(victim, 0, 0)
    os.chown(victim / "huggingface", 0, 0)
    (tmp_path / "cache").symlink_to(victim)
    real = tmp_path / "real"
    real.mkdir()

    _run_helper(tmp_path / "cache", tmp_path, puid=65534, pgid=65534, shim_chown=False)
    _run_helper(tmp_path / "cache" / "huggingface", tmp_path, puid=65534, pgid=65534, shim_chown=False)
    _run_helper(real, tmp_path, puid=65534, pgid=65534, shim_chown=False)

    assert victim.stat().st_uid == 0
    assert (victim / "huggingface").stat().st_uid == 0
    assert real.stat().st_uid == 65534


# The recursive repairs pipe `find ... -print0` into `xargs chown`. A path that
# is (or is swapped for, between the find and the chown) a symlink must have
# the link itself chowned, never its target: chown needs -h for that.

TREE_REPAIRS = ["repair_tree_ownership", "repair_app_tree_ownership"]


def _run_tree_repair(name, tmp_path, *, puid, pgid, shim_chown):
    """Run `name` over a sandbox tree holding a file and a symlink to a file
    outside it. Returns (victim, tree root)."""
    victim = tmp_path / "etc" / "shadow"
    victim.parent.mkdir()
    victim.write_text("root only", encoding="utf-8")
    root = tmp_path / ("data" if name == "repair_tree_ownership" else "app")
    root.mkdir()
    (root / "note.txt").write_text("x", encoding="utf-8")
    (root / "link").symlink_to(victim)
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        for path in (victim.parent, victim, root, root / "note.txt"):
            os.chown(path, 0, 0)
        os.lchown(root / "link", 0, 0)
    if name == "repair_tree_ownership":
        target, app_root = root, None
    else:
        target, app_root = "", root
    _run_function(name, target, tmp_path, puid=puid, pgid=pgid, shim_chown=shim_chown, app_root=app_root)
    return victim, root


@pytest.mark.parametrize("name", TREE_REPAIRS)
def test_tree_repair_chowns_with_no_dereference(tmp_path, name):
    _victim, root = _run_tree_repair(name, tmp_path, puid=4242, pgid=4343, shim_chown=True)
    calls = _shim_calls(tmp_path)
    assert calls, f"{name} never ran chown"
    assert all(call.startswith("-h 4242:4343 ") for call in calls), calls
    chowned = " ".join(calls)
    assert str(root / "link") in chowned
    assert str(root / "note.txt") in chowned


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() != 0, reason="needs root to chown")
@pytest.mark.parametrize("name", TREE_REPAIRS)
def test_tree_repair_as_root_never_changes_symlink_target_ownership(tmp_path, name):
    victim, root = _run_tree_repair(name, tmp_path, puid=65534, pgid=65534, shim_chown=False)
    assert victim.stat().st_uid == 0
    assert (root / "note.txt").stat().st_uid == 65534
    assert (root / "link").lstat().st_uid == 65534
