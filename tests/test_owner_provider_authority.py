from __future__ import annotations

import hashlib
import io
import json
import os
import struct
import subprocess
import threading
import time
from contextlib import suppress
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from benchmarks.hosts import native_slot_frames as frames
from benchmarks.hosts import owner_provider_authority as authority
from benchmarks.hosts.native_provider_bridge import (
    ProviderBridgeError,
    decode_provider_reply,
    encode_provider_reply,
)

_BINDING = "b" * 64
_BODY = b'{ "model": "deepseek-v4-flash", "messages": [{"role":"user","content":"public"}] }'
_POSIX = pytest.mark.skipif(
    os.name != "posix", reason="owner authority pipe/kill/reap is POSIX-only",
)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _entry(tmp_path: Path, mode: str = "echo") -> tuple[Path, str]:
    # Synthetic callbacks only. The generated private entry contains neither
    # credentials nor a key loader, Provider client, Host or network operation.
    parent = tmp_path.resolve()
    parent.chmod(0o700)
    path = parent / "synthetic_authority.py"
    source = f'''from benchmarks.hosts.owner_provider_authority import serve_authority
from benchmarks.hosts import native_slot_frames as frames
import os
import time
mode = {mode!r}
calls = 0
def forward(body, seconds):
    global calls
    calls += 1
    if mode == "slow_open":
        time.sleep(20)
    if mode == "ambiguous":
        raise BrokenPipeError("payload /private/credential must not escape")
    if mode == "exit":
        os._exit(7)
    if mode == "oversized":
        return (200, "application/json", b"x" * frames.MAX_PROVIDER_REPLY_PAYLOAD)
    if mode == "response_limit":
        return (200, "application/json", b"x" * (frames.MAX_PROVIDER_REPLY_PAYLOAD - 28))
    if mode == "content_type":
        return (200, "/private/payload\\n", body)
    if mode == "counter":
        return (200, "application/json", str(calls).encode())
    return (200, "application/json", body)
result = serve_authority(forward)
raise SystemExit(0 if result["first_failure"] is None else 1)
'''
    path.write_text(source)
    path.chmod(0o600)
    return path, _sha(source.encode())


def _owner(tmp_path: Path, *, mode: str = "echo", profile: str = "fixed_probe",
           timeout: float = 10) -> authority.OwnerProviderAuthority:
    path, digest = _entry(tmp_path, mode)
    return authority.OwnerProviderAuthority(
        path, digest, profile=profile, execution_binding_sha256=_BINDING,
        timeout_seconds=timeout,
    )


def _schema() -> dict:
    path = Path(__file__).resolve().parents[1] / "contracts"
    return json.loads((path / "owner-provider-authority-protocol.v1.schema.json").read_bytes())


def _validate(value: dict) -> None:
    Draft202012Validator(_schema()).validate(value)


@_POSIX
def test_original_body_response_hash_closed_environment_and_fresh_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OWNER_SECRET_CANARY", "must-not-inherit")
    owner = _owner(tmp_path).start()
    try:
        original = _BODY + b"\n  "
        assert owner.forward(original, 2) == (200, "application/json", original)
        assert owner.process.pid != os.getpid()
        config = owner.config
        _validate(config)
        assert set(config) == {
            "schema_version", "message_kind", "profile", "model_pin", "nonce",
            "execution_binding_sha256", "deadline_monotonic_ns", "binding_sha256",
        }
        assert config["model_pin"] == "deepseek/deepseek-v4-flash"
        assert owner.forward.__name__ == "forward"
        receipt = owner.close()
        _validate(receipt)
        assert receipt["requests"] == [{
            "sequence": 1, "request_sha256": _sha(original), "request_bytes": len(original),
            "response_state": "complete", "response_sha256": _sha(original),
            "response_bytes": len(original),
        }]
        assert receipt["cleanup_confirmed"] and receipt["child_reaped"]
        assert receipt["child_exit_code"] == 0 and not receipt["authority_killed"]
        assert receipt["formal_admission"] is False
        assert len(receipt["actors"]) == 2
        assert {item["role"] for item in receipt["actors"]} == {
            "observer", "credential_authority",
        }
        assert len({item["process_identity_sha256"] for item in receipt["actors"]}) == 2
        assert receipt["process_identity_source"] == "local_popen_pid_nonce_binding"
        public = json.dumps(receipt)
        assert '"pid":' not in public and str(tmp_path) not in public
        assert original.decode().strip() not in public and "must-not-inherit" not in public
        other = _owner(tmp_path).start()
        try:
            assert other.config["nonce"] != config["nonce"]
            assert other.config["binding_sha256"] != config["binding_sha256"]
        finally:
            other.close()
    finally:
        owner.close()


