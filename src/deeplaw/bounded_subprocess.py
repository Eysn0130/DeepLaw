from __future__ import annotations

import ctypes
import math
import os
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO


class BoundedSubprocessFailureKind(StrEnum):
    """Stable, data-free failure categories for bounded subprocesses."""

    START_FAILED = "start_failed"
    PIPES_UNAVAILABLE = "pipes_unavailable"
    TIMEOUT = "timeout"
    STDOUT_LIMIT = "stdout_limit"
    STDERR_LIMIT = "stderr_limit"


class BoundedSubprocessError(RuntimeError):
    """Raised when a child exceeds its I/O or wall-clock contract."""

    def __init__(
        self,
        message: str,
        *,
        kind: BoundedSubprocessFailureKind | str,
        returncode: int | None = None,
        stdout: bytes = b"",
        stderr: bytes = b"",
        stdout_truncated: bool = False,
        stderr_truncated: bool = False,
    ) -> None:
        super().__init__(message)
        self.kind = BoundedSubprocessFailureKind(kind)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.stdout_truncated = stdout_truncated
        self.stderr_truncated = stderr_truncated


@dataclass(frozen=True, slots=True)
class BoundedProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class _Capture:
    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.value = bytearray()
        self.exceeded = threading.Event()
        self._lock = threading.Lock()

    def drain(self, stream: BinaryIO) -> None:
        try:
            while chunk := stream.read(64 * 1024):
                with self._lock:
                    remaining = self.maximum - len(self.value)
                    if remaining > 0:
                        self.value.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        self.exceeded.set()
        finally:
            stream.close()


def _send_input(stream: BinaryIO, payload: bytes) -> None:
    try:
        stream.write(payload)
        stream.flush()
    except (BrokenPipeError, OSError):
        pass
    finally:
        stream.close()


def _kill(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        elif os.name == "nt" and _kill_windows_process_tree(process.pid):
            return
        else:
            process.kill()
    except ProcessLookupError:
        return


def _kill_windows_process_tree(pid: int) -> bool:
    try:
        buffer = ctypes.create_unicode_buffer(32_768)
        length = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
    except (AttributeError, OSError):
        return False
    if not 0 < length < len(buffer):
        return False
    taskkill = Path(buffer.value) / "taskkill.exe"
    if not taskkill.is_file():
        return False
    try:
        completed = subprocess.run(
            [str(taskkill), "/PID", str(pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def run_bounded_subprocess(
    command: Sequence[str],
    *,
    input_bytes: bytes = b"",
    environment: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    timeout_seconds: float,
    max_stdout_bytes: int,
    max_stderr_bytes: int,
) -> BoundedProcessResult:
    """Run exact argv while enforcing independent stdout, stderr, and time bounds."""
    if (
        not command
        or not 0 <= len(input_bytes) <= 64 * 1024 * 1024
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
        or max_stdout_bytes < 0
        or max_stderr_bytes < 0
    ):
        raise ValueError("bounded subprocess arguments are invalid")
    try:
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=dict(environment) if environment is not None else None,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=os.name == "posix",
            creationflags=(
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                if os.name == "nt"
                else 0
            ),
        )
    except OSError as error:
        raise BoundedSubprocessError(
            "bounded subprocess failed to start",
            kind=BoundedSubprocessFailureKind.START_FAILED,
        ) from error
    if process.stdin is None or process.stdout is None or process.stderr is None:
        _kill(process)
        process.wait()
        raise BoundedSubprocessError(
            "bounded subprocess pipes are unavailable",
            kind=BoundedSubprocessFailureKind.PIPES_UNAVAILABLE,
        )

    stdout = _Capture(max_stdout_bytes)
    stderr = _Capture(max_stderr_bytes)
    threads = [
        threading.Thread(target=stdout.drain, args=(process.stdout,), daemon=True),
        threading.Thread(target=stderr.drain, args=(process.stderr,), daemon=True),
        threading.Thread(
            target=_send_input,
            args=(process.stdin, input_bytes),
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()

    deadline = time.monotonic() + timeout_seconds
    failure: str | None = None
    failure_kind: BoundedSubprocessFailureKind | None = None
    while process.poll() is None:
        if stdout.exceeded.is_set():
            failure = f"stdout exceeded {max_stdout_bytes} bytes"
            failure_kind = BoundedSubprocessFailureKind.STDOUT_LIMIT
            break
        if stderr.exceeded.is_set():
            failure = f"stderr exceeded {max_stderr_bytes} bytes"
            failure_kind = BoundedSubprocessFailureKind.STDERR_LIMIT
            break
        if time.monotonic() >= deadline:
            failure = f"timed out after {timeout_seconds:g} seconds"
            failure_kind = BoundedSubprocessFailureKind.TIMEOUT
            break
        time.sleep(0.01)
    if failure is not None:
        _kill(process)
    process.wait()
    for thread in threads:
        thread.join(timeout=2)
    if failure is None and stdout.exceeded.is_set():
        failure = f"stdout exceeded {max_stdout_bytes} bytes"
        failure_kind = BoundedSubprocessFailureKind.STDOUT_LIMIT
    if failure is None and stderr.exceeded.is_set():
        failure = f"stderr exceeded {max_stderr_bytes} bytes"
        failure_kind = BoundedSubprocessFailureKind.STDERR_LIMIT
    if failure is not None:
        assert failure_kind is not None
        raise BoundedSubprocessError(
            f"bounded subprocess {failure}",
            kind=failure_kind,
            returncode=process.returncode,
            stdout=bytes(stdout.value),
            stderr=bytes(stderr.value),
            stdout_truncated=stdout.exceeded.is_set(),
            stderr_truncated=stderr.exceeded.is_set(),
        )
    return BoundedProcessResult(
        returncode=process.returncode,
        stdout=bytes(stdout.value),
        stderr=bytes(stderr.value),
    )
