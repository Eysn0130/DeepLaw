from __future__ import annotations

import json
from collections import deque

import pytest

from benchmarks.hosts import linux_process_observer as observer
from benchmarks.hosts.linux_proc_connector import ProcessEvent, ProcessEventWhat


def _roots_json() -> dict[str, dict[str, int]]:
    return {
        "host": {"pid": 100, "uid": 1000, "captured_at_ns": 20},
        "mcp": {"pid": 200, "uid": 1001, "captured_at_ns": 20},
    }


def _finish_line(**overrides: object) -> bytes:
    value: dict[str, object] = {
        "op": "finish",
        "roots": _roots_json(),
        "cgroups_empty": True,
    }
    value.update(overrides)
    return json.dumps(value, separators=(",", ":")).encode()


def _event(
    what: ProcessEventWhat,
    sequence: int,
    *,
    cpu: int,
    pid: int,
    exit_code: int = 0,
) -> ProcessEvent:
    if what is ProcessEventWhat.EXIT:
        values = {
            "process_pid": pid,
            "process_tgid": pid,
            "exit_code": exit_code,
            "exit_signal": 0,
            "parent_pid": 1,
            "parent_tgid": 1,
        }
    else:
        values = {
            "process_pid": pid,
            "process_tgid": pid,
        }
    return ProcessEvent(
        what=what,
        cpu=cpu,
        sequence=sequence,
        timestamp_ns=sequence + 1,
        values=values,
    )


def test_finish_json_is_strict_and_normalizes_owner_roots() -> None:
    request = observer._parse_finish_line(_finish_line())

    assert request["op"] == "finish"
    assert request["cgroups_empty"] is True
    assert set(request["roots"]) == {"host", "mcp"}
    assert request["roots"]["host"].pid == 100

    with pytest.raises(observer.ProcessObserverGap, match="finish_invalid"):
        observer._parse_finish_line(_finish_line(extra=True))
    with pytest.raises(observer.ProcessObserverGap, match="finish_invalid"):
        observer._parse_finish_line(_finish_line(cgroups_empty=False))
    with pytest.raises(observer.ProcessObserverGap, match="finish_roots_invalid"):
        observer._parse_finish_line(_finish_line(roots={"host": {}, "mcp": {}}))
    with pytest.raises(observer.ProcessObserverGap, match="finish_json_invalid"):
        observer._parse_finish_line(b'{"op":"finish", "op":"finish"}')


def test_finish_reader_handles_partial_input_and_rejects_extra_bytes() -> None:
    chunks = deque([_finish_line()[:19], _finish_line()[19:] + b"\n"])

    def select_stdin(_readable, _writable, _exceptional, _timeout):
        return ([3], [], [])

    def read_stdin(_fd: int, _size: int) -> bytes:
        return chunks.popleft()

    request = observer._read_finish_request(
        object(),
        [],
        deadline=10,
        clock=lambda: 0,
        select_fn=select_stdin,
        read_fn=read_stdin,
        stdin_fd=3,
    )
    assert request["roots"]["mcp"].uid == 1001

    extra = _finish_line() + b"\n{}"
    with pytest.raises(observer.ProcessObserverGap, match="control_extra"):
        observer._read_finish_request(
            object(),
            [],
            deadline=10,
            clock=lambda: 0,
            select_fn=select_stdin,
            read_fn=lambda _fd, _size: extra,
            stdin_fd=3,
        )


def test_finish_reader_reports_eof_budget_and_timeout() -> None:
    def select_stdin(_readable, _writable, _exceptional, _timeout):
        return ([3], [], [])

    with pytest.raises(observer.ProcessObserverGap, match="control_eof"):
        chunks = deque([b"{", b""])
        observer._read_finish_request(
            object(),
            [],
            deadline=10,
            clock=lambda: 0,
            select_fn=select_stdin,
            read_fn=lambda _fd, _size: chunks.popleft(),
            stdin_fd=3,
        )

    oversized = b"x" * (observer.MAX_CONTROL_BYTES + 1)
    with pytest.raises(observer.ProcessObserverGap, match="control_budget"):
        observer._read_finish_request(
            object(),
            [],
            deadline=10,
            clock=lambda: 0,
            select_fn=select_stdin,
            read_fn=lambda _fd, _size: oversized,
            stdin_fd=3,
        )

    with pytest.raises(observer.ProcessObserverGap, match="collector_timeout"):
        observer._read_finish_request(
            object(),
            [],
            deadline=1,
            clock=lambda: 2,
            select_fn=select_stdin,
            read_fn=lambda _fd, _size: b"",
            stdin_fd=3,
        )