@_POSIX
@pytest.mark.parametrize("profile, maximum, seconds", [
    ("fixed_probe", 1, 120), ("maintenance", 6, 180),
])
def test_profiles_are_frozen_and_budget_consumed_before_send(
    tmp_path: Path, profile: str, maximum: int, seconds: int,
) -> None:
    owner = _owner(tmp_path, profile=profile).start()
    try:
        for _ in range(maximum):
            assert owner.forward(_BODY, 2)[2] == _BODY
        with pytest.raises(authority.AuthorityError, match="authority_request_budget"):
            owner.forward(_BODY, 2)
        receipt = owner.receipt()
        _validate(receipt)
        assert receipt["consumed_requests"] == maximum
        assert receipt["child_reaped"] and receipt["cleanup_confirmed"]
        assert owner.process.poll() is not None
        with pytest.raises(authority.AuthorityError):
            owner.forward(_BODY, 2)
        assert owner.receipt()["consumed_requests"] == maximum
    finally:
        owner.close()
    path, digest = _entry(tmp_path)
    with pytest.raises(authority.AuthorityError, match="authority_config_invalid"):
        authority.OwnerProviderAuthority(path, digest, profile=profile,
                                        execution_binding_sha256=_BINDING,
                                        timeout_seconds=seconds + 0.001)
    with pytest.raises(TypeError):
        authority.OwnerProviderAuthority(path, digest, profile=profile,
                                        execution_binding_sha256=_BINDING, max_requests=6)


@_POSIX
def test_blocked_open_is_forcibly_killed_and_reaped_at_request_deadline(tmp_path: Path) -> None:
    owner = _owner(tmp_path, mode="slow_open").start()
    started = time.monotonic()
    with pytest.raises(authority.AuthorityError, match="authority_frame_timeout"):
        owner.forward(_BODY, 0.15)
    assert time.monotonic() - started < 1.5
    receipt = owner.close()
    assert receipt["child_reaped"] and receipt["authority_killed"]
    assert owner.process.poll() is not None
    assert receipt["requests"][0]["response_state"] == "unknown"
    assert receipt["requests"][0]["response_sha256"] is None
    assert receipt["consumed_requests"] == 1
    assert receipt["first_failure"]["code"] == "authority_frame_timeout"
    _validate(receipt)


@_POSIX
def test_absolute_instance_deadline_kills_even_without_an_active_reader(tmp_path: Path) -> None:
    owner = _owner(tmp_path, timeout=2).start()
    assert owner._watchdog_stop.wait(3)
    receipt = owner.close()
    assert receipt["first_failure"]["code"] == "authority_deadline_exceeded"
    assert receipt["child_reaped"] and receipt["authority_killed"]
    assert owner.process.poll() is not None
    assert receipt["consumed_requests"] == 0


@_POSIX
@pytest.mark.parametrize("mode, code", [
    ("ambiguous", "authority_callback_failed"), ("exit", "authority_frame_truncated"),
    ("oversized", "authority_response_invalid"), ("content_type", "authority_response_invalid"),
])
def test_ambiguous_callback_exit_and_response_failure_are_terminal_without_replay(
    tmp_path: Path, mode: str, code: str,
) -> None:
    owner = _owner(tmp_path, mode=mode).start()
    with pytest.raises(authority.AuthorityError, match=code):
        owner.forward(_BODY, 2)
    with pytest.raises(authority.AuthorityError, match=code):
        owner.forward(_BODY, 2)
    receipt = owner.close()
    _validate(receipt)
    assert receipt["consumed_requests"] == 1
    assert receipt["requests"][0]["response_state"] == "unknown"
    assert receipt["child_reaped"] and owner.process.poll() is not None
    public = json.dumps(receipt)
    assert "/private/" not in public and "payload" not in public and str(tmp_path) not in public


