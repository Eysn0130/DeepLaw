from __future__ import annotations

import ctypes
import math
import os
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from ctypes import wintypes
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, BinaryIO


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_longlong),
        ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong),
        ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


class _THREADENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD),
        ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", wintypes.LONG),
        ("tpDeltaPri", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
    ]


_CREATE_SUSPENDED = 0x00000004
_TH32CS_SNAPTHREAD = 0x00000004
_PROCESS_SET_QUOTA = 0x00000100
_PROCESS_TERMINATE = 0x00000001
_PROCESS_SYNCHRONIZE = 0x00100000
_PROCESS_ATTACH_ACCESS = (
    _PROCESS_SET_QUOTA | _PROCESS_TERMINATE | _PROCESS_SYNCHRONIZE
)
_THREAD_SUSPEND_RESUME = 0x0002
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102
_WAIT_FAILED = 0xFFFFFFFF
_ERROR_NO_MORE_FILES = 18
_INVALID_HANDLE_VALUES = {
    0,
    -1,
    ctypes.c_void_p(-1).value,
}
_CLEANUP_WAIT_TIMEOUT_SECONDS = 2
_START_CLEANUP_TIMEOUT_SECONDS = 2
_JOB_CLEANUP_POLL_SECONDS = 0.01


class BoundedSubprocessFailureKind(StrEnum):
    """Stable, data-free failure categories for bounded subprocesses."""

    START_FAILED = "start_failed"
    PIPES_UNAVAILABLE = "pipes_unavailable"
    TIMEOUT = "timeout"
    STDOUT_LIMIT = "stdout_limit"
    STDERR_LIMIT = "stderr_limit"
    CLEANUP_UNCONFIRMED = "cleanup_unconfirmed"


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


def _handle_value(handle: object) -> int | None:
    value = getattr(handle, "value", handle)
    return value if type(value) is int else None


def _valid_handle(handle: object) -> bool:
    value = _handle_value(handle)
    return value is not None and value not in _INVALID_HANDLE_VALUES


def _last_error() -> int | None:
    try:
        return ctypes.get_last_error()
    except AttributeError:
        return None


def _clear_last_error() -> None:
    try:
        ctypes.set_last_error(0)
    except AttributeError:
        return


def _kernel32_for_jobs() -> object:
    """Load the small public Win32 surface used by the job guard."""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        wintypes.INT,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        wintypes.INT,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    thread_entry_pointer = ctypes.POINTER(_THREADENTRY32)
    kernel32.Thread32First.argtypes = [wintypes.HANDLE, thread_entry_pointer]
    kernel32.Thread32First.restype = wintypes.BOOL
    kernel32.Thread32Next.argtypes = [wintypes.HANDLE, thread_entry_pointer]
    kernel32.Thread32Next.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenThread.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _close_handle(kernel32: object, handle: object | None) -> bool:
    if handle is None:
        return True
    try:
        return bool(kernel32.CloseHandle(handle))
    except Exception:
        return False


def _query_job_active_processes(kernel32: object, job_handle: object) -> int | None:
    accounting = _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
    return_length = wintypes.DWORD()
    try:
        _clear_last_error()
        if not kernel32.QueryInformationJobObject(
            job_handle,
            _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
            ctypes.byref(accounting),
            ctypes.sizeof(accounting),
            ctypes.byref(return_length),
        ):
            return None
        active_processes = int(accounting.ActiveProcesses)
    except Exception:
        return None
    return active_processes if active_processes >= 0 else None


