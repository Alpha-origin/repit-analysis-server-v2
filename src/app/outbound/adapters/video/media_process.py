"""Shell-free child process runner with wall-clock, output, memory and cancellation bounds.

The RSS watchdog samples once per interval and kills the child after it crosses the threshold. It is a
monitored abort, not an instantaneous OS hard limit; deployments that need hard isolation should also
set process/container memory limits.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

STDERR_KEEP_BYTES = 64 * 1024
_CHUNK = 64 * 1024

LimitKind = Literal["timeout", "output", "memory", "missing_binary"]


class ProcessLimitError(Exception):
    def __init__(self, kind: LimitKind) -> None:
        super().__init__(kind)
        self.kind: LimitKind = kind


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: bytes  # only when collect_stdout=True
    stdout_bytes: int
    stderr_bytes: int


@dataclass(frozen=True)
class ProcessLimits:
    timeout_seconds: float
    max_stdout_bytes: int
    max_line_bytes: int
    max_rss_bytes: int
    rss_interval_seconds: float = 1.0


def _child_environment() -> dict[str, str]:
    # Minimal environment: no FFREPORT or other variables that make tools write logs/reports.
    return {"PATH": os.environ.get("PATH", ""), "LC_ALL": "C"}


async def run_bounded(
    executable: str,
    arguments: list[str],
    limits: ProcessLimits,
    *,
    on_line: Callable[[bytes], None] | None = None,
) -> ProcessResult:
    """Run ``executable`` (resolved on PATH, or an absolute path) without a shell.

    With ``on_line`` the stdout is streamed line by line (each line bounded); otherwise stdout is
    collected up to ``max_stdout_bytes``. Any exception from ``on_line`` aborts the child.
    """
    binary = _resolve(executable)
    if binary is None:
        raise ProcessLimitError("missing_binary")
    process = await asyncio.create_subprocess_exec(
        binary,
        *arguments,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_child_environment(),
        limit=limits.max_line_bytes,
        start_new_session=True,
    )
    collected = bytearray()
    counters = {"stdout": 0, "stderr": 0}
    reader = asyncio.create_task(_read_stdout(process, limits, on_line, collected, counters))
    drainer = asyncio.create_task(_drain_stderr(process, counters))
    watcher = asyncio.create_task(_watch_rss(process.pid, limits))
    try:
        try:
            async with asyncio.timeout(limits.timeout_seconds):
                done, _ = await asyncio.wait({reader, watcher}, return_when=asyncio.FIRST_COMPLETED)
                if watcher in done:
                    watcher.result()
                reader.result()
                await drainer
                returncode = await process.wait()
        except TimeoutError as exc:
            raise ProcessLimitError("timeout") from exc
    finally:
        for task in (reader, drainer, watcher):
            task.cancel()
        await asyncio.gather(reader, drainer, watcher, return_exceptions=True)
        await _reap(process)
    return ProcessResult(
        returncode=returncode,
        stdout=bytes(collected),
        stdout_bytes=counters["stdout"],
        stderr_bytes=counters["stderr"],
    )


def _resolve(executable: str) -> str | None:
    binary = executable if Path(executable).is_absolute() else shutil.which(executable)
    return binary if binary is not None and Path(binary).exists() else None


async def _read_stdout(
    process: asyncio.subprocess.Process,
    limits: ProcessLimits,
    on_line: Callable[[bytes], None] | None,
    collected: bytearray,
    counters: dict[str, int],
) -> None:
    stream = process.stdout
    if stream is None:
        return
    while True:
        if on_line is None:
            chunk = await stream.read(_CHUNK)
            if not chunk:
                return
            counters["stdout"] += len(chunk)
            if counters["stdout"] > limits.max_stdout_bytes:
                raise ProcessLimitError("output")
            collected.extend(chunk)
            continue
        try:
            line = await stream.readline()
        except (ValueError, asyncio.LimitOverrunError) as exc:
            raise ProcessLimitError("output") from exc  # a single line longer than max_line_bytes
        if not line:
            return
        counters["stdout"] += len(line)
        if counters["stdout"] > limits.max_stdout_bytes:
            raise ProcessLimitError("output")
        on_line(line.rstrip(b"\r\n"))


async def _drain_stderr(process: asyncio.subprocess.Process, counters: dict[str, int]) -> None:
    # stderr is counted only (any output at -v error means a decoder complaint); raw text is never kept.
    stream = process.stderr
    if stream is None:
        return
    while chunk := await stream.read(_CHUNK):
        counters["stderr"] += len(chunk)


async def _watch_rss(pid: int, limits: ProcessLimits) -> None:
    while True:
        await asyncio.sleep(limits.rss_interval_seconds)
        rss = await resident_bytes(pid)
        if rss is not None and rss > limits.max_rss_bytes:
            raise ProcessLimitError("memory")


def _linux_rss(pid: int) -> int | None:
    with contextlib.suppress(OSError, ValueError):
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    return None


async def resident_bytes(pid: int) -> int | None:
    if sys.platform.startswith("linux"):
        return _linux_rss(pid)
    else:
        ps = shutil.which("ps")
        if ps is None:
            return None
        probe = await asyncio.create_subprocess_exec(
            ps, "-o", "rss=", "-p", str(pid), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        output, _ = await probe.communicate()
        try:
            return int(output.decode().strip()) * 1024
        except ValueError:
            return None


async def _reap(process: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    # Always wait so no zombie or orphaned decoder survives cancellation, lease loss or limits.
    await asyncio.shield(process.wait())
