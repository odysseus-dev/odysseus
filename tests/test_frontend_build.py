"""Exercise publication failure modes without changing the real static tree."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.helpers.cli_loader import load_script

ROOT = Path(__file__).resolve().parents[1]
build = load_script("build_frontend.py")


def project(tmp_path, entries=None):
    source = tmp_path / "frontend/src/shared/helper.js"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"export const value = 1;\n")
    manifest = {"version": 1, "modules": entries if entries is not None else [
        {"source": "shared/helper.js", "deployment": "js/helper.js"}
    ]}
    (tmp_path / "frontend/modules.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / ".gitattributes").write_text("* text=auto\nfrontend/src/** text eol=lf\nstatic/js/helper.js text eol=lf\n", encoding="utf-8")
    return source, tmp_path / "static/js/helper.js"


def test_check_detects_missing_and_stale_output_without_writing(tmp_path):
    source, output = project(tmp_path)
    assert build.synchronize(tmp_path, check=True) == ["static/js/helper.js"]
    assert not output.exists()
    assert build.synchronize(tmp_path) == ["static/js/helper.js"]
    assert output.read_bytes() == source.read_bytes()
    source.write_bytes(b"export const value = 2;\n")
    assert build.synchronize(tmp_path, check=True) == ["static/js/helper.js"]
    assert output.read_bytes() == b"export const value = 1;\n"


def test_publication_is_idempotent_and_preserves_unowned_assets(tmp_path):
    source, output = project(tmp_path)
    unowned = tmp_path / "static/js/notes.js"
    unowned.parent.mkdir(parents=True)
    unowned.write_bytes(b"legacy state owner\n")
    build.synchronize(tmp_path)
    timestamp = output.stat().st_mtime_ns
    assert build.synchronize(tmp_path) == []
    assert output.stat().st_mtime_ns == timestamp
    assert unowned.read_bytes() == b"legacy state owner\n"
    assert output.read_bytes() == source.read_bytes()


@pytest.mark.parametrize("bad", ["../outside.js", "/absolute.js", "shared/../helper.js", "shared\\helper.js", "shared/helper.js?v=1", "shared/helper.js#fragment", "shared/helper.ts"])
def test_invalid_plan_does_not_partially_publish(tmp_path, bad):
    project(tmp_path, [
        {"source": "shared/helper.js", "deployment": "js/helper.js"},
        {"source": bad, "deployment": "js/other.js"},
    ])
    with pytest.raises(ValueError, match="invalid module path"):
        build.synchronize(tmp_path)
    assert not (tmp_path / "static").exists()


def test_duplicate_outputs_and_unmapped_sources_are_rejected(tmp_path):
    source, output = project(tmp_path)
    extra = source.with_name("extra.js")
    extra.write_bytes(b"export const value = 2;\n")
    with pytest.raises(ValueError, match="missing from manifest"):
        build.synchronize(tmp_path)
    manifest_path = tmp_path / "frontend/modules.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["modules"].append({"source": "shared/extra.js", "deployment": "js/helper.js"})
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="must be unique"):
        build.synchronize(tmp_path)
    assert not output.exists()


def test_symlink_output_cannot_overwrite_another_file(tmp_path):
    _, output = project(tmp_path)
    outside = tmp_path / "outside.js"
    outside.write_bytes(b"untouched\n")
    output.parent.mkdir(parents=True)
    output.symlink_to(outside)
    with pytest.raises(ValueError, match="escapes|symlink"):
        build.synchronize(tmp_path)
    assert outside.read_bytes() == b"untouched\n"


def test_real_checkout_check_works_from_another_directory(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / "scripts/build_frontend.py"), "--check"], cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (ROOT / "frontend/src/shared/actionMenuOrder.js").read_bytes() == (ROOT / "static/js/actionMenuOrder.js").read_bytes()


def test_isolated_python_distribution_runs_without_source_or_node():
    result = subprocess.run([sys.executable, str(ROOT / "scripts/verify_frontend_runtime.py")], cwd=ROOT, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS: isolated Python lifespan" in result.stdout


@pytest.mark.parametrize("check", [False, True])
@pytest.mark.parametrize("path,rule", [
    ("frontend/src/shared/helper.js", "text"),
    ("static/js/helper.js", "text"),
    ("frontend/src/shared/helper.js", "text eol=crlf"),
    ("static/js/helper.js", "text=auto eol=lf"),
    ("static/js/helper.js", "-text eol=lf"),
])
def test_each_manifest_path_requires_lf_even_when_bytes_already_match(tmp_path, check, path, rule):
    source, output = project(tmp_path)
    output.parent.mkdir(parents=True)
    output.write_bytes(source.read_bytes())
    other = "static/js/helper.js" if path.startswith("frontend/") else "frontend/src/shared/helper.js"
    (tmp_path / ".gitattributes").write_text(f"* text=auto\n{other} text eol=lf\n{path} {rule}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="require repository-owned.*" + path):
        build.synchronize(tmp_path, check=check)
    assert output.read_bytes() == source.read_bytes()


def test_future_manifest_pair_cannot_publish_without_output_attributes(tmp_path):
    source, output = project(tmp_path)
    extra = source.with_name("future.js")
    extra.write_bytes(b"export const future = true;\n")
    manifest_file = tmp_path / "frontend/modules.json"
    manifest = json.loads(manifest_file.read_text())
    manifest["modules"].append({"source": "shared/future.js", "deployment": "js/future.js"})
    manifest_file.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="static/js/future.js"):
        build.synchronize(tmp_path)
    assert not output.exists()
    assert not (tmp_path / "static/js/future.js").exists()
    with (tmp_path / ".gitattributes").open("a", encoding="utf-8") as attributes:
        attributes.write("static/js/future.js text eol=lf\n")
    assert build.synchronize(tmp_path) == ["static/js/helper.js", "static/js/future.js"]
    assert build.synchronize(tmp_path, check=True) == []


def test_nested_git_attribute_rules_and_paths_with_spaces_are_supported(tmp_path):
    source, _ = project(tmp_path, [{"source": "shared/helper.js", "deployment": "js/feature space/helper.js"}])
    attributes = tmp_path / "static/js/feature space/.gitattributes"
    attributes.parent.mkdir(parents=True)
    attributes.write_text("*.js text eol=lf\n", encoding="utf-8")
    assert build.synchronize(tmp_path) == ["static/js/feature space/helper.js"]
    assert (attributes.parent / "helper.js").read_bytes() == source.read_bytes()
    attributes.write_text("*.js text eol=crlf\n", encoding="utf-8")
    with pytest.raises(ValueError, match="eol=crlf"):
        build.synchronize(tmp_path, check=True)


def test_global_and_local_git_settings_cannot_mask_missing_repository_rule(tmp_path, monkeypatch):
    project(tmp_path)
    (tmp_path / ".gitattributes").write_text("frontend/src/** text eol=lf\n", encoding="utf-8")
    global_attributes = tmp_path / "global-attributes"
    global_attributes.write_text("* text eol=lf\n", encoding="utf-8")
    config = tmp_path / "global-config"
    config.write_text(f'[core]\n attributesFile = "{global_attributes.as_posix()}"\n autocrlf = true\n', encoding="utf-8")
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / ".git/info/attributes").write_text("* text eol=lf\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_DIR", str(tmp_path / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path))
    with pytest.raises(ValueError, match="static/js/helper.js"):
        build.synchronize(tmp_path, check=True)


def test_attribute_enforcement_fails_clearly_when_development_git_is_missing(tmp_path, monkeypatch):
    _, output = project(tmp_path)
    monkeypatch.setattr(build.shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="Git is required for frontend publication"):
        build.synchronize(tmp_path)
    assert not output.exists()
