# services/shell/service.py
"""Shell service — safe command execution."""

from dataclasses import dataclass
from typing import Optional, AsyncIterator
import asyncio
from pathlib import Path


@dataclass
class ShellResult:
    """Result of a shell command."""
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False


class ShellService:
    """
    Shell execution service.

    Usage:
        service = ShellService()
        result = await service.execute("ls -la")
        print(result.stdout)
    """

    def __init__(self, timeout: int = 30, max_output: int = 200_000):
        self.timeout = timeout
        self.max_output = max_output
        self.cwd = str(Path.home())

    async def execute(
        self,
        command: str,
        timeout: Optional[int] = None,
        cwd: Optional[str] = None,
    ) -> ShellResult:
        """
        Execute a shell command.

        Args:
            command: Shell command to run
            timeout: Timeout in seconds (default: self.timeout)
            cwd: Working directory (default: home)

        Returns:
            ShellResult with stdout, stderr, exit_code
        """
        timeout = timeout or self.timeout
        cwd = cwd or self.cwd

        proc = None
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            stdout = stdout_b.decode(errors="replace")[:self.max_output]
            stderr = stderr_b.decode(errors="replace")[:self.max_output]
            return ShellResult(
                stdout=stdout,
                stderr=stderr,
                exit_code=proc.returncode,
            )
        except asyncio.TimeoutError:
            if proc:
                try:
                    proc.kill()
                    await proc.wait()
                except ProcessLookupError:
                    pass
            return ShellResult(
                stdout="",
                stderr=f"Command timed out after {timeout}s",
                exit_code=-1,
                timed_out=True,
            )
        except Exception as e:
            return ShellResult(stdout="", stderr=str(e), exit_code=-1)

    async def stream(
        self,
        command: str,
        timeout: int = 120,
    ) -> AsyncIterator[dict]:
        """
        Execute a command and stream output.

        Yields:
            {"stream": "stdout"|"stderr", "data": line}
            {"exit_code": int}
        """

        proc = None
        reader_tasks = []
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
            )

            q: asyncio.Queue = asyncio.Queue()

            async def _reader(stream, name):
                try:
                    while True:
                        line = await stream.readline()
                        if not line:
                            break
                        await q.put((name, line.decode(errors="replace").rstrip("\n")))
                finally:
                    await q.put((name, None))

            reader_tasks = [
                asyncio.create_task(_reader(proc.stdout, "stdout")),
                asyncio.create_task(_reader(proc.stderr, "stderr")),
            ]

            loop = asyncio.get_running_loop()
            finished = 0
            deadline = loop.time() + timeout
            while finished < 2:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise asyncio.TimeoutError()

                try:
                    name, text = await asyncio.wait_for(q.get(), timeout=min(remaining, 2.0))
                except asyncio.TimeoutError:
                    continue

                if text is None:
                    finished += 1
                    continue
                yield {"stream": name, "data": text}

            await proc.wait()
            yield {"exit_code": proc.returncode}

        except asyncio.TimeoutError:
            if proc:
                try:
                    proc.kill()
                    await proc.wait()
                except ProcessLookupError:
                    pass
            yield {"stream": "stderr", "data": f"Command timed out after {timeout}s"}
            yield {"exit_code": -1}
        except Exception as e:
            yield {"stream": "stderr", "data": str(e)}
            yield {"exit_code": -1}
        finally:
            for t in reader_tasks:
                t.cancel()
