import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _module_identity(source: str, module: str) -> str:
    match = re.search(
        rf"(?:/static/|\./)js/{re.escape(module)}\.js(?:\?v=([A-Za-z0-9_-]+))?",
        source,
    )
    assert match, f"missing {module}.js reference"
    return match.group(1) or "unversioned"


def test_app_imports_match_direct_index_module_versions():
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    for module in ("chat", "document", "chatRenderer", "gallery", "models", "settings"):
        assert _module_identity(app, module) == _module_identity(index, module)


def test_chat_module_identity_matches_service_worker_cache():
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    service_worker = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")

    identity = _module_identity(index, "chat")
    assert _module_identity(app, "chat") == identity
    assert _module_identity(service_worker, "chat") == identity


def test_local_modules_have_one_identity_across_imports_html_and_cache():
    static = ROOT / "static"
    sources = [*static.rglob("*.js"), static / "index.html"]
    identities: dict[Path, dict[str, list[str]]] = {}

    for path in sources:
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(
            r"['\"]((?:/static/|\.\.?/)[^'\"]+\.js)(?:\?v=([A-Za-z0-9_-]+))?['\"]",
            source,
        ):
            specifier, version = match.groups()
            if specifier.startswith("/static/"):
                target = static / specifier.removeprefix("/static/")
            else:
                target = path.parent / specifier
            target = target.resolve()
            identity = version or "unversioned"
            line = source.count("\n", 0, match.start()) + 1
            identities.setdefault(target, {}).setdefault(identity, []).append(
                f"{path.relative_to(ROOT)}:{line}"
            )

    conflicts = {
        str(path.relative_to(ROOT)): versions
        for path, versions in identities.items()
        if len(versions) > 1
    }
    assert not conflicts, f"local modules loaded under multiple URL identities: {conflicts}"