class WindowsJobGuard:
    """Own one creation-time Windows Job Object and its cleanup proof."""

    __slots__ = ("_cleanup_result", "_job_handle", "_kernel32", "_lock")

    def __init__(self, kernel32: object, job_handle: object) -> None:
        if not _valid_handle(job_handle):
            raise ValueError("invalid Windows job handle")
        self._kernel32 = kernel32
        self._job_handle = job_handle
        self._cleanup_result: bool | None = None
        self._lock = threading.Lock()

    def __del__(self) -> None:
        # Explicit cleanup supplies the proof.  This is only last-resort
        # containment: KILL_ON_JOB_CLOSE may terminate members, but a
        # destructor path must never be reported as confirmed cleanup.
        try:
            with self._lock:
                if _valid_handle(self._job_handle):
                    _close_handle(self._kernel32, self._job_handle)
                    self._job_handle = None
                    self._cleanup_result = False
        except Exception:
            return

    @property
    def closed(self) -> bool:
        return self._cleanup_result is not None and not _valid_handle(self._job_handle)

    def cleanup(self, *, timeout_seconds: float = 5) -> bool:
        """Terminate remaining members and prove the Job Object is empty."""

        with self._lock:
            if self._cleanup_result is not None:
                return self._cleanup_result
            if (
                type(timeout_seconds) not in (int, float)
                or not math.isfinite(timeout_seconds)
                or timeout_seconds <= 0
                or not _valid_handle(self._job_handle)
            ):
                return False

            deadline = time.monotonic() + timeout_seconds
            success = True
            try:
                active_processes = _query_job_active_processes(
                    self._kernel32,
                    self._job_handle,
                )
                if active_processes is None:
                    success = False
                elif active_processes:
                    try:
                        _clear_last_error()
                        if not self._kernel32.TerminateJobObject(self._job_handle, 1):
                            success = False
                    except Exception:
                        success = False
                    while success:
                        if time.monotonic() >= deadline:
                            success = False
                            break
                        active_processes = _query_job_active_processes(
                            self._kernel32,
                            self._job_handle,
                        )
                        if active_processes is None:
                            success = False
                            break
                        if active_processes == 0:
                            break
                        time.sleep(
                            min(
                                _JOB_CLEANUP_POLL_SECONDS,
                                max(0, deadline - time.monotonic()),
                            )
                        )
                if time.monotonic() >= deadline:
                    success = False
            except Exception:
                success = False
            finally:
                # Closing an empty Job Object is part of the proof; closing a
                # live one invokes KILL_ON_JOB_CLOSE as last-resort containment.
                close_ok = _close_handle(self._kernel32, self._job_handle)
                if not close_ok:
                    success = False
                if close_ok:
                    self._job_handle = None
                if time.monotonic() >= deadline:
                    success = False
                self._cleanup_result = success
            return success


class WindowsJobStartError(RuntimeError):
    """Data-free error raised when a target cannot be safely attached."""


def _configure_job(kernel32: object, job_handle: object) -> bool:
    information = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    information.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    try:
        _clear_last_error()
        return bool(
            kernel32.SetInformationJobObject(
                job_handle,
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(information),
                ctypes.sizeof(information),
            )
        )
    except Exception:
        return False


def _primary_thread_id(kernel32: object, process_id: int) -> int | None:
    snapshot: object | None = None
    owner_threads: list[int] = []
    valid = True
    try:
        _clear_last_error()
        snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
        if not _valid_handle(snapshot):
            return None
        entry = _THREADENTRY32()
        entry.dwSize = ctypes.sizeof(entry)
        _clear_last_error()
        if kernel32.Thread32First(snapshot, ctypes.byref(entry)):
            while True:
                if int(entry.th32OwnerProcessID) == process_id:
                    thread_id = int(entry.th32ThreadID)
                    if thread_id <= 0:
                        valid = False
                    else:
                        owner_threads.append(thread_id)
                _clear_last_error()
                if kernel32.Thread32Next(snapshot, ctypes.byref(entry)):
                    continue
                if _last_error() != _ERROR_NO_MORE_FILES:
                    valid = False
                break
        elif _last_error() != _ERROR_NO_MORE_FILES:
            valid = False
    except Exception:
        valid = False
    finally:
        if snapshot is not None and not _close_handle(kernel32, snapshot):
            valid = False
    if not valid or len(owner_threads) != 1:
        return None
    return owner_threads[0]


def _bounded_process_wait(process: subprocess.Popen[bytes], timeout_seconds: float) -> bool:
    try:
        process.wait(timeout=max(0.001, timeout_seconds))
        return True
    except (OSError, TypeError, ValueError, subprocess.SubprocessError):
        return False


def _failed_windows_spawn_cleanup(
    process: subprocess.Popen[bytes] | None,
    guard: WindowsJobGuard | None,
    kernel32: object | None,
    process_handle: object | None,
) -> None:
    if kernel32 is not None and _valid_handle(process_handle):
        with suppress(Exception):
            kernel32.TerminateProcess(process_handle, 1)
    if process is not None:
        with suppress(OSError):
            process.kill()
        _bounded_process_wait(process, _START_CLEANUP_TIMEOUT_SECONDS)
    if guard is not None:
        guard.cleanup(timeout_seconds=_START_CLEANUP_TIMEOUT_SECONDS)


