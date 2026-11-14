import hashlib
import importlib.util
import json
import os
import pathlib
import sys
import tarfile
import types
import urllib.request
from collections import OrderedDict
from unittest.mock import patch

import pytest
import yaml

ODYSSEUS_ROOT = pathlib.Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ODYSSEUS_ROOT / "docker" / "build-realesrgan-wheels.sh"

EXPECTED_SDIST_SPECS = {
    "basicsr-1.4.2.tar.gz": (
        "basicsr",
        "1.4.2",
        "b89b595a87ef964cda9913b4d99380ddb6554c965577c0c10cb7b78e31301e87",
    ),
    "gfpgan-1.3.8.tar.gz": (
        "gfpgan",
        "1.3.8",
        "21618b06ce8ea6230448cb526b012004f23a9ab956b55c833f69b9fc8a60c4f9",
    ),
    "facexlib-0.3.0.tar.gz": (
        "facexlib",
        "0.3.0",
        "7ae784a520eb52e05583e8bf9f68f77f45083239ac754d646d635017b49e7763",
    ),
}
EXPECTED_SDIST_SHA256 = {
    name: spec[2] for name, spec in EXPECTED_SDIST_SPECS.items()
}
EXPECTED_PATCHED_DIST_UTIL_SHA256 = (
    "78441c95da015d9af4a8242123777786ee0e49cb207508eb4170e4f3ee9f8820"
)
EXPECTED_PATCHED_OPTIONS_SHA256 = (
    "3a278ea7dd747b96b0176f4ba4f89907dc65a42fa3e3fbf6a87843325928d71e"
)

ORIG_DIST_UTIL = """import os
import subprocess
import torch

def _init_dist_slurm(backend, port=None):
    proc_id = int(os.environ['SLURM_PROCID'])
    ntasks = int(os.environ['SLURM_NTASKS'])
    node_list = os.environ['SLURM_NODELIST']
    num_gpus = torch.cuda.device_count()
    torch.cuda.set_device(proc_id % num_gpus)
    addr = subprocess.getoutput(f'scontrol show hostname {node_list} | head -n1')
    if port is not None:
        os.environ['MASTER_PORT'] = str(port)
    os.environ['MASTER_ADDR'] = addr
"""

ORIG_OPTIONS = """import argparse
import random
import torch
import yaml
from collections import OrderedDict
from os import path as osp

from basicsr.utils import set_random_seed
from basicsr.utils.dist_util import get_dist_info, init_dist, master_only


def ordered_yaml():
    try:
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
    return Loader, Dumper


def _postprocess_yml_value(value):
    if value == '~' or value.lower() == 'none':
        return None
    if value.lower() == 'true':
        return True
    elif value.lower() == 'false':
        return False
    if value.startswith('!!float'):
        return float(value.replace('!!float', ''))
    if value.isdigit():
        return int(value)
    elif value.replace('.', '', 1).isdigit() and value.count('.') < 2:
        return float(value)
    # list
    if value.startswith('['):
        return eval(value)
    return value


def parse_options(root_path, is_train=True):
    parser = argparse.ArgumentParser()
    parser.add_argument('-opt', type=str, required=True)
    parser.add_argument('--launcher', choices=['none', 'pytorch', 'slurm'], default='none')
    parser.add_argument('--auto_resume', action='store_true')
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--local_rank', type=int, default=0)
    parser.add_argument('--force_yml', nargs='+', default=None)
    args = parser.parse_args()

    with open(args.opt, mode='r') as f:
        opt = yaml.load(f, Loader=ordered_yaml()[0])

    if args.force_yml is not None:
        for entry in args.force_yml:
            keys, value = entry.split('=')
            keys, value = keys.strip(), value.strip()
            value = _postprocess_yml_value(value)
            eval_str = 'opt'
            for key in keys.split(':'):
                eval_str += f'["{key}"]'
            eval_str += '=value'
            # using exec function
            exec(eval_str)

    return opt, args
"""

ORIG_SETUP = """def get_version():
    with open(version_file, 'r') as f:
        exec(compile(f.read(), version_file, 'exec'))
    return locals()['__version__']
"""


def _is_ci_environment() -> bool:
    for var in ("CI", "GITHUB_ACTIONS", "ODYSSEUS_STRICT_SECURITY_TESTS"):
        val = os.environ.get(var, "").strip().lower()
        if val in ("1", "true", "yes", "on"):
            return True
    return False


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().lower()


