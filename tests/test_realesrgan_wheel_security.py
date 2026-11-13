import hashlib
import importlib.util
import os
import pathlib
import sys
import tarfile
import types
from collections import OrderedDict
from unittest.mock import patch
import pytest

yaml = pytest.importorskip("yaml")

ODYSSEUS_ROOT = pathlib.Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ODYSSEUS_ROOT / "docker" / "build-realesrgan-wheels.sh"

LAB_AUDIT_DIR = pathlib.Path(r"C:\Users\Hachi\Projects\odysseus-dependency-remediation-lab\source-audit")
EXPECTED_SDIST_SHA256 = {
    "basicsr-1.4.2.tar.gz": "b89b595a87ef964cda9913b4d99380ddb6554c965577c0c10cb7b78e31301e87",
    "gfpgan-1.3.8.tar.gz": "21618b06ce8ea6230448cb526b012004f23a9ab956b55c833f69b9fc8a60c4f9",
    "facexlib-0.3.0.tar.gz": "7ae784a520eb52e05583e8bf9f68f77f45083239ac754d646d635017b49e7763",
}
EXPECTED_PATCHED_DIST_UTIL_SHA256 = "78441c95da015d9af4a8242123777786ee0e49cb207508eb4170e4f3ee9f8820"
EXPECTED_PATCHED_OPTIONS_SHA256 = "3a278ea7dd747b96b0176f4ba4f89907dc65a42fa3e3fbf6a87843325928d71e"

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


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().lower()


def _sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().lower()


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
        exec(compile(patch_code, str(BUILD_SCRIPT), "exec"), {"__name__": "__main__"})
    finally:
        os.chdir(prev_cwd)


def _load_isolated_modules(dist_util_path: pathlib.Path, options_path: pathlib.Path):
    torch = types.ModuleType("torch")
    torch.__path__ = []
    torch.cuda = types.SimpleNamespace(device_count=lambda: 2, set_device=lambda _: None)
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

    with patch.dict(sys.modules, {
        "torch": torch,
        "torch.distributed": torch_dist,
        "torch.multiprocessing": torch_mp,
        "basicsr": basicsr,
        "basicsr.utils": basicsr_utils,
        "basicsr.utils.dist_util": basicsr_dist,
    }):
        dist_spec = importlib.util.spec_from_file_location("isolated_patched_dist_util", dist_util_path)
        dist_mod = importlib.util.module_from_spec(dist_spec)
        dist_spec.loader.exec_module(dist_mod)

        opt_spec = importlib.util.spec_from_file_location("isolated_patched_options", options_path)
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
    for archive_name, expected_digest in EXPECTED_SDIST_SHA256.items():
        archive_path = LAB_AUDIT_DIR / archive_name
        if not archive_path.is_file():
            pytest.skip(f"Local sdist archive not available: {archive_path}")
        actual_digest = _sha256_file(archive_path)
        assert actual_digest == expected_digest, (
            f"SHA-256 mismatch for {archive_name}: {actual_digest} != {expected_digest}"
        )

    work = tmp_path_factory.mktemp("real_sdist_patch")
    for archive_name in EXPECTED_SDIST_SHA256:
        with tarfile.open(LAB_AUDIT_DIR / archive_name, "r:gz") as tf:
            tf.extractall(work, filter="data")

    patch_code = _extract_inline_patch_python(BUILD_SCRIPT)
    _apply_patcher_in_dir(work, patch_code)

    dist_util_path = work / "basicsr-1.4.2" / "basicsr" / "utils" / "dist_util.py"
    options_path = work / "basicsr-1.4.2" / "basicsr" / "utils" / "options.py"
    dist_mod, opt_mod = _load_isolated_modules(dist_util_path, options_path)
    return {
        "work": work,
        "dist_util_path": dist_util_path,
        "options_path": options_path,
        "dist_mod": dist_mod,
        "opt_mod": opt_mod,
    }


def test_build_script_pins_all_sdist_sha256_digests():
    text = BUILD_SCRIPT.read_text(encoding="utf-8")
    for expected_digest in EXPECTED_SDIST_SHA256.values():
        assert expected_digest in text.lower()
    assert "sha256sum -c -" in text


@pytest.mark.parametrize("payload", ["", "-help", "node list", "node\nother", "x" * 4097])
def test_synthetic_slurm_rejects_invalid_nodelist_before_subprocess(synthetic_patched_modules, payload):
    dist_mod, _ = synthetic_patched_modules
    env = {"SLURM_PROCID": "0", "SLURM_NTASKS": "2", "SLURM_NODELIST": payload}
    with patch.dict(os.environ, env, clear=False), patch.object(dist_mod.subprocess, "run") as run:
        with pytest.raises(ValueError):
            dist_mod._init_dist_slurm("gloo")
    run.assert_not_called()


