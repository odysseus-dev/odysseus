"""Headless Odysseus runner as a native Windows service.

Registered and run as the `Odysseus` Windows service through the pywin32
ServiceFramework, so it speaks the Service Control Manager (SCM) protocol:
on start it reports SERVICE_START_PENDING -> SERVICE_RUNNING, and it handles a
clean SERVICE_STOP. A bare ``uvicorn.run()`` never reports to the SCM, so the
service fails to start with *"Error 1053: the service did not respond to the
start or control request in a timely fashion"*. This wrapper fixes that.

It launches uvicorn (the FastAPI app + its background TaskScheduler) on a
worker thread with no console window, so scheduled jobs keep firing after the
user closes every terminal or the desktop app. HOST/PORT come from the
environment (ODYSSEUS_HOST / ODYSSEUS_PORT), defaulting to 0.0.0.0:7000. Logs
go to data/service.log.

Command line (handled by win32serviceutil.HandleCommandLine):
    python scripts/windows_service_runner.py install|remove|start|stop|...
When the SCM launches the registered binary (no verb) it enters the service
control dispatcher instead. install-service.ps1 wraps the install/remove flow.
"""

import logging
import os
import sys
import threading
import time

# Run from the repo root so relative paths (data/, static/) resolve and
# `app:app` is importable, regardless of the SCM's default working directory
# (services start in C:\Windows\System32).
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

LOG_PATH = os.path.join(ROOT, "data", "service.log")
os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("odysseus.service")

import servicemanager
import win32event
import win32service
import win32serviceutil

# The interpreter the service is registered to run. Prefer the repo venv so the
# service is independent of whatever python invoked the installer.
_VENV_PY = os.path.join(ROOT, "venv", "Scripts", "python.exe")
_SERVICE_PY = _VENV_PY if os.path.exists(_VENV_PY) else sys.executable


def _host_port():
    host = os.getenv("ODYSSEUS_HOST", "0.0.0.0")
    port = int(os.getenv("ODYSSEUS_PORT", "7000"))
    return host, port


class OdysseusService(win32serviceutil.ServiceFramework):
    _svc_name_ = "Odysseus"
    _svc_display_name_ = "Odysseus AI Workspace"
    _svc_description_ = (
        "Odysseus self-hosted AI workspace (FastAPI + scheduler). "
        "Keeps scheduled jobs firing headlessly with no window open."
    )
    # Register the service to run the repo venv interpreter against THIS script;
    # the __main__ guard below drops into the SCM dispatcher when launched with
    # no verb. This avoids depending on pythonservice.exe / its DLL search path.
    _exe_name_ = _SERVICE_PY
    _exe_args_ = '"%s"' % os.path.abspath(__file__)

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        # Manual-reset event; SvcStop signals it, SvcDoRun blocks on it.
        self._stop_event = win32event.CreateEvent(None, 1, 0, None)
        self._server = None
        self._uvicorn_thread = None

    # --- SCM stop -------------------------------------------------------
    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING, waitHint=20000)
        logger.info("SvcStop: signalling uvicorn graceful shutdown")
        if self._server is not None:
            # uvicorn watches this flag and exits its serve loop cleanly,
            # running shutdown events (the scheduler stops too).
            self._server.should_exit = True
        win32event.SetEvent(self._stop_event)

    # --- SCM run --------------------------------------------------------
    def SvcDoRun(self):
        try:
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STARTED,
                (self._svc_name_, ""),
            )
        except Exception:
            pass
        try:
            self._run()
        except Exception:
            logger.exception("Odysseus service crashed")
            raise

    def _run(self):
        # Services have no console; std handles may be invalid under the SCM.
        # Route any stray stdout/stderr to the log so a rogue print() can never
        # crash the process. Structured logs already go to LOG_PATH via logging.
        try:
            _devnull = open(os.devnull, "w")
            sys.stdout = _devnull
            sys.stderr = _devnull
        except Exception:
            pass

        import uvicorn

        host, port = _host_port()
        logger.info("Odysseus service starting on %s:%d (cwd=%s)", host, port, ROOT)

        config = uvicorn.Config("app:app", host=host, port=port, log_config=None)
        self._server = uvicorn.Server(config)
        # Signal handlers can only be installed on the main thread; uvicorn runs
        # on a worker thread, so disable its handlers. The SCM drives shutdown
        # through should_exit instead.
        self._server.install_signal_handlers = lambda: None

        self._uvicorn_thread = threading.Thread(
            target=self._server.run, name="uvicorn", daemon=True
        )
        self._uvicorn_thread.start()

        # Hold the SCM in START_PENDING (bumping the checkpoint so it never
        # times us out -> the 1053 trap) until uvicorn has actually bound the
        # port, so SERVICE_RUNNING genuinely means "serving".
        deadline = time.time() + 90
        while not getattr(self._server, "started", False):
            if not self._uvicorn_thread.is_alive() or time.time() > deadline:
                break
            self.ReportServiceStatus(win32service.SERVICE_START_PENDING, waitHint=3000)
            time.sleep(0.4)

        if getattr(self._server, "started", False):
            self.ReportServiceStatus(win32service.SERVICE_RUNNING)
            logger.info("Odysseus service RUNNING on %s:%d", host, port)
        else:
            logger.error("uvicorn failed to start within timeout; stopping service")
            self._server.should_exit = True
            return  # framework reports SERVICE_STOPPED

        # Block until SvcStop signals, then drain uvicorn.
        win32event.WaitForSingleObject(self._stop_event, win32event.INFINITE)
        logger.info("Odysseus service stopping; waiting for uvicorn to drain")
        self._server.should_exit = True
        if self._uvicorn_thread is not None:
            self._uvicorn_thread.join(timeout=20)
        logger.info("Odysseus service stopped")


def _is_scm_launch():
    # The SCM launches the registered binary with no verb (argv == [script]).
    # A human passes install / remove / start / stop / restart / etc.
    return len(sys.argv) == 1


if __name__ == "__main__":
    if _is_scm_launch():
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(OdysseusService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(OdysseusService)
