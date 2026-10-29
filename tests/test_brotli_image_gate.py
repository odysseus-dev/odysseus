"""Offline image acceptance regressions for Debian's native Brotli backport."""

import contextlib
import ctypes
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.helpers.cli_loader import load_script


checker = load_script("check_brotli.py")
ROOT = Path(__file__).resolve().parents[1]


class PackageFloorTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dpkg"), "Debian version comparisons require dpkg")
    def test_debian_backport_floors_use_real_dpkg_comparisons(self):
        cases = [
            ("12", "1.0.9-2", False),
            ("12", "1.0.9-2+deb12u1", True),
            ("12", "1.0.9-2+deb12u2", True),
            ("13", "1.1.0-2+b7", False),
            ("13", "1.1.0-2+deb13u1~pre1", False),
            ("13", "1.1.0-2+deb13u1", True),
            ("13", "1.1.0-2+deb13u1+b1", True),
            ("13", "1.1.0-2+deb13u2", True),
        ]
        for release, version, accepted in cases:
            with self.subTest(release=release, version=version), patch.object(
                checker.platform, "freedesktop_os_release", return_value={"ID": "debian", "VERSION_ID": release}
            ), patch.object(checker, "command", side_effect=[
                f"install ok installed\n{version}", "/usr/lib/example/libbrotlidec.so.1"
            ]):
                if accepted:
                    self.assertEqual(checker.check_package()[0], version)
                else:
                    with self.assertRaisesRegex(ValueError, "fixed floor"):
                        checker.check_package()

    def test_unknown_distro_and_release_fail_closed(self):
        for release in ({"ID": "ubuntu", "VERSION_ID": "13"},
                        {"ID": "debian", "VERSION_ID": "14"}, {}):
            with self.subTest(release=release), patch.object(
                checker.platform, "freedesktop_os_release", return_value=release
            ), self.assertRaisesRegex(ValueError, "not defined"):
                checker.check_package()

    def test_incomplete_or_missing_package_records_are_rejected(self):
        for record in ("deinstall ok config-files\n1.1.0-2+deb13u1", "", "install ok installed"):
            with self.subTest(record=record), patch.object(
                checker.platform, "freedesktop_os_release", return_value={"ID": "debian", "VERSION_ID": "13"}
            ), patch.object(checker, "command", return_value=record), self.assertRaises(ValueError):
                checker.check_package()