def _sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().lower()


def _candidate_archive_dirs() -> list[pathlib.Path]:
    dirs: list[pathlib.Path] = []
    explicit_dir = os.environ.get("ODYSSEUS_REALESRGAN_SDIST_DIR", "").strip()
    if explicit_dir:
        dirs.append(pathlib.Path(explicit_dir).expanduser())

    # Portable relative sibling path to the local remediation lab audit directory
    dirs.append(
        ODYSSEUS_ROOT.parent
        / "odysseus-dependency-remediation-lab"
        / "source-audit"
    )

    cache_override = os.environ.get("ODYSSEUS_REALESRGAN_CACHE_DIR", "").strip()
    if cache_override:
        dirs.append(pathlib.Path(cache_override).expanduser())
    else:
        dirs.append(
            pathlib.Path.home() / ".cache" / "odysseus" / "realesrgan-sdists"
        )
    return dirs


def _download_verified_sdist(
    archive_name: str, dest_dir: pathlib.Path
) -> pathlib.Path:
    pkg_name, version, expected_digest = EXPECTED_SDIST_SPECS[archive_name]
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / archive_name

    metadata_url = f"https://pypi.org/pypi/{pkg_name}/{version}/json"
    with urllib.request.urlopen(metadata_url, timeout=30) as resp:
        metadata = json.loads(resp.read().decode("utf-8"))

    sdist_url = None
    for item in metadata.get("urls", []):
        if item.get("packagetype") == "sdist":
            meta_digest = (
                item.get("digests", {}).get("sha256", "").strip().lower()
            )
            if meta_digest != expected_digest:
                raise AssertionError(
                    f"PyPI metadata SHA-256 mismatch for {archive_name}: "
                    f"{meta_digest} != {expected_digest}"
                )
            sdist_url = item["url"]
            break

    if not sdist_url:
        raise AssertionError(f"No sdist entry found on PyPI for {archive_name}")

    tmp_path = dest_dir / f".{archive_name}.tmp.{os.getpid()}"
    try:
        with urllib.request.urlopen(sdist_url, timeout=60) as resp:
            tmp_path.write_bytes(resp.read())
        actual_digest = _sha256_file(tmp_path)
        if actual_digest != expected_digest:
            raise AssertionError(
                f"Downloaded sdist SHA-256 mismatch for {archive_name}: "
                f"{actual_digest} != {expected_digest}"
            )
        os.replace(tmp_path, dest_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)

    return dest_path


def _resolve_sdist_archives(
    fallback_cache_dir: pathlib.Path,
) -> tuple[dict[str, pathlib.Path], pathlib.Path | None]:
    explicit_dir_env = os.environ.get(
        "ODYSSEUS_REALESRGAN_SDIST_DIR", ""
    ).strip()
    candidate_dirs = _candidate_archive_dirs()
    resolved: dict[str, pathlib.Path] = {}
    audit_dir: pathlib.Path | None = None

    for candidate in candidate_dirs:
        if (candidate / "patched").is_dir():
            audit_dir = candidate
        for archive_name in EXPECTED_SDIST_SPECS:
            if archive_name not in resolved:
                candidate_file = candidate / archive_name
                if candidate_file.is_file():
                    resolved[archive_name] = candidate_file

    if explicit_dir_env and len(resolved) < len(EXPECTED_SDIST_SPECS):
        missing = sorted(set(EXPECTED_SDIST_SPECS) - set(resolved))
        pytest.fail(
            f"ODYSSEUS_REALESRGAN_SDIST_DIR={explicit_dir_env!r} is missing "
            f"required archives: {missing}"
        )

    missing = [name for name in EXPECTED_SDIST_SPECS if name not in resolved]
    if missing:
        offline_only = os.environ.get(
            "ODYSSEUS_OFFLINE_TESTS", ""
        ).strip().lower() in ("1", "true", "yes")
        if offline_only and not _is_ci_environment():
            pytest.skip(
                f"Offline mode enabled and sdist archives not cached: {missing}"
            )

        download_dir = candidate_dirs[-1] if candidate_dirs else fallback_cache_dir
        for archive_name in missing:
            try:
                resolved[archive_name] = _download_verified_sdist(
                    archive_name, download_dir
                )
            except Exception as exc:
                if _is_ci_environment():
                    pytest.fail(
                        f"Security-critical archive {archive_name} could not be "
                        f"resolved or fetched in CI: {exc}"
                    )
                pytest.skip(
                    f"Upstream sdist archive {archive_name} unavailable locally "
                    f"and fetch failed ({exc}); set CI=1 to enforce failure."
                )

    for archive_name, archive_path in resolved.items():
        expected_digest = EXPECTED_SDIST_SHA256[archive_name]
        actual_digest = _sha256_file(archive_path)
        assert actual_digest == expected_digest, (
            f"SHA-256 mismatch for {archive_name} at {archive_path}: "
            f"{actual_digest} != {expected_digest}"
        )

    return resolved, audit_dir


