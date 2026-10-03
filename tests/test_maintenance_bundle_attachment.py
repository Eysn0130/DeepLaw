"""Public byte attachment staging is engineering evidence, never qualification."""

import os
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from benchmarks.hosts import maintenance_bundle_attachment as attachment
from benchmarks.hosts import maintenance_task_mcp
from benchmarks.hosts.maintenance_task_cases import (
    CONFIGURATION_ORDER,
    FROZEN_INPUT_SHA256,
    SCENARIO_ORDER,
    MaintenanceTaskSession,
    score_host_trace,
)
from deeplaw.util import sha256_bytes, strict_json_loads
from tests.test_maintenance_run_evidence import (
    _case,
    _copy,
    _replace_case,
    _state,
)
from tests.test_maintenance_run_evidence import (
    public_run as public_run,
)

_POSIX = pytest.mark.skipif(os.name != "posix", reason="attachment staging requires POSIX")
_EXPECTED = {"run_id": "synthetic-run", "candidate_id": "synthetic-candidate",
             "binary_sha256": "b" * 64}


def _destination(tmp_path):
    parent = tmp_path / "attachments"
    parent.mkdir(mode=0o700)
    return parent / "c6_public_maintenance"


def _stage(root, destination, **expected):
    return attachment.stage_public_maintenance_attachment(root, destination, **{
        **_EXPECTED, **expected,
    })


def _schema_example():
    """Shape-only synthetic sample, never staged or represented as execution."""
    slots = [{
        "configuration_id": configuration, "scenario_id": scenario,
        "run_id": "synthetic-run-" + configuration + "-" + scenario,
        "producer_status": "failed", "score_sha256": "a" * 64, "passed": False,
        "unknown_outcome": scenario == "unknown_action",
        "outcome_unknown": scenario == "unknown_action",
        "safe_termination": scenario == "unknown_action", "event_count": 0,
        "final_state_sha256": "c" * 64, "failure_codes": ["missing_action"],
    } for configuration in CONFIGURATION_ORDER for scenario in SCENARIO_ORDER]
    files = [{"path": f"case-{index:02}.json", "size": 1, "sha256": "a" * 64}
             for index in range(1, 18)]
    result = {
        "schema_version": attachment.SCHEMA_VERSION,
        "attachment_subtree": attachment.ATTACHMENT_SUBTREE, **_EXPECTED,
        "public_input_sha256": FROZEN_INPUT_SHA256, "source_files": files,
        "closure_sha256": attachment._digest(files), "slots": slots,
        "summary": attachment._summary(slots),
        "generated_empty_directories": list(attachment.GENERATED_EMPTY_DIRECTORIES),
        "directory_topology": "derived_fixed_empty_directories",
        "execution_evidence": "producer_declarations_only", "formal_admission": False,
        "claim_scope": "public_engineering_attachment_only", "caption": attachment._CAPTION,
    }
    result["record_sha256"] = attachment.record_sha256(result)
    return result


def test_attachment_schema_is_closed_and_requires_unique_full_fifteen_slot_topology():
    schema = strict_json_loads(attachment.SCHEMA_PATH.read_bytes())
    Draft202012Validator.check_schema(schema)
    attachment.validate_attachment_receipt(_schema_example())
    for tamper in ("extra", "duplicate_slot", "missing_slot", "extra_slot", "slot_extra",
                   "official", "native", "commercial", "directory_extra", "directory_swap"):
        value = _schema_example()
        if tamper == "duplicate_slot":
            value["slots"][1] = deepcopy(value["slots"][0])
        elif tamper == "missing_slot":
            value["slots"].pop()
        elif tamper == "extra_slot":
            value["slots"].append(deepcopy(value["slots"][0]))
        elif tamper == "slot_extra":
            value["slots"][0]["extra"] = True
        elif tamper == "directory_extra":
            value["generated_empty_directories"].append("private")
        elif tamper == "directory_swap":
            value["generated_empty_directories"][0] = "private"
        elif tamper == "official":
            value["formal_admission"] = True
        elif tamper == "native":
            value["execution_evidence"] = "native_attestation"
        elif tamper == "commercial":
            value["commercial_pass"] = True
        else:
            value["extra"] = True
        value["record_sha256"] = attachment.record_sha256(value)
        with pytest.raises(ValueError, match="receipt validation failed"):
            attachment.validate_attachment_receipt(value)


@pytest.mark.parametrize("tamper", ["duplicate_ref", "closure", "record", "summary", "run_id",
                                  "file_extra", "absolute_ref", "traversal_ref", "oversize"])