@_POSIX
def test_response_limit_uses_the_existing_finite_header_and_content_type_codec(
    tmp_path: Path,
) -> None:
    owner = _owner(tmp_path, mode="response_limit").start()
    try:
        result = owner.forward(_BODY, 3)
        assert len(encode_provider_reply(*result)) == frames.MAX_PROVIDER_REPLY_PAYLOAD
        assert owner.receipt()["requests"][0]["response_sha256"] == _sha(result[2])
    finally:
        owner.close()


@_POSIX
def test_request_limit_and_overflow_do_not_invoke_callback(tmp_path: Path) -> None:
    owner = _owner(tmp_path).start()
    try:
        raw = b"x" * frames.MAX_PROVIDER_REQUEST_PAYLOAD
        assert owner.forward(raw, 2)[2] == raw
    finally:
        owner.close()
    owner = _owner(tmp_path).start()
    try:
        with pytest.raises(authority.AuthorityError, match="authority_request_bound"):
            owner.forward(b"x" * (frames.MAX_PROVIDER_REQUEST_PAYLOAD + 1), 2)
        assert owner.receipt()["consumed_requests"] == 0
        assert owner.receipt()["child_reaped"]
    finally:
        owner.close()


@_POSIX
def test_child_rejects_replayed_sequence_without_a_second_callback(tmp_path: Path) -> None:
    owner = _owner(tmp_path, profile="maintenance", mode="counter").start()
    try:
        assert owner.forward(_BODY, 2)[2] == b"1"
        dispatch = authority._message(owner.config, "dispatch", request_sha256=_sha(_BODY),
                                      request_bytes=len(_BODY),
                                      deadline_monotonic_ns=owner.config["deadline_monotonic_ns"])
        frames.write_frame(owner.connection, frames.CONTROL_REQUEST, 2,
                           authority._encode(dispatch), timeout=2)
        frame = frames.read_frame(owner.connection, timeout=2)
        assert frame.kind == frames.FINAL
        value = json.loads(frame.payload)
        _validate(value)
        assert value["diagnostic"]["code"] == "authority_sequence_invalid"
        assert value["formal_admission"] is False
        owner.process.wait(timeout=2)
        assert owner.process.returncode == 1
        with pytest.raises(authority.AuthorityError, match="authority_stop_unconfirmed"):
            owner.close()
        assert owner.receipt()["consumed_requests"] == 1
        assert owner.receipt()["cleanup_confirmed"] is False
        assert owner.receipt()["child_reaped"]
    finally:
        if not owner.child_reaped:
            owner._kill_and_reap()


@_POSIX
def test_child_enforces_profile_budget_independently(tmp_path: Path) -> None:
    owner = _owner(tmp_path, mode="counter").start()
    try:
        assert owner.forward(_BODY, 2)[2] == b"1"
        value = authority._message(owner.config, "dispatch", request_sha256=_sha(_BODY),
                                   request_bytes=len(_BODY),
                                   deadline_monotonic_ns=owner.config["deadline_monotonic_ns"])
        frames.write_frame(owner.connection, frames.CONTROL_REQUEST, 3,
                           authority._encode(value), timeout=2)
        result = frames.read_frame(owner.connection, timeout=2)
        assert json.loads(result.payload)["diagnostic"]["code"] == "authority_request_budget"
        owner.process.wait(timeout=2)
        assert owner.process.returncode == 1
    finally:
        with pytest.raises(authority.AuthorityError):
            owner.close()


