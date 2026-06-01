"""Codex CLI device-code authentication service.

This deliberately delegates credential storage and token refresh to the
official Codex CLI. Odysseus only tracks the in-flight login state needed by
the browser UI.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from typing import Any, Optional


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
URL_RE = re.compile(r"https?://[^\s<>'\"]+")
CODE_RE = re.compile(r"\b[A-Z0-9]{4,}(?:-[A-Z0-9]{3,})+\b")
log = logging.getLogger(__name__)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class CodexAuthState:
    status: str = "idle"
    message: str = ""
    authenticated: bool = False
    auth_mode: str = ""
    verification_url: str = ""
    user_code: str = ""
    started_at: float = 0.0
    updated_at: float = field(default_factory=time.time)
    error_code: str = ""
    process_running: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "authenticated": self.authenticated,
            "codex_authenticated": self.authenticated,
            "auth_mode": self.auth_mode,
            "verification_url": self.verification_url,
            "user_code": self.user_code,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "error_code": self.error_code,
            "process_running": self.process_running,
            "device_login_active": self.process_running or self.status in {"starting", "pending"},
        }


class CodexAuthService:
    """Small async wrapper around `codex login --device-auth`."""

    def __init__(
        self,
        *,
        codex_bin: str | None = None,
        codex_home: str | None = None,
        enabled: bool | None = None,
        login_timeout_seconds: int = 15 * 60 + 30,
        code_timeout_seconds: int = 45,
    ) -> None:
        self.codex_bin = codex_bin or os.getenv("CODEX_BIN", "codex")
        self.codex_home = codex_home if codex_home is not None else os.getenv("CODEX_HOME", "")
        self.enabled = (
            enabled
            if enabled is not None
            else not _truthy(os.getenv("ODYSSEUS_CODEX_AUTH_DISABLED"))
            and _truthy(os.getenv("ODYSSEUS_CODEX_AUTH_ENABLED", "true"))
        )
        self.login_timeout_seconds = login_timeout_seconds
        self.code_timeout_seconds = code_timeout_seconds
        self._lock = asyncio.Lock()
        self._state = CodexAuthState()
        self._process: asyncio.subprocess.Process | None = None
        self._watch_task: asyncio.Task | None = None
        log.debug(
            "Codex auth service configured: enabled=%s codex_bin=%s codex_bin_from_env=%s codex_home_configured=%s",
            self.enabled,
            self.codex_bin,
            "CODEX_BIN" in os.environ,
            bool(self.codex_home),
        )

    def _candidate_bin_path(self) -> Optional[str]:
        codex_bin = os.path.expanduser(self.codex_bin)
        if os.path.sep in codex_bin or (os.path.altsep and os.path.altsep in codex_bin):
            return codex_bin
        return shutil.which(self.codex_bin)

    def _bin_path(self) -> Optional[str]:
        path = self._candidate_bin_path()
        if not path:
            return None
        return path if os.path.isfile(path) and os.access(path, os.X_OK) else None

    def _bin_diagnostics(self) -> dict[str, Any]:
        candidate = self._candidate_bin_path()
        cli_found = bool(candidate and os.path.isfile(candidate))
        cli_executable = bool(cli_found and os.access(candidate, os.X_OK))
        codex_home_path = os.path.expanduser(self.codex_home) if self.codex_home else os.path.expanduser("~/.codex")
        return {
            "configured_binary": self.codex_bin,
            "binary_source": "CODEX_BIN" if "CODEX_BIN" in os.environ else "default",
            "resolved_binary_path": candidate,
            "binary_exists": bool(candidate and os.path.exists(candidate)),
            "binary_is_file": cli_found,
            "binary_executable": cli_executable,
            "cli_found": cli_found,
            "cli_executable": cli_executable,
            "codex_home_source": "CODEX_HOME" if "CODEX_HOME" in os.environ else "default",
            "codex_home_configured": bool(self.codex_home),
            "codex_home_path": codex_home_path,
        }

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self.codex_home:
            env["CODEX_HOME"] = os.path.expanduser(self.codex_home)
        return env

    def _base_capabilities(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "method": "codex_cli",
            "sdk_available": False,
            "sdk_status": "not_available",
            "codex_cli_available": self._bin_path() is not None,
            "codex_home": "custom" if self.codex_home else "default",
            "stores_credentials": "codex_cli",
            "codex_authenticated": False,
            "device_login_active": False,
            **self._bin_diagnostics(),
        }

    def _cli_unavailable_state(self, caps: dict[str, Any]) -> CodexAuthState:
        if caps.get("cli_found"):
            return CodexAuthState(
                status="cli_not_executable",
                message="Codex CLI found but CODEX_BIN is not executable",
                error_code="cli_not_executable",
            )
        return CodexAuthState(
            status="cli_missing",
            message="Codex CLI missing or CODEX_BIN invalid",
            error_code="cli_missing",
        )

    @staticmethod
    def _classify_status_output(output: str, returncode: int) -> tuple[bool, str, str]:
        clean = ANSI_RE.sub("", output or "").strip()
        lower = clean.lower()
        if returncode == 0 and "logged in using chatgpt" in lower:
            return True, "ChatGPT", "Logged in using ChatGPT"
        if returncode == 0 and "logged in using an api key" in lower:
            return True, "API key", "Logged in using an API key"
        if returncode == 0 and "logged in using access token" in lower:
            return True, "access token", "Logged in using access token"
        if "not logged in" in lower:
            return False, "", "Not logged in"
        if clean:
            return False, "", "Codex login status returned an unrecognized response"
        return False, "", "Unable to determine Codex login status"

    async def _run_command(self, args: list[str], timeout: float = 15.0) -> tuple[int, str]:
        bin_path = self._bin_path()
        if not bin_path:
            log.warning("Codex command skipped because CLI is unavailable: %s", self._bin_diagnostics())
            return 127, "Codex CLI is not installed or CODEX_BIN is invalid"
        try:
            proc = await asyncio.create_subprocess_exec(
                bin_path,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=self._env(),
            )
        except Exception as exc:
            log.exception("Failed to start Codex command %s", args[:1])
            return 1, f"Failed to start Codex CLI: {exc.__class__.__name__}"
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            log.warning("Codex command timed out: %s", args[:1])
            return 124, "Command timed out"
        return proc.returncode or 0, (out or b"").decode("utf-8", errors="replace")

    async def status(self) -> dict[str, Any]:
        caps = self._base_capabilities()
        if not self.enabled:
            log.info("Codex auth status requested while disabled")
            return {**caps, **CodexAuthState(status="disabled", message="Codex auth is disabled", error_code="disabled").public()}
        if not caps["codex_cli_available"]:
            log.warning("Codex auth status reports missing CLI: %s", self._bin_diagnostics())
            return {**caps, **self._cli_unavailable_state(caps).public()}

        async with self._lock:
            state = self._state.public()
            running = self._process is not None and self._process.returncode is None
            if running:
                state["process_running"] = True
                return {**caps, **state}
            if (
                self._state.status in {"failed", "timeout", "canceled", "succeeded"}
                and time.time() - self._state.updated_at < 300
            ):
                return {**caps, **state}

        rc, out = await self._run_command(["login", "status"], timeout=10)
        if rc != 0:
            log.warning("Codex login status command returned rc=%s", rc)
        authenticated, mode, msg = self._classify_status_output(out, rc)
        status = "authenticated" if authenticated else "not_authenticated"
        if not authenticated:
            msg = "Codex CLI ready. Not signed in."
        return {
            **caps,
            **CodexAuthState(
                status=status,
                message=msg,
                authenticated=authenticated,
                auth_mode=mode,
            ).public(),
        }

    async def start(self) -> dict[str, Any]:
        caps = self._base_capabilities()
        if not self.enabled:
            log.info("Codex auth start requested while disabled")
            return {**caps, **CodexAuthState(status="disabled", message="Codex auth is disabled", error_code="disabled").public()}
        bin_path = self._bin_path()
        if not bin_path:
            log.warning("Codex auth start reports missing CLI: %s", self._bin_diagnostics())
            return {**caps, **self._cli_unavailable_state(caps).public()}

        current = await self.status()
        if current.get("authenticated"):
            current["status"] = "already_authenticated"
            return current

        async with self._lock:
            if self._process is not None and self._process.returncode is None:
                return {**caps, **self._state.public()}
            self._state = CodexAuthState(
                status="starting",
                message="Starting Codex device-code login",
                started_at=time.time(),
                process_running=True,
            )
            try:
                self._process = await asyncio.create_subprocess_exec(
                    bin_path,
                    "login",
                    "--device-auth",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env=self._env(),
                )
            except Exception as exc:
                log.exception("Failed to start Codex device auth")
                self._state = CodexAuthState(
                    status="failed",
                    message=f"Failed to start Codex CLI: {exc.__class__.__name__}",
                    error_code="start_failed",
                )
                self._process = None
                return {**caps, **self._state.public()}
            self._watch_task = asyncio.create_task(self._watch_login(self._process))
            return {**caps, **self._state.public()}

    async def _watch_login(self, proc: asyncio.subprocess.Process) -> None:
        url = ""
        code = ""
        code_seen_deadline = time.time() + self.code_timeout_seconds
        overall_deadline = time.time() + self.login_timeout_seconds
        last_safe_line = ""

        async def _set(**kwargs: Any) -> None:
            async with self._lock:
                for k, v in kwargs.items():
                    setattr(self._state, k, v)
                self._state.updated_at = time.time()
                self._state.process_running = proc.returncode is None

        try:
            assert proc.stdout is not None
            while True:
                if time.time() > overall_deadline:
                    await self._terminate_process(proc)
                    log.warning("Codex device auth timed out")
                    await _set(status="timeout", message="Codex device auth timed out", error_code="timeout", process_running=False)
                    return
                try:
                    raw = await asyncio.wait_for(proc.stdout.readline(), timeout=1.0)
                except asyncio.TimeoutError:
                    if not code and time.time() > code_seen_deadline:
                        await self._terminate_process(proc)
                        log.warning("Codex device auth did not produce a device code")
                        await _set(status="failed", message="Codex CLI did not produce a device code", error_code="device_code_unavailable", process_running=False)
                        return
                    if proc.returncode is not None:
                        break
                    continue
                if not raw:
                    break
                line = ANSI_RE.sub("", raw.decode("utf-8", errors="replace")).strip()
                if not line:
                    continue
                if "device code login is not enabled" in line.lower():
                    last_safe_line = "Device-code login is not enabled for this Codex account or server"
                elif "error" in line.lower():
                    last_safe_line = "Codex CLI reported an error during device-code login"
                found_url = URL_RE.search(line)
                if found_url and any(host in found_url.group(0).lower() for host in ("openai.com", "chatgpt.com")):
                    url = found_url.group(0)
                found_code = CODE_RE.search(line)
                if found_code:
                    code = found_code.group(0)
                if url and code:
                    await _set(
                        status="pending",
                        message="Waiting for browser verification",
                        verification_url=url,
                        user_code=code,
                        error_code="",
                    )
            rc = await proc.wait()
            if rc == 0:
                await _set(
                    status="succeeded",
                    message="Codex login completed",
                    authenticated=True,
                    auth_mode="ChatGPT",
                    user_code="",
                    verification_url="",
                    error_code="",
                    process_running=False,
                )
            else:
                msg = last_safe_line or "Codex device-code login failed"
                err_code = "device_auth_disabled" if "not enabled" in msg.lower() else "login_failed"
                log.warning("Codex device auth failed with rc=%s error_code=%s", rc, err_code)
                await _set(status="failed", message=msg, error_code=err_code, user_code="", verification_url="", process_running=False)
        except Exception:
            log.exception("Codex device auth watcher failed")
            await _set(status="failed", message="Codex device-code login failed", error_code="login_failed", user_code="", verification_url="", process_running=False)
        finally:
            async with self._lock:
                if self._process is proc:
                    self._process = None
                self._state.process_running = False

    async def _terminate_process(self, proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=3)
        except ProcessLookupError:
            return
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                await proc.wait()
            except Exception:
                pass

    async def cancel(self) -> dict[str, Any]:
        async with self._lock:
            proc = self._process
            task = self._watch_task
        if proc is not None and proc.returncode is None:
            await self._terminate_process(proc)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        async with self._lock:
            self._process = None
            self._watch_task = None
            self._state = CodexAuthState(status="canceled", message="Codex login canceled")
            return {**self._base_capabilities(), **self._state.public()}

    async def logout(self) -> dict[str, Any]:
        await self.cancel()
        if not self.enabled:
            log.info("Codex logout requested while disabled")
            return {**self._base_capabilities(), **CodexAuthState(status="disabled", message="Codex auth is disabled", error_code="disabled").public()}
        caps = self._base_capabilities()
        if not caps["codex_cli_available"]:
            log.warning("Codex logout reports missing CLI: %s", self._bin_diagnostics())
            return {**caps, **self._cli_unavailable_state(caps).public()}
        rc, out = await self._run_command(["logout"], timeout=20)
        clean = ANSI_RE.sub("", out or "").strip()
        if rc == 0:
            state = CodexAuthState(status="logged_out", message="Codex credentials removed")
        else:
            log.warning("Codex logout failed with rc=%s", rc)
            state = CodexAuthState(status="failed", message="Codex logout failed", error_code="logout_failed")
        async with self._lock:
            self._state = state
        return {**self._base_capabilities(), **state.public()}

    async def test(self) -> dict[str, Any]:
        status = await self.status()
        if not status.get("authenticated"):
            log.info("Codex test requested while not authenticated: status=%s error_code=%s", status.get("status"), status.get("error_code"))
            return {**status, "ok": False}
        return {**status, "ok": True}


_service: CodexAuthService | None = None


def get_codex_auth_service() -> CodexAuthService:
    global _service
    if _service is None:
        _service = CodexAuthService()
    return _service


def set_codex_auth_service(service: CodexAuthService | None) -> None:
    global _service
    _service = service
