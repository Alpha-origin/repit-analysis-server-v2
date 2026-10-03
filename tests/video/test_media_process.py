"""Real (harmless) Python children exercise every bound of the media runner."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

from app.outbound.adapters.video.media_process import ProcessLimitError, ProcessLimits, run_bounded

LIMITS = ProcessLimits(timeout_seconds=10, max_stdout_bytes=1024, max_line_bytes=256, max_rss_bytes=2**40)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def _pid_of(
    directory: Path, script: str, limits: ProcessLimits, expected: str, *, streaming: bool = False
) -> int:
    """Run a child that writes its pid to a file, assert the expected limit, return the pid."""
    pid_file = directory / f"{expected}.pid"
    pid_file.unlink(missing_ok=True)
    code = f"import os,pathlib; pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); {script}"
    with pytest.raises(ProcessLimitError) as caught:
        await run_bounded(sys.executable, ["-c", code], limits, on_line=(lambda line: None) if streaming else None)
    assert caught.value.kind == expected
    pid = int(pid_file.read_text())
    pid_file.unlink()
    return pid


@pytest.mark.asyncio
async def test_bounded_child_success() -> None:
    lines: list[bytes] = []
    result = await run_bounded(sys.executable, ["-c", "print('a=1|b=2'); print('c=3')"], LIMITS, on_line=lines.append)
    assert result.returncode == 0
    assert lines == [b"a=1|b=2", b"c=3"]
    collected = await run_bounded(
        sys.executable, ["-c", "import sys; sys.stdout.write('x'*100); sys.stderr.write('warn')"], LIMITS
    )
    assert (collected.stdout, collected.stderr_bytes) == (b"x" * 100, 4)  # stderr counted, never kept


@pytest.mark.asyncio
async def test_timeout_cancel_and_limits_reap_child(tmp_path: Path) -> None:
    hang = "import time; time.sleep(60)"
    pid = await _pid_of(tmp_path, hang, ProcessLimits(0.5, 1024, 256, 2**40), "timeout")
    assert not _alive(pid)
    pid = await _pid_of(
        tmp_path, "import sys; sys.stdout.write('x'*5000); sys.stdout.flush(); " + hang, LIMITS, "output"
    )
    assert not _alive(pid)
    pid = await _pid_of(
        tmp_path,
        "import sys; sys.stdout.write('y'*1000 + chr(10)); sys.stdout.flush(); " + hang,
        ProcessLimits(10, 10_000, 256, 2**40),
        "output",
        streaming=True,
    )  # one over-long line
    assert not _alive(pid)
    pid = await _pid_of(
        tmp_path,
        "blob = bytearray(64 * 1024 * 1024); " + hang,
        ProcessLimits(10, 1024, 256, 16 * 1024 * 1024, rss_interval_seconds=0.1),
        "memory",
    )
    assert not _alive(pid)
    # Cancellation (lease loss) also kills and reaps the child.
    pid_file = tmp_path / "cancel.pid"
    code = f"import os,pathlib,time; pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(60)"
    task = asyncio.create_task(run_bounded(sys.executable, ["-c", code], LIMITS))
    for _ in range(50):
        if pid_file.exists() and pid_file.read_text():
            break
        await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not _alive(int(pid_file.read_text()))
    pid_file.unlink()


@pytest.mark.asyncio
async def test_on_line_violation_aborts_child() -> None:
    def reject(line: bytes) -> None:
        raise ValueError("limit")

    with pytest.raises(ValueError, match="limit"):
        await run_bounded(
            sys.executable, ["-u", "-c", "import time; print('frame'); time.sleep(60)"], LIMITS, on_line=reject
        )


@pytest.mark.asyncio
async def test_missing_binary_is_internal_error(tmp_path: Path) -> None:
    with pytest.raises(ProcessLimitError) as caught:
        await run_bounded(str(tmp_path / "ffprobe"), [], LIMITS)
    assert caught.value.kind == "missing_binary"
    with pytest.raises(ProcessLimitError):
        await run_bounded("definitely-not-a-real-binary-name", [], LIMITS)