@_POSIX
def test_cleanup_failure_retains_first_failure_and_still_reaps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _owner(tmp_path, mode="slow_open").start()
    real_wait = owner.process.wait
    calls = 0

    def failed_once(*args: object, **kwargs: object) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("/private/key cleanup must not escape")
        return real_wait(*args, **kwargs)

    monkeypatch.setattr(owner.process, "wait", failed_once)
    with pytest.raises(authority.AuthorityError, match="authority_frame_timeout"):
        owner.forward(_BODY, 0.1)
    with pytest.raises(authority.AuthorityError, match="authority_stop_unconfirmed"):
        owner.close()
    receipt = owner.receipt()
    _validate(receipt)
    assert receipt["first_failure"]["code"] == "authority_frame_timeout"
    assert receipt["cleanup_failures"] == [{
        "stage": "authority_cleanup", "code": "authority_reap_failed",
    }]
    assert receipt["child_reaped"] and owner.process.poll() is not None
    assert receipt["cleanup_confirmed"] is False
    assert "/private/" not in json.dumps(receipt)


@_POSIX
def test_stop_reply_without_child_exit_is_unconfirmed_and_forcibly_reaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _ = _entry(tmp_path)
    source = path.read_text().replace(
        'raise SystemExit(0 if result["first_failure"] is None else 1)',
        'time.sleep(20)\nraise SystemExit(0 if result["first_failure"] is None else 1)',
    )
    path.write_text(source)
    owner = authority.OwnerProviderAuthority(path, _sha(source.encode()), profile="fixed_probe",
                                            execution_binding_sha256=_BINDING).start()
    monkeypatch.setattr(authority, "CLEANUP_SECONDS", 0.1)
    with pytest.raises(authority.AuthorityError, match="authority_stop_unconfirmed"):
        owner.close()
    receipt = owner.receipt()
    _validate(receipt)
    assert receipt["first_failure"] is None
    assert receipt["cleanup_failures"] == [{
        "stage": "authority_cleanup", "code": "authority_stop_unconfirmed",
    }]
    assert receipt["child_reaped"] and receipt["authority_killed"]
    assert owner.process.poll() is not None and receipt["cleanup_confirmed"] is False


@_POSIX
def test_one_parent_reader_concurrent_forward_kills_authority_without_replay(
    tmp_path: Path,
) -> None:
    owner = _owner(tmp_path, mode="slow_open", profile="maintenance").start()
    errors: list[authority.AuthorityError] = []

    def first() -> None:
        try:
            owner.forward(_BODY, 2)
        except authority.AuthorityError as error:
            errors.append(error)

    reader = threading.Thread(target=first)
    reader.start()
    limit = time.monotonic() + 1
    while owner.receipt()["consumed_requests"] == 0 and time.monotonic() < limit:
        time.sleep(0.005)
    try:
        with pytest.raises(authority.AuthorityError, match="authority_reader_busy"):
            owner.forward(_BODY, 2)
        reader.join(2)
        assert not reader.is_alive()
        assert errors and errors[0].code == "authority_reader_busy"
        assert owner.receipt()["consumed_requests"] == 1
        assert owner.receipt()["child_reaped"]
    finally:
        owner.close()
        reader.join(2)


@_POSIX
@pytest.mark.parametrize("change", ["hash", "mode", "symlink"])
def test_private_entry_is_exact_owner_only_and_paths_do_not_escape(
    tmp_path: Path, change: str,
) -> None:
    path, digest = _entry(tmp_path)
    if change == "hash":
        digest = "a" * 64
    elif change == "mode":
        path.chmod(0o644)
    else:
        link = path.parent / "linked.py"
        link.symlink_to(path)
        path = link
    with pytest.raises(authority.AuthorityError, match="authority_entry_invalid") as caught:
        authority.OwnerProviderAuthority(path, digest, profile="fixed_probe",
                                        execution_binding_sha256=_BINDING)
    assert str(path) not in str(caught.value)


def _configuration() -> dict:
    unsigned = {"schema_version": authority.SCHEMA_VERSION, "message_kind": "configure",
                "profile": "fixed_probe", "model_pin": authority.MODEL_PIN, "nonce": "a" * 64,
                "execution_binding_sha256": _BINDING,
                "deadline_monotonic_ns": time.monotonic_ns() + 10_000_000_000}
    return {**unsigned, "binding_sha256": _sha(authority._encode(unsigned))}


