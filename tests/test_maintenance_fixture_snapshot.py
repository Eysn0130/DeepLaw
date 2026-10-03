"""A public fixture can be frozen without granting or changing its source Vault."""

import os
import sqlite3
from contextlib import suppress
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.hosts import (
    maintenance_fixture_snapshot,
    maintenance_host_context,
    maintenance_task_mcp,
)
from benchmarks.hosts.maintenance_fixture_snapshot import freeze_public_fixture_vault
from benchmarks.hosts.maintenance_task_cases import (
    CONFIGURATION_ORDER,
    MaintenanceTaskSession,
    score_host_trace,
)
from deeplaw.context_compiler import verify_capsule
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore
from deeplaw.knowledge_store import KnowledgeVault
from deeplaw.util import canonical_json, sha256_bytes, strict_json_loads

pytestmark = pytest.mark.skipif(os.name != "posix", reason="fixture snapshot requires POSIX")


def test_snapshot_rejects_fifo_without_waiting_for_a_writer(tmp_path):
    vault, vault_id = _prepare(tmp_path)
    os.mkfifo(vault / "unexpected-fifo", mode=0o600)
    destination = tmp_path / "snapshot"
    with pytest.raises(ValueError, match="unsafe"):
        freeze_public_fixture_vault(vault, destination, expected_vault_id=vault_id)
    assert not destination.exists()