def spawn_process(
    command: Sequence[str],
    **popen_kwargs: Any,
) -> tuple[subprocess.Popen[bytes], WindowsJobGuard | None]:
    """Spawn a bounded child, attaching it to a kill-on-close Job on Windows."""

    if os.name != "nt":
        return subprocess.Popen(command, **popen_kwargs), None

    kernel32: object | None = None
    job_handle: object | None = None
    process: subprocess.Popen[bytes] | None = None
    guard: WindowsJobGuard | None = None
    process_handle: object | None = None
    temporary_handles: list[object] = []
    try:
        kernel32 = _kernel32_for_jobs()
        _clear_last_error()
        job_handle = kernel32.CreateJobObjectW(None, None)
        if not _valid_handle(job_handle) or not _configure_job(kernel32, job_handle):
            raise WindowsJobStartError("windows job process setup failed")
        guard = WindowsJobGuard(kernel32, job_handle)
        options = dict(popen_kwargs)
        creation_flags = options.get("creationflags", 0)
        if type(creation_flags) is not int:
            raise WindowsJobStartError("windows job process setup failed")
        options["creationflags"] = creation_flags | _CREATE_SUSPENDED
        process = subprocess.Popen(command, **options)
        _clear_last_error()
        process_handle = kernel32.OpenProcess(
            _PROCESS_ATTACH_ACCESS,
            False,
            process.pid,
        )
        if not _valid_handle(process_handle):
            raise WindowsJobStartError("windows job process setup failed")
        temporary_handles.append(process_handle)
        _clear_last_error()
        if not kernel32.AssignProcessToJobObject(job_handle, process_handle):
            raise WindowsJobStartError("windows job process setup failed")
        thread_id = _primary_thread_id(kernel32, process.pid)
        if thread_id is None:
            raise WindowsJobStartError("windows job process setup failed")
        _clear_last_error()
        thread_handle = kernel32.OpenThread(
            _THREAD_SUSPEND_RESUME,
            False,
            thread_id,
        )
        if not _valid_handle(thread_handle):
            raise WindowsJobStartError("windows job process setup failed")
        temporary_handles.append(thread_handle)
        _clear_last_error()
        prior_suspend_count = kernel32.ResumeThread(thread_handle)
        if prior_suspend_count != 1:
            raise WindowsJobStartError("windows job process setup failed")
        if not _close_handle(kernel32, thread_handle):
            raise WindowsJobStartError("windows job process setup failed")
        temporary_handles.remove(thread_handle)
        if not _close_handle(kernel32, process_handle):
            raise WindowsJobStartError("windows job process setup failed")
        temporary_handles.remove(process_handle)
        process_handle = None
        return process, guard
    except (OSError, ValueError, TypeError, WindowsJobStartError) as error:
        _failed_windows_spawn_cleanup(process, guard, kernel32, process_handle)
        for handle in reversed(temporary_handles):
            _close_handle(kernel32, handle)
        if guard is None and job_handle is not None and kernel32 is not None:
            _close_handle(kernel32, job_handle)
        if isinstance(error, WindowsJobStartError):
            raise error
        raise WindowsJobStartError("windows job process setup failed") from error
    except Exception as error:
        _failed_windows_spawn_cleanup(process, guard, kernel32, process_handle)
        for handle in reversed(temporary_handles):
            _close_handle(kernel32, handle)
        if guard is None and job_handle is not None and kernel32 is not None:
            _close_handle(kernel32, job_handle)
        raise WindowsJobStartError("windows job process setup failed") from error


def _kill(
    process: subprocess.Popen[bytes],
    guard: WindowsJobGuard | None = None,
) -> bool:
    if os.name == "posix":
        process_pid = getattr(process, "pid", None)
        if process_pid is None:
            if process.poll() is not None:
                return True
            with suppress(Exception):
                process.kill()
            return False
        try:
            os.killpg(process_pid, signal.SIGKILL)
        except ProcessLookupError:
            return True
        except (OSError, TypeError, ValueError):
            with suppress(Exception):
                process.kill()
            return False
        return True
    if os.name == "nt":
        cleanup_confirmed = guard is not None and guard.cleanup(timeout_seconds=5)
        with suppress(Exception):
            process.kill()
        return cleanup_confirmed
    if process.poll() is not None:
        return True
    with suppress(Exception):
        process.kill()
    return False


