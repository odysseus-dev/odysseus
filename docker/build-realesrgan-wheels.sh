#!/usr/bin/env bash
# Build patched wheels for Real-ESRGAN's unmaintained dependencies.
#
# 1. Python 3.13+ (PEP 667) compatibility: patch get_version() in setup.py to
#    exec() into an explicit namespace dict instead of mutating locals().
# 2. Security remediation (basicsr/utils/dist_util.py): replace shell=True
#    subprocess.getoutput() in _init_dist_slurm with validated argv execution
#    and strict RFC 1123 hostname verification.
# 3. Security remediation (basicsr/utils/options.py): replace unsafe yaml.Loader
#    with SafeLoader subclasses, eval() with ast.literal_eval(), and exec()
#    key assignment with direct nested dictionary traversal.
# 4. Supply-chain integrity: verify pinned SHA-256 digests for every sdist.
#
# Usage: build-realesrgan-wheels.sh [OUTPUT_DIR]   (default: /wheels)
set -euo pipefail

OUT="${1:-/wheels}"
mkdir -p "$OUT"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cd "$work"

# Pinned to the versions Real-ESRGAN 0.3.0 resolves to, with verified PyPI sdist SHA-256 digests.
SPECS=(
  "basicsr==1.4.2==b89b595a87ef964cda9913b4d99380ddb6554c965577c0c10cb7b78e31301e87"
  "gfpgan==1.3.8==21618b06ce8ea6230448cb526b012004f23a9ab956b55C833F69B9FC8A60C4F9"
  "facexlib==0.3.0==7ae784a520eb52e05583e8bf9f68f77f45083239ac754d646d635017b49e7763"
)

for entry in "${SPECS[@]}"; do
  name="${entry%%==*}"
  rest="${entry#*==}"
  ver="${rest%%==*}"
  expected_sha256="${rest##*==}"
  expected_sha256="${expected_sha256,,}"

  # pip download builds metadata (and trips the same bug), so fetch the raw
  # sdist URL from the PyPI JSON API instead. Use curl with retries to
  # survive transient TLS / network failures that urllib cannot recover from.
  metadata="${name}-${ver}.json"
  curl --fail --silent --show-error --location \
    --proto '=https' --tlsv1.2 \
    --retry 8 \
    --retry-delay 3 \
    --retry-max-time 120 \
    --retry-all-errors \
    "https://pypi.org/pypi/${name}/${ver}/json" \
    -o "$metadata"

  url="$(python - "$metadata" "$name" "$ver" "$expected_sha256" <<'PY'
import json, sys

metadata_path, name, ver, expected_sha = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4].lower()
with open(metadata_path, encoding="utf-8") as f:
    data = json.load(f)

for item in data["urls"]:
    if item["packagetype"] == "sdist":
        actual_sha = item.get("digests", {}).get("sha256", "").lower()
        if actual_sha != expected_sha:
            sys.exit(f"PyPI metadata sha256 mismatch for {name}=={ver}: {actual_sha} != {expected_sha}")
        print(item["url"])
        break
else:
    raise SystemExit("no sdist found")
PY
  )"
  rm -f "$metadata"

  echo ">> fetching ${name} ${ver}: ${url}"
  curl --fail --silent --show-error --location \
    --proto '=https' --tlsv1.2 \
    --retry 8 \
    --retry-delay 3 \
    --retry-max-time 120 \
    --retry-all-errors \
    "$url" \
    -o "${name}.tar.gz"
  echo "${expected_sha256}  ${name}.tar.gz" | sha256sum -c -
  tar xzf "${name}.tar.gz"
  rm -f "${name}.tar.gz"
done

echo ">> patching get_version(), dist_util.py, and options.py"
python - <<'PY'
import ast
import pathlib

# 1. Patch PEP 667 locals() issue in setup.py across all 3 packages
old_exec = "exec(compile(f.read(), version_file, 'exec'))"
new_exec = "_ver_ns = {}\n        exec(compile(f.read(), version_file, 'exec'), _ver_ns)"
old_ret = "return locals()['__version__']"
new_ret = "return _ver_ns['__version__']"
patched = 0
for setup in pathlib.Path(".").glob("*/setup.py"):
    s = setup.read_text(encoding="utf-8")
    if old_exec in s and old_ret in s:
        setup.write_text(s.replace(old_exec, new_exec).replace(old_ret, new_ret), encoding="utf-8")
        print("   patched", setup)
        patched += 1
assert patched == 3, f"expected to patch 3 setup.py files, patched {patched}"

