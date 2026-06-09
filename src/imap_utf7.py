"""Modified UTF-7 (RFC 3501 §5.1.3) codec for IMAP mailbox names.

IMAP servers encode non-ASCII mailbox names in a *modified* form of UTF-7, so a
Cyrillic ``Входящие`` arrives over the wire as ``&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-``.
``imaplib`` does no decoding, and Python ships no ``imap4-utf-7`` codec, so folder
names must be decoded for display and re-encoded before they go back to the server
in ``SELECT`` / ``COPY`` / ``MOVE`` / ``APPEND`` commands.

The two differences from standard UTF-7 (RFC 2152):
  * the shift character is ``&`` instead of ``+`` (a literal ``&`` is ``&-``);
  * the Base64 alphabet uses ``,`` instead of ``/`` and carries no ``=`` padding.

Dependency-light (stdlib only) and free of the FastAPI / IMAP import chain so it
can be imported and unit-tested in isolation.
"""

import base64

__all__ = ["decode_imap_utf7", "encode_imap_utf7"]


def decode_imap_utf7(name):
    """Decode an IMAP modified-UTF-7 mailbox name to a Python ``str``.

    Accepts ``str`` or ``bytes`` (bytes are treated as ASCII, which the wire
    format always is). Pure-ASCII names pass through unchanged. Malformed shift
    sequences are left verbatim rather than raising, so a decode never turns a
    listable folder into an exception.
    """
    if isinstance(name, bytes):
        name = name.decode("ascii", "replace")
    if "&" not in name:
        return name

    out = []
    i = 0
    n = len(name)
    while i < n:
        ch = name[i]
        if ch != "&":
            out.append(ch)
            i += 1
            continue
        end = name.find("-", i + 1)
        if end == -1:
            # Unterminated shift sequence — pass the rest through untouched.
            out.append(name[i:])
            break
        chunk = name[i + 1:end]
        if chunk == "":
            out.append("&")  # "&-" is a literal ampersand
        else:
            b64 = chunk.replace(",", "/")
            b64 += "=" * (-len(b64) % 4)
            try:
                # validate=True so a run of non-Base64 chars raises rather than
                # being silently stripped to empty, keeping the verbatim fallback.
                out.append(base64.b64decode(b64, validate=True).decode("utf-16-be"))
            except (ValueError, UnicodeDecodeError):
                out.append(name[i:end + 1])  # leave malformed run as-is
        i = end + 1
    return "".join(out)


def encode_imap_utf7(name):
    """Encode a Python ``str`` mailbox name to IMAP modified UTF-7.

    Printable ASCII passes through (a literal ``&`` becomes ``&-``); runs of any
    other characters are emitted as a ``&…-`` Base64 shift sequence. Encoding an
    already-encoded name would double-encode it, so callers must only encode
    decoded/Unicode names — see the module docstring.
    """
    if name is None:
        return ""

    out = []
    i = 0
    n = len(name)
    while i < n:
        ch = name[i]
        if ch == "&":
            out.append("&-")
            i += 1
        elif "\x20" <= ch <= "\x7e":
            out.append(ch)
            i += 1
        else:
            # Gather the maximal run of characters that need shifting.
            start = i
            while i < n and not ("\x20" <= name[i] <= "\x7e") and name[i] != "&":
                i += 1
            run = name[start:i]
            b64 = base64.b64encode(run.encode("utf-16-be")).decode("ascii")
            out.append("&" + b64.rstrip("=").replace("/", ",") + "-")
    return "".join(out)
