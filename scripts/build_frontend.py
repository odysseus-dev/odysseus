#!/usr/bin/env python3
"""Publish explicitly owned native ES modules; never run at application startup."""

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def module_path(base: Path, relative: str) -> Path:
    """Accept canonical JS paths only, contained within the declared tree."""
    if not isinstance(relative, str):
        raise ValueError("module paths must be strings")
    parts = PurePosixPath(relative)
    if (
        parts.is_absolute()
        or parts.as_posix() != relative
        or ".." in parts.parts
        or "\\" in relative
        or "?" in relative
        or "#" in relative
        or parts.suffix != ".js"
    ):
        raise ValueError(f"invalid module path: {relative!r}")
    path = base / relative
    if not path.resolve().is_relative_to(base.resolve()):
        raise ValueError(f"module path escapes its tree: {relative!r}")
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError(f"symlink in module path: {relative!r}")
    return path


def check_lf_attributes(root: Path, paths: set[Path]) -> None:
    """Require portable, repository-owned LF attributes for every mapped path.

    Ask Git in an isolated temporary repository so global attributes, local
    info/attributes and alternate Git directories cannot mask missing rules.
    Copy only attribute files at ancestors of the manifest's source/output paths.
    """
    git = shutil.which("git")
    if git is None:
        raise ValueError("Git is required for frontend publication attribute checks")
    attributes = {root / ".gitattributes"}
    for path in paths:
        for parent in path.parents:
            if parent == root:
                break
            attributes.add(parent / ".gitattributes")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    relative = sorted(path.relative_to(root).as_posix() for path in paths)
    with tempfile.TemporaryDirectory(prefix="ody-frontend-attributes-") as directory:
        target = Path(directory)
        for attribute in attributes:
            if attribute.is_symlink():
                raise ValueError(f"symlink attribute file: {attribute.relative_to(root)}")
            if attribute.is_file():
                copy = target / attribute.relative_to(root)
                copy.parent.mkdir(parents=True, exist_ok=True)
                copy.write_bytes(attribute.read_bytes())
        command = [git, "-c", f"core.attributesFile={os.devnull}", "-C", str(target)]
        try:
            subprocess.run(command + ["init", "--quiet", "--template="], env=env, check=True, capture_output=True, timeout=15)
            result = subprocess.run(
                command + ["check-attr", "-z", "--stdin", "text", "eol"],
                input=("\0".join(relative) + "\0").encode("utf-8"),
                env=env, check=True, capture_output=True, timeout=15,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            raise ValueError("Git frontend attribute check failed") from error
    fields = result.stdout.decode("utf-8").split("\0")[:-1]
    effective = {name: {} for name in relative}
    for index in range(0, len(fields), 3):
        name, attribute, value = fields[index:index + 3]
        effective[name][attribute] = value
    invalid = [
        f"{name} (text={values.get('text')}, eol={values.get('eol')})"
        for name, values in effective.items()
        if values != {"text": "set", "eol": "lf"}
    ]
    if invalid:
        raise ValueError("manifest paths require repository-owned 'text eol=lf' Git attributes: " + ", ".join(invalid))


def synchronize(root: Path = ROOT, *, check: bool = False) -> list[str]:
    """Validate the entire plan before writing. Return stale deployment paths."""
    root = root.resolve()
    source_root = root / "frontend" / "src"
    static_root = root / "static"
    manifest = json.loads((root / "frontend" / "modules.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("version") != 1 or not isinstance(manifest.get("modules"), list):
        raise ValueError("expected frontend module manifest version 1")
    if not manifest["modules"]:
        raise ValueError("module manifest must not be empty")
    sources, outputs, plan = set(), set(), []
    for entry in manifest["modules"]:
        source = module_path(source_root, entry["source"])
        output = module_path(static_root, entry["deployment"])
        if not entry["deployment"].startswith("js/"):
            raise ValueError("deployment modules must be under static/js/")
        if source in sources or output in outputs:
            raise ValueError("each source and deployment path must be unique")
        sources.add(source)
        outputs.add(output)
        content = source.read_bytes()
        if output.exists() and not output.is_file():
            raise ValueError(f"deployment path is not a file: {output}")
        plan.append((output, content))
    unlisted = set(source_root.rglob("*.js")) - sources
    if unlisted:
        raise ValueError(f"source modules missing from manifest: {sorted(map(str, unlisted))}")
    check_lf_attributes(root, sources | outputs)
    stale = []
    for output, content in plan:
        if not output.is_file() or output.read_bytes() != content:
            stale.append(output.relative_to(root).as_posix())
    if not check:
        for output, content in plan:
            if output.relative_to(root).as_posix() in stale:
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(content)
    return stale


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail on invalid attributes or stale output; write no deployment files")
    args = parser.parse_args()
    try:
        stale = synchronize(check=args.check)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, f"frontend: {error}\n")
    if args.check and stale:
        print("Stale frontend deployment modules: " + ", ".join(stale))
        print("Run python scripts/build_frontend.py and include the source and output changes together.")
        return 1
    print(f"Frontend {'check passed' if args.check else 'published'} ({len(stale)} changed modules).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
