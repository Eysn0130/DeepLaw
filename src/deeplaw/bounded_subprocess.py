from __future__ import annotations

import math
import os
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


class BoundedSubprocessError(RuntimeError):
    """Raised when a child exceeds its I/O or wall-clock contract."""

    def __init__(
        self,
        message: str,
        *,
        returncode: int | None = None,
        stdout: bytes = b"",
        stderr: bytes = b"",
        stdout_truncated: bool = False,
        stderr_truncated: bool = False,
    ) -> None:
        super().__init__(message)
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
        else:
            process.kill()
    except ProcessLookupError:
        return


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
        )
    except OSError as error:
        raise BoundedSubprocessError("bounded subprocess failed to start") from error
    if process.stdin is None or process.stdout is None or process.stderr is None:
        _kill(process)
        process.wait()
        raise BoundedSubprocessError("bounded subprocess pipes are unavailable")

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
    while process.poll() is None:
        if stdout.exceeded.is_set():
            failure = f"stdout exceeded {max_stdout_bytes} bytes"
            break
        if stderr.exceeded.is_set():
            failure = f"stderr exceeded {max_stderr_bytes} bytes"
            break
        if time.monotonic() >= deadline:
            failure = f"timed out after {timeout_seconds:g} seconds"
            break
        time.sleep(0.01)
    if failure is not None:
        _kill(process)
    process.wait()
    for thread in threads:
        thread.join(timeout=2)
    if failure is None and stdout.exceeded.is_set():
        failure = f"stdout exceeded {max_stdout_bytes} bytes"
    if failure is None and stderr.exceeded.is_set():
        failure = f"stderr exceeded {max_stderr_bytes} bytes"
    if failure is not None:
        raise BoundedSubprocessError(
            f"bounded subprocess {failure}",
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
