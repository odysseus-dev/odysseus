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
# We also drop their setup_requires (cython, numpy and, for basicsr, torch).
# Those are only needed for the optional CUDA ops, which are compiled only when
# BASICSR_EXT=True, but setuptools' PEP 517 backend reports setup_requires as
# build requirements. pip then installs them into its isolated build env, which
# downloads the full CUDA torch stack (several GB) just to package three
# pure-Python wheels, and starves the parallel runtime stage's network requests.
#
# Usage: build-realesrgan-wheels.sh [OUTPUT_DIR]   (default: /wheels)
set -euo pipefail

OUT="${1:-/wheels}"
mkdir -p "$OUT"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cd "$work"

# Pinned to the versions Real-ESRGAN 0.3.0 resolves to.
SPECS="basicsr==1.4.2 gfpgan==1.3.8 facexlib==0.3.0"

for spec in $SPECS; do
  name="${spec%%==*}"
  ver="${spec##*==}"
  # pip download builds metadata (and trips the same bug), so fetch the raw
  # sdist URL from the PyPI JSON API instead.  Use curl with retries to
  # survive transient TLS / network failures that urllib cannot recover from.
  metadata="${name}-${ver}.json"
  curl --fail --silent --show-error --location \
    --retry 8 \
    --retry-delay 3 \
    --retry-max-time 120 \
    --retry-all-errors \
    "https://pypi.org/pypi/${name}/${ver}/json" \
    -o "$metadata"

  url="$(python - "$metadata" <<'PY'
import json, sys

with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)

for item in data["urls"]:
    if item["packagetype"] == "sdist":
        print(item["url"])
        break
else:
    raise SystemExit("no sdist found")
PY
  )"
  rm -f "$metadata"

  echo ">> fetching ${name} ${ver}: ${url}"
  curl --fail --silent --show-error --location \
    --retry 8 \
    --retry-delay 3 \
    --retry-max-time 120 \
    --retry-all-errors \
    "$url" \
    -o "${name}.tar.gz"
  tar xzf "${name}.tar.gz"
  rm -f "${name}.tar.gz"
done

echo ">> patching get_version() and dropping setup_requires"
python - <<'PY'
import pathlib
import re
old_exec = "exec(compile(f.read(), version_file, 'exec'))"
new_exec = "_ver_ns = {}\n        exec(compile(f.read(), version_file, 'exec'), _ver_ns)"
old_ret = "return locals()['__version__']"
new_ret = "return _ver_ns['__version__']"
setup_requires = re.compile(r"^[ \t]*setup_requires=\[[^\]]*\],[ \t]*\n", re.MULTILINE)
patched = 0
for setup in pathlib.Path(".").glob("*/setup.py"):
    s = setup.read_text()
    if old_exec in s and old_ret in s:
        s = s.replace(old_exec, new_exec).replace(old_ret, new_ret)
        s, dropped = setup_requires.subn("", s)
        assert dropped == 1, f"{setup}: expected one setup_requires line, found {dropped}"
        setup.write_text(s)
        print("   patched", setup)
        patched += 1
assert patched == 3, f"expected to patch 3 setup.py files, patched {patched}"
PY

echo ">> building wheels into ${OUT}"
pip wheel --no-deps -w "$OUT" ./basicsr-* ./gfpgan-* ./facexlib-*
ls -l "$OUT"