def test_attachment_receipt_rejects_cross_field_drift(tamper):
    value = _schema_example()
    if tamper == "duplicate_ref":
        value["source_files"][1] = deepcopy(value["source_files"][0])
    elif tamper in {"closure", "record"}:
        value[tamper + "_sha256"] = "0" * 64
    elif tamper == "summary":
        value["summary"]["passed_count"] = 1
    elif tamper == "run_id":
        value["slots"][0]["run_id"] = "unbound-public-run"
    elif tamper == "file_extra":
        value["source_files"][0]["extra"] = True
    elif tamper == "oversize":
        value["source_files"][0]["size"] = 16 * 1024 * 1024 + 1
    else:
        value["source_files"][0]["path"] = "/private" if tamper == "absolute_ref" else "../private"
    if tamper != "record":
        value["record_sha256"] = attachment.record_sha256(value)
    with pytest.raises(ValueError, match="receipt validation failed"):
        attachment.validate_attachment_receipt(value)


def test_attachment_receipt_keeps_existing_consumed_inventory_byte_bound():
    value = _schema_example()
    value["source_files"] = [{
        "path": f"public-{index:02}.json", "size": 16 * 1024 * 1024, "sha256": "a" * 64,
    } for index in range(33)]
    value["closure_sha256"] = attachment._digest(value["source_files"])
    value["record_sha256"] = attachment.record_sha256(value)
    with pytest.raises(ValueError, match="receipt validation failed"):
        attachment.validate_attachment_receipt(value)


def test_attachment_staging_fails_closed_on_unsupported_platform(tmp_path, monkeypatch):
    monkeypatch.setattr(attachment.os, "name", "unsupported")
    with pytest.raises(ValueError, match=r"^maintenance attachment staging failed$"):
        _stage(tmp_path, tmp_path / "uncreated")
    assert not (tmp_path / "uncreated").exists()


@_POSIX
def test_stage_public_run_copies_exact_original_closure_and_reopens(
    public_run, tmp_path, monkeypatch,
):
    root = _copy(public_run, tmp_path)
    canary = root / "unused-private-key-canary"
    canary.write_bytes(b"Public unused canary; this is not credential material.")
    canary.chmod(0o600)
    before = _state(root)
    validated = attachment.consumer.validate_run_evidence(root, **_EXPECTED)
    original_read = attachment._read
    original_open = os.open
    source_reads = []

    def read(directory, relative, maximum):
        if directory == root:
            source_reads.append(relative)
        assert "unused-private-key-canary" not in relative
        return original_read(directory, relative, maximum)

    monkeypatch.setattr(attachment, "_read", read)

    def open_file(path, *args, **kwargs):
        assert canary.name not in os.fspath(path)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", open_file)
    destination = _destination(tmp_path)
    receipt = _stage(root, destination)
    assert receipt["source_files"] == validated["files"]
    assert set(source_reads) == {reference["path"] for reference in validated["files"]}
    assert receipt["closure_sha256"] == attachment._digest(validated["files"])
    assert receipt["public_input_sha256"] == FROZEN_INPUT_SHA256
    assert receipt["formal_admission"] is False
    assert receipt["execution_evidence"] == "producer_declarations_only"
    assert receipt["summary"] == {"slot_count": 15, "passed_count": 3, "failed_count": 12,
                                   "unknown_count": 3, "producer_succeeded_count": 3,
                                   "producer_failed_count": 12}
    assert attachment.consumer.validate_run_evidence(destination, **_EXPECTED) == validated
    assert _state(root) == before
    assert {path.relative_to(destination).as_posix() for path in destination.rglob("*")
            if path.is_file()} == {reference["path"] for reference in validated["files"]}
    for reference in receipt["source_files"]:
        copied = destination / reference["path"]
        assert copied.read_bytes() == (root / reference["path"]).read_bytes()
        assert sha256_bytes(copied.read_bytes()) == reference["sha256"]
        assert copied.lstat().st_nlink == 1 and copied.lstat().st_mode & 0o077 == 0
    for relative in receipt["generated_empty_directories"]:
        directory = destination / relative
        assert directory.is_dir() and not any(directory.iterdir())
    assert destination.stat().st_mode & 0o077 == 0
    assert not (destination / canary.name).exists()


