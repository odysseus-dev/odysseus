"""Every variable a shipped compose file forwards must carry a default.

A bare ``${VAR}`` makes ``docker compose`` print "variable is not set" on every
command for a default ``.env`` (#6619), which greets fresh installs with a
warning that looks like a misconfiguration.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILES = sorted(ROOT.glob("docker-compose*.yml")) + sorted((ROOT / "docker").glob("*.yml"))

# ${VAR} with no ``:-``/``-``/``:?``/``?`` modifier.
BARE_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_compose_variables_have_defaults(path):
    bare = [
        f"{path.name}:{lineno}: ${{{name}}}"
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if not line.lstrip().startswith("#")
        for name in BARE_VAR.findall(line)
    ]
    assert not bare, "compose variables without a default:\n" + "\n".join(bare)
