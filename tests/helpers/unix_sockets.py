"""Bind an AF_UNIX socket at a path the kernel will actually accept.

``sun_path`` is 104 bytes on macOS, terminator included, so a bind path longer
than 103 characters fails with ``OSError: AF_UNIX path too long``. pytest's
``tmp_path`` is rooted at ``$TMPDIR``, which on stock macOS is a 49-character
``/var/folders/<2>/<30>/T/`` path; adding ``pytest-of-<user>/pytest-<n>/`` and
the test's own (truncated) name spends the rest of the budget before the
filename is appended.

That is why this reads as flaky rather than broken. Linux allows 108 bytes and
roots ``$TMPDIR`` at ``/tmp``, so it never bites there; on macOS whether it
bites depends on the length of ``$TMPDIR``, the test's name, and how many
digits pytest's run counter is currently using. A run under a shortened
``$TMPDIR`` passes, the same checkout under the default one does not.

The path is resolved before it is handed back, for the same reason the rest of
this change resolves temp paths: on macOS ``/tmp`` is a symlink to
``/private/tmp``, and a test that binds one spelling and asserts on the other
is comparing two names for the same socket.
"""

import os
import shutil
import socket
import tempfile
from contextlib import contextmanager

# Short enough to leave room for the socket's own name under every platform's
# sun_path budget. A relative root would depend on the working directory.
_SHORT_ROOT = os.path.realpath(tempfile.gettempdir() if os.name == "nt" else "/tmp")


@contextmanager
def bound_unix_socket(name="docker.sock"):
    """Yield the path of a listening AF_UNIX socket, cleaned up on exit."""
    directory = os.path.realpath(tempfile.mkdtemp(prefix="odysseus-sock-", dir=_SHORT_ROOT))
    path = os.path.join(directory, name)
    if len(path) > 103:  # pragma: no cover - guards the guard
        raise AssertionError(f"socket path is {len(path)} bytes, over the limit: {path}")
    sock = socket.socket(socket.AF_UNIX)
    try:
        sock.bind(path)
        yield path
    finally:
        sock.close()
        shutil.rmtree(directory, ignore_errors=True)