def test_cpu_fence_restores_affinity_and_requires_all_real_markers() -> None:
    records = [
        _event(ProcessEventWhat.EXIT, 10, cpu=0, pid=501),
        _event(ProcessEventWhat.EXIT, 20, cpu=1, pid=502),
    ]
    pids = iter((501, 502))
    affinity_calls: list[set[int]] = []

    def set_affinity(_pid: int, cpus: set[int]) -> None:
        affinity_calls.append(set(cpus))

    def waitpid(pid: int, _options: int) -> tuple[int, int]:
        return pid, 0

    markers = observer._capture_fence_with_markers(
        object(),
        records,
        (0, 1),
        deadline=10,
        clock=lambda: 0,
        get_affinity=lambda _pid: {0, 1},
        set_affinity=set_affinity,
        fork_fn=lambda: next(pids),
        waitpid_fn=waitpid,
    )

    assert markers[0] is records[0]
    assert markers[1] is records[1]
    assert affinity_calls == [{0}, {1}, {0, 1}]


def test_cpu_fence_does_not_reclassify_a_buffered_marker_as_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _event(ProcessEventWhat.EXIT, 10, cpu=0, pid=511)
    second = _event(ProcessEventWhat.EXIT, 20, cpu=1, pid=512)
    records = [first]

    def pump(_socket, current, **_kwargs):
        assert current is records
        current.append(second)
        return True

    monkeypatch.setattr(observer, "_pump_socket", pump)
    markers = observer._capture_fence_with_markers(
        object(),
        records,
        (0, 1),
        deadline=10,
        clock=lambda: 0,
        get_affinity=lambda _pid: {0, 1},
        set_affinity=lambda _pid, _cpus: None,
        fork_fn=iter((511, 512)).__next__,
        waitpid_fn=lambda pid, _options: (pid, 0),
    )

    assert markers == {0: first, 1: second}


def test_cpu_inventory_rejects_unsupported_and_boolean_cpu_ids() -> None:
    with pytest.raises(observer.ProcessObserverGap, match="cpu_inventory_invalid"):
        observer._cpu_inventory(lambda _pid: {0, 1, 2})
    with pytest.raises(observer.ProcessObserverGap, match="cpu_inventory_invalid"):
        observer._cpu_inventory(lambda _pid: {True})


def test_cpu_fence_rejects_nonzero_child_and_missing_or_bad_marker() -> None:
    def wait_nonzero(pid: int, _options: int) -> tuple[int, int]:
        return pid, 256

    with pytest.raises(observer.ProcessObserverGap, match="fence_exit_nonzero"):
        observer._capture_fence_with_markers(
            object(),
            [],
            (0,),
            deadline=10,
            clock=lambda: 0,
            get_affinity=lambda _pid: {0},
            set_affinity=lambda _pid, _cpus: None,
            fork_fn=lambda: 601,
            waitpid_fn=wait_nonzero,
            kill_fn=lambda _pid, _signal: None,
        )

    now = iter((0, 2, 2, 2))

    def select_none(_readable, _writable, _exceptional, _timeout):
        return ([], [], [])

    with pytest.raises(observer.ProcessObserverGap, match="fence_event_missing"):
        observer._capture_fence_with_markers(
            object(),
            [],
            (0,),
            deadline=1,
            clock=lambda: next(now),
            select_fn=select_none,
            get_affinity=lambda _pid: {0},
            set_affinity=lambda _pid, _cpus: None,
            fork_fn=lambda: 602,
            waitpid_fn=lambda pid, _options: (pid, 0),
        )

    bad_marker = [_event(ProcessEventWhat.EXIT, 1, cpu=1, pid=603)]
    with pytest.raises(observer.ProcessObserverGap, match="fence_identity_invalid"):
        observer._capture_fence_with_markers(
            object(),
            bad_marker,
            (0,),
            deadline=10,
            clock=lambda: 0,
            get_affinity=lambda _pid: {0},
            set_affinity=lambda _pid, _cpus: None,
            fork_fn=lambda: 603,
            waitpid_fn=lambda pid, _options: (pid, 0),
        )


def test_cpu_windows_require_identity_endpoints_and_contiguous_wraparound() -> None:
    begin0 = _event(ProcessEventWhat.EXIT, 0xFFFFFFFF, cpu=0, pid=701)
    middle0 = _event(ProcessEventWhat.EXIT, 0, cpu=0, pid=702)
    end0 = _event(ProcessEventWhat.EXIT, 1, cpu=0, pid=703)
    begin1 = _event(ProcessEventWhat.EXIT, 9, cpu=1, pid=704)
    end1 = _event(ProcessEventWhat.EXIT, 10, cpu=1, pid=705)
    records = [begin0, middle0, end0, begin1, end1]
    windows = observer._close_cpu_windows(
        records,
        {0: begin0, 1: begin1},
        {0: end0, 1: end1},
        (0, 1),
    )
    assert windows == [
        {"cpu": 0, "start_sequence": 0xFFFFFFFF, "end_sequence": 1, "event_count": 3},
        {"cpu": 1, "start_sequence": 9, "end_sequence": 10, "event_count": 2},
    ]

    gap = _event(ProcessEventWhat.EXIT, 3, cpu=0, pid=706)
    with pytest.raises(observer.ProcessObserverGap, match="cpu_sequence_gap"):
        observer._close_cpu_windows(
            [begin0, gap, end0],
            {0: begin0},
            {0: end0},
            (0,),
        )


