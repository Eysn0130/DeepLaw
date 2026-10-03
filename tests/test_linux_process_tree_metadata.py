from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from benchmarks.hosts import linux_process_tree_metadata as metadata
from benchmarks.hosts.linux_proc_connector import ProcessEvent, ProcessEventWhat

HOST_PID = 100
MCP_PID = 200
HOST_CHILD_PID = 101
HOST_THREAD_PID = 102


def _roots(*, host_pid: int = HOST_PID, host_capture: int = 20) -> dict[str, dict[str, int]]:
    return {
        "host": {"pid": host_pid, "uid": 1000, "captured_at_ns": host_capture},
        "mcp": {"pid": MCP_PID, "uid": 1001, "captured_at_ns": 20},
    }


def _event(
    what: ProcessEventWhat,
    sequence: int,
    timestamp_ns: int,
    values: dict[str, int],
    *,
    cpu: int = 0,
) -> ProcessEvent:
    return ProcessEvent(
        what=what,
        cpu=cpu,
        sequence=sequence,
        timestamp_ns=timestamp_ns,
        values=values,
    )


def _fork(
    sequence: int,
    timestamp_ns: int,
    *,
    parent_pid: int,
    parent_tgid: int,
    child_pid: int,
    child_tgid: int,
    cpu: int = 0,
) -> ProcessEvent:
    return _event(
        ProcessEventWhat.FORK,
        sequence,
        timestamp_ns,
        {
            "parent_pid": parent_pid,
            "parent_tgid": parent_tgid,
            "child_pid": child_pid,
            "child_tgid": child_tgid,
        },
        cpu=cpu,
    )


def _exec(sequence: int, timestamp_ns: int, *, pid: int, tgid: int) -> ProcessEvent:
    return _event(
        ProcessEventWhat.EXEC,
        sequence,
        timestamp_ns,
        {"process_pid": pid, "process_tgid": tgid},
    )


def _exit(sequence: int, timestamp_ns: int, *, pid: int, tgid: int, code: int = 0) -> ProcessEvent:
    return _event(
        ProcessEventWhat.EXIT,
        sequence,
        timestamp_ns,
        {
            "process_pid": pid,
            "process_tgid": tgid,
            "exit_code": code,
            "exit_signal": 0,
            "parent_pid": 1,
            "parent_tgid": 1,
        },
    )


def _base_records() -> list[ProcessEvent]:
    return [
        _fork(1, 10, parent_pid=1, parent_tgid=1, child_pid=HOST_PID, child_tgid=HOST_PID),
        _fork(2, 11, parent_pid=1, parent_tgid=1, child_pid=MCP_PID, child_tgid=MCP_PID),
        _exec(3, 21, pid=HOST_PID, tgid=HOST_PID),
        _exec(4, 22, pid=MCP_PID, tgid=MCP_PID),
        _fork(
            5,
            23,
            parent_pid=HOST_PID,
            parent_tgid=HOST_PID,
            child_pid=HOST_CHILD_PID,
            child_tgid=HOST_CHILD_PID,
        ),
        _fork(
            6,
            24,
            parent_pid=HOST_PID,
            parent_tgid=HOST_PID,
            child_pid=HOST_THREAD_PID,
            child_tgid=HOST_PID,
        ),
        _exit(7, 25, pid=HOST_CHILD_PID, tgid=HOST_CHILD_PID),
        _exit(8, 26, pid=HOST_THREAD_PID, tgid=HOST_PID),
        _exit(9, 27, pid=HOST_PID, tgid=HOST_PID),
        _exit(10, 28, pid=MCP_PID, tgid=MCP_PID),
    ]


def _summarize(records, **kwargs):
    return metadata.summarize_process_tree(
        records,
        roots=_roots(),
        source_bound=True,
        cpu_windows_closed=True,
        **kwargs,
    )


