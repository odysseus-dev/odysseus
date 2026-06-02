"""PR-2 Windows service / scheduler — Windows smoke test (minimal pass/fail).

Loads ``scripts/windows_service_runner.py`` and asserts ``OdysseusService``
presents a valid Service Control Manager (SCM) contract: it subclasses the
pywin32 ``ServiceFramework`` and exposes ``_svc_name_`` + ``SvcDoRun`` /
``SvcStop``. That contract is exactly what lets the service answer the SCM
start handshake instead of failing with *Error 1053*.

This deliberately does NOT install or start a real Windows service (that needs
Administrator + ``sc.exe`` and leaves persistent state) — proving the service
actually runs end-to-end is the e2e proof ``e2e/test_scheduler_fires.py``
(PR-4), which exercises the scheduler firing a due job.

The runner imports pywin32 at module load, so off-Windows this smoke SKIPs.

Run:   python e2e/smoke/pr2_service_smoke.py
Exit:  0 = PASS (or SKIP off-Windows), 1 = FAIL
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
RUNNER = os.path.join(ROOT, "scripts", "windows_service_runner.py")


def main() -> int:
    if os.name != "nt":
        print("SKIP: Windows service smoke is win32-only")
        return 0

    try:
        spec = importlib.util.spec_from_file_location("windows_service_runner", RUNNER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except ImportError as exc:
        print(f"FAIL: cannot import service runner (pywin32 missing?): {exc}")
        return 1

    import win32serviceutil

    svc = getattr(mod, "OdysseusService", None)
    if svc is None:
        print("FAIL: OdysseusService class not found")
        return 1

    host, port = mod._host_port()
    checks = [
        ("subclasses pywin32 ServiceFramework", isinstance(svc, type) and issubclass(svc, win32serviceutil.ServiceFramework)),
        ("_svc_name_ == 'Odysseus'", getattr(svc, "_svc_name_", None) == "Odysseus"),
        ("_svc_display_name_ set", bool(getattr(svc, "_svc_display_name_", ""))),
        ("SvcDoRun callable", callable(getattr(svc, "SvcDoRun", None))),
        ("SvcStop callable", callable(getattr(svc, "SvcStop", None))),
        ("_host_port() -> int port", isinstance(port, int)),
    ]
    for name, passed in checks:
        print(f"  [{'OK' if passed else 'XX'}] {name}")

    if all(passed for _, passed in checks):
        print(f"PASS: OdysseusService presents a valid SCM contract (default {host}:{port})")
        return 0
    print("FAIL: SCM contract incomplete")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
