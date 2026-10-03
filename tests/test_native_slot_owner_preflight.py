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

from benchmarks.hosts import native_provider_bridge as bridge
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


@pytest.mark.parametrize("observed,expected", [(False, False), (True, True),
                                              (False, True), (True, False)])
def test_route_receipt_cannot_substitute_another_execution_profile(observed, expected):
    from benchmarks.hosts.linux_http_route_observer import RouteCapture

    receipt = RouteCapture(model_probe=observed).finish(
        kernel_packets=0, kernel_drops=0, namespace_sha256="a" * 64,
    )
    if observed == expected:
        assert owner._route_observation_for_profile(receipt, model_probe=expected) == receipt
    else:
        with pytest.raises(owner.NativePreflightError, match="route_observation_profile_gap"):
            owner._route_observation_for_profile(receipt, model_probe=expected)


@pytest.mark.parametrize("code,expected", [("packet_protocol_gap", "packet_protocol_gap"),
                                          ("/private/canary", "other_gap"), ({}, "other_gap")])
def test_route_capture_diagnostic_retains_only_finite_failure_codes(code, expected):
    with pytest.raises(owner.NativePreflightError, match="^route_capture_" + expected + "$"):
        owner._route_observation_for_profile(
            {"status": "gap", "failure": code, "formal_admission": False}, model_probe=True,
        )


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
    challenges = []

    def validate_execution(value, roles, expected_host, expected_binding):
        assert value == {"binding_sha256": expected_binding}
        assert expected_binding == challenges[0]
        assert expected_host == sha
        return value

    monkeypatch.setattr(owner, "_validate_execution_observation", validate_execution)

    def respond():
        with server:
            challenge = read_frame(server)
            assert challenge.sequence == 1
            request = json.loads(challenge.payload)
            assert request["op"] == "bind_execution"
            assert request["run_id"] == "native-test-run"
            assert request["candidate_id"] == "native-test-candidate"
            challenges.append(request["binding_sha256"])
            write_frame(server, FrameKind.CONTROL_REPLY, 1, json.dumps({
                "ok": True, "formal_admission": False,
                "result": {"binding_sha256": challenges[0]},
            }).encode())
            for seq, result in enumerate(({"healthy": True}, {"session_id": "s1"},
                                          {"connected": True}, {"session_id": "s2"},
                                          {"stopping": True}), 2):
                request = read_frame(server)
                assert request.sequence == seq
                write_frame(server, FrameKind.CONTROL_REPLY, seq, json.dumps({
                    "ok": True, "result": result, "formal_admission": False,
                }).encode())
            write_frame(server, FrameKind.FINAL, 7, json.dumps({
                "formal_admission": False, "launcher_receipt": receipt,
                "process_observation": observation,
                "execution_observation": {"binding_sha256": challenges[0]},
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
            expected_host_sha256=sha,
            execution_context={
                "run_id": "native-test-run", "candidate_id": "native-test-candidate",
                "candidate_binding": {"commit": "a" * 40, "tree": "b" * 40,
                                      "wheel_sha256": "c" * 64, "sdist_sha256": "d" * 64,
                                      "lock_sha256": "e" * 64},
            },
        )
        future.result(timeout=2)
    assert result["failure"] == expected
    assert result["formal_admission"] is False
    assert result["credentials_supplied"] is False
    if tamper is None:
        assert result["mcp_connected"] is True
        assert result["mcp_exercised"] is False
    if tamper == "tree_gap":
        assert result["process_observation"]["tree_receipt"] == tree


@pytest.mark.parametrize("key", ["_model_probe_forward", "_model_probe_bind_authority"])
def test_zero_model_entry_rejects_provider_forwarder_before_native_start(key):
    with pytest.raises(owner.NativePreflightError, match="zero_model_provider_forbidden"):
        owner.run_preflight(**{key: lambda *args: None})


def test_model_probe_requires_complete_observation_options_before_native_start():
    with pytest.raises(owner.NativePreflightError, match="model_probe_observations_required"):
        owner.run_model_probe(forward=lambda *args: None)


def test_model_probe_rejects_noncallable_authority_binding_before_native_start():
    with pytest.raises(owner.NativePreflightError, match="model_probe_observations_required"):
        owner.run_model_probe(
            forward=lambda *args: None, bind_authority=True,
            require_process_observation=True, require_boundary_observation=True,
            require_fork_observation=True, require_route_observation=True, fork_routes_only=True,
        )


@pytest.mark.skipif(os.name != "posix", reason="native POSIX file boundary")
@pytest.mark.parametrize("phase", ["accepted", "rejected", "authority_error"])
def test_authority_binds_only_after_exact_guest_execution_challenge(
    tmp_path, monkeypatch, phase,
):
    source = tmp_path / "input"
    source.write_bytes(b"public native fixture")
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    observed = []
    calls = []

    class Connection:
        def close(self):
            pass

    class Process:
        returncode = None

        def __init__(self, argv, **kwargs):
            Path(argv[argv.index("--fd-handoff-socket") + 1]).touch()

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def wait(self, **kwargs):
            return self.returncode

    def exchange(connection, sequence, operation, **kwargs):
        calls.append(operation)
        if operation == "bind_execution":
            return {"binding_sha256": "0" * 64 if phase == "rejected"
                    else kwargs["binding_sha256"]}
        raise owner.NativePreflightError("fixture_stop_after_challenge")

    def bind_authority(binding):
        observed.append(binding)
        if phase == "authority_error":
            raise ValueError("private callback error must not escape")

    def forward(*args):
        raise AssertionError("fixture must stop before Provider dispatch")

    monkeypatch.setattr(owner.sys, "platform", "darwin")
    monkeypatch.setattr(owner.subprocess, "Popen", Process)
    monkeypatch.setattr(owner, "_guest_fd", lambda path: Connection())
    monkeypatch.setattr(owner, "_exchange", exchange)
    result = owner.run_model_probe(
        forward=forward, bind_authority=bind_authority,
        native=source, kernel=source, initrd=source, destination=tmp_path / "result",
        native_sha256=sha, kernel_sha256=sha, initrd_sha256=sha,
        require_process_observation=True, require_boundary_observation=True,
        require_fork_observation=True, require_route_observation=True, fork_routes_only=True,
        expected_host_sha256=sha,
        execution_context={
            "run_id": "authority-test", "candidate_id": "public-candidate",
            "candidate_binding": {"commit": "a" * 40, "tree": "b" * 40,
                                  "wheel_sha256": "c" * 64, "sdist_sha256": "d" * 64,
                                  "lock_sha256": "e" * 64},
        },
    )
    if phase == "rejected":
        assert observed == []
        assert result["failure"] == "execution_challenge_reply_gap"
        assert calls == ["bind_execution"]
    else:
        assert observed == [result["execution_binding"]["binding_sha256"]]
        assert result["failure"] == (
            "fixture_stop_after_challenge" if phase == "accepted" else "authority_binding_failed"
        )
        assert calls == (["bind_execution", "health"] if phase == "accepted"
                         else ["bind_execution"])
    assert result["formal_admission"] is False
    assert result["model_invocations"] is None
    assert result["credentials_supplied"] is None


def _model_probe_evidence(*, output=3, reasoning=0, completed=1234):
    native = {
        "info": {
            "role": "assistant", "id": "message-probe", "sessionID": "session-probe",
            "providerID": "deepseek", "modelID": "deepseek-flash", "finish": "stop",
            "time": {"completed": completed},
            "tokens": {"input": 8, "output": output, "reasoning": reasoning,
                       "cache": {"read": 0, "write": 0}},
        },
        "parts": [{"type": "text", "text": bridge.PROBE_REPLY}],
    }
    client = bridge.sanitize_native_response(200, json.dumps(native).encode(), "session-probe")
    return {
        "schema_version": "deeplaw.fixed-native-model-probe/v1",
        "formal_admission": False, "mcp_functional_claim": False,
        "model_invocation_count": None, "model_task_executed": client["model_task_executed"],
        "provider_requests_admitted": 1, "provider_requests_forwarded": 1,
        "client": client, "proxy": {"admitted": 1, "rejected": 0},
        "children": {"client": {"reaped": True, "exit_code": 0},
                     "proxy": {"reaped": True, "exit_code": 0}},
        "cleanup_confirmed": True, "gaps": [],
    }


@pytest.mark.parametrize("output,reasoning", [(3, 0), (0, 2)])
def test_model_probe_observed_requires_bound_assistant_and_closed_cleanup(output, reasoning):
    value = _model_probe_evidence(output=output, reasoning=reasoning)
    assistant = value["client"]["assistant"]
    assert assistant["sessionID_sha256"] == hashlib.sha256(b"session-probe").hexdigest()
    assert assistant["time_completed"] == 1234
    assert assistant["tokens"]["output"] == output
    assert assistant["tokens"]["reasoning"] == reasoning
    assert owner._model_probe_observed(value, "session-probe") is True
    assert value["formal_admission"] is False and value["mcp_functional_claim"] is False
    assert value["model_invocation_count"] is None


@pytest.mark.parametrize("mutation", [
    "session", "zero_tokens", "unfinished", "client_missing", "proxy_missing",
    "proxy_not_admitted", "proxy_rejected", "admitted_zero", "forwarded_zero", "forwarded_extra",
    "admitted_bool", "forwarded_bool", "unreaped", "nonzero_exit", "signaled_exit", "exit_missing",
    "child_missing", "cleanup_open", "gap",
])
def test_model_probe_nonadmissible_observation_returns_false(mutation):
    value = _model_probe_evidence(
        output=0 if mutation == "zero_tokens" else 3,
        completed=None if mutation == "unfinished" else 1234,
    )
    if mutation == "session":
        value["client"]["assistant"]["sessionID_sha256"] = hashlib.sha256(b"other").hexdigest()
    elif mutation in {"client_missing", "proxy_missing"}:
        value[mutation.removesuffix("_missing")] = None
    elif mutation == "proxy_not_admitted":
        value["proxy"]["admitted"] = 0
    elif mutation == "proxy_rejected":
        value["proxy"]["rejected"] = 1
    elif mutation in {"admitted_zero", "admitted_bool", "forwarded_zero", "forwarded_bool",
                      "forwarded_extra"}:
        field = "provider_requests_" + mutation.split("_")[0]
        value[field] = True if mutation.endswith("bool") else (
            2 if mutation == "forwarded_extra" else 0
        )
    elif mutation == "unreaped":
        value["children"]["proxy"]["reaped"] = False
    elif mutation in {"nonzero_exit", "signaled_exit", "exit_missing"}:
        value["children"]["proxy"]["exit_code"] = {
            "nonzero_exit": 1, "signaled_exit": -15, "exit_missing": None,
        }[mutation]
    elif mutation == "child_missing":
        value["children"].pop("proxy")
    elif mutation == "cleanup_open":
        value["cleanup_confirmed"] = False
    elif mutation == "gap":
        value["gaps"] = ["provider_request_not_observed"]
    assert owner._model_probe_observed(value, "session-probe") is False
    # Missing evidence never becomes a zero-call claim.
    assert value["model_invocation_count"] is None


@pytest.mark.parametrize("mutation", [
    "extra_field", "missing_field", "schema", "formal", "mcp_claim", "invocations",
    "executed_integer", "cleanup_integer", "gaps_type", "gap_private", "children_type",
    "extra_child", "extra_child_field", "child_field_missing", "child_reaped_integer",
    "child_exit_bool",
])
def test_model_probe_invalid_outer_or_child_lifecycle_structure_raises(mutation):
    value = _model_probe_evidence()
    if mutation == "extra_field":
        value["raw_response"] = "public synthetic canary"
    elif mutation == "missing_field":
        value.pop("client")
    elif mutation == "schema":
        value["schema_version"] = "unknown/v1"
    elif mutation in {"formal", "mcp_claim"}:
        value["formal_admission" if mutation == "formal" else "mcp_functional_claim"] = True
    elif mutation == "invocations":
        value["model_invocation_count"] = 0
    elif mutation in {"executed_integer", "cleanup_integer"}:
        value["model_task_executed" if mutation == "executed_integer" else "cleanup_confirmed"] = 1
    elif mutation == "gaps_type":
        value["gaps"] = "not-a-list"
    elif mutation == "gap_private":
        value["gaps"] = ["/private/synthetic"]
    elif mutation == "children_type":
        value["children"] = []
    elif mutation == "extra_child":
        value["children"]["other"] = {"reaped": True, "exit_code": 0}
    elif mutation == "extra_child_field":
        value["children"]["client"]["stdout"] = "public synthetic canary"
    elif mutation == "child_field_missing":
        value["children"]["client"].pop("exit_code")
    elif mutation == "child_reaped_integer":
        value["children"]["client"]["reaped"] = 1
    else:
        value["children"]["client"]["exit_code"] = False
    with pytest.raises(owner.NativePreflightError, match=r"^model_probe_observation_invalid$"):
        owner._model_probe_observed(value, "session-probe")


@pytest.mark.parametrize("mutation", [
    "proxy_extra", "proxy_bool", "proxy_out_of_bound", "client_extra", "client_status_bool",
    "client_hash", "assistant_extra", "assistant_role", "assistant_provider", "assistant_model",
    "message_hash", "tokens_extra", "tokens_bool", "completion_bool", "completion_bound",
    "execution_mismatch",
])
def test_model_probe_invalid_closed_native_metadata_raises(mutation):
    value = _model_probe_evidence()
    client, proxy = value["client"], value["proxy"]
    assistant = client["assistant"]
    if mutation == "proxy_extra":
        proxy["raw"] = "public synthetic canary"
    elif mutation == "proxy_bool":
        proxy["admitted"] = True
    elif mutation == "proxy_out_of_bound":
        proxy["admitted"] = 2
    elif mutation == "client_extra":
        client["raw"] = "public synthetic canary"
    elif mutation == "client_status_bool":
        client["http_status"] = True
    elif mutation == "client_hash":
        client["http_response_sha256"] = "invalid"
    elif mutation == "assistant_extra":
        assistant["sessionID"] = "session-probe"
    elif mutation in {"assistant_role", "assistant_provider", "assistant_model"}:
        field = {"assistant_role": "role", "assistant_provider": "providerID",
                 "assistant_model": "modelID"}[mutation]
        assistant[field] = "different"
    elif mutation == "message_hash":
        assistant["messageID_sha256"] = "invalid"
    elif mutation == "tokens_extra":
        assistant["tokens"]["total"] = 11
    elif mutation == "tokens_bool":
        assistant["tokens"]["output"] = True
    elif mutation == "completion_bool":
        assistant["time_completed"] = True
    elif mutation == "completion_bound":
        assistant["time_completed"] = 10**17
    else:
        client["model_task_executed"] = False
    with pytest.raises(bridge.ProviderBridgeError, match=r"^child_observation_invalid$"):
        owner._model_probe_observed(value, "session-probe")