def _files(root: Path):
    return {
        path.relative_to(root).as_posix(): sha256_bytes(path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
    }


def _prepare(tmp_path, configuration="governed_maintenance"):
    vault = tmp_path / "source"
    maintenance_host_context.prepare_context_vault(vault, configuration)
    with KnowledgeVault(vault, read_only=True) as store:
        vault_id = store.vault_id
    return vault, vault_id


def _snapshot_state(root):
    return {
        path.relative_to(root).as_posix(): (
            path.lstat().st_mode, sha256_bytes(path.read_bytes()) if path.is_file() else None,
        )
        for path in (root, *root.rglob("*"))
    }


@pytest.mark.parametrize("configuration", CONFIGURATION_ORDER)
def test_frozen_fixture_reopens_strictly_and_preserves_every_source_file(tmp_path, configuration):
    vault, vault_id = _prepare(tmp_path, configuration)
    context = maintenance_host_context.capture_context(vault, configuration, "source_update")
    before = _files(vault)
    destination = tmp_path / "snapshot"
    receipt = freeze_public_fixture_vault(vault, destination, expected_vault_id=vault_id)
    assert _files(vault) == before
    frozen_before, manifest_before = _snapshot_state(destination), deepcopy(receipt)
    actual = maintenance_fixture_snapshot.validate_public_fixture_snapshot(
        destination, receipt, expected_vault_id=vault_id,
    )
    assert actual == receipt == manifest_before
    assert actual is not receipt and actual["inventory"] is not receipt["inventory"]
    assert _snapshot_state(destination) == frozen_before
    with KnowledgeVault(destination, read_only=True) as store:
        assert verify_capsule(context["capsule"], vault=store)["valid"] is True
        assert store.audit_head == receipt["legacy_audit_head"]
    with AutonomousKnowledgeStore(destination, read_only=True) as store:
        inspected = store.inspect()
        assert inspected["verification"]["valid"] is True
        assert inspected["counts"]["active_grants"] == 0
        assert store.audit_head == receipt["autonomous_audit_head"]
        assert store.sequence == receipt["autonomous_sequence"]
    inventory = receipt["inventory"]
    assert inventory == sorted(inventory, key=lambda item: item["path"])
    assert {item["path"] for item in inventory} == set(_files(destination))
    for item in inventory:
        assert set(item) == {"path", "sha256", "size"}
        path = destination / item["path"]
        assert not Path(item["path"]).is_absolute()
        assert ".." not in Path(item["path"]).parts
        assert item["sha256"] == sha256_bytes(path.read_bytes())
        assert item["size"] == path.stat().st_size
        assert path.stat().st_mode & 0o077 == 0
    assert destination.stat().st_mode & 0o077 == 0
    assert not list((destination / ".deeplaw/capabilities").iterdir())
    assert not list((destination / ".deeplaw/staging").iterdir())
    rendered = canonical_json(receipt)
    assert str(vault) not in rendered and str(destination) not in rendered
    assert ".token" not in rendered and "grant_id" not in rendered


@pytest.fixture(scope="module")
def public_snapshot(tmp_path_factory):
    directory = tmp_path_factory.mktemp("public-snapshot")
    vault, vault_id = _prepare(directory)
    frozen = directory / "snapshot"
    manifest = freeze_public_fixture_vault(vault, frozen, expected_vault_id=vault_id)
    return frozen, vault_id, manifest


@pytest.mark.parametrize("tamper", [
    "extra_field", "schema", "input", "vault_id", "legacy_revision", "legacy_head",
    "sequence", "autonomous_head", "inventory_empty", "inventory_order", "inventory_duplicate",
    "inventory_extra_field", "parent_path", "absolute_path", "backslash_path", "missing_entry",
    "hash", "size", "boolean_size", "oversize", "inventory_digest",
])
def test_read_only_snapshot_manifest_is_closed_and_exact(public_snapshot, tamper):
    frozen, vault_id, original = public_snapshot
    manifest = deepcopy(original)
    before = _snapshot_state(frozen)
    if tamper == "extra_field":
        manifest["unapproved_fixture_note"] = "Synthetic private marker"
    elif tamper in {"schema", "input", "vault_id", "legacy_head", "autonomous_head"}:
        key = {"schema": "schema_version", "input": "public_input_sha256",
               "vault_id": "vault_id", "legacy_head": "legacy_audit_head",
               "autonomous_head": "autonomous_audit_head"}[tamper]
        manifest[key] = "0" * 64
    elif tamper in {"legacy_revision", "sequence"}:
        manifest["legacy_revision" if tamper == "legacy_revision" else "autonomous_sequence"] = True
    elif tamper == "inventory_empty":
        manifest["inventory"] = []
    elif tamper == "inventory_order":
        manifest["inventory"].reverse()
    elif tamper == "inventory_duplicate":
        manifest["inventory"].insert(0, deepcopy(manifest["inventory"][0]))
    elif tamper == "inventory_extra_field":
        manifest["inventory"][0]["unapproved_fixture_note"] = "Synthetic private marker"
    elif tamper in {"parent_path", "absolute_path", "backslash_path"}:
        manifest["inventory"][0]["path"] = {
            "parent_path": "../outside-fixture", "absolute_path": "/outside-fixture",
            "backslash_path": "unsafe\\fixture",
        }[tamper]
    elif tamper == "missing_entry":
        manifest["inventory"].pop()
    elif tamper == "hash":
        manifest["inventory"][0]["sha256"] = "0" * 64
    elif tamper == "size":
        manifest["inventory"][0]["size"] += 1
    elif tamper == "boolean_size":
        manifest["inventory"][0]["size"] = True
    elif tamper == "oversize":
        manifest["inventory"][0]["size"] = 16 * 1024 * 1024 + 1
    else:
        manifest["inventory_sha256"] = "0" * 64
    if tamper != "inventory_digest":
        manifest["inventory_sha256"] = maintenance_fixture_snapshot._digest(manifest["inventory"])
    original_input = deepcopy(manifest)
    with pytest.raises(ValueError):
        maintenance_fixture_snapshot.validate_public_fixture_snapshot(
            frozen, manifest, expected_vault_id=vault_id,
        )
    assert manifest == original_input and _snapshot_state(frozen) == before


@pytest.mark.parametrize("tamper", [
    "missing", "cas_extra", "default_extra", "cas_drift", "symlink", "hardlink", "fifo",
    "empty_extra", "directory_mode",
])
def test_read_only_snapshot_rejects_unsafe_or_unregistered_bytes(tmp_path, tamper):
    vault, vault_id = _prepare(tmp_path)
    frozen = tmp_path / "snapshot"
    manifest = freeze_public_fixture_vault(vault, frozen, expected_vault_id=vault_id)
    cas = next(frozen.glob(".deeplaw/objects/sha256/*/*"))
    if tamper == "missing":
        cas.unlink()
    elif tamper == "cas_drift":
        cas.write_bytes(cas.read_bytes() + b"Synthetic tampered bytes")
    elif tamper == "symlink":
        cas.unlink()
        cas.symlink_to(vault / "vault.json")
    elif tamper == "hardlink":
        (tmp_path / "unexpected-link").hardlink_to(cas)
    elif tamper == "fifo":
        os.mkfifo(frozen / "unexpected-fifo", mode=0o600)
    elif tamper == "empty_extra":
        (frozen / "unregistered-empty-directory").mkdir(mode=0o700)
    elif tamper == "directory_mode":
        cas.parent.chmod(0o755)
    else:
        if tamper == "cas_extra":
            raw = b"Synthetic unregistered object"
            digest = sha256_bytes(raw)
            extra = frozen / ".deeplaw/objects/sha256" / digest[:2] / digest[2:]
            extra.parent.mkdir(mode=0o700, exist_ok=True)
        else:
            # Exporter source closure allows this exact default, but the
            # snapshot manifest and registered file closure must still reject it.
            raw = maintenance_fixture_snapshot.autonomy._VAULT_AGENT_GUIDE.encode()
            extra = frozen / "AGENTS.md"
        extra.write_bytes(raw)
        extra.chmod(0o600)
        manifest["inventory"].append({"path": extra.relative_to(frozen).as_posix(),
                                      "sha256": sha256_bytes(raw), "size": len(raw)})
        manifest["inventory"].sort(key=lambda item: item["path"])
        manifest["inventory_sha256"] = maintenance_fixture_snapshot._digest(manifest["inventory"])
    with pytest.raises((ValueError, OSError)):
        maintenance_fixture_snapshot.validate_public_fixture_snapshot(
            frozen, manifest, expected_vault_id=vault_id,
        )


def test_read_only_snapshot_rejects_integrity_valid_private_mutation_key(tmp_path, monkeypatch):
    original_sink = maintenance_host_context.handle_knowledge_sink

    def substitute_key(request, **kwargs):
        if request.get("idempotency_key") == "seed-0":
            request = {**request, "idempotency_key": "synthetic-unapproved-private-fixture-key"}
        return original_sink(request, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(maintenance_host_context, "handle_knowledge_sink", substitute_key)
        vault, vault_id = _prepare(tmp_path)
    with AutonomousKnowledgeStore(vault, read_only=True) as store:
        assert store.inspect()["verification"]["valid"] is True
    frozen = tmp_path / "snapshot"
    with monkeypatch.context() as patch:
        # Construct a hostile snapshot using only the public synthetic fixture.
        # General Ledger integrity is valid; the new public consumer must reject
        # it independently of the exporter that normally prevents these bytes.
        patch.setattr(maintenance_fixture_snapshot, "_validate_fixture_mutations", lambda *_: None)
        manifest = freeze_public_fixture_vault(vault, frozen, expected_vault_id=vault_id)
    before = _snapshot_state(frozen)
    with pytest.raises(ValueError, match="fixture mutation"):
        maintenance_fixture_snapshot.validate_public_fixture_snapshot(
            frozen, manifest, expected_vault_id=vault_id,
        )
    assert _snapshot_state(frozen) == before


def test_wrong_identity_or_existing_destination_is_rejected_without_writes(tmp_path):
    vault, vault_id = _prepare(tmp_path)
    before = _files(vault)
    destination = tmp_path / "snapshot"
    with pytest.raises(ValueError, match="identity"):
        freeze_public_fixture_vault(vault, destination, expected_vault_id="vault:wrong")
    assert not destination.exists()
    destination.mkdir()
    with pytest.raises(ValueError, match="new"):
        freeze_public_fixture_vault(vault, destination, expected_vault_id=vault_id)
    with pytest.raises(ValueError, match="outside"):
        freeze_public_fixture_vault(vault, vault / "snapshot", expected_vault_id=vault_id)
    assert _files(vault) == before


def _prepare_outcome(tmp_path, configuration="governed_maintenance"):
    vault, vault_id = _prepare(tmp_path, configuration)
    context = maintenance_host_context.capture_context(vault, configuration, "source_update")
    session = MaintenanceTaskSession(configuration, "source_update")
    parameters = {
        "resource_id": "orchid-archive",
        "version": "v2",
        "source_ref": "source:orchid-v2",
    }
    if configuration == "governed_maintenance":
        parameters["experience_id"] = "experience:governed-v2"
    session.submit(
        {
            "action_id": "fixture-source-update",
            "observed_state_sha256": session.state_sha256,
            "kind": "submit_resource_version",
            "parameters": parameters,
        }
    )
    binding = maintenance_task_mcp.make_owner_binding(
        configuration,
        "source_update",
        run_id="snapshot-outcome",
        candidate_id="fixture-candidate",
        context_id=context["context_id"],
        capsule_digest=maintenance_host_context.digest(context["provider_capsule"]),
    )
    trace_path = tmp_path / "trace.json"
    maintenance_task_mcp._TraceStore(trace_path, binding, session).persist(session)
    trace = maintenance_task_mcp.load_persisted_trace(trace_path, binding=binding)
    with AutonomousKnowledgeStore(vault, read_only=True) as store:
        grant_id = store.connection.execute(
            "SELECT grant_id FROM knowledge_sink_grants_v3"
        ).fetchone()[0]
    receipt = maintenance_host_context.record_host_outcome(
        vault,
        grant_id,
        configuration=configuration,
        scenario="source_update",
        host_run_id="snapshot-outcome",
        host_id="synthetic-host",
        model_id="synthetic-test",
        context=context,
        trace_payload=trace,
        candidate_id="fixture-candidate",
        score=score_host_trace(configuration, "source_update", trace["events"]),
    )
    return vault, vault_id, receipt


@pytest.mark.parametrize("configuration", CONFIGURATION_ORDER)
def test_fixture_freeze_preserves_actual_post_outcome_run_and_feedback(tmp_path, configuration):
    vault, vault_id, receipt = _prepare_outcome(tmp_path, configuration)
    before = _files(vault)
    frozen = freeze_public_fixture_vault(vault, tmp_path / "snapshot", expected_vault_id=vault_id)
    assert _files(vault) == before
    with AutonomousKnowledgeStore(tmp_path / "snapshot", read_only=True) as store:
        assert store.inspect()["verification"]["valid"] is True
        assert (
            store.connection.execute("SELECT COUNT(*) FROM knowledge_run_records_v4").fetchone()[0]
            == 1
        )
        assert store.connection.execute("SELECT COUNT(*) FROM knowledge_feedback_v3").fetchone()[
            0
        ] == len(receipt["feedback_records"])
        assert store.audit_head == frozen["autonomous_audit_head"]


@pytest.fixture(scope="module")
def public_mutation_fixture(tmp_path_factory):
    vault, vault_id, _ = _prepare_outcome(tmp_path_factory.mktemp("public-mutations"))
    return vault, vault_id, maintenance_fixture_snapshot._source_files(vault)


@pytest.mark.parametrize(
    "tamper",
    [
        None,
        "setup_key",
        "run_key",
        "feedback_key",
        "result_kind",
        "result_id",
        "explicit_run_id",
        "knowledge_response_identity",
        "run_response_identity",
        "feedback_response_identity",
        "response_extra_text",
        "response_audit_head",
        "event_identity",
        "event_extra_text",
    ],
)
def test_fixture_mutation_closure_accepts_only_fixed_bindings(public_mutation_fixture, tamper):
    vault, vault_id, files = public_mutation_fixture
    before = _files(vault)
    with sqlite3.connect(":memory:") as memory:
        memory.row_factory = sqlite3.Row
        with AutonomousKnowledgeStore(vault, read_only=True) as source:
            source.connection.backup(memory)
        kind = (
            "run_record" if tamper in {"run_key", "explicit_run_id", "run_response_identity"}
            else "knowledge_feedback" if tamper in {"feedback_key", "feedback_response_identity"}
            else "knowledge_revision"
        )
        row = memory.execute(
            "SELECT rowid, * FROM mutation_idempotency_v3 WHERE result_kind = ? LIMIT 1",
            (kind,),
        ).fetchone()
        if tamper in {"setup_key", "run_key", "feedback_key"}:
            memory.execute(
                "UPDATE mutation_idempotency_v3 SET idempotency_key = ? WHERE rowid = ?",
                ("Synthetic private key outside the fixed fixture", row["rowid"]),
            )
        elif tamper in {"result_kind", "result_id"}:
            column = tamper
            memory.execute(
                f"UPDATE mutation_idempotency_v3 SET {column} = ? WHERE rowid = ?",
                ("unrelated-synthetic-identity", row["rowid"]),
            )
        elif tamper == "explicit_run_id":
            # Coherently change the retained identities; only the generated-ID
            # rule can distinguish this explicit identifier from a fixture run.
            run_id = "run:synthetic-private-identifier"
            memory.execute(
                "UPDATE knowledge_run_records_v4 SET run_id = ? WHERE run_id = ?",
                (run_id, row["result_id"]),
            )
            response = strict_json_loads(row["response_json"])
            response["run_id"] = run_id
            memory.execute(
                "UPDATE mutation_idempotency_v3 SET result_id = ?, response_json = ? "
                "WHERE rowid = ?",
                (run_id, canonical_json(response), row["rowid"]),
            )
            memory.execute(
                "UPDATE autonomous_events_v3 SET object_id = ? "
                "WHERE event_type = 'knowledge_run_recorded' AND object_id = ?",
                (run_id, row["result_id"]),
            )
        elif tamper and tamper.startswith("event_"):
            event = memory.execute(
                "SELECT sequence, payload_json FROM autonomous_events_v3 "
                "WHERE event_type = 'knowledge_revision_committed' AND object_id = ?",
                (row["result_id"],),
            ).fetchone()
            payload = strict_json_loads(event["payload_json"])
            payload["knowledge_id" if tamper == "event_identity" else "extra_private_text"] = (
                "Synthetic private audit identifier"
            )
            memory.execute(
                "UPDATE autonomous_events_v3 SET payload_json = ? WHERE sequence = ?",
                (canonical_json(payload), event["sequence"]),
            )
        elif tamper:
            response = strict_json_loads(row["response_json"])
            field = {
                "knowledge_response_identity": "knowledge_id",
                "run_response_identity": "run_id",
                "feedback_response_identity": "knowledge_id",
                "response_extra_text": "extra_private_text",
                "response_audit_head": "audit_head",
            }[tamper]
            response[field] = "Synthetic private response identifier"
            memory.execute(
                "UPDATE mutation_idempotency_v3 SET response_json = ? WHERE rowid = ?",
                (canonical_json(response), row["rowid"]),
            )
        store = SimpleNamespace(connection=memory, vault_id=vault_id)
        if tamper is None:
            maintenance_fixture_snapshot._validate_fixture_mutations(store, files)
        else:
            with pytest.raises(ValueError, match="fixture mutation"):
                maintenance_fixture_snapshot._validate_fixture_mutations(store, files)
    assert _files(vault) == before


def test_active_grant_is_rejected_instead_of_stripping_its_token(tmp_path, monkeypatch):
    vault = tmp_path / "source"
    with monkeypatch.context() as patch:
        patch.setattr(AutonomousKnowledgeStore, "disable_grant", lambda *_: {})
        # The owner preparation entry point may itself enforce revocation.
        with suppress(ValueError):
            maintenance_host_context.prepare_context_vault(vault, "no_memory")
    with KnowledgeVault(vault, read_only=True) as store:
        vault_id = store.vault_id
    before = _files(vault)
    assert any(path.endswith(".token") for path in before)
    with pytest.raises(ValueError, match="active grant"):
        freeze_public_fixture_vault(vault, tmp_path / "snapshot", expected_vault_id=vault_id)
    assert _files(vault) == before
    assert not (tmp_path / "snapshot").exists()


def test_private_body_with_valid_governance_is_rejected(tmp_path, monkeypatch):
    original = maintenance_host_context.public_task_projection

    def private_record(configuration, scenario):
        task = deepcopy(original(configuration, scenario))
        records = task["environment"]["experience"]["records"]
        if records:
            records[0]["content"] = "Private customer notes that are not public fixture material."
        return task

    monkeypatch.setattr(maintenance_host_context, "public_task_projection", private_record)
    vault, vault_id = _prepare(tmp_path, "frozen_unmaintained")
    with AutonomousKnowledgeStore(vault, read_only=True) as store:
        assert store.inspect()["verification"]["valid"] is True
    before = _files(vault)
    with pytest.raises(ValueError, match="fixture knowledge"):
        freeze_public_fixture_vault(vault, tmp_path / "snapshot", expected_vault_id=vault_id)
    assert _files(vault) == before
    assert not (tmp_path / "snapshot").exists()


def test_unexpected_writer_is_rejected(tmp_path, monkeypatch):
    original = AutonomousKnowledgeStore.enable_grant

    def other_writer(store, **kwargs):
        return original(store, **{**kwargs, "writer_id": "unrelated-owner"})

    monkeypatch.setattr(AutonomousKnowledgeStore, "enable_grant", other_writer)
    vault, vault_id = _prepare(tmp_path)
    with AutonomousKnowledgeStore(vault, read_only=True) as store:
        assert store.inspect()["verification"]["valid"] is True
    with pytest.raises(ValueError, match="fixture grant"):
        freeze_public_fixture_vault(vault, tmp_path / "snapshot", expected_vault_id=vault_id)
    assert not (tmp_path / "snapshot").exists()


@pytest.mark.parametrize(
    "unsafe", ["symlink", "hardlink", "extra", "source", "staging", "cas", "oversize", "default"]
)
def test_unsafe_or_unreferenced_material_is_rejected(tmp_path, unsafe):
    vault, vault_id = _prepare(tmp_path)
    if unsafe == "symlink":
        (vault / "unreferenced").symlink_to(tmp_path)
    elif unsafe == "hardlink":
        (tmp_path / "linked-manifest").hardlink_to(vault / "vault.json")
    elif unsafe == "source":
        (vault / "sources/private.md").write_text("Unregistered source content.")
    elif unsafe == "staging":
        (vault / ".deeplaw/staging/pending.json").write_text("{}")
    elif unsafe == "cas":
        payload = b"Unreferenced CAS content."
        digest = sha256_bytes(payload)
        path = vault / ".deeplaw/objects/sha256" / digest[:2] / digest[2:]
        path.parent.mkdir(mode=0o700, exist_ok=True)
        path.write_bytes(payload)
        path.chmod(0o600)
    elif unsafe == "oversize":
        path = vault / "oversize.bin"
        path.write_bytes(b"x" * (16 * 1024 * 1024 + 1))
        path.chmod(0o600)
    elif unsafe == "default":
        (vault / "AGENTS.md").write_text("Owner-private instructions are not a public fixture.")
    else:
        (vault / "unexpected.txt").write_text("Unreferenced content.")
        (vault / "unexpected.txt").chmod(0o600)
    with pytest.raises((ValueError, RuntimeError)):
        freeze_public_fixture_vault(vault, tmp_path / "snapshot", expected_vault_id=vault_id)
    assert not (tmp_path / "snapshot").exists()