def test_complete_tree_is_nonformal_and_path_free() -> None:
    receipt = _summarize(_base_records())

    assert receipt["status"] == "observed"
    assert receipt["formal_admission"] is False
    assert receipt["claim_eligible"] is False
    assert receipt["counts"] == {
        "events": 10,
        "nodes": 4,
        "forks": 4,
        "execs": 2,
        "exits": 4,
    }
    assert [item["role"] for item in receipt["roles"]] == ["host", "mcp"]
    host = receipt["roles"][0]
    assert host["root_pid_sha256"] == metadata.pid_sha256(HOST_PID)
    assert host["counts"] == {"nodes": 3, "forks": 2, "execs": 1, "exits": 3}
    assert len(host["pid_sha256"]) == 3
    assert receipt["failure_codes"] == []
    assert metadata.validate_process_tree_receipt(receipt) == receipt

    public = json.dumps(receipt, sort_keys=True)
    assert str(HOST_PID) not in public
    assert str(MCP_PID) not in public
    assert "comm" not in public.lower()


def test_canonical_hashes_bind_the_ordered_numeric_metadata() -> None:
    records = _base_records()
    first = _summarize(records)
    second = _summarize(reversed(records))
    assert second["status"] == "observed"
    assert second["graph_sha256"] == first["graph_sha256"]
    assert second["event_sequence_sha256"] == first["event_sequence_sha256"]
    assert second["record_sha256"] == first["record_sha256"]

    changed = _base_records()
    changed[-1] = _exit(10, 28, pid=MCP_PID, tgid=MCP_PID, code=1)
    changed_receipt = _summarize(changed)
    assert changed_receipt["event_sequence_sha256"] != first["event_sequence_sha256"]
    assert changed_receipt["record_sha256"] != first["record_sha256"]


def test_missing_seed_fork_is_a_gap() -> None:
    records = [
        item
        for item in _base_records()
        if not (item.what is ProcessEventWhat.FORK and item.values.get("child_pid") == HOST_PID)
    ]
    receipt = _summarize(records)
    assert receipt["status"] == "gap"
    assert "seed_birth_missing" in receipt["failure_codes"]


def test_empty_or_non_lifecycle_window_cannot_be_observed() -> None:
    empty = _summarize([])
    assert empty["status"] == "gap"
    assert "records_empty" in empty["failure_codes"]

    ack_only = _summarize([
        _event(ProcessEventWhat.NONE, 1, 10, {"error": 0}),
    ])
    assert ack_only["status"] == "gap"
    assert "seed_birth_missing" in ack_only["failure_codes"]


def test_missing_descendant_exit_is_a_gap() -> None:
    records = [
        item
        for item in _base_records()
        if not (
            item.what is ProcessEventWhat.EXIT
            and item.values.get("process_pid") == HOST_CHILD_PID
        )
    ]
    receipt = _summarize(records)
    assert receipt["status"] == "gap"
    assert "descendant_exit_missing" in receipt["failure_codes"]


def test_active_pid_double_birth_is_rejected_but_post_exit_reuse_is_generation_bound() -> None:
    duplicate = _base_records()
    duplicate.insert(
        6,
        _fork(
            11,
            24,
            parent_pid=HOST_PID,
            parent_tgid=HOST_PID,
            child_pid=HOST_CHILD_PID,
            child_tgid=HOST_CHILD_PID,
        ),
    )
    duplicate_receipt = _summarize(duplicate)
    assert duplicate_receipt["status"] == "gap"
    assert "active_pid_birth_duplicate" in duplicate_receipt["failure_codes"]

    reused = _base_records()
    reused[7:7] = [
        _fork(
            11,
            26,
            parent_pid=HOST_PID,
            parent_tgid=HOST_PID,
            child_pid=HOST_CHILD_PID,
            child_tgid=HOST_CHILD_PID,
        ),
        _exit(12, 27, pid=HOST_CHILD_PID, tgid=HOST_CHILD_PID),
    ]
    reused_receipt = _summarize(reused)
    assert reused_receipt["status"] == "observed"
    assert reused_receipt["counts"]["nodes"] == 5
    assert reused_receipt["roles"][0]["pid_sha256"].count(metadata.pid_sha256(HOST_CHILD_PID)) == 2
    assert reused_receipt["graph_sha256"] != _summarize(_base_records())["graph_sha256"]


def test_post_exit_unrelated_pid_and_tgid_reuse_is_not_pulled_into_the_role() -> None:
    records = [
        *_base_records(),
        _fork(
            11,
            29,
            parent_pid=2,
            parent_tgid=2,
            child_pid=HOST_CHILD_PID,
            child_tgid=HOST_CHILD_PID,
        ),
        _exit(12, 30, pid=HOST_CHILD_PID, tgid=HOST_CHILD_PID),
    ]
    receipt = _summarize(records)
    assert receipt["status"] == "observed"
    assert receipt["counts"]["nodes"] == 4
    assert receipt["roles"][0]["pid_sha256"].count(metadata.pid_sha256(HOST_CHILD_PID)) == 1


def test_unknown_lifecycle_pid_with_role_tgid_is_a_gap() -> None:
    records = _base_records()
    records.insert(4, _exec(11, 22, pid=999, tgid=HOST_PID))
    receipt = _summarize(records)
    assert receipt["status"] == "gap"
    assert "unknown_lifecycle_process" in receipt["failure_codes"]


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"source_bound": False}, "source_unbound"),
        ({"source_bound": None}, "source_binding_missing"),
        ({"cpu_windows_closed": False}, "cpu_window_unclosed"),
        ({"cpu_windows_closed": None}, "cpu_window_evidence_missing"),
    ],
)
def test_external_source_and_cpu_window_evidence_are_required(kwargs, code: str) -> None:
    receipt = metadata.summarize_process_tree(
        _base_records(), roots=_roots(), source_bound=kwargs.get("source_bound", True),
        cpu_windows_closed=kwargs.get("cpu_windows_closed", True),
    )
    assert receipt["status"] == "gap"
    assert code in receipt["failure_codes"]
    assert receipt["formal_admission"] is False


def test_bounded_event_iterator_stops_at_one_over_the_limit(monkeypatch) -> None:
    monkeypatch.setattr(metadata, "MAX_EVENTS", 3)
    consumed = 0

    def stream():
        nonlocal consumed
        for item in _base_records():
            consumed += 1
            yield item

    receipt = _summarize(stream())
    assert consumed == 4
    assert receipt["status"] == "gap"
    assert receipt["failure_codes"] == ["event_count_exceeded"]


def test_node_budget_is_checked_before_public_projection(monkeypatch) -> None:
    monkeypatch.setattr(metadata, "MAX_NODES", 3)
    receipt = _summarize(_base_records())
    assert receipt["status"] == "gap"
    assert "node_count_exceeded" in receipt["failure_codes"]
    assert receipt["counts"]["nodes"] <= 3


def test_comm_names_are_not_an_accepted_input_or_public_output() -> None:
    clean = _summarize(
        [
            *_base_records(),
            _event(
                ProcessEventWhat.COMM,
                11,
                29,
                {"process_pid": HOST_PID, "process_tgid": HOST_PID},
            ),
        ]
    )
    assert clean["status"] == "observed"
    assert "comm" not in json.dumps(clean).lower()

    forged = [
        *_base_records(),
        SimpleNamespace(
            what=ProcessEventWhat.COMM,
            cpu=0,
            sequence=11,
            timestamp_ns=29,
            values={
                "process_pid": HOST_PID,
                "process_tgid": HOST_PID,
                "comm": "secret",
            },
        ),
    ]
    rejected = _summarize(forged)
    assert rejected["status"] == "gap"
    assert "event_values_invalid" in rejected["failure_codes"]
    assert "secret" not in repr(rejected)


def test_root_must_exit_with_the_actual_kernel_zero_code() -> None:
    records = _base_records()
    records[-2] = _exit(9, 27, pid=HOST_PID, tgid=HOST_PID, code=1)
    receipt = _summarize(records)
    assert receipt["status"] == "gap"
    assert "root_exit_nonzero" in receipt["failure_codes"]


def test_tampered_receipt_digest_is_rejected() -> None:
    receipt = _summarize(_base_records())
    tampered = {**receipt, "counts": {**receipt["counts"], "nodes": 3}}
    with pytest.raises(metadata.ProcessTreeMetadataError, match="receipt digest differs"):
        metadata.validate_process_tree_receipt(tampered)
