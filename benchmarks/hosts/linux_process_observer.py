"""Bounded Linux ``cn_proc`` observer for one owner-controlled slot.

The observer has one native collection window.  It subscribes to the Linux
process connector, emits one fixed ``ready`` JSON line after per-CPU start
fences, accepts one ``finish`` JSON line on stdin, and emits one final closed
observation.  It is not a daemon or a general RPC endpoint.

Process identifiers and connector payloads stay in memory while the window is
assembled.  The tree metadata component receives those in-memory events and
returns its own digest-bound, non-formal receipt; this wrapper leaves that
receipt untouched and binds only its surrounding native-window metadata.
"""

from __future__ import annotations

import hashlib
import json
import os
import select
import signal
import socket
import struct
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from itertools import pairwise
from pathlib import Path
from typing import Any, Final

if __package__:
    from .linux_proc_connector import (
        MAX_PACKET_BYTES as PROC_MAX_PACKET_BYTES,
    )
    from .linux_proc_connector import (
        ProcessConnectorError,
        ProcessEvent,
        ProcessEventWhat,
        decode_packet,
    )
    from .linux_process_tree_metadata import (
        ProcessTreeMetadataError,
        ProcessTreeMetadataGap,
        normalize_role_seeds,
        summarize_process_tree,
    )
else:  # pragma: no cover - exercised only by direct installed-file execution
    package_root = str(Path(__file__).resolve().parents[2])
    if package_root not in sys.path:
        sys.path.insert(0, package_root)
    from benchmarks.hosts.linux_proc_connector import (  # type: ignore[no-redef]
        MAX_PACKET_BYTES as PROC_MAX_PACKET_BYTES,
    )
    from benchmarks.hosts.linux_proc_connector import (
        ProcessConnectorError,
        ProcessEvent,
        ProcessEventWhat,
        decode_packet,
    )
    from benchmarks.hosts.linux_process_tree_metadata import (  # type: ignore[no-redef]
        ProcessTreeMetadataError,
        ProcessTreeMetadataGap,
        normalize_role_seeds,
        summarize_process_tree,
    )


MAX_EVENTS: Final = 8192
MAX_CPUS: Final = 2
MAX_RUNTIME_SECONDS: Final = 60.0
MAX_CONTROL_BYTES: Final = 4096
MAX_OUTPUT_BYTES: Final = 256 * 1024
NETLINK_CONNECTOR: Final = 11
PROC_MULTICAST_GROUP: Final = 1
CPU_SENTINEL: Final = 2**32 - 1
MAX_SEQUENCE: Final = 2**32

_NETLINK_HEADER = struct.Struct("<IHHII")
_SUBSCRIPTION_BODY = struct.Struct("<IIIIHHI")
_KILL_SIGNAL: Final[int] = getattr(signal, "SIGKILL", signal.SIGTERM)
_WAIT_NOHANG: Final[int] = getattr(os, "WNOHANG", 1)


def _get_affinity(pid: int) -> set[int]:
    getter = getattr(os, "sched_getaffinity", None)
    if getter is None:
        raise OSError("sched_getaffinity_unavailable")
    return set(getter(pid))


def _set_affinity(pid: int, cpus: set[int]) -> None:
    setter = getattr(os, "sched_setaffinity", None)
    if setter is None:
        raise OSError("sched_setaffinity_unavailable")
    setter(pid, cpus)


def _fork() -> int:
    fork = getattr(os, "fork", None)
    if fork is None:
        raise OSError("fork_unavailable")
    return int(fork())


def _waitpid(pid: int, options: int) -> tuple[int, int]:
    waitpid = getattr(os, "waitpid", None)
    if waitpid is None:
        raise OSError("waitpid_unavailable")
    return waitpid(pid, options)


def _kill(pid: int, signum: int) -> None:
    kill = getattr(os, "kill", None)
    if kill is None:
        raise OSError("kill_unavailable")
    kill(pid, signum)