def test_schema_is_closed_and_cannot_mix_profiles_or_smuggle_key_paths() -> None:
    schema = _schema()
    Draft202012Validator.check_schema(schema)
    config = _configuration()
    validator = Draft202012Validator(schema)
    validator.validate(config)
    for field, value in {"max_requests": 6, "key": "secret", "key_file": "/private/key",
                         "schema_version": "deeplaw.owner-provider-authority-protocol/v2",
                         "model_pin": "deepseek/deepseek-v4.1-flash"}.items():
        bad = {**config, field: value}
        assert list(validator.iter_errors(bad))
        with pytest.raises(authority.AuthorityError):
            authority._config(bad)
    for profile, seconds in (("fixed_probe", 120.1), ("maintenance", 180.1)):
        unsigned = {key: value for key, value in config.items() if key != "binding_sha256"}
        unsigned.update(profile=profile,
                        deadline_monotonic_ns=time.monotonic_ns() + int(seconds * 1e9))
        bad = {**unsigned, "binding_sha256": _sha(authority._encode(unsigned))}
        with pytest.raises(authority.AuthorityError, match="authority_config_invalid"):
            authority._config(bad)


def test_configuration_binding_rejects_mutations_and_duplicate_fields() -> None:
    config = _configuration()
    assert authority._config(config) == config
    for field, value in {"nonce": "c" * 64, "execution_binding_sha256": "d" * 64,
                         "profile": "maintenance", "binding_sha256": "e" * 64}.items():
        with pytest.raises(authority.AuthorityError, match="authority_binding_invalid"):
            authority._config({**config, field: value})
    with pytest.raises(authority.AuthorityError, match="authority_frame_invalid"):
        authority._decode(b'{"schema_version":"one","schema_version":"two"}')


def test_frozen_profiles_and_shortened_timeouts_are_closed() -> None:
    assert dict(authority.PROFILES) == {"fixed_probe": (1, 120.0), "maintenance": (6, 180.0)}
    with pytest.raises(TypeError):
        authority.PROFILES["fixed_probe"] = (6, 180.0)
    for _, maximum in authority.PROFILES.values():
        assert authority._seconds(0.05, maximum) == 0.05
        for invalid in (False, 0, -1, maximum + 0.01, float("inf"), float("nan"), "180"):
            with pytest.raises(authority.AuthorityError, match="authority_config_invalid"):
                authority._seconds(invalid, maximum)


def test_existing_response_codec_preserves_bytes_and_bounds_headers() -> None:
    body = b"\x00original response\xff\n"
    encoded = encode_provider_reply(200, "application/json", body)
    assert decode_provider_reply(encoded) == (200, "application/json", body)
    assert _sha(decode_provider_reply(encoded)[2]) == _sha(body)
    for raw in (encoded[:-1], b"BAD!" + encoded[4:], encoded + b"extra"):
        with pytest.raises(ProviderBridgeError):
            decode_provider_reply(raw)
    for content_type in ("x" * 129, "\nprivate", "", "\u00e9"):
        with pytest.raises(ProviderBridgeError):
            encode_provider_reply(200, content_type, body)


def test_non_posix_launch_fails_closed_before_touching_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(authority.os, "name", "nt")
    with pytest.raises(authority.AuthorityError, match="authority_platform_unsupported"):
        authority._entry(None, "a" * 64)


@_POSIX
def test_startup_deadline_kills_and_reaps_child_before_any_request(tmp_path: Path) -> None:
    path, _ = _entry(tmp_path)
    source = "import time\ntime.sleep(20)\n" + path.read_text()
    path.write_text(source)
    owner = authority.OwnerProviderAuthority(path, _sha(source.encode()), profile="fixed_probe",
                                            execution_binding_sha256=_BINDING, timeout_seconds=0.1)
    started = time.monotonic()
    with pytest.raises(authority.AuthorityError) as caught:
        owner.start()
    assert caught.value.code in {"authority_frame_timeout", "authority_deadline_exceeded"}
    assert time.monotonic() - started < 1.5
    receipt = owner.close()
    assert receipt["child_reaped"] and receipt["authority_killed"]
    assert receipt["consumed_requests"] == 0