def _extract_inline_patch_python(script_path: pathlib.Path) -> str:
    assert script_path.is_file(), f"Missing build script at {script_path}"
    lines = script_path.read_text(encoding="utf-8").splitlines()
    start = lines.index("python - <<'PY'") + 1
    end = len(lines) - 1 - lines[::-1].index("PY")
    return "\n".join(lines[start:end])


def _apply_patcher_in_dir(work_dir: pathlib.Path, patch_code: str) -> None:
    prev_cwd = os.getcwd()
    try:
        os.chdir(work_dir)
        exec(
            compile(patch_code, str(BUILD_SCRIPT), "exec"),
            {"__name__": "__main__"},
        )
    finally:
        os.chdir(prev_cwd)


def _load_isolated_modules(
    dist_util_path: pathlib.Path, options_path: pathlib.Path
):
    torch = types.ModuleType("torch")
    torch.__path__ = []
    torch.cuda = types.SimpleNamespace(
        device_count=lambda: 2, set_device=lambda _: None
    )
    torch_dist = types.ModuleType("torch.distributed")
    torch_dist.init_process_group = lambda *a, **kw: None
    torch_dist.get_rank = lambda: 0
    torch_dist.get_world_size = lambda: 1
    torch_mp = types.ModuleType("torch.multiprocessing")
    torch.distributed = torch_dist
    torch.multiprocessing = torch_mp

    basicsr = types.ModuleType("basicsr")
    basicsr.__path__ = []
    basicsr_utils = types.ModuleType("basicsr.utils")
    basicsr_utils.__path__ = []
    basicsr_utils.set_random_seed = lambda seed: None
    basicsr_dist = types.ModuleType("basicsr.utils.dist_util")
    basicsr_dist.get_dist_info = lambda: (0, 1)
    basicsr_dist.init_dist = lambda *a, **kw: None
    basicsr_dist.master_only = lambda fn: fn

    with patch.dict(
        sys.modules,
        {
            "torch": torch,
            "torch.distributed": torch_dist,
            "torch.multiprocessing": torch_mp,
            "basicsr": basicsr,
            "basicsr.utils": basicsr_utils,
            "basicsr.utils.dist_util": basicsr_dist,
        },
    ):
        dist_spec = importlib.util.spec_from_file_location(
            "isolated_patched_dist_util", dist_util_path
        )
        dist_mod = importlib.util.module_from_spec(dist_spec)
        dist_spec.loader.exec_module(dist_mod)

        opt_spec = importlib.util.spec_from_file_location(
            "isolated_patched_options", options_path
        )
        opt_mod = importlib.util.module_from_spec(opt_spec)
        opt_spec.loader.exec_module(opt_mod)

    return dist_mod, opt_mod


@pytest.fixture(scope="module")
def synthetic_patched_modules(tmp_path_factory):
    work = tmp_path_factory.mktemp("synthetic_wheel_patch")
    for pkg in ("basicsr-1.4.2", "gfpgan-1.3.8", "facexlib-0.3.0"):
        (work / pkg).mkdir()
        (work / pkg / "setup.py").write_text(ORIG_SETUP, encoding="utf-8")

    utils_dir = work / "basicsr-1.4.2" / "basicsr" / "utils"
    utils_dir.mkdir(parents=True)
    dist_util_path = utils_dir / "dist_util.py"
    options_path = utils_dir / "options.py"
    dist_util_path.write_text(ORIG_DIST_UTIL, encoding="utf-8")
    options_path.write_text(ORIG_OPTIONS, encoding="utf-8")

    patch_code = _extract_inline_patch_python(BUILD_SCRIPT)
    _apply_patcher_in_dir(work, patch_code)
    return _load_isolated_modules(dist_util_path, options_path)


