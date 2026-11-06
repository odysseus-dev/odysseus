"""Private filesystem defaults established before application imports."""

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest


def bootstrap_runtime():
    """Establish defaults before collection; APP_PORT opts into live smoke."""
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if os.environ.get("APP_PORT") and not worker:
        return None
    runtime = isolated_runtime(worker or "main")
    runtime.root = runtime.__enter__()
    return runtime


def configure_runtime(config, runtime):
    """Register ownership even when configuration or collection fails."""
    if runtime is not None:
        config.add_cleanup(lambda: runtime.__exit__(None, None, None))
        # pytest's default <TMPDIR>/pytest-of-<user>/pytest-<n> beneath the
        # private TMPDIR, plus xdist's popen-gw<n>, overflows the 107-byte
        # AF_UNIX limit for sockets in tmp_path. Workers inherit a basetemp
        # under the controller's; an explicit --basetemp still wins.
        if config.option.basetemp is None and not hasattr(config, "workerinput"):
            config.option.basetemp = str(runtime.root / "pytest")
    parallel = bool(getattr(config.option, "numprocesses", None)) or hasattr(config, "workerinput")
    # This consumes the existing test option; its public read and documented
    # source location remain in the static-server fixture.
    port_variable = "ODYSSEUS_TEST_STATIC_PORT"
    if parallel and int(os.environ.get(port_variable) or 0):
        raise pytest.UsageError(
            "parallel tests require an ephemeral static-server port; "
            "unset ODYSSEUS_TEST_STATIC_PORT or use -n 0"
        )


@contextmanager
def isolated_runtime(worker="main"):
    """Own mutable defaults for one pytest process, including subprocesses.

    The random suffix separates simultaneous runs, even with the same worker
    name. Test-specific monkeypatches and function-scoped databases still own
    their resources; this is a fallback namespace, not a shared DB fixture.
    """
    with tempfile.TemporaryDirectory(prefix=f"ody-{worker}-") as directory:
        root = Path(directory)
        with pytest.MonkeyPatch.context() as patcher:
            paths = {
                "ODYSSEUS_DATA_DIR": root / "data",
                "ODYSSEUS_MAIL_ATTACHMENTS_DIR": root / "mail",
                "FASTEMBED_CACHE_PATH": root / "fastembed",
                "XDG_RUNTIME_DIR": root / "runtime",
            }
            for name, path in paths.items():
                path.mkdir(mode=0o700)
                patcher.setenv(name, str(path))
            (root / "data" / "agent_workspace").mkdir(mode=0o700, exist_ok=True)
            # Browser resolution falls back to our XDG runtime directory.
            patcher.delenv("AGENT_BROWSER_SOCKET_DIR", raising=False)
            tmp = root / "tmp"
            tmp.mkdir(mode=0o700)
            for name in ("TMPDIR", "TMP", "TEMP"):
                patcher.setenv(name, str(tmp))
            # tempfile may already have cached the caller's directory.
            patcher.setattr(tempfile, "tempdir", str(tmp))
            yield root