# 2. Patch Slurm command injection in basicsr/utils/dist_util.py
dist_util = pathlib.Path("basicsr-1.4.2/basicsr/utils/dist_util.py")
src = dist_util.read_text(encoding="utf-8")
old_slurm = "    addr = subprocess.getoutput(f'scontrol show hostname {node_list} | head -n1')"
new_slurm = "\n".join([
    "    if not node_list or len(node_list) > 4096 or node_list.startswith('-') or any(ord(c) < 32 or ord(c) == 127 or c.isspace() for c in node_list):",
    "        raise ValueError('Invalid SLURM_NODELIST')",
    "    try:",
    "        result = subprocess.run(['scontrol', 'show', 'hostname', node_list], check=True, capture_output=True, text=True)",
    "    except (OSError, subprocess.CalledProcessError) as exc:",
    "        raise RuntimeError('Unable to resolve SLURM_NODELIST using scontrol') from exc",
    "    hosts = result.stdout.splitlines()",
    "    if not hosts:",
    "        raise RuntimeError('scontrol returned no hostnames')",
    "    addr = hosts[0].strip()",
    "    if not addr or len(addr) > 253:",
    "        raise RuntimeError('scontrol returned an invalid hostname')",
    "    hostname = addr[:-1] if addr.endswith('.') else addr",
    "    labels = hostname.split('.')",
    "    if any(not label or len(label) > 63 or not label[0].isascii() or not label[0].isalnum() or not label[-1].isascii() or not label[-1].isalnum() or any(not (c.isascii() and (c.isalnum() or c == '-')) for c in label) for label in labels):",
    "        raise RuntimeError('scontrol returned an invalid hostname')",
])
assert src.count(old_slurm) == 1, f"expected exactly 1 vulnerable scontrol call in {dist_util}"
patched_dist = src.replace(old_slurm, new_slurm)
ast.parse(patched_dist)
dist_util.write_text(patched_dist, encoding="utf-8")
print("   patched", dist_util)

# 3. Patch unsafe YAML, eval(), and exec() in basicsr/utils/options.py
options_py = pathlib.Path("basicsr-1.4.2/basicsr/utils/options.py")
opt_src = options_py.read_text(encoding="utf-8")

old_yaml = """    try:
        from yaml import CDumper as Dumper
        from yaml import CLoader as Loader
    except ImportError:
        from yaml import Dumper, Loader

    _mapping_tag = yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG

    def dict_representer(dumper, data):
        return dumper.represent_dict(data.items())

    def dict_constructor(loader, node):
        return OrderedDict(loader.construct_pairs(node))

    Dumper.add_representer(OrderedDict, dict_representer)
    Loader.add_constructor(_mapping_tag, dict_constructor)
    return Loader, Dumper"""

new_yaml = """    try:
        from yaml import CSafeDumper as BaseDumper
        from yaml import CSafeLoader as BaseLoader
    except ImportError:
        from yaml import SafeDumper as BaseDumper
        from yaml import SafeLoader as BaseLoader

    class OrderedSafeLoader(BaseLoader):
        pass

    class OrderedSafeDumper(BaseDumper):
        pass

    _mapping_tag = yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG

    def dict_representer(dumper, data):
        return dumper.represent_dict(data.items())

    def dict_constructor(loader, node):
        return OrderedDict(loader.construct_pairs(node))

    OrderedSafeDumper.add_representer(OrderedDict, dict_representer)
    OrderedSafeLoader.add_constructor(_mapping_tag, dict_constructor)
    return OrderedSafeLoader, OrderedSafeDumper"""

old_eval = """    # list
    if value.startswith('['):
        return eval(value)"""

new_eval = """    # list
    if value.startswith('['):
        parsed = ast.literal_eval(value)
        if not isinstance(parsed, list):
            raise ValueError('Expected a list literal')
        return parsed"""

old_key_exec = """            keys, value = entry.split('=')
            keys, value = keys.strip(), value.strip()
            value = _postprocess_yml_value(value)
            eval_str = 'opt'
            for key in keys.split(':'):
                eval_str += f'["{key}"]'
            eval_str += '=value'
            # using exec function
            exec(eval_str)"""

new_key_exec = """            if '=' not in entry:
                raise ValueError(f'Invalid --force_yml entry (missing "="): {entry}')
            keys, value = entry.split('=', 1)
            keys, value = keys.strip(), value.strip()
            value = _postprocess_yml_value(value)
            key_parts = keys.split(':')
            target = opt
            for key in key_parts[:-1]:
                if not isinstance(target, dict) or key not in target:
                    raise KeyError(key)
                target = target[key]
            if not isinstance(target, dict) or key_parts[-1] not in target:
                raise KeyError(key_parts[-1])
            target[key_parts[-1]] = value"""

assert opt_src.count("import argparse\n") == 1
assert opt_src.count(old_yaml) == 1
assert opt_src.count(old_eval) == 1
assert opt_src.count(old_key_exec) == 1

patched_opt = (
    opt_src.replace("import argparse\n", "import argparse\nimport ast\n", 1)
    .replace(old_yaml, new_yaml, 1)
    .replace(old_eval, new_eval, 1)
    .replace(old_key_exec, new_key_exec, 1)
)
ast.parse(patched_opt)
options_py.write_text(patched_opt, encoding="utf-8")
print("   patched", options_py)
PY

echo ">> building wheels into ${OUT}"
pip wheel --no-deps -w "$OUT" ./basicsr-* ./gfpgan-* ./facexlib-*
ls -l "$OUT"