def _wait_after_unconfirmed_cleanup(
    process: subprocess.Popen[bytes],
    *,
    timeout_seconds: float,
) -> bool:
    wait_seconds = min(
        _CLEANUP_WAIT_TIMEOUT_SECONDS,
        max(0.001, timeout_seconds),
    )
    try:
        process.wait(timeout=wait_seconds)
        return True
    except subprocess.TimeoutExpired:
        with suppress(OSError):
            process.kill()
        try:
            process.wait(timeout=wait_seconds)
            return True
        except (
            OSError,
            TypeError,
            ValueError,
            subprocess.SubprocessError,
        ):
            return False
    except (OSError, TypeError, ValueError, subprocess.SubprocessError):
        return False


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
        process, guard = spawn_process(
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
    except (OSError, ValueError, WindowsJobStartError) as error:
        raise BoundedSubprocessError(
            "bounded subprocess failed to start",
            kind=BoundedSubprocessFailureKind.START_FAILED,
        ) from error
    if process.stdin is None or process.stdout is None or process.stderr is None:
        cleanup_confirmed = _kill(process, guard)
        if cleanup_confirmed:
            cleanup_confirmed = _bounded_process_wait(
                process,
                _CLEANUP_WAIT_TIMEOUT_SECONDS,
            )
        else:
            # A parent wait is only best-effort after the Job Object proof has
            # failed.  An exited parent cannot upgrade an unconfirmed guard
            # result: descendants may still be running.
            _wait_after_unconfirmed_cleanup(
                process,
                timeout_seconds=timeout_seconds,
            )
        failure_kind = (
            BoundedSubprocessFailureKind.PIPES_UNAVAILABLE
            if cleanup_confirmed
            else BoundedSubprocessFailureKind.CLEANUP_UNCONFIRMED
        )
        message = "bounded subprocess pipes are unavailable"
        if not cleanup_confirmed:
            message += "; cleanup could not be confirmed"
        raise BoundedSubprocessError(
            message,
            kind=failure_kind,
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
    cleanup_confirmed = True
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
    cleanup_before_join = failure is None and os.name == "posix"
    if cleanup_before_join:
        cleanup_confirmed = _kill(process, guard)
    if failure is not None:
        cleanup_confirmed = _kill(process, guard)
        if cleanup_confirmed:
            cleanup_confirmed = _bounded_process_wait(
                process,
                _CLEANUP_WAIT_TIMEOUT_SECONDS,
            )
        else:
            _wait_after_unconfirmed_cleanup(
                process,
                timeout_seconds=timeout_seconds,
            )
    for thread in threads:
        thread.join(timeout=2)
    if failure is None and stdout.exceeded.is_set():
        failure = f"stdout exceeded {max_stdout_bytes} bytes"
        failure_kind = BoundedSubprocessFailureKind.STDOUT_LIMIT
        if not cleanup_before_join:
            cleanup_confirmed = _kill(process, guard)
    if failure is None and stderr.exceeded.is_set():
        failure = f"stderr exceeded {max_stderr_bytes} bytes"
        failure_kind = BoundedSubprocessFailureKind.STDERR_LIMIT
        if not cleanup_before_join:
            cleanup_confirmed = _kill(process, guard)
    if failure is None:
        if not cleanup_before_join:
            cleanup_confirmed = _kill(process, guard)
        if not cleanup_confirmed:
            failure = "cleanup could not be confirmed"
            failure_kind = BoundedSubprocessFailureKind.CLEANUP_UNCONFIRMED
    if failure is not None and not cleanup_confirmed:
        failure_kind = BoundedSubprocessFailureKind.CLEANUP_UNCONFIRMED
    if failure is not None:
        assert failure_kind is not None
        message = f"bounded subprocess {failure}"
        if not cleanup_confirmed:
            message += "; cleanup could not be confirmed"
        raise BoundedSubprocessError(
            message,
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
