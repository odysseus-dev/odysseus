#!/usr/bin/env bash
# Build patched wheels for Real-ESRGAN's unmaintained dependencies.
#
# basicsr / gfpgan / facexlib (xinntao, last released 2022) read their version
# in setup.py with:
#
#     exec(compile(f.read(), version_file, 'exec'))
#     return locals()['__version__']
#
# Python 3.13+ implements PEP 667: locals() inside a function returns an
# independent snapshot that exec() can no longer mutate, so the read raises
# `KeyError: '__version__'` and the sdist build fails. That is why the Cookbook
# "install realesrgan" button dies on the python:3.14 image. The packages have
# no fixed release, so we patch get_version() to exec into an explicit namespace
# dict (works on every Python) and build wheels from the patched source.
#
# basicsr also declares `torch` in setup_requires. With setuptools' legacy
# installer that triggers a ~2GB PyTorch download (and network timeouts) while
# building the wheel, even though torch is not needed at build time. We remove
# ONLY torch from setup_requires and build with --no-build-isolation, so the
# remaining build requirements (cython, numpy) must be preinstalled below.
#
# Usage: build-realesrgan-wheels.sh [OUTPUT_DIR]   (default: /wheels)
set -euo pipefail

OUT="${1:-/wheels}"
mkdir -p "$OUT"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cd "$work"

# Install legacy build requirements before wheel generation.
# numpy/cython are needed here because we build with --no-build-isolation,
# so nothing else will supply the packages left in setup_requires.
pip install --no-cache-dir "setuptools<70" wheel "cython<3.0" numpy

# Pinned to the versions Real-ESRGAN 0.3.0 resolves to.
SPECS="basicsr==1.4.2 gfpgan==1.3.8 facexlib==0.3.0"

for spec in $SPECS; do
  name="${spec%%==*}"
  ver="${spec##*==}"
  # pip download builds metadata (and trips the same bug), so fetch the raw
  # sdist URL from the PyPI JSON API instead.
  url="$(python - "$name" "$ver" <<'PY'
import json, sys, urllib.request
name, ver = sys.argv[1], sys.argv[2]
data = json.load(urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{ver}/json"))
for f in data["urls"]:
    if f["packagetype"] == "sdist":
        print(f["url"]); break
else:
    sys.exit(f"no sdist found for {name}=={ver}")
PY
)"
  echo ">> fetching ${name} ${ver}: ${url}"
  curl -fsSL "$url" -o "${name}.tar.gz"
  tar xzf "${name}.tar.gz"
done

echo ">> patching get_version() and removing torch from setup_requires"
python - <<'PY'
import pathlib, re

old_exec = "exec(compile(f.read(), version_file, 'exec'))"
new_exec = "_ver_ns = {}\n        exec(compile(f.read(), version_file, 'exec'), _ver_ns)"
old_ret = "return locals()['__version__']"
new_ret = "return _ver_ns['__version__']"


def drop_torch(match):
    """Rewrite setup_requires=[...] keeping everything except torch."""
    items = re.findall(r"""['"]([^'"]+)['"]""", match.group(1))
    kept = [i for i in items if not re.match(r"\s*torch\b", i, re.I)]
    return "setup_requires=" + repr(kept)


patched = 0
for setup in pathlib.Path(".").glob("*/setup.py"):
    s = setup.read_text()
    if old_exec in s and old_ret in s:
        s = s.replace(old_exec, new_exec).replace(old_ret, new_ret)
        # Drop only torch (avoids the huge download); keep cython/numpy etc.
        s = re.sub(r"setup_requires\s*=\s*\[([^\]]*)\]", drop_torch, s)
        setup.write_text(s)
        print("   patched", setup)
        patched += 1
assert patched == 3, f"expected to patch 3 setup.py files, patched {patched}"
PY

echo ">> building wheels into ${OUT}"
pip wheel --no-build-isolation --no-deps -w "$OUT" ./basicsr-* ./gfpgan-* ./facexlib-*
ls -l "$OUT"