@pytest.fixture(scope="module")
def real_archive_patched_environment(tmp_path_factory):
    fallback_cache = tmp_path_factory.mktemp("sdist_cache")
    archives, audit_dir = _resolve_sdist_archives(fallback_cache)

    work = tmp_path_factory.mktemp("real_sdist_patch")
    for archive_name in EXPECTED_SDIST_SPECS:
        with tarfile.open(archives[archive_name], "r:gz") as tf:
            tf.extractall(work, filter="data")

    patch_code = _extract_inline_patch_python(BUILD_SCRIPT)
    _apply_patcher_in_dir(work, patch_code)

    dist_util_path = (
        work / "basicsr-1.4.2" / "basicsr" / "utils" / "dist_util.py"
    )
    options_path = work / "basicsr-1.4.2" / "basicsr" / "utils" / "options.py"
    dist_mod, opt_mod = _load_isolated_modules(dist_util_path, options_path)
    return {
        "work": work,
        "archives": archives,
        "audit_dir": audit_dir,
        "dist_util_path": dist_util_path,
        "options_path": options_path,
        "dist_mod": dist_mod,
        "opt_mod": opt_mod,
    }


def test_build_script_pins_all_sdist_sha256_digests():
    text = BUILD_SCRIPT.read_text(encoding="utf-8").lower()
    for expected_digest in EXPECTED_SDIST_SHA256.values():
        assert expected_digest in text
    assert "sha256sum -c -" in text


def test_upstream_source_archives_match_pinned_sha256(
    real_archive_patched_environment,
):
    archives = real_archive_patched_environment["archives"]
    assert set(archives.keys()) == set(EXPECTED_SDIST_SHA256.keys())
    for archive_name, expected_digest in EXPECTED_SDIST_SHA256.items():
        archive_path = archives[archive_name]
        assert archive_path.is_file()
        assert _sha256_file(archive_path) == expected_digest


@pytest.mark.parametrize(
    "payload", ["", "-help", "node list", "node\nother", "x" * 4097]
)
def test_synthetic_slurm_rejects_invalid_nodelist_before_subprocess(
    synthetic_patched_modules, payload
):
    dist_mod, _ = synthetic_patched_modules
    env = {"SLURM_PROCID": "0", "SLURM_NTASKS": "2", "SLURM_NODELIST": payload}
    with (
        patch.dict(os.environ, env, clear=False),
        patch.object(dist_mod.subprocess, "run") as run,
    ):
        with pytest.raises(ValueError):
            dist_mod._init_dist_slurm("gloo")
    run.assert_not_called()


@pytest.mark.parametrize(
    "bad_host",
    [
        "-node01\n",
        "node01-\n",
        "node..01\n",
        "node_01\n",
        ("a" * 64) + "\n",
        "bad;host\n",
    ],
)
def test_synthetic_slurm_rejects_non_rfc1123_hostname(
    synthetic_patched_modules, bad_host
):
    dist_mod, _ = synthetic_patched_modules
    completed = dist_mod.subprocess.CompletedProcess(
        args=[], returncode=0, stdout=bad_host, stderr=""
    )
    env = {"SLURM_PROCID": "0", "SLURM_NTASKS": "2", "SLURM_NODELIST": "node01"}
    with (
        patch.dict(os.environ, env, clear=False),
        patch.object(dist_mod.subprocess, "run", return_value=completed),
    ):
        with pytest.raises(RuntimeError):
            dist_mod._init_dist_slurm("gloo")


def test_synthetic_options_blocks_unsafe_yaml_eval_and_exec(
    synthetic_patched_modules, tmp_path
):
    _, opt_mod = synthetic_patched_modules
    loader, _ = opt_mod.ordered_yaml()
    with pytest.raises(yaml.constructor.ConstructorError):
        yaml.load(
            "pwn: !!python/object/apply:os.system ['id']\n", Loader=loader
        )

    with pytest.raises((ValueError, SyntaxError)):
        opt_mod._postprocess_yml_value("[__import__('os').system('id')]")

    yml = tmp_path / "cfg.yml"
    yml.write_text("train:\n  ema_decay: 0.9\n", encoding="utf-8")
    evil = (
        'train:ema_decay"] = 1; __import__("os").system("id"); '
        'opt["train"]["ema_decay=0.5'
    )
    with patch.object(
        sys, "argv", ["prog", "-opt", str(yml), "--force_yml", evil]
    ):
        with pytest.raises(KeyError):
            opt_mod.parse_options(str(tmp_path), is_train=True)