class ProcessObserverError(ValueError):
    """A content-free observer failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ProcessObserverGap(ProcessObserverError):
    """A typed gap that cannot support a complete observation."""


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, UnicodeError, ValueError) as error:
        raise ProcessObserverError("receipt_not_canonical") from error


def _record_digest(value: Mapping[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "record_sha256"}
    return hashlib.sha256(_canonical_bytes(body)).hexdigest()


def _write_json_line(value: Mapping[str, Any]) -> None:
    encoded = _canonical_bytes(value)
    if len(encoded) + 1 > MAX_OUTPUT_BYTES:
        raise ProcessObserverGap("output_budget_exceeded")
    try:
        sys.stdout.write(encoded.decode("utf-8"))
        sys.stdout.write("\n")
        sys.stdout.flush()
    except (OSError, UnicodeError) as error:
        raise ProcessObserverGap("stdout_failed") from error


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_field")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("json_constant_invalid")


def _parse_finish_line(line: bytes) -> dict[str, Any]:
    if not isinstance(line, bytes) or not line:
        raise ProcessObserverGap("finish_json_invalid")
    try:
        value = json.loads(
            line.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise ProcessObserverGap("finish_json_invalid") from error
    if not isinstance(value, dict) or set(value) != {"op", "roots", "cgroups_empty"}:
        raise ProcessObserverGap("finish_invalid")
    if value["op"] != "finish" or value["cgroups_empty"] is not True:
        raise ProcessObserverGap("finish_invalid")
    try:
        roots = normalize_role_seeds(value["roots"])
    except (ProcessTreeMetadataGap, TypeError, ValueError) as error:
        raise ProcessObserverGap("finish_roots_invalid") from error
    return {
        "op": "finish",
        "roots": {seed.role: seed for seed in roots},
        "cgroups_empty": True,
    }


def _open_proc_socket(socket_factory: Callable[..., Any] = socket.socket) -> Any:
    if not sys.platform.startswith("linux") or not hasattr(socket, "AF_NETLINK"):
        raise ProcessObserverGap("netlink_unavailable")
    proc_socket: Any | None = None
    try:
        proc_socket = socket_factory(socket.AF_NETLINK, socket.SOCK_DGRAM, NETLINK_CONNECTOR)
        proc_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
        proc_socket.bind((os.getpid(), PROC_MULTICAST_GROUP))
        proc_socket.setblocking(False)
        body = _SUBSCRIPTION_BODY.pack(1, 1, 1, 0, 4, 0, 1)
        frame = _NETLINK_HEADER.pack(
            _NETLINK_HEADER.size + len(body),
            3,
            0,
            1,
            os.getpid(),
        ) + body
        proc_socket.sendto(frame, (0, 0))
    except (AttributeError, OSError, TypeError, ValueError, struct.error) as error:
        if proc_socket is not None:
            with suppress(Exception):
                proc_socket.close()
        raise ProcessObserverGap("netlink_subscribe_failed") from error
    return proc_socket


def _pump_socket(
    proc_socket: Any,
    records: list[ProcessEvent],
    *,
    timeout: float,
    deadline: float,
    clock: Callable[[], float] = time.monotonic,
    select_fn: Callable[..., Any] = select.select,
) -> bool:
    remaining = deadline - clock()
    if remaining <= 0:
        raise ProcessObserverGap("collector_timeout")
    wait = min(max(float(timeout), 0.0), remaining)
    try:
        readable, _writable, exceptional = select_fn([proc_socket], [], [], wait)
    except (AttributeError, OSError, TypeError, ValueError) as error:
        raise ProcessObserverGap("source_select_failed") from error
    if proc_socket in exceptional:
        raise ProcessObserverGap("source_select_failed")
    if proc_socket not in readable:
        return False
    try:
        raw, ancillary, flags, sender = proc_socket.recvmsg(PROC_MAX_PACKET_BYTES)
    except (AttributeError, OSError, TimeoutError, TypeError, ValueError) as error:
        raise ProcessObserverGap("source_receive_failed") from error
    try:
        events = decode_packet(raw, sender=sender, flags=flags, ancillary=ancillary)
    except ProcessConnectorError as error:
        raise ProcessObserverGap("source_packet_invalid") from error
    if len(records) + len(events) > MAX_EVENTS:
        raise ProcessObserverGap("event_budget_exceeded")
    records.extend(events)
    return bool(events)


def _await_subscription_ack(
    proc_socket: Any,
    records: list[ProcessEvent],
    *,
    deadline: float,
    clock: Callable[[], float] = time.monotonic,
    select_fn: Callable[..., Any] = select.select,
) -> None:
    while True:
        for event in records:
            if event.what is ProcessEventWhat.NONE:
                if event.values.get("error") != 0:
                    raise ProcessObserverGap("subscription_rejected")
                return
        if deadline <= clock():
            raise ProcessObserverGap("subscription_timeout")
        _pump_socket(
            proc_socket,
            records,
            timeout=0.05,
            deadline=deadline,
            clock=clock,
            select_fn=select_fn,
        )


def _cpu_inventory(
    get_affinity: Callable[[int], set[int]] = _get_affinity,
) -> tuple[int, ...]:
    try:
        cpus = tuple(sorted(get_affinity(0)))
    except (AttributeError, OSError, TypeError, ValueError) as error:
        raise ProcessObserverGap("cpu_inventory_invalid") from error
    if (
        not 1 <= len(cpus) <= MAX_CPUS
        or len(set(cpus)) != len(cpus)
        or any(
            isinstance(cpu, bool) or not isinstance(cpu, int) or cpu not in {0, 1}
            for cpu in cpus
        )
    ):
        raise ProcessObserverGap("cpu_inventory_invalid")
    return cpus


def _terminate_child(
    pid: int,
    *,
    kill_fn: Callable[[int, int], Any] = _kill,
    waitpid_fn: Callable[[int, int], tuple[int, int]] = _waitpid,
) -> None:
    with suppress(ChildProcessError, OSError):
        kill_fn(pid, _KILL_SIGNAL)
    with suppress(ChildProcessError, OSError):
        waitpid_fn(pid, 0)


def _wait_child(
    pid: int,
    *,
    deadline: float,
    clock: Callable[[], float] = time.monotonic,
    waitpid_fn: Callable[[int, int], tuple[int, int]] = _waitpid,
    kill_fn: Callable[[int, int], Any] = _kill,
    sleep_fn: Callable[[float], Any] = time.sleep,
) -> None:
    while True:
        if deadline <= clock():
            _terminate_child(pid, kill_fn=kill_fn, waitpid_fn=waitpid_fn)
            raise ProcessObserverGap("fence_timeout")
        try:
            waited_pid, status = waitpid_fn(pid, _WAIT_NOHANG)
        except (ChildProcessError, OSError, TypeError, ValueError) as error:
            raise ProcessObserverGap("fence_child_wait_failed") from error
        if waited_pid == 0:
            sleep_fn(min(0.01, max(0.0, deadline - clock())))
            continue
        if (
            isinstance(waited_pid, bool)
            or not isinstance(waited_pid, int)
            or waited_pid != pid
        ):
            raise ProcessObserverGap("fence_child_wait_failed")
        if isinstance(status, bool) or not isinstance(status, int) or status != 0:
            raise ProcessObserverGap("fence_exit_nonzero")
        return


def _capture_fence_with_markers(
    proc_socket: Any,
    records: list[ProcessEvent],
    cpus: Sequence[int],
    *,
    deadline: float,
    clock: Callable[[], float] = time.monotonic,
    select_fn: Callable[..., Any] = select.select,
    get_affinity: Callable[[int], set[int]] = _get_affinity,
    set_affinity: Callable[[int, set[int]], Any] = _set_affinity,
    fork_fn: Callable[[], int] = _fork,
    exit_fn: Callable[[int], Any] = os._exit,
    waitpid_fn: Callable[[int, int], tuple[int, int]] = _waitpid,
    kill_fn: Callable[[int, int], Any] = _kill,
    sleep_fn: Callable[[float], Any] = time.sleep,
) -> dict[int, ProcessEvent]:
    """Capture one child exit marker for every selected CPU."""

    try:
        original_affinity = set(get_affinity(0))
    except (AttributeError, OSError, TypeError, ValueError) as error:
        raise ProcessObserverGap("cpu_affinity_unavailable") from error
    fence_pids: dict[int, int] = {}
    children: set[int] = set()
    primary: ProcessObserverGap | None = None
    try:
        for cpu in cpus:
            try:
                set_affinity(0, {cpu})
                pid = fork_fn()
            except (OSError, TypeError, ValueError) as error:
                raise ProcessObserverGap("fence_fork_failed") from error
            if isinstance(pid, bool) or not isinstance(pid, int):
                raise ProcessObserverGap("fence_pid_invalid")
            if pid == 0:
                exit_fn(0)
                raise ProcessObserverGap("fence_child_returned")
            if pid < 0:
                raise ProcessObserverGap("fence_pid_invalid")
            if pid in children or pid in fence_pids.values():
                raise ProcessObserverGap("fence_pid_invalid")
            fence_pids[cpu] = pid
            children.add(pid)
            _wait_child(
                pid,
                deadline=deadline,
                clock=clock,
                waitpid_fn=waitpid_fn,
                kill_fn=kill_fn,
                sleep_fn=sleep_fn,
            )
            children.remove(pid)
    except ProcessObserverGap as error:
        primary = error
    finally:
        for pid in tuple(children):
            _terminate_child(pid, kill_fn=kill_fn, waitpid_fn=waitpid_fn)
        try:
            set_affinity(0, original_affinity)
        except (AttributeError, OSError, TypeError, ValueError):
            if primary is None:
                primary = ProcessObserverGap("cpu_affinity_restore_failed")
    if primary is not None:
        raise primary

    expected_by_pid = {pid: cpu for cpu, pid in fence_pids.items()}
    found: dict[int, ProcessEvent] = {}
    marker_events: dict[int, ProcessEvent] = {}
    while len(found) < len(cpus):
        for event in records:
            if event.what is not ProcessEventWhat.EXIT:
                continue
            pid = event.values.get("process_pid")
            if pid not in expected_by_pid:
                continue
            prior = marker_events.get(pid)
            if prior is event:
                continue
            if prior is not None:
                raise ProcessObserverGap("fence_marker_duplicate")
            marker_events[pid] = event
            cpu = expected_by_pid[pid]
            if (
                event.cpu != cpu
                or event.values.get("process_tgid") != pid
                or event.values.get("exit_code") != 0
            ):
                raise ProcessObserverGap("fence_identity_invalid")
            found[cpu] = event
        if len(found) == len(cpus):
            return found
        if deadline <= clock():
            raise ProcessObserverGap("fence_event_missing")
        _pump_socket(
            proc_socket,
            records,
            timeout=0.05,
            deadline=deadline,
            clock=clock,
            select_fn=select_fn,
        )
    return found


def _validate_event_cpus(records: Sequence[ProcessEvent], cpus: Sequence[int]) -> None:
    allowed = set(cpus)
    for event in records:
        if event.what is ProcessEventWhat.NONE and event.cpu == CPU_SENTINEL:
            continue
        if event.cpu not in allowed:
            raise ProcessObserverGap("event_cpu_invalid")


def _event_identity_index(events: Sequence[ProcessEvent], target: ProcessEvent) -> int:
    for index, event in enumerate(events):
        if event is target:
            return index
    raise ProcessObserverGap("cpu_window_marker_missing")


def _close_cpu_windows(
    records: Sequence[ProcessEvent],
    begin: Mapping[int, ProcessEvent],
    end: Mapping[int, ProcessEvent],
    cpus: Sequence[int],
) -> list[dict[str, int]]:
    windows: list[dict[str, int]] = []
    for cpu in cpus:
        stream = [event for event in records if event.cpu == cpu]
        start = _event_identity_index(stream, begin[cpu])
        finish = _event_identity_index(stream, end[cpu])
        if finish < start:
            raise ProcessObserverGap("cpu_window_endpoints")
        sequence = stream[start : finish + 1]
        if not sequence:
            raise ProcessObserverGap("cpu_window_endpoints")
        for previous, current in pairwise(sequence):
            if current.sequence != (previous.sequence + 1) % MAX_SEQUENCE:
                raise ProcessObserverGap("cpu_sequence_gap")
        windows.append(
            {
                "cpu": cpu,
                "start_sequence": sequence[0].sequence,
                "end_sequence": sequence[-1].sequence,
                "event_count": len(sequence),
            }
        )
    return windows


def _wrap_tree_receipt(
    tree_receipt: Mapping[str, Any],
    *,
    source_bound: bool,
    cpu_windows_closed: bool,
    native_cpu_windows: Sequence[Mapping[str, int]],
) -> dict[str, Any]:
    observation: dict[str, Any] = {
        "formal_admission": False,
        "claim_eligible": False,
        "source_bound": source_bound,
        "cpu_windows_closed": cpu_windows_closed,
        "tree_receipt": tree_receipt,
        "native_cpu_windows": [dict(window) for window in native_cpu_windows],
        "record_sha256": "",
    }
    observation["record_sha256"] = _record_digest(observation)
    return observation


def _gap_observation(code: str) -> dict[str, Any]:
    tree_receipt: dict[str, Any] = {
        "status": "gap",
        "formal_admission": False,
        "claim_eligible": False,
        "failure_codes": [code],
        "record_sha256": "",
    }
    tree_receipt["record_sha256"] = _record_digest(tree_receipt)
    return _wrap_tree_receipt(
        tree_receipt,
        source_bound=False,
        cpu_windows_closed=False,
        native_cpu_windows=(),
    )


def _run_observer(
    *,
    ready_callback: Callable[[], Any] | None = None,
    socket_factory: Callable[..., Any] = socket.socket,
    clock: Callable[[], float] = time.monotonic,
    select_fn: Callable[..., Any] = select.select,
    read_fn: Callable[[int, int], bytes] = os.read,
    stdin_fd: int | None = None,
) -> dict[str, Any]:
    deadline = clock() + MAX_RUNTIME_SECONDS
    proc_socket: Any | None = None
    records: list[ProcessEvent] = []
    result: dict[str, Any] | None = None
    pending: Exception | None = None
    try:
        proc_socket = _open_proc_socket(socket_factory)
        _await_subscription_ack(
            proc_socket,
            records,
            deadline=deadline,
            clock=clock,
            select_fn=select_fn,
        )
        cpus = _cpu_inventory()
        begin = _capture_fence_with_markers(
            proc_socket,
            records,
            cpus,
            deadline=deadline,
            clock=clock,
            select_fn=select_fn,
        )
        if ready_callback is not None:
            ready_callback()
        request = _read_finish_request(
            proc_socket,
            records,
            deadline=deadline,
            clock=clock,
            select_fn=select_fn,
            read_fn=read_fn,
            stdin_fd=sys.stdin.fileno() if stdin_fd is None else stdin_fd,
        )
        end = _capture_fence_with_markers(
            proc_socket,
            records,
            cpus,
            deadline=deadline,
            clock=clock,
            select_fn=select_fn,
        )
        _validate_event_cpus(records, cpus)
        windows = _close_cpu_windows(records, begin, end, cpus)
        tree_receipt = summarize_process_tree(
            records,
            roots=request["roots"],
            source_bound=True,
            cpu_windows_closed=True,
        )
        result = _wrap_tree_receipt(
            tree_receipt,
            source_bound=True,
            cpu_windows_closed=True,
            native_cpu_windows=windows,
        )
    except ProcessObserverGap as error:
        pending = error
    except ProcessConnectorError as error:
        pending = ProcessObserverGap("source_packet_invalid")
        pending.__cause__ = error
    except ProcessTreeMetadataError as error:
        pending = ProcessObserverGap("tree_metadata_invalid")
        pending.__cause__ = error
    except (OSError, TimeoutError) as error:
        pending = ProcessObserverGap("observer_io_failed")
        pending.__cause__ = error
    except Exception as error:
        pending = ProcessObserverGap("observer_failed")
        pending.__cause__ = error
    finally:
        if proc_socket is not None:
            try:
                proc_socket.close()
            except Exception as error:
                if pending is None:
                    pending = ProcessObserverGap("socket_cleanup_failed")
                    pending.__cause__ = error
    if pending is not None:
        raise pending
    if result is None:
        raise ProcessObserverGap("observer_result_missing")
    return result


def _read_finish_request(
    proc_socket: Any,
    records: list[ProcessEvent],
    *,
    deadline: float,
    clock: Callable[[], float] = time.monotonic,
    select_fn: Callable[..., Any] = select.select,
    read_fn: Callable[[int, int], bytes] = os.read,
    stdin_fd: int,
) -> dict[str, Any]:
    control = bytearray()
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            raise ProcessObserverGap("collector_timeout")
        try:
            readable, _writable, exceptional = select_fn(
                [proc_socket, stdin_fd], [], [], min(remaining, 0.1)
            )
        except (AttributeError, OSError, TypeError, ValueError) as error:
            raise ProcessObserverGap("control_select_failed") from error
        if proc_socket in exceptional or stdin_fd in exceptional:
            raise ProcessObserverGap("control_select_failed")
        if proc_socket in readable:
            _pump_socket(
                proc_socket,
                records,
                timeout=0,
                deadline=deadline,
                clock=clock,
                select_fn=select_fn,
            )
        if stdin_fd not in readable:
            continue
        read_size = MAX_CONTROL_BYTES - len(control) + 1
        try:
            part = read_fn(stdin_fd, read_size)
        except (OSError, TypeError, ValueError) as error:
            raise ProcessObserverGap("control_read_failed") from error
        if not part:
            raise ProcessObserverGap("control_eof")
        if not isinstance(part, bytes):
            raise ProcessObserverGap("control_read_invalid")
        control.extend(part)
        if len(control) > MAX_CONTROL_BYTES:
            raise ProcessObserverGap("control_budget")
        newline = control.find(b"\n")
        if newline < 0:
            continue
        if newline != len(control) - 1:
            raise ProcessObserverGap("control_extra")
        return _parse_finish_line(bytes(control[:newline]))


def main() -> int:
    try:
        observation = _run_observer(
            ready_callback=lambda: _write_json_line(
                {"ready": True, "formal_admission": False}
            )
        )
    except ProcessObserverGap as error:
        _write_json_line(_gap_observation(error.code))
        return 1
    except ProcessConnectorError:
        _write_json_line(_gap_observation("source_packet_invalid"))
        return 1
    except ProcessTreeMetadataError:
        _write_json_line(_gap_observation("tree_metadata_invalid"))
        return 1
    except (OSError, TimeoutError):
        _write_json_line(_gap_observation("observer_io_failed"))
        return 1
    except Exception:
        _write_json_line(_gap_observation("observer_failed"))
        return 1
    _write_json_line(observation)
    return 0 if observation["tree_receipt"].get("status") == "observed" else 1


__all__ = [
    "CPU_SENTINEL",
    "MAX_CONTROL_BYTES",
    "MAX_CPUS",
    "MAX_EVENTS",
    "MAX_RUNTIME_SECONDS",
    "ProcessObserverError",
    "ProcessObserverGap",
    "main",
]


if __name__ == "__main__":  # pragma: no cover - exercised by the guest entry point
    raise SystemExit(main())
