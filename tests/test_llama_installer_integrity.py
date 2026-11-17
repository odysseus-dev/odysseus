"""Release integrity, resumable verified download and CUDA companion-library install.

Local HTTP fixtures and fake executables only; nothing is downloaded from the internet.
"""
import hashlib
import io
import json
import re
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src import llama_runtime as lr

SERVER = "#!/usr/bin/env bash\necho 'llama-server (fake) b9'\n"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture()
def server():
    state = {"body": b"", "ranges": [], "ignore_range": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body, header = state["body"], self.headers.get("Range")
            state["ranges"].append(header)
            if header and not state["ignore_range"]:
                start = int(re.match(r"bytes=(\d+)-", header).group(1))
                self.send_response(206)
                chunk = body[start:]
            else:
                self.send_response(200)
                chunk = body
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            self.wfile.write(chunk)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{httpd.server_address[1]}/a.zip"
    yield state
    httpd.shutdown()


def _drain(gen):
    last = None
    for last in gen:
        pass
    return last


def test_download_verifies_and_publishes_only_after_digest_matches(tmp_path, server):
    server["body"] = b"x" * 300_000
    dest = tmp_path / "a.zip"
    _drain(lr.download_verified(server["url"], dest, sha256=_sha(server["body"]), size=300_000))
    assert dest.read_bytes() == server["body"]
    assert not list(tmp_path.glob("*.part*"))


def test_digest_mismatch_deletes_partial_and_never_creates_archive(tmp_path, server):
    server["body"] = b"tampered" * 1000
    dest = tmp_path / "a.zip"
    with pytest.raises(lr.DownloadError, match="SHA-256 mismatch"):
        _drain(lr.download_verified(server["url"], dest, sha256=_sha(b"expected"), size=len(server["body"])))
    assert not dest.exists() and not list(tmp_path.glob("*.part*"))


def test_missing_published_digest_refuses_download(tmp_path, server):
    with pytest.raises(lr.DownloadError, match="No published SHA-256"):
        _drain(lr.download_verified(server["url"], tmp_path / "a.zip", sha256=""))
    assert server["ranges"] == []


def test_cancelled_download_keeps_resumable_partial_and_resume_sends_range(tmp_path, server):
    server["body"] = bytes(range(256)) * 4096  # 1 MiB
    dest, digest = tmp_path / "a.zip", _sha(server["body"])
    gen = lr.download_verified(server["url"], dest, sha256=digest, size=len(server["body"]), chunk=65536)
    next(gen)
    gen.close()  # client disconnected
    part = tmp_path / "a.zip.part"
    assert part.is_file() and not dest.exists()
    held = part.stat().st_size
    assert 0 < held < len(server["body"])
    _drain(lr.download_verified(server["url"], dest, sha256=digest, size=len(server["body"])))
    assert server["ranges"][-1] == f"bytes={held}-"
    assert dest.read_bytes() == server["body"]


def test_resume_restarts_cleanly_when_server_ignores_range(tmp_path, server):
    server["body"] = b"abc" * 100_000
    server["ignore_range"] = True
    dest, digest = tmp_path / "a.zip", _sha(server["body"])
    (tmp_path / "a.zip.part").write_bytes(server["body"][:50_000])
    (tmp_path / "a.zip.part.json").write_text(json.dumps({"url": server["url"], "sha256": digest}))
    _drain(lr.download_verified(server["url"], dest, sha256=digest, size=len(server["body"])))
    assert dest.read_bytes() == server["body"]


def test_foreign_partial_is_not_resumed(tmp_path, server):
    server["body"] = b"real" * 50_000
    digest = _sha(server["body"])
    (tmp_path / "a.zip.part").write_bytes(b"someone elses bytes")
    (tmp_path / "a.zip.part.json").write_text(json.dumps({"url": "http://other/x.zip", "sha256": "0" * 64}))
    _drain(lr.download_verified(server["url"], tmp_path / "a.zip", sha256=digest, size=len(server["body"])))
    assert server["ranges"] == [None]


def test_verify_archive_checks_size_and_digest(tmp_path):
    f = tmp_path / "f.zip"
    f.write_bytes(b"data")
    assert lr.verify_archive(f, _sha(b"data"), 4)[0]
    assert "size" in lr.verify_archive(f, _sha(b"data"), 5)[1]
    assert "mismatch" in lr.verify_archive(f, _sha(b"other"))[1]
    assert not lr.verify_archive(f, "")[0]


RELEASE = {"assets": [
    {"name": "llama-b9-bin-win-cuda-12.4-x64.zip", "digest": "sha256:" + "a" * 64, "size": 1},
    {"name": "llama-b9-bin-win-cuda-13.5-x64.zip", "digest": "sha256:" + "b" * 64, "size": 2},
    {"name": "cudart-llama-bin-win-cuda-12.4-x64.zip", "browser_download_url": "u12",
     "digest": "sha256:" + "c" * 64, "size": 3},
    {"name": "cudart-llama-bin-win-cuda-13.5-x64.zip", "browser_download_url": "u13",
     "digest": "sha256:" + "d" * 64, "size": 4},
    {"name": "cudart-llama-bin-win-cuda-13.5-arm64.zip", "browser_download_url": "arm", "size": 5},
]}


def test_companion_matches_cuda_version_and_arch_and_carries_digest():
    dep = lr.select_runtime_dependencies(RELEASE, "llama-b9-bin-win-cuda-13.5-x64.zip")
    assert [d["url"] for d in dep] == ["u13"] and dep[0]["sha256"] == "d" * 64
    assert lr.select_runtime_dependencies(RELEASE, "llama-b9-bin-win-cuda-12.4-x64.zip")[0]["url"] == "u12"
    assert lr.select_runtime_dependencies(RELEASE, "llama-b9-bin-win-cpu-x64.zip") == []


def test_cuda_versions_install_side_by_side():
    assert lr.install_key("b9", "llama-b9-bin-win-cuda-13.5-x64.zip") == "b9-cuda-13.5"
    assert lr.install_key("b9", "llama-b9-bin-win-cuda-12.4-x64.zip") == "b9-cuda-12.4"
    assert lr.install_key("b9", "llama-b9-bin-win-cpu-x64.zip") == "b9"


def _main_zip(path, imports=(b"cudart64_13.dll", b"cublas64_13.dll")):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("llama-server.exe", SERVER)
        zf.writestr("ggml-cuda.dll", b"MZ\0" + b"\0".join(imports) + b"\0")
    return path


def _dep_zip(path, names=("cudart64_13.dll", "cublas64_13.dll")):
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(name, b"lib-" + name.encode())
    return path


def test_install_with_companion_is_complete_and_records_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(lr, "llama_runtime_root", lambda: tmp_path / "rt")
    main, dep = _main_zip(tmp_path / "m.zip"), _dep_zip(tmp_path / "d.zip")
    manifest = {"archives": [{"name": "m.zip", "sha256": "1" * 64}, {"name": "d.zip", "sha256": "2" * 64}]}
    ok, msg, info = lr.install_llama_archive(main, "b9-cuda-13.5", dependencies=(dep,), manifest=manifest)
    assert ok, msg
    target = lr.llama_install_dir("b9-cuda-13.5")
    assert (target / "cudart64_13.dll").is_file() and (target / lr.INSTALL_MANIFEST).is_file()
    again = lr.existing_install_matches("b9-cuda-13.5", [
        {"name": "m.zip", "sha256": "1" * 64}, {"name": "d.zip", "sha256": "2" * 64}])
    assert again and again["already_installed"]
    assert lr.existing_install_matches("b9-cuda-13.5", [{"name": "m.zip", "sha256": "9" * 64}]) is None
    entry = lr.llama_runtime_summary()["installed"][0]
    assert entry["build"] == "cuda" and entry["cuda_ready"]


def test_cuda_install_without_its_libraries_is_refused_not_silently_cpu(tmp_path, monkeypatch):
    monkeypatch.setattr(lr, "llama_runtime_root", lambda: tmp_path / "rt")
    ok, msg, info = lr.install_llama_archive(_main_zip(tmp_path / "m.zip"), "b9-cuda-13.5")
    assert not ok and "Incomplete CUDA runtime" in msg and "cudart64_13.dll" in msg
    assert not lr.llama_install_dir("b9-cuda-13.5").exists()
    assert not (tmp_path / "rt" / "b9-cuda-13.5.staging").exists()


def test_failed_cuda_install_leaves_existing_other_version_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(lr, "llama_runtime_root", lambda: tmp_path / "rt")
    ok, *_ = lr.install_llama_archive(_main_zip(tmp_path / "a.zip", (b"cudart64_12.dll",)), "b9-cuda-12.4",
                                      dependencies=(_dep_zip(tmp_path / "a-dep.zip", ("cudart64_12.dll",)),))
    assert ok
    before = sorted(p.name for p in lr.llama_install_dir("b9-cuda-12.4").iterdir())
    ok, *_ = lr.install_llama_archive(_main_zip(tmp_path / "b.zip"), "b9-cuda-13.5")
    assert not ok
    assert sorted(p.name for p in lr.llama_install_dir("b9-cuda-12.4").iterdir()) == before


def test_runtime_library_archive_with_paths_is_rejected(tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../evil.dll", b"x")
    ok, msg = lr.extract_runtime_libraries(bad, tmp_path)
    assert not ok and "Unexpected" in msg
    assert not (tmp_path.parent / "evil.dll").exists()


def test_extract_runtime_libraries_never_overwrites(tmp_path):
    (tmp_path / "cudart64_13.dll").write_bytes(b"original")
    ok, _ = lr.extract_runtime_libraries(_dep_zip(tmp_path / "d.zip"), tmp_path)
    assert ok and (tmp_path / "cudart64_13.dll").read_bytes() == b"original"
    assert (tmp_path / "cublas64_13.dll").is_file()