def test_real_archive_patched_files_match_verified_sha256_hashes(
    real_archive_patched_environment,
):
    work = real_archive_patched_environment["work"]
    for pkg in ("basicsr-1.4.2", "gfpgan-1.3.8", "facexlib-0.3.0"):
        setup_text = (work / pkg / "setup.py").read_text(encoding="utf-8")
        assert "_ver_ns = {}" in setup_text
        assert "return _ver_ns['__version__']" in setup_text
        assert "return locals()['__version__']" not in setup_text

    dist_bytes = (
        real_archive_patched_environment["dist_util_path"]
        .read_bytes()
        .replace(b"\r\n", b"\n")
    )
    opt_bytes = (
        real_archive_patched_environment["options_path"]
        .read_bytes()
        .replace(b"\r\n", b"\n")
    )

    assert _sha256_bytes(dist_bytes) == EXPECTED_PATCHED_DIST_UTIL_SHA256
    assert _sha256_bytes(opt_bytes) == EXPECTED_PATCHED_OPTIONS_SHA256

    audit_dir = real_archive_patched_environment["audit_dir"]
    if audit_dir is not None:
        ref_dist_path = audit_dir / "patched" / "dist_util.py"
        ref_opt_path = audit_dir / "patched" / "options.py"
        if ref_dist_path.is_file():
            assert dist_bytes == ref_dist_path.read_bytes().replace(
                b"\r\n", b"\n"
            )
        if ref_opt_path.is_file():
            assert opt_bytes == ref_opt_path.read_bytes().replace(
                b"\r\n", b"\n"
            )


def test_real_basicsr_slurm_hardening_end_to_end(
    real_archive_patched_environment,
):
    dist_mod = real_archive_patched_environment["dist_mod"]
    completed = dist_mod.subprocess.CompletedProcess(
        args=[], returncode=0, stdout="node01.cluster.local\n", stderr=""
    )
    env = {
        "SLURM_PROCID": "0",
        "SLURM_NTASKS": "2",
        "SLURM_NODELIST": "node[01-02];id",
    }
    with (
        patch.dict(os.environ, env, clear=False),
        patch.object(dist_mod.subprocess, "run", return_value=completed) as run,
    ):
        dist_mod._init_dist_slurm("gloo", port=29500)
        assert run.call_args.args[0] == [
            "scontrol",
            "show",
            "hostname",
            "node[01-02];id",
        ]
        assert run.call_args.kwargs.get("shell", False) is False
        assert os.environ["MASTER_ADDR"] == "node01.cluster.local"


def test_real_basicsr_options_safe_yaml_preserves_ordered_mappings(
    real_archive_patched_environment,
):
    opt_mod = real_archive_patched_environment["opt_mod"]
    loader, dumper = opt_mod.ordered_yaml()

    safe_loaders = tuple(
        cls for cls in (yaml.SafeLoader, getattr(yaml, "CSafeLoader", None)) if cls is not None
    )
    safe_dumpers = tuple(
        cls for cls in (yaml.SafeDumper, getattr(yaml, "CSafeDumper", None)) if cls is not None
    )
    assert issubclass(loader, safe_loaders)
    assert issubclass(dumper, safe_dumpers)
    assert not issubclass(loader, (yaml.UnsafeLoader, yaml.FullLoader))

    with pytest.raises(yaml.constructor.ConstructorError):
        yaml.load(
            "exploit: !!python/object/apply:os.system ['echo pwned']\n",
            Loader=loader,
        )

    raw_yaml = (
        "zeta: 1\n"
        "alpha:\n"
        "  third: 30\n"
        "  first: 10\n"
        "  second: 20\n"
        "middle: ok\n"
    )
    loaded = yaml.load(raw_yaml, Loader=loader)
    assert isinstance(loaded, OrderedDict)
    assert list(loaded.keys()) == ["zeta", "alpha", "middle"]
    assert isinstance(loaded["alpha"], OrderedDict)
    assert list(loaded["alpha"].keys()) == ["third", "first", "second"]

    dumped = yaml.dump(loaded, Dumper=dumper, default_flow_style=False)
    reloaded = yaml.load(dumped, Loader=loader)
    assert isinstance(reloaded, OrderedDict)
    assert list(reloaded.keys()) == ["zeta", "alpha", "middle"]
    assert list(reloaded["alpha"].keys()) == ["third", "first", "second"]