@_POSIX
def test_pipe_setup_failure_still_kills_reaps_and_closes_started_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _owner(tmp_path)

    def fail_setup(*_args: object) -> None:
        raise OSError("/private/key pipe setup must not escape")

    monkeypatch.setattr(authority.os, "set_blocking", fail_setup)
    with pytest.raises(authority.AuthorityError, match="authority_start_failed"):
        owner.start()
    receipt = owner.close()
    assert receipt["child_reaped"] and receipt["cleanup_confirmed"]
    assert owner._watchdog_stop.is_set()
    assert owner.process.poll() is not None
    assert owner.process.stdin.closed and owner.process.stdout.closed
    assert receipt["consumed_requests"] == 0
    assert "/private/" not in json.dumps(receipt)


@_POSIX
def test_repeated_start_is_terminal_and_reaps_original_authority(tmp_path: Path) -> None:
    owner = _owner(tmp_path).start()
    with pytest.raises(authority.AuthorityError, match="authority_closed"):
        owner.start()
    assert owner.receipt()["child_reaped"] and owner.process.poll() is not None
    assert owner.receipt()["consumed_requests"] == 0
    owner.close()


@_POSIX
def test_exited_leader_cannot_leave_same_group_forward_descendant_running(tmp_path: Path) -> None:
    path, _ = _entry(tmp_path)
    pid_path = path.parent / "synthetic_descendant_pid"
    source = path.read_text().replace(
        'calls += 1',
        'calls += 1\n'
        '    descendant = os.fork()\n'
        '    if descendant == 0:\n'
        '        time.sleep(20)\n'
        '        os._exit(0)\n'
        f'    with open({str(pid_path)!r}, "w") as output:\n'
        '        output.write(str(descendant))\n'
        '    os._exit(7)',
    )
    path.write_text(source)
    owner = authority.OwnerProviderAuthority(path, _sha(source.encode()), profile="fixed_probe",
                                            execution_binding_sha256=_BINDING).start()
    try:
        with pytest.raises(authority.AuthorityError, match="authority_frame_timeout"):
            owner.forward(_BODY, 0.15)
        descendant = int(pid_path.read_text())
        receipt = owner.receipt()
        assert receipt["child_exit_code"] == 7 and receipt["child_reaped"]
        assert receipt["authority_killed"]
        assert receipt["first_failure"]["code"] == "authority_frame_timeout"
        assert receipt["requests"][0]["response_state"] == "unknown"
        state = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(descendant)], check=False,
            capture_output=True, timeout=2,
        )
        # Some container init processes do not reap orphan zombies promptly.
        # A zombie cannot forward; a nonempty group still cannot be confirmed.
        assert len(state.stdout) <= 128
        assert not state.stdout.strip() or state.stdout.strip().startswith(b"Z")
        try:
            os.killpg(owner.process.pid, 0)
        except ProcessLookupError:
            if receipt["authority_group_empty"] is True:
                assert receipt["cleanup_confirmed"]
                owner.close()
            else:
                # Later disappearance does not retroactively certify an earlier
                # ambiguous cleanup observation.
                assert receipt["cleanup_confirmed"] is False
                assert any(item["code"] == "authority_group_unconfirmed"
                           for item in receipt["cleanup_failures"])
                with pytest.raises(authority.AuthorityError, match="authority_stop_unconfirmed"):
                    owner.close()
        else:
            assert receipt["authority_group_empty"] is False
            assert receipt["cleanup_confirmed"] is False
            assert any(item["code"] == "authority_group_unconfirmed"
                       for item in receipt["cleanup_failures"])
            with pytest.raises(authority.AuthorityError, match="authority_stop_unconfirmed"):
                owner.close()
        _validate(receipt)
    finally:
        # Exact synthetic instance only; do not leave its descendant alive even
        # when an assertion exposes a regression in authority group cleanup.
        with suppress(ProcessLookupError):
            os.killpg(owner.process.pid, 9)