def test_source_errors_are_typed_and_socket_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(observer.sys, "platform", "linux")
    monkeypatch.setattr(observer.socket, "AF_NETLINK", 16, raising=False)

    class FakeSocket:
        closed = False

        def setsockopt(self, *_args: object) -> None:
            return None

        def bind(self, *_args: object) -> None:
            return None

        def setblocking(self, _enabled: bool) -> None:
            return None

        def sendto(self, *_args: object) -> None:
            return None

        def close(self) -> None:
            self.closed = True

    fake = FakeSocket()

    def reject(*_args: object, **_kwargs: object) -> None:
        raise observer.ProcessObserverGap("subscription_rejected")

    monkeypatch.setattr(observer, "_await_subscription_ack", reject)

    with pytest.raises(observer.ProcessObserverGap, match="subscription_rejected"):
        observer._run_observer(
            socket_factory=lambda *_args: fake,
            clock=lambda: 0,
            stdin_fd=3,
        )
    assert fake.closed is True


def test_socket_setup_failure_closes_the_created_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(observer.sys, "platform", "linux")
    monkeypatch.setattr(observer.socket, "AF_NETLINK", 16, raising=False)

    class FakeSocket:
        closed = False

        def setsockopt(self, *_args: object) -> None:
            raise OSError("setup")

        def close(self) -> None:
            self.closed = True

    fake = FakeSocket()
    with pytest.raises(observer.ProcessObserverGap, match="netlink_subscribe_failed"):
        observer._open_proc_socket(lambda *_args: fake)
    assert fake.closed is True


def test_source_packet_decode_failure_is_a_typed_gap() -> None:
    class FakeSocket:
        pass

    fake = FakeSocket()

    def select_source(_readable, _writable, _exceptional, _timeout):
        return ([fake], [], [])

    fake.recvmsg = lambda _size: (b"", [], 0, (0, 1))
    with pytest.raises(observer.ProcessObserverGap, match="source_packet_invalid"):
        observer._pump_socket(
            fake,
            [],
            timeout=0,
            deadline=1,
            clock=lambda: 0,
            select_fn=select_source,
        )


def test_child_timeout_force_kills_and_reaps_before_reporting_gap() -> None:
    calls: list[tuple[str, int]] = []
    now = iter((0, 0, 2))

    def waitpid(pid: int, options: int) -> tuple[int, int]:
        calls.append(("wait", options))
        return (0, 0) if options == observer._WAIT_NOHANG else (pid, 0)

    with pytest.raises(observer.ProcessObserverGap, match="fence_timeout"):
        observer._wait_child(
            900,
            deadline=1,
            clock=lambda: next(now),
            waitpid_fn=waitpid,
            kill_fn=lambda pid, _signal: calls.append(("kill", pid)),
            sleep_fn=lambda _seconds: None,
        )
    assert calls == [("wait", observer._WAIT_NOHANG), ("kill", 900), ("wait", 0)]


def test_wrapper_preserves_tree_receipt_hash_and_gap_has_no_fake_windows() -> None:
    tree = {
        "status": "observed",
        "formal_admission": False,
        "claim_eligible": False,
        "record_sha256": "",
    }
    tree["record_sha256"] = observer._record_digest(tree)
    windows = [{"cpu": 0, "start_sequence": 1, "end_sequence": 2, "event_count": 2}]
    observation = observer._wrap_tree_receipt(
        tree,
        source_bound=True,
        cpu_windows_closed=True,
        native_cpu_windows=windows,
    )

    assert observation["tree_receipt"] is tree
    assert observation["tree_receipt"]["record_sha256"] == tree["record_sha256"]
    assert observation["formal_admission"] is False
    assert observation["claim_eligible"] is False
    assert observation["record_sha256"] == observer._record_digest(observation)

    gap = observer._gap_observation("source_packet_invalid")
    assert gap["source_bound"] is False
    assert gap["cpu_windows_closed"] is False
    assert gap["native_cpu_windows"] == []
    assert gap["tree_receipt"]["status"] == "gap"
    assert gap["record_sha256"] == observer._record_digest(gap)


def test_main_reports_nonzero_gap_without_native_invocation(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        observer,
        "_run_observer",
        lambda **_kwargs: (_ for _ in ()).throw(observer.ProcessObserverGap("control_eof")),
    )

    assert observer.main() == 1
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    result = json.loads(lines[0])
    assert result["formal_admission"] is False
    assert result["source_bound"] is False
    assert result["cpu_windows_closed"] is False
    assert result["tree_receipt"]["failure_codes"] == ["control_eof"]
    assert "100" not in lines[0]


def test_output_budget_rejects_before_writing(capsys) -> None:
    with pytest.raises(observer.ProcessObserverGap, match="output_budget_exceeded"):
        observer._write_json_line({"value": "x" * observer.MAX_OUTPUT_BYTES})
    assert capsys.readouterr().out == ""