@unittest.skipUnless(shutil.which("sh") and shutil.which("dpkg"), "diagnostics require sh and dpkg")
class DiagnosticTests(unittest.TestCase):
    def run_checker(self, body="raise SystemExit(checker.main([]))", **overrides):
        # Keep the real subprocess/CLI diagnostic boundary; only external command
        # responses and distro identity are controlled by this offline fixture.
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            query = directory / "dpkg-query"
            query.write_text('''#!/bin/sh
case "$1" in
    -W) printf '%s' "$BROTLI_TEST_RECORD"
        printf '%s' "$BROTLI_TEST_QUERY_STDERR" >&2
        exit "$BROTLI_TEST_QUERY_EXIT" ;;
    -L) printf '%s' '/usr/lib/example/libbrotlidec.so.1'
        printf '%s' "$BROTLI_TEST_LIST_STDERR" >&2
        exit "$BROTLI_TEST_LIST_EXIT" ;;
esac
exit 2
''')
            query.chmod(0o755)
            ldd = directory / "ldd"
            ldd.write_text('#!/bin/sh\nprintf "%s" "ldd: cannot inspect executable" >&2\nexit 1\n')
            ldd.chmod(0o755)
            env = dict(os.environ, PATH=tmp + os.pathsep + os.environ.get("PATH", os.defpath),
                       BROTLI_TEST_RECORD="install ok installed\n1.1.0-2+deb13u1",
                       BROTLI_TEST_QUERY_STDERR="", BROTLI_TEST_QUERY_EXIT="0",
                       BROTLI_TEST_LIST_STDERR="", BROTLI_TEST_LIST_EXIT="0")
            env.update(overrides)
            program = (
                "from pathlib import Path\n"
                "import subprocess\n"
                "from tests.helpers.cli_loader import load_script\n"
                "checker = load_script('check_brotli.py')\n"
                "checker.platform.freedesktop_os_release = lambda: {'ID': 'debian', 'VERSION_ID': '13'}\n"
                + body
            )
            return subprocess.run([sys.executable, "-c", program], cwd=ROOT, env=env,
                                  capture_output=True, text=True)

    def test_failed_package_queries_preserve_stderr(self):
        for overrides, diagnostic in [
            ({"BROTLI_TEST_QUERY_EXIT": "1", "BROTLI_TEST_QUERY_STDERR":
              "dpkg-query: no packages found matching libbrotli1"}, "no packages found"),
            ({"BROTLI_TEST_LIST_EXIT": "2", "BROTLI_TEST_LIST_STDERR":
              "dpkg-query: cannot read package file list"}, "cannot read package file list"),
        ]:
            with self.subTest(overrides=overrides):
                result = self.run_checker(**overrides)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(diagnostic, result.stderr)
                self.assertIn("Brotli acceptance failed:", result.stderr)

    def test_ldd_failure_preserves_stderr(self):
        result = self.run_checker(body=(
            "try:\n"
            "    checker.check_node(Path('/usr/bin/node'), Path('/usr/lib/example/libbrotlidec.so.1'))\n"
            "except subprocess.CalledProcessError:\n"
            "    raise SystemExit(1)\n"
        ))
        self.assertEqual(result.returncode, 1)
        self.assertIn("ldd: cannot inspect executable", result.stderr)

    def test_package_query_warning_does_not_corrupt_record(self):
        result = self.run_checker(body="print(checker.check_package()[0])",
                                  BROTLI_TEST_QUERY_STDERR="dpkg-query: warning: package metadata warning\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "1.1.0-2+deb13u1")
        self.assertIn("package metadata warning", result.stderr)

    def test_invalid_package_status_has_its_own_diagnostic(self):
        for status in ("deinstall ok config-files", "install ok unpacked", "install reinstreq half-installed"):
            with self.subTest(status=status):
                result = self.run_checker(BROTLI_TEST_RECORD=status + "\n1.1.0-2+deb13u1")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("libbrotli1 is not fully installed", result.stderr)
                self.assertIn(status, result.stderr)
                self.assertNotIn("fixed floor", result.stderr)

    def test_malformed_package_records_have_actionable_diagnostic(self):
        for record in ("", "install ok installed", "install ok installed\n", "\n1.1.0-2+deb13u1",
                       "install ok installed\n1.1.0-2+deb13u1\nextra"):
            with self.subTest(record=record):
                result = self.run_checker(BROTLI_TEST_RECORD=record)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("invalid libbrotli1 package record: expected status and version", result.stderr)
                self.assertNotIn("unpack", result.stderr)
                self.assertNotIn("fixed floor", result.stderr)


def decoder(marker=b"_ERROR_FORMAT_BLOCK_SWITCH", result=1, decoded=b"hello world"):
    def decompress(encoded_size, encoded, size, output):
        # Check the valid fixture as well as the caller's output handling.
        assert encoded_size == len(encoded) == 15
        ctypes.memmove(output, decoded, len(decoded))
        ctypes.cast(size, ctypes.POINTER(ctypes.c_size_t))[0] = len(decoded)
        return result

    return SimpleNamespace(BrotliDecoderErrorString=lambda code: marker,
                           BrotliDecoderDecompress=decompress)


class DecoderTests(unittest.TestCase):
    def test_fixed_debian_12_plain_marker_is_accepted(self):
        checker.check_decoder(decoder(marker=b"BLOCK_SWITCH"))

    def test_fixed_native_decoder_is_accepted_without_upstream_version_metadata(self):
        # A Debian 1.1.0 backport has no new upstream version requirement.
        checker.check_decoder(decoder())

    def test_unpatched_native_error_table_is_rejected(self):
        for marker in (b"INVALID", b"_ERROR_FORMAT_DISTANCE", b"BLOCK_SWITCH_EXTRA", None):
            with self.subTest(marker=marker), self.assertRaisesRegex(ValueError, "BLOCK_SWITCH"):
                checker.check_decoder(decoder(marker=marker))

    def test_fix_marker_does_not_replace_valid_decoding_control(self):
        for marker in (b"BLOCK_SWITCH", b"_ERROR_FORMAT_BLOCK_SWITCH"):
            for native in (decoder(marker=marker, result=0), decoder(marker=marker, decoded=b"wrong bytes"),
                           decoder(marker=marker, decoded=b"hello")):
                with self.subTest(marker=marker, native=native), self.assertRaisesRegex(ValueError, "valid-stream"):
                    checker.check_decoder(native)

    def test_node_must_link_the_same_decoder(self):
        native = Path("/usr/lib/example/libbrotlidec.so.1")
        for linked, accepted in [
            (f"libbrotlidec.so.1 => {native} (0x1234)\n", True),
            ("libbrotlidec.so.1 => /usr/local/lib/libbrotlidec.so.1 (0x1234)", False),
            ("libbrotlidec.so.1 => not found", False),
            ("statically linked", False),
        ]:
            with self.subTest(linked=linked), patch.object(checker, "command", return_value=linked):
                if accepted:
                    checker.check_node(Path("/usr/bin/node"), native)
                else:
                    with self.assertRaisesRegex(ValueError, "does not select"):
                        checker.check_node(Path("/usr/bin/node"), native)

    def test_a_shadow_decoder_is_rejected_even_with_a_fixed_package_installed(self):
        with patch.object(checker, "check_package", return_value=("1.1.0-2+deb13u1", {Path("/usr/lib/decoder")})), \
             patch.object(checker.ctypes, "CDLL"), \
             patch.object(checker, "loaded_path", return_value=Path("/usr/local/lib/decoder")), \
             contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(checker.main([]), 1)
        self.assertIn("outside libbrotli1", stderr.getvalue())


class ImageRecipeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("sh"), "Docker RUN guard requires a POSIX shell")
    def test_base_ca_guard_failure_stops_before_package_network_access(self):
        base = (ROOT / "Dockerfile").read_text(encoding="utf-8").split("# ---- builder:", 1)[0]
        # Execute the actual first RUN with command stubs. A failed CA check must
        # stop the shell before either changing sources or invoking APT.
        command = base.split("\nRUN ", 1)[1].split("\n\n", 1)[0].replace("\\\n", "")
        self.assertLess(base.index("COPY scripts/check_ca_trust.py"), base.index("\nRUN "))
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            trace = directory / "commands"
            for name in ("python", "sed", "apt-get"):
                stub = directory / name
                stub.write_text('#!/bin/sh\nprintf "%s\\n" "$0 $*" >> "$TRACE"\n' +
                                ("exit 1\n" if name == "python" else "exit 0\n"))
                stub.chmod(0o755)
            result = subprocess.run([shutil.which("sh"), "-c", command],
                                    env={"PATH": tmp, "TRACE": str(trace)}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(trace.read_text().splitlines(),
                             [f"{directory}/python /usr/local/bin/check-ca-trust.py --system"])

    def test_builder_and_runtime_inherit_the_guarded_package_layer(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        # This declarative inheritance is not exercised by Python unit tests.
        _, base, builder, runtime = dockerfile.split("\nFROM ")
        self.assertIn("AS brotli-base", base)
        self.assertIn("apt-get install -y --no-install-recommends libbrotli1", base)
        self.assertIn("python /usr/local/bin/check-brotli.py", base)
        self.assertTrue(builder.startswith("brotli-base AS realesrgan-wheels\n"))
        self.assertTrue(runtime.startswith("brotli-base\n"))
        # Recheck Node's effective decoder after system and Python dependencies.
        gates = runtime.split("python /usr/local/bin/check-brotli.py --node /usr/bin/node")
        self.assertEqual(len(gates), 3)
        self.assertIn("nodejs", gates[0])
        self.assertIn("pip install", gates[1])


if __name__ == "__main__":
    unittest.main()