@_POSIX
def test_group_observation_failure_preserves_first_failure_and_cannot_confirm_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _owner(tmp_path, mode="slow_open").start()
    real_killpg = os.killpg

    def ambiguous_observation(pgid: int, sig: int) -> None:
        if sig == 0:
            raise OSError("/private/key group observation must not escape")
        real_killpg(pgid, sig)

    monkeypatch.setattr(authority.os, "killpg", ambiguous_observation)
    with pytest.raises(authority.AuthorityError, match="authority_frame_timeout"):
        owner.forward(_BODY, 0.1)
    with pytest.raises(authority.AuthorityError, match="authority_stop_unconfirmed"):
        owner.close()
    receipt = owner.receipt()
    _validate(receipt)
    assert receipt["child_reaped"] and owner.process.poll() is not None
    assert receipt["authority_group_empty"] is False
    assert receipt["cleanup_confirmed"] is False
    assert receipt["first_failure"]["code"] == "authority_frame_timeout"
    assert receipt["cleanup_failures"] == [{
        "stage": "authority_cleanup", "code": "authority_group_unconfirmed",
    }]
    assert "/private/" not in json.dumps(receipt)


@_POSIX
def test_truncated_response_header_is_unknown_terminal_and_reaped(tmp_path: Path) -> None:
    path, _ = _entry(tmp_path)
    source = '''import os
import sys
from benchmarks.hosts import owner_provider_authority as a
from benchmarks.hosts import native_slot_frames as frames
c = a._PipeConnection(sys.stdin.buffer, sys.stdout.buffer)
frame = frames.read_frame(c, timeout=3)
config = a._config(a._decode(frame.payload))
ready = a._message(config, "ready", role="credential_authority", formal_admission=False,
                   process_identity_sha256=a._commit("credential_authority", os.getpid(), config))
frames.write_frame(c, frames.CONTROL_REPLY, 1, a._encode(ready), timeout=3)
frames.read_frame(c, timeout=3)
frames.read_frame(c, timeout=3)
os.write(sys.stdout.fileno(), b"DLS1")
'''
    path.write_text(source)
    owner = authority.OwnerProviderAuthority(path, _sha(source.encode()), profile="fixed_probe",
                                            execution_binding_sha256=_BINDING).start()
    with pytest.raises(authority.AuthorityError, match="authority_frame_truncated"):
        owner.forward(_BODY, 2)
    assert owner.receipt()["requests"][0]["response_state"] == "unknown"
    assert owner.receipt()["child_reaped"]
    owner.close()


@_POSIX
def test_bad_pipe_header_fails_closed_without_payload_diagnostic(tmp_path: Path) -> None:
    owner = _owner(tmp_path).start()
    try:
        header = struct.pack(frames.HEADER_FORMAT, b"BAD!", int(frames.CONTROL_REQUEST), 0, 2, 0)
        owner.connection.sendall(header)
        owner.process.wait(timeout=2)
        assert owner.process.returncode == 1
        with pytest.raises(authority.AuthorityError):
            owner.forward(_BODY, 2)
        receipt = owner.close()
        assert receipt["requests"][0]["response_state"] == "unknown"
        assert receipt["child_reaped"]
        assert "BAD!" not in json.dumps(receipt)
    finally:
        owner.close()


@_POSIX
def test_no_inherited_authentication_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OWNER_SECRET_CANARY", "must-not-inherit")
    path, _ = _entry(tmp_path)
    source = path.read_text().replace(
        'calls += 1', 'calls += 1\n    assert "OWNER_SECRET_CANARY" not in os.environ')
    path.write_text(source)
    owner = authority.OwnerProviderAuthority(path, _sha(source.encode()), profile="fixed_probe",
                                            execution_binding_sha256=_BINDING).start()
    try:
        assert owner.forward(_BODY, 2)[2] == _BODY
    finally:
        owner.close()


class _MockAuthorityProcess:
    pid = 987654321
    returncode = None

    def __init__(self) -> None:
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO()
        self.wait_calls = 0
        self.kill_calls = 0

    def wait(self, *, timeout: float) -> int:
        self.wait_calls += 1
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.kill_calls += 1


class _MockAuthorityConnection:
    close_failed = False

    def close(self) -> None:
        pass


