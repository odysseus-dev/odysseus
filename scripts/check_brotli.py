"""Fail image builds that select an unfixed Debian native Brotli decoder.

Debian Node links libbrotlidec rather than bundling upstream Node's decoder.
Use Debian's backport revision, not BrotliDecoderVersion() or Python wheel
metadata, to establish the package floor for CVE-2026-111497:
https://security-tracker.debian.org/tracker/CVE-2026-111497
"""

import argparse
import ctypes
from pathlib import Path
import platform
import re
import subprocess
import sys


DEBIAN_FIXED = {"12": "1.0.9-2+deb12u1", "13": "1.1.0-2+deb13u1"}
DECODER_SONAME = "libbrotlidec.so.1"


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def check_package():
    release = platform.freedesktop_os_release()
    floor = DEBIAN_FIXED.get(release.get("VERSION_ID"))
    if release.get("ID") != "debian" or floor is None:
        raise ValueError("Brotli package floor is not defined for this distribution")
    record = command(
        "dpkg-query", "-W", "-f=${Status}\n${Version}", "libbrotli1"
    ).splitlines()
    if len(record) != 2 or any(not value.strip() for value in record):
        raise ValueError("invalid libbrotli1 package record: expected status and version")
    status, version = record
    if status != "install ok installed":
        raise ValueError(f"libbrotli1 is not fully installed (status: {status})")
    if subprocess.run(
        ["dpkg", "--compare-versions", version, "ge", floor], check=False
    ).returncode != 0:
        raise ValueError(f"libbrotli1 {version} does not meet Debian's fixed floor {floor}")
    owned = {
        Path(line).resolve()
        for line in command("dpkg-query", "-L", "libbrotli1").splitlines()
        if Path(line).name == DECODER_SONAME
    }
    if not owned:
        raise ValueError("libbrotli1 has no packaged native decoder")
    return version, owned


def loaded_path(library):
    # Inspect the library actually selected by the dynamic loader. An installed
    # fixed package alone would not catch LD_LIBRARY_PATH or /usr/local shadows.
    class DlInfo(ctypes.Structure):
        _fields_ = [("filename", ctypes.c_char_p), ("base", ctypes.c_void_p),
                    ("symbol", ctypes.c_char_p), ("address", ctypes.c_void_p)]

    dladdr = ctypes.CDLL(None).dladdr
    dladdr.argtypes = [ctypes.c_void_p, ctypes.POINTER(DlInfo)]
    dladdr.restype = ctypes.c_int
    info = DlInfo()
    if not dladdr(ctypes.cast(library.BrotliDecoderDecompress, ctypes.c_void_p), ctypes.byref(info)):
        raise ValueError("cannot identify the loaded Brotli decoder")
    return Path(info.filename.decode()).resolve()


def check_decoder(library):
    # The fix adds error -17. Older decoders return INVALID here, including
    # upstream 1.1.0 and 1.2.0. Fixed Debian 12 and 13 backports expose the same
    # marker with different error-string prefixes.
    # No malformed attacker-controlled stream is needed for this build check.
    library.BrotliDecoderErrorString.argtypes = [ctypes.c_int]
    library.BrotliDecoderErrorString.restype = ctypes.c_char_p
    if library.BrotliDecoderErrorString(-17) not in (b"BLOCK_SWITCH", b"_ERROR_FORMAT_BLOCK_SWITCH"):
        raise ValueError("native decoder lacks the CVE-2026-111497 BLOCK_SWITCH fix marker")

    library.BrotliDecoderDecompress.argtypes = [
        ctypes.c_size_t, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p
    ]
    library.BrotliDecoderDecompress.restype = ctypes.c_int
    # A small, valid uncompressed Brotli stream must still decode correctly.
    encoded = bytes.fromhex("0b058068656c6c6f20776f726c6403")
    output = ctypes.create_string_buffer(11)
    size = ctypes.c_size_t(len(output))
    result = library.BrotliDecoderDecompress(len(encoded), encoded, ctypes.byref(size), output)
    if result != 1 or size.value != 11 or output.raw != b"hello world":
        raise ValueError("native Brotli decoder failed the valid-stream control")


def check_node(node, decoder):
    # ldd inspects the distro executable and its libnode dependency without
    # executing JavaScript or relying on process.versions.brotli.
    linked = command("ldd", str(node))
    match = re.search(r"libbrotlidec\.so\.1\s+=>\s+(/\S+)", linked)
    if not match or Path(match[1]).resolve() != decoder:
        raise ValueError(f"{node} does not select the checked Debian Brotli decoder")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", type=Path, help="also verify this distro Node executable's decoder")
    args = parser.parse_args(argv)
    try:
        version, owned = check_package()
        library = ctypes.CDLL(DECODER_SONAME)
        decoder = loaded_path(library)
        if decoder not in owned:
            raise ValueError(f"selected decoder is outside libbrotli1: {decoder}")
        check_decoder(library)
        if args.node:
            check_node(args.node, decoder)
    except (ValueError, OSError, AttributeError, subprocess.CalledProcessError) as exc:
        print(f"Brotli acceptance failed: {exc}", file=sys.stderr)
        return 1
    print(f"Brotli accepted: Debian libbrotli1 {version}, decoder {decoder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