@_POSIX
def test_stage_accepts_all_honest_failures_including_unknown(public_run, tmp_path):
    root = _copy(public_run, tmp_path)
    for index, configuration in enumerate(CONFIGURATION_ORDER):
        case_index = index * len(SCENARIO_ORDER)
        item = _case(root, case_index)
        session = MaintenanceTaskSession(configuration, "source_update")
        path = root / item["evidence_files"]["action_trace"]["path"]
        empty_trace = tmp_path / (configuration + "-empty-trace.json")
        store = maintenance_task_mcp._TraceStore(empty_trace, item["binding"], session)
        store.persist(session)
        path.write_bytes(empty_trace.read_bytes())
        trace = maintenance_task_mcp.load_persisted_trace(path, binding=item["binding"])
        item["trace"] = trace
        item["score"] = score_host_trace(configuration, "source_update", trace["events"])
        item["status"] = "failed"
        item["knowledge_outcome"] = {"status": "not_committed",
                                     "reason": "case_preparation_or_execution_failed"}
        item["evidence_files"]["action_trace"] = {
            "path": path.relative_to(root).as_posix(), "sha256": sha256_bytes(path.read_bytes()),
            "size": path.stat().st_size,
        }
        _replace_case(root, item, case_index)
    receipt = _stage(root, _destination(tmp_path))
    assert receipt["summary"]["passed_count"] == 0
    assert receipt["summary"]["failed_count"] == receipt["summary"]["producer_failed_count"] == 15
    assert receipt["summary"]["unknown_count"] == 3
    unknown = [slot for slot in receipt["slots"] if slot["scenario_id"] == "unknown_action"]
    assert all(slot["safe_termination"] and slot["unknown_outcome"] and not slot["passed"]
               for slot in unknown)


@_POSIX
@pytest.mark.parametrize("kind", [
    "identity_drift", "extra_reference", "symlink", "hardlink", "fifo",
])
def test_stage_rejects_unsafe_source_and_preserves_evidence(public_run, tmp_path, kind):
    root = _copy(public_run, tmp_path)
    item = _case(root)
    original = root / item["evidence_files"]["action_trace"]["path"]
    if kind == "extra_reference":
        item["evidence_files"]["action_trace"]["extra"] = "public-canary"
        _replace_case(root, item)
    elif kind in {"symlink", "hardlink", "fifo"}:
        saved = root / "saved-public-trace"
        original.rename(saved)
        if kind == "symlink":
            original.symlink_to(saved)
        elif kind == "hardlink":
            os.link(saved, original)
        else:
            os.mkfifo(original, 0o600)
    before = _state(root)
    destination = _destination(tmp_path)
    expected = {"binary_sha256": "0" * 64} if kind == "identity_drift" else {}
    with pytest.raises(ValueError, match=r"^maintenance attachment staging failed$"):
        _stage(root, destination, **expected)
    assert _state(root) == before
    assert not destination.exists()


@_POSIX
def test_stage_rejects_destination_reuse_and_preserves_existing_bytes(public_run, tmp_path):
    destination = _destination(tmp_path)
    destination.mkdir(mode=0o700)
    canary = destination / "retained-public-evidence"
    canary.write_bytes(b"Retain on failure.")
    with pytest.raises(ValueError, match=r"^maintenance attachment staging failed$"):
        _stage(public_run, destination)
    assert canary.read_bytes() == b"Retain on failure."


@_POSIX
def test_stage_rejects_replacement_while_output_descriptor_is_open(
    public_run, tmp_path, monkeypatch,
):
    destination = _destination(tmp_path)
    before = _state(public_run)
    original_write = os.write
    replaced = False

    def write(descriptor, raw):
        nonlocal replaced
        result = original_write(descriptor, raw)
        if not replaced:
            target = destination / "case-01.json"
            temporary = target.with_name("public-replacement")
            temporary.write_bytes(bytes(raw))
            temporary.chmod(0o600)
            temporary.replace(target)
            replaced = True
        return result

    monkeypatch.setattr(os, "write", write)
    with pytest.raises(ValueError, match=r"^maintenance attachment staging failed$"):
        _stage(public_run, destination)
    assert replaced and (destination / "case-01.json").is_file()
    assert not (destination / "case-02.json").exists()
    assert _state(public_run) == before


@_POSIX
@pytest.mark.parametrize("which", ["source", "destination"])
def test_stage_rejects_same_byte_replacement_during_copy_and_retains_partial_output(
    public_run, tmp_path, monkeypatch, which,
):
    root = _copy(public_run, tmp_path)
    destination = _destination(tmp_path)
    original_write = attachment._write_original
    replaced = None

    def write(directory, relative, raw):
        nonlocal replaced
        identity = original_write(directory, relative, raw)
        if replaced is None:
            target = (root if which == "source" else destination) / relative
            temporary = target.with_name("public-replacement")
            temporary.write_bytes(raw)
            temporary.chmod(0o600)
            temporary.replace(target)
            replaced = target
        return identity

    monkeypatch.setattr(attachment, "_write_original", write)
    with pytest.raises(ValueError, match=r"^maintenance attachment staging failed$"):
        _stage(root, destination)
    assert replaced is not None and replaced.is_file()
    assert destination.is_dir() and any(path.is_file() for path in destination.rglob("*"))
    assert (root / "frozen-input.json").is_file()
    assert (destination / "case-01.json").is_file()