def _mock_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> authority.OwnerProviderAuthority:
    # No files, native pipes, process creation or real signals in these portable
    # regressions. The mock PID is only passed to the mocked signal seam.
    monkeypatch.setattr(authority, "_entry", lambda path, _digest: path)
    monkeypatch.setattr(authority.signal, "SIGKILL", 9, raising=False)
    return authority.OwnerProviderAuthority(
        tmp_path / "unused-entry.py", "a" * 64, profile="fixed_probe",
        execution_binding_sha256=_BINDING,
    )


@pytest.mark.parametrize("group_unknown", [False, True])
def test_terminal_cleanup_never_resignals_a_reused_process_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, group_unknown: bool,
) -> None:
    owner = _mock_owner(tmp_path, monkeypatch)
    process = _MockAuthorityProcess()
    owner.process = process
    owner.connection = _MockAuthorityConnection()
    owner.config = _configuration()
    owner._started = True
    calls: list[tuple[int, int]] = []

    def first_group(pgid: int, sig: int) -> None:
        calls.append((pgid, sig))
        if sig == 0:
            if group_unknown:
                raise OSError("synthetic group observation unavailable")
            raise ProcessLookupError

    monkeypatch.setattr(authority.os, "killpg", first_group, raising=False)
    owner._kill_and_reap()
    before = owner.receipt()
    assert before["child_reaped"] is True
    assert before["authority_group_empty"] is (not group_unknown)
    assert before["cleanup_confirmed"] is (not group_unknown)
    assert calls == [(process.pid, 9), (process.pid, 0)]
    assert process.wait_calls == 1

    def reused_group(pgid: int, sig: int) -> None:
        # Any call now targets an unrelated reused PGID. Record it without
        # invoking an operating-system signal or observation operation.
        calls.append((pgid, sig))

    monkeypatch.setattr(authority.os, "killpg", reused_group, raising=False)
    with pytest.raises(authority.AuthorityError, match="authority_closed"):
        owner.forward(b"public", 1)
    with pytest.raises(authority.AuthorityError, match="authority_closed"):
        owner.start()
    if group_unknown:
        with pytest.raises(authority.AuthorityError, match="authority_stop_unconfirmed"):
            owner.close()
    else:
        assert owner.close()["cleanup_confirmed"] is True
    # Simulate a watchdog that passed its event check before cleanup completed.
    owner._watchdog_stop.clear()
    owner.config["deadline_monotonic_ns"] = time.monotonic_ns() - 1
    owner._deadline_watchdog()
    after = owner.receipt()
    _validate(after)
    assert calls == [(process.pid, 9), (process.pid, 0)]
    assert process.wait_calls == 1 and process.kill_calls == 0
    assert after["first_failure"] == {"stage": "authority_protocol", "code": "authority_closed"}
    assert after["cleanup_failures"] == before["cleanup_failures"]
    assert after["authority_group_empty"] is before["authority_group_empty"]
    assert after["cleanup_confirmed"] is before["cleanup_confirmed"]
    assert owner._watchdog_stop.is_set()


def test_early_abort_does_not_consume_cleanup_for_a_late_created_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _mock_owner(tmp_path, monkeypatch)
    process = _MockAuthorityProcess()
    calls: list[tuple[int, int]] = []

    def closed_while_creating(*_args: object, **_kwargs: object) -> _MockAuthorityProcess:
        owner._abort(authority.AuthorityError("authority_reader_busy"))
        assert owner.process is None
        return process

    def signal_group(pgid: int, sig: int) -> None:
        calls.append((pgid, sig))
        if sig == 0:
            raise ProcessLookupError

    monkeypatch.setattr(authority.subprocess, "Popen", closed_while_creating)
    monkeypatch.setattr(authority.os, "killpg", signal_group, raising=False)
    with pytest.raises(authority.AuthorityError, match="authority_reader_busy"):
        owner.start()
    receipt = owner.close()
    _validate(receipt)
    assert calls == [(process.pid, 9), (process.pid, 0)]
    assert process.wait_calls == 1 and process.kill_calls == 0
    assert process.stdin.closed and process.stdout.closed
    assert receipt["child_reaped"] and receipt["authority_group_empty"]
    assert receipt["cleanup_confirmed"] is True and owner._watchdog_stop.is_set()
    assert receipt["first_failure"]["code"] == "authority_reader_busy"
    assert receipt["consumed_requests"] == 0