def test_real_basicsr_options_valid_nested_force_yml_overrides(
    real_archive_patched_environment, tmp_path
):
    opt_mod = real_archive_patched_environment["opt_mod"]
    yml = tmp_path / "valid_nested.yml"
    yml.write_text(
        "name: realesrgan_x4\n"
        "num_gpu: 1\n"
        "use_amp: true\n"
        "train:\n"
        "  ema_decay: 0.999\n"
        "  lr_list: [1e-4, 2e-4]\n"
        "  optim_g:\n"
        "    lr: 0.0001\n"
        "    use_grad_clip: false\n"
        "datasets:\n"
        "  train:\n"
        "    name: DIV2K\n"
        "    dataroot_gt: /old/path\n"
        "path:\n"
        "  pretrain_network_g: placeholder.pth\n",
        encoding="utf-8",
    )

    overrides = [
        "num_gpu=4",
        "use_amp=false",
        "train:ema_decay=0.9995",
        "train:lr_list=[5e-5, 1e-4]",
        "train:optim_g:lr=!!float 2e-4",
        "train:optim_g:use_grad_clip=true",
        "datasets:train:name=FFHQ",
        "datasets:train:dataroot_gt=/data/ffhq",
        "path:pretrain_network_g=none",
    ]
    with patch.object(
        sys, "argv", ["train.py", "-opt", str(yml), "--force_yml", *overrides]
    ):
        opt, args = opt_mod.parse_options(str(tmp_path), is_train=True)

    assert args.force_yml == overrides
    assert opt["num_gpu"] == 4
    assert opt["use_amp"] is False
    assert opt["train"]["ema_decay"] == pytest.approx(0.9995)
    assert opt["train"]["lr_list"] == [5e-5, 1e-4]
    assert opt["train"]["optim_g"]["lr"] == pytest.approx(2e-4)
    assert opt["train"]["optim_g"]["use_grad_clip"] is True
    assert opt["datasets"]["train"]["name"] == "FFHQ"
    assert opt["datasets"]["train"]["dataroot_gt"] == "/data/ffhq"
    assert opt["path"]["pretrain_network_g"] is None

    with patch.object(
        sys, "argv", ["train.py", "-opt", str(yml), "--force_yml", "no_equals_sign"]
    ):
        with pytest.raises(ValueError, match="missing"):
            opt_mod.parse_options(str(tmp_path), is_train=True)

    with patch.object(
        sys,
        "argv",
        ["train.py", "-opt", str(yml), "--force_yml", "datasets:nonexistent:name=X"],
    ):
        with pytest.raises(KeyError):
            opt_mod.parse_options(str(tmp_path), is_train=True)


def test_real_basicsr_options_hardening_end_to_end(
    real_archive_patched_environment, tmp_path
):
    opt_mod = real_archive_patched_environment["opt_mod"]
    loader, _ = opt_mod.ordered_yaml()
    with pytest.raises(yaml.constructor.ConstructorError):
        yaml.load(
            "exploit: !!python/object/apply:os.system ['echo pwned']\n",
            Loader=loader,
        )

    yml = tmp_path / "real_config.yml"
    yml.write_text(
        "name: realesrgan_x4\n"
        "num_gpu: 1\n"
        "train:\n"
        "  ema_decay: 0.999\n"
        "  lr_list: [1e-4, 2e-4]\n"
        "datasets:\n"
        "  train:\n"
        "    name: DIV2K\n"
        "path:\n"
        "  pretrain_network_g: null\n",
        encoding="utf-8",
    )

    marker = tmp_path / "pwned.txt"
    evil_key = (
        f'train:ema_decay"] = 1; open(r"{marker}", "w").write("x"); '
        'opt["train"]["ema_decay=0.5'
    )
    with patch.object(
        sys, "argv", ["train.py", "-opt", str(yml), "--force_yml", evil_key]
    ):
        with pytest.raises(KeyError):
            opt_mod.parse_options(str(tmp_path), is_train=True)
    assert not marker.exists()