"""Owner boundary regressions without starting a VM or model."""

import array
import hashlib
import json
import os
import socket
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from benchmarks.hosts import native_slot_owner_preflight as owner
from benchmarks.hosts.native_slot_frames import FrameKind, read_frame, write_frame


@pytest.mark.skipif(os.name != "posix", reason="native POSIX file boundary")
def test_freeze_preserves_exact_bytes_and_rejects_replacement(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"immutable input")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    target = tmp_path / "frozen"
    assert owner._freeze(source, target, digest) == {"sha256": digest, "bytes": 15}
    assert target.read_bytes() == source.read_bytes()
    assert target.stat().st_mode & 0o777 == 0o400
    with pytest.raises(FileExistsError):
        owner._freeze(source, target, digest)
    link = tmp_path / "link"
    link.symlink_to(source)
    with pytest.raises(OSError):
        owner._freeze(link, tmp_path / "unsafe", digest)
    assert not (tmp_path / "unsafe").exists()


@pytest.mark.skipif(os.name != "posix", reason="native POSIX file boundary")
def test_freeze_rejects_wrong_digest(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"changed")
    with pytest.raises(owner.NativePreflightError, match="input_digest_mismatch"):
        owner._freeze(source, tmp_path / "frozen", "0" * 64)


@pytest.mark.parametrize(
    "sequence,formal,accepted", [(1, False, True), (2, False, False), (1, True, False)]
)
def test_control_exchange_binds_sequence_and_nonformal_status(sequence, formal, accepted):
    left, right = socket.socketpair()
    left.settimeout(2)
    right.settimeout(2)

    def server():
        with right:
            request = read_frame(right)
            assert request.kind == FrameKind.CONTROL_REQUEST
            assert json.loads(request.payload) == {"op": "health"}
            write_frame(
                right,
                FrameKind.CONTROL_REPLY,
                sequence,
                json.dumps(
                    {
                        "ok": True,
                        "result": {"healthy": True},
                        "formal_admission": formal,
                    }
                ).encode(),
            )

    with left, ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(server)
        if accepted:
            assert owner._exchange(left, 1, "health") == {"healthy": True}
        else:
            with pytest.raises(owner.NativePreflightError):
                owner._exchange(left, 1, "health")
        future.result(timeout=2)


@pytest.mark.skipif(os.name != "posix", reason="native POSIX file boundary")
def test_freeze_rejects_fifo_without_waiting_for_writer(tmp_path):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(owner.NativePreflightError, match="input_file_invalid"):
        owner._freeze(fifo, tmp_path / "frozen", "0" * 64)
    assert not (tmp_path / "frozen").exists()


def test_fork_reply_timeout_is_transport_option_only():
    left, right = socket.socketpair()

    def server():
        with right:
            request = read_frame(right, timeout=2)
            assert json.loads(request.payload) == {"op": "fork", "session_id": "parent"}
            write_frame(right, FrameKind.CONTROL_REPLY, 4, json.dumps({
                "ok": True, "result": {"session_id": "child"}, "formal_admission": False,
            }).encode(), timeout=2)

    with left, ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(server)
        assert owner._exchange(
            left, 4, "fork", session_id="parent", reply_timeout=31,
        ) == {"session_id": "child"}
        future.result(timeout=2)


@pytest.mark.skipif(not hasattr(socket, "SCM_RIGHTS"), reason="SCM_RIGHTS unavailable")
def test_handoff_accepts_native_waitall_flag_and_transfers_one_socket():
    directory = tempfile.TemporaryDirectory(prefix="dl-fd-", dir="/tmp")
    path = Path(directory.name) / "fd"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(1)
    guest, peer = socket.socketpair()

    def server():
        accepted, _ = listener.accept()
        with accepted:
            accepted.sendmsg(
                [b"DLVZ\x01\x01\x00\x10" + (4050).to_bytes(4, "big") + b"\0" * 4],
                [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [guest.fileno()]))],
            )

    with directory, listener, guest, peer, ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(server)
        with owner._guest_fd(path) as received:
            received.sendall(b"probe")
            assert peer.recv(5) == b"probe"
        future.result(timeout=2)

@pytest.mark.parametrize("tamper,expected", [
    (None, None),
    ("tree_digest", "process_tree_digest_invalid"),
    ("cpu_window", "process_cpu_windows_invalid"),
    ("source", "process_observation_gap"),
    ("tree_gap", "process_tree_gap"),
])
@pytest.mark.skipif(os.name != "posix", reason="native input freeze is POSIX")
def test_owner_validates_separate_observation_over_control_frames(
    tmp_path, monkeypatch, tamper, expected,
):
    from benchmarks.hosts import linux_role_launcher as launcher

    def digest(value):
        value["record_sha256"] = hashlib.sha256(json.dumps(
            {k: v for k, v in value.items() if k != "record_sha256"},
            sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        return value

    tree = digest({"status": "gap" if tamper == "tree_gap" else "observed",
                   "formal_admission": False, "claim_eligible": False})
    if tamper == "tree_digest":
        tree["record_sha256"] = "0" * 64
    observation = digest({
        "formal_admission": False, "source_bound": tamper != "source", "cpu_windows_closed": True,
        "tree_receipt": tree,
        "native_cpu_windows": [{"cpu": 0, "start_sequence": 1, "end_sequence": 3,
                                "event_count": 9 if tamper == "cpu_window" else 3},
                               {"cpu": 1, "start_sequence": 4, "end_sequence": 5,
                                "event_count": 2}],
    })
    receipt = launcher._receipt(
        status="observed", config_sha256="a" * 64, failure_codes=[], native_mutation=True,
        roles=[{"role": role, "uid": uid, "exit_code": 0, "cgroup_populated_checked": True,
                "cgroup_populated": 0} for role, uid in (("host", 1000), ("mcp", 1001))],
    )
    client, server = socket.socketpair()
    client.settimeout(2)
    server.settimeout(2)

    class FakeVM:
        returncode = None

        def __init__(self, *args, **kwargs):
            (kwargs["cwd"] / "control.sock").touch()

        def poll(self):
            return self.returncode

        def wait(self, **kwargs):
            self.returncode = 0 if self.returncode is None else self.returncode
            return self.returncode

        def terminate(self):
            self.returncode = -15

    monkeypatch.setattr(owner.sys, "platform", "darwin")
    monkeypatch.setattr(owner.subprocess, "Popen", FakeVM)
    monkeypatch.setattr(owner, "_guest_fd", lambda path: client)

    def respond():
        with server:
            for seq, result in enumerate(({"healthy": True}, {"session_id": "s1"},
                                          {"connected": True}, {"session_id": "s2"},
                                          {"stopping": True}), 1):
                request = read_frame(server)
                assert request.sequence == seq
                write_frame(server, FrameKind.CONTROL_REPLY, seq, json.dumps({
                    "ok": True, "result": result, "formal_admission": False,
                }).encode())
            write_frame(server, FrameKind.FINAL, 6, json.dumps({
                "formal_admission": False, "launcher_receipt": receipt,
                "process_observation": observation,
            }).encode())

    source = tmp_path / "input"
    source.write_bytes(b"synthetic input")
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(respond)
        result = owner.run_preflight(
            native=source, kernel=source, initrd=source, destination=tmp_path / "result",
            native_sha256=sha, kernel_sha256=sha, initrd_sha256=sha,
            require_process_observation=True,
        )
        future.result(timeout=2)
    assert result["failure"] == expected
    assert result["formal_admission"] is False
    if tamper == "tree_gap":
        assert result["process_observation"]["tree_receipt"] == tree