@pytest.mark.parametrize("bad_host", ["-node01\n", "node01-\n", "node..01\n", "node_01\n", ("a" * 64) + "\n", "bad;host\n"])
def test_synthetic_slurm_rejects_non_rfc1123_hostname(synthetic_patched_modules, bad_host):
    dist_mod, _ = synthetic_patched_modules
    completed = dist_mod.subprocess.CompletedProcess(args=[], returncode=0, stdout=bad_host, stderr="")
    env = {"SLURM_PROCID": "0", "SLURM_NTASKS": "2", "SLURM_NODELIST": "node01"}
    with patch.dict(os.environ, env, clear=False), patch.object(dist_mod.subprocess, "run", return_value=completed):
        with pytest.raises(RuntimeError):
            dist_mod._init_dist_slurm("gloo")


def test_synthetic_options_blocks_unsafe_yaml_eval_and_exec(synthetic_patched_modules, tmp_path):
    _, opt_mod = synthetic_patched_modules
    loader, _ = opt_mod.ordered_yaml()
    with pytest.raises(yaml.constructor.ConstructorError):
        yaml.load("pwn: !!python/object/apply:os.system ['id']\n", Loader=loader)

    with pytest.raises((ValueError, SyntaxError)):
        opt_mod._postprocess_yml_value("[__import__('os').system('id')]")

    yml = tmp_path / "cfg.yml"
    yml.write_text("train:\n  ema_decay: 0.9\n", encoding="utf-8")
    evil = 'train:ema_decay"] = 1; __import__("os").system("id"); opt["train"]["ema_decay=0.5'
    with patch.object(sys, "argv", ["prog", "-opt", str(yml), "--force_yml", evil]):
        with pytest.raises(KeyError):
            opt_mod.parse_options(str(tmp_path), is_train=True)


def test_real_archive_patched_files_match_verified_sha256_hashes(real_archive_patched_environment):
    dist_bytes = real_archive_patched_environment["dist_util_path"].read_bytes().replace(b"\r\n", b"\n")
    opt_bytes = real_archive_patched_environment["options_path"].read_bytes().replace(b"\r\n", b"\n")

    assert _sha256_bytes(dist_bytes) == EXPECTED_PATCHED_DIST_UTIL_SHA256
    assert _sha256_bytes(opt_bytes) == EXPECTED_PATCHED_OPTIONS_SHA256

    ref_dist = (LAB_AUDIT_DIR / "patched" / "dist_util.py").read_bytes().replace(b"\r\n", b"\n")
    ref_opt = (LAB_AUDIT_DIR / "patched" / "options.py").read_bytes().replace(b"\r\n", b"\n")
    assert dist_bytes == ref_dist
    assert opt_bytes == ref_opt


def test_real_basicsr_slurm_hardening_end_to_end(real_archive_patched_environment):
    dist_mod = real_archive_patched_environment["dist_mod"]
    completed = dist_mod.subprocess.CompletedProcess(args=[], returncode=0, stdout="node01.cluster.local\n", stderr="")
    env = {"SLURM_PROCID": "0", "SLURM_NTASKS": "2", "SLURM_NODELIST": "node[01-02];id"}
    with patch.dict(os.environ, env, clear=False), patch.object(dist_mod.subprocess, "run", return_value=completed) as run:
        dist_mod._init_dist_slurm("gloo", port=29500)
        assert run.call_args.args[0] == ["scontrol", "show", "hostname", "node[01-02];id"]
        assert run.call_args.kwargs.get("shell", False) is False
        assert os.environ["MASTER_ADDR"] == "node01.cluster.local"


def test_real_basicsr_options_hardening_end_to_end(real_archive_patched_environment, tmp_path):
    opt_mod = real_archive_patched_environment["opt_mod"]
    loader, _ = opt_mod.ordered_yaml()
    with pytest.raises(yaml.constructor.ConstructorError):
        yaml.load("exploit: !!python/object/apply:os.system ['echo pwned']\n", Loader=loader)
    ordered = yaml.load("z: 1\na: 2\n", Loader=loader)
    assert isinstance(ordered, OrderedDict)
    assert list(ordered.keys()) == ["z", "a"]

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
    evil_key = f'train:ema_decay"] = 1; open(r"{marker}", "w").write("x"); opt["train"]["ema_decay=0.5'
    with patch.object(sys, "argv", ["train.py", "-opt", str(yml), "--force_yml", evil_key]):
        with pytest.raises(KeyError):
            opt_mod.parse_options(str(tmp_path), is_train=True)
    assert not marker.exists()