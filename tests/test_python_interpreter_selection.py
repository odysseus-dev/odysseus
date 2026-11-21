"""Run the shell selector against controlled executable interpreters."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

HELPER = Path(__file__).resolve().parents[1] / 'scripts/python-interpreter.sh'
BASH = shutil.which('bash')
pytestmark = pytest.mark.skipif(os.name == 'nt' or not BASH, reason='macOS launcher uses POSIX bash')


def interpreter(tmp_path, name, version=(3, 12), machine='arm64'):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    wrapper = f'''import platform,sys
platform.machine=lambda:{machine!r}
sys.version_info={version!r}
args=sys.argv[1:]
assert args[0]=='-c'
sys.argv=['probe',*args[2:]]
exec(args[1])
'''
    path.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' -c ' + shlex.quote(wrapper) + ' "$@"\n')
    path.chmod(0o755)
    return path


def select(tmp_path, machine, candidates):
    return subprocess.run([BASH, '-c', 'source "$1"; shift; find_python_interpreter "$@"',
                           'selector', str(HELPER), machine, *map(str, candidates)],
                          env={**os.environ, 'PATH': str(tmp_path)}, capture_output=True, text=True, timeout=10)


@pytest.mark.parametrize('minor', [11, 12, 13, 14])
def test_accepts_supported_native_python(tmp_path, minor):
    path = interpreter(tmp_path, 'python3', (3, minor))
    result = select(tmp_path, 'arm64', ['python3'])
    assert result.returncode == 0 and result.stdout.strip() == str(path)


def test_skips_old_and_rosetta_candidates_before_native_python(tmp_path):
    old = interpreter(tmp_path, 'old', (3, 10))
    rosetta = interpreter(tmp_path, 'rosetta', machine='x86_64')
    native = interpreter(tmp_path, 'native')
    result = select(tmp_path, 'arm64', [old, rosetta, native])
    assert result.returncode == 0 and result.stdout.strip() == str(native)


def test_intel_mac_accepts_supported_intel_python(tmp_path):
    path = interpreter(tmp_path, 'python3', machine='x86_64')
    result = select(tmp_path, 'x86_64', ['python3'])
    assert result.returncode == 0 and result.stdout.strip() == str(path)


def test_missing_broken_and_unsupported_candidates_fail_without_selection(tmp_path):
    old = interpreter(tmp_path, 'old', (3, 9))
    broken = tmp_path / 'broken'
    broken.write_text('#!/bin/sh\nexit 99\n')
    broken.chmod(0o755)
    result = select(tmp_path, 'arm64', ['missing', broken, old])
    assert result.returncode != 0 and result.stdout == ''


def test_native_path_with_spaces_is_kept_as_one_executable(tmp_path):
    path = interpreter(tmp_path, 'pyenv root/versions/3.12.0/bin/python3')
    result = select(tmp_path, 'arm64', [path])
    assert result.returncode == 0 and result.stdout.strip() == str(path)


def test_first_compatible_candidate_wins(tmp_path):
    first = interpreter(tmp_path, 'preferred')
    second = interpreter(tmp_path, 'fallback')
    result = select(tmp_path, 'arm64', [first, second])
    assert result.returncode == 0 and result.stdout.strip() == str(first)
