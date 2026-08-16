"""Offline development candidate for the Evidence Wiki read chain.

The candidate accepts one source-only JSON fixture, compiles it through the public
domain services, and emits bounded identity/receipt facts.  It deliberately does
not accept Gold, a scorer, a provider, or a host credential.  Source and derived
text are used inside a temporary Vault but are never copied into the result.
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from deeplaw.api import KnowledgeOS
from deeplaw.compilation.observation_store import ObservationStore
from deeplaw.compilation.profiles import compiler_profile
from deeplaw.evidence import (
    StatementEvidenceStore,
    build_input_set_sha256,
    statement_id,
    statement_sha256,
)
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore, initialize_autonomous_core
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.persistent_read_runtime import PersistentReadRuntime
from deeplaw.util import canonical_json, sha256_bytes, stable_id
from deeplaw.wiki.link_index import load_link_index
from deeplaw.wiki.registry import load_page_registry
from deeplaw.wiki.resolver import load_resolver

SCHEMA_VERSION = "deeplaw.v013.evidence-wiki-candidate/v1"
MAX_SOURCE_BYTES = 64 * 1024
MAX_SOURCE_CHARS = 12_000
MAX_STATEMENT_CHARS = 8_000
MAX_INTERPRETATION_CHARS = 4_000
PROVIDER_HARD_LIMIT = 65_536
LOCAL_HARD_LIMIT = 256 * 1024

_ABSOLUTE_PATH = re.compile(r"(?:^|[\s=])/(?:Users|home|tmp|private|var)(?:[\s/]|$)")
_WINDOWS_PATH = re.compile(r"[A-Za-z]:[\\/]")
_SECRET_LIKE = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|auth(?:orization)?|bearer|secret)\s*[:=]"
)


def _bounded_text(value: Any, *, field: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\x00" in value
        or len(value) > maximum
    ):
        raise ValueError(f"{field} is outside its bound")
    if _ABSOLUTE_PATH.search(value) or _WINDOWS_PATH.search(value) or _SECRET_LIKE.search(value):
        raise ValueError(f"{field} contains disallowed material")
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _source_statement(source_text: str) -> str:
    paragraphs = [
        item.strip()
        for item in re.split(r"\n\s*\n", source_text)
        if item.strip() and not item.lstrip().startswith("#")
    ]
    candidates = [item for item in paragraphs if "Source Revision" in item]
    if len(candidates) != 1:
        raise ValueError("source_text must contain one Source Revision evidence paragraph")
    return candidates[0]


def _load_source(source: str | Path | Mapping[str, Any]) -> tuple[dict[str, str], str]:
    if isinstance(source, Mapping):
        encoded = canonical_json(dict(source)).encode("utf-8")
        value = dict(source)
    else:
        path = Path(source)
        if path.is_symlink() or not path.is_file():
            raise ValueError("source fixture must be a regular non-symlink file")
        encoded = path.read_bytes()
        if len(encoded) > MAX_SOURCE_BYTES:
            raise ValueError("source fixture exceeds its byte bound")
        try:
            value = json.loads(encoded.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("source fixture must be UTF-8 JSON") from error
    if len(encoded) > MAX_SOURCE_BYTES or not isinstance(value, dict):
        raise ValueError("source fixture must contain one bounded JSON object")
    if value.get("schema_version") != "deeplaw.evidence-wiki-development-source/v1":
        raise ValueError("source fixture schema version is invalid")
    required = {
        "schema_version",
        "case_id",
        "source_filename",
        "source_text",
        "agent_interpretation",
        "human_task",
        "agent_task",
    }
    if set(value) != required:
        raise ValueError("source fixture contract is closed")
    case_id = _bounded_text(value["case_id"], field="case_id", maximum=200)
    source_filename = _bounded_text(
        value["source_filename"], field="source_filename", maximum=200
    )
    if Path(source_filename).name != source_filename or not source_filename.endswith(".md"):
        raise ValueError("source_filename must be one relative Markdown filename")
    source_text = _bounded_text(value["source_text"], field="source_text", maximum=MAX_SOURCE_CHARS)
    interpretation = value.get("agent_interpretation")
    if not isinstance(interpretation, Mapping) or set(interpretation) != {
        "title",
        "body",
        "semantic_key",
    }:
        raise ValueError("agent_interpretation contract is closed")
    statement_text = _source_statement(source_text)
    statement_text = _bounded_text(
        statement_text, field="statement_text", maximum=MAX_STATEMENT_CHARS
    )
    interpretation_text = _bounded_text(
        interpretation["body"],
        field="interpretation_text",
        maximum=MAX_INTERPRETATION_CHARS,
    )
    return {
        "case_id": case_id,
        "source_filename": source_filename,
        "source_text": source_text,
        "statement_text": statement_text,
        "interpretation_text": interpretation_text,
    }, sha256_bytes(encoded)


def _source_ref(packet: Mapping[str, Any], fragment: Mapping[str, Any]) -> dict[str, str]:
    return {
        "source_revision_id": str(packet["source_revision_id"]),
        "fragment_id": str(fragment["fragment_id"]),
        "locator": str(fragment["locator"]),
        "quote_sha256": str(fragment["text_sha256"]),
    }


def _observation(
    *,
    run_id: str,
    packet: Mapping[str, Any],
    source_ref: dict[str, str],
    semantic_key: str,
    title: str,
    body: str,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "observation_id": "",
        "packet_id": packet["packet_id"],
        "semantic_key_candidate": semantic_key,
        "kind": "claim",
        "title_candidate": title,
        "body_candidate": body,
        "aliases": [title],
        "source_refs": [source_ref],
        "assertion": None,
        "applicability": {
            "description": "A bounded source claim is present.",
            "scopes": [],
            "conditions": [],
            "exclusions": [],
        },
        "tags": ["evidence-wiki-development"],
        "reason": "Record one exact source-bound claim for the development qualification lane.",
    }
    value["observation_id"] = ObservationStore.observation_id(
        compilation_run_id=run_id,
        packet_id=str(packet["packet_id"]),
        observation=value,
    )
    return value


def _source_plan(
    *,
    packet: Mapping[str, Any],
    source_ref: dict[str, str],
    semantic_key: str,
    knowledge_id: str,
    title: str,
    body: str,
) -> dict[str, Any]:
    fragment_ids = [str(item["fragment_id"]) for item in packet["fragments"]]
    action = {
        "action": "create",
        "kind": "claim",
        "semantic_key": semantic_key,
        "knowledge_id": knowledge_id,
        "expected_revision_id": None,
        "title": title,
        "body": body,
        "aliases": [title],
        "epistemic_state": "supported",
        "source_refs": [source_ref],
        "assertion": None,
        "tags": ["evidence-wiki-development"],
        "valid_from": None,
        "valid_to": None,
        "applicability": {
            "description": "A bounded source claim is present.",
            "scopes": [],
            "conditions": [],
            "exclusions": [],
        },
        "synthesis_inputs": None,
        "reason": "Persist an exact Source Revision-bound claim.",
    }
    return {
        "schema_version": "deeplaw.source-compilation-plan/v1",
        "source_revision_id": packet["source_revision_id"],
        "packet_id": packet["packet_id"],
        "expected_audit_head": packet["input_audit_head"],
        "object_actions": [action],
        "relation_actions": [],
        "identity_actions": [],
        "unresolved_identities": [],
        "contradictions": [],
        "coverage": {
            "packet_fragment_count": len(fragment_ids),
            "covered_fragment_ids": fragment_ids,
            "omitted_fragment_ids": [],
            "ratio": 1.0,
            "completeness": "complete",
        },
        "skipped_fragments": [],
        "warnings": [],
    }


def _statement_plan(
    *,
    packet: Mapping[str, Any],
    source_ref: dict[str, str],
    statement_text: str,
    body: str,
) -> dict[str, Any]:
    start = body.find(statement_text)
    if start < 0:
        raise ValueError("statement is not present in the compiled claim body")
    gaps: list[dict[str, str]] = []
    statement = {
        "ordinal": 1,
        "char_start": start,
        "char_end": start + len(statement_text),
        "statement_text": statement_text,
        "statement_sha256": statement_sha256(statement_text),
        "statement_type": "factual",
        "support_status": "supported",
        "source_refs": [source_ref],
        "knowledge_revision_refs": [],
        "relation_revision_refs": [],
        "valid_from": None,
        "valid_to": None,
        "limitation": None,
        "gaps": gaps,
        "input_set_sha256": build_input_set_sha256(
            source_refs=[source_ref],
            knowledge_revision_refs=[],
            relation_revision_refs=[],
            valid_from=None,
            valid_to=None,
            statement_type="factual",
            support_status="supported",
            limitation=None,
            gaps=gaps,
        ),
    }
    return {
        "packet_id": packet["packet_id"],
        "object_action_ordinal": 1,
        "statements": [statement],
    }


def _publication_plan(
    *,
    run: Any,
    packet: Mapping[str, Any],
    source_plan: dict[str, Any],
    statement_plan: dict[str, Any],
    observation: dict[str, Any],
    inventory: Mapping[str, Any],
    finalization: Mapping[str, Any],
) -> dict[str, Any]:
    dispositions = [
        {
            "observation_id": observation["observation_id"],
            "disposition": "published",
            "target_ref": source_plan["object_actions"][0]["knowledge_id"],
            "reason": (
                "The bounded development output is published through the compiler "
                "commit boundary."
            ),
        }
    ]
    return {
        "schema_version": "deeplaw.semantic-publication-plan/v3",
        "compiler_profile_version": "3",
        "compilation_run_id": run.compilation_run_id,
        "source_revision_id": packet["source_revision_id"],
        "expected_audit_head": packet["input_audit_head"],
        "inventory_sha256": inventory["inventory_sha256"],
        "finalization_packet_id": finalization["finalization_packet_id"],
        "applicability_policy_sha256": finalization["applicability_policy_sha256"],
        "applicability_digest": finalization["applicability_digest"],
        "packet_plans": [source_plan],
        "statement_plans": [statement_plan],
        "observation_dispositions": dispositions,
        "duty_reports": [dict(item) for item in finalization["duties"]],
        "semantic_status": "partial",
        "warnings": ["Development lane: semantic duties remain bounded and may be unresolved."],
    }


def _find_anchor_record(
    records: list[Mapping[str, Any]], *, field: str, value: str, source_revision_id: str
) -> tuple[str | None, bool]:
    for record in records:
        path = record.get("canonical_page_path")
        for anchor in record.get("anchors", ()):
            if not isinstance(anchor, Mapping):
                continue
            target = anchor.get(field)
            if not isinstance(target, Mapping):
                continue
            if field == "source_fragment":
                target_value = target.get("fragment_id") or target.get("fragment_revision_id")
                if target.get("source_revision_id") == source_revision_id and target_value == value:
                    return path if isinstance(path, str) else None, True
            elif target.get(field.replace("_target", "_id")) == value:
                return path if isinstance(path, str) else None, True
    return None, False


def _projection_facts(
    root: Path,
    *,
    source_revision_id: str,
    claim_id: str,
    statement_id_value: str,
    fragment_id: str,
) -> dict[str, Any]:
    runtime = PersistentReadRuntime(root)
    try:
        snapshot = runtime.snapshot
        bundle = snapshot.wiki
        if bundle is None:
            raise RuntimeError("Living Wiki v3 projection is unavailable")
        manifest = _plain(bundle.v3_manifest)
        registry = load_page_registry(root, manifest)
        link_index = load_link_index(root, manifest, registry)
        resolver = load_resolver(root, manifest, registry)
        records = [item for item in registry["records"] if isinstance(item, Mapping)]
        source_path = f"wiki/sources/{source_revision_id}.md"
        claim_record = next(
            (item for item in records if item.get("knowledge_id") == claim_id), None
        )
        source_record = next(
            (item for item in records if item.get("canonical_page_path") == source_path), None
        )
        fragment_path, fragment_anchor = _find_anchor_record(
            records,
            field="source_fragment",
            value=fragment_id,
            source_revision_id=source_revision_id,
        )
        statement_path, statement_anchor = _find_anchor_record(
            records,
            field="statement_target",
            value=statement_id_value,
            source_revision_id=source_revision_id,
        )
        source_resolver = resolver.resolve(
            {
                "source_fragment": {
                    "source_revision_id": source_revision_id,
                    "fragment_id": fragment_id,
                }
            },
            scope="project",
            max_sensitivity="private",
            allowed_freshness=["fresh", "unknown"],
        )
        claim_resolver = resolver.resolve(
            {"knowledge_id": claim_id},
            scope="project",
            max_sensitivity="private",
            allowed_freshness=["fresh", "unknown"],
        )
        statement_resolver = resolver.resolve(
            {"statement_id": statement_id_value},
            scope="project",
            max_sensitivity="private",
            allowed_freshness=["fresh", "unknown"],
        )
        registry_valid = (
            bool(registry.get("valid"))
            and source_record is not None
            and claim_record is not None
        )
        return {
            "manifest_sha256": manifest.get("manifest_sha256"),
            "registry": {
                "valid": registry_valid,
                "record_count": len(records),
                "source_page_registered": source_record is not None,
                "claim_page_registered": claim_record is not None,
                "source_fragment_anchor": fragment_anchor,
                "statement_anchor": statement_anchor,
            },
            "paths": {
                "source_page": source_path if source_record is not None else None,
                "claim_page": claim_record.get("canonical_page_path") if claim_record else None,
                "source_fragment_index": fragment_path,
                "statement_page": statement_path,
            },
            "link_index": {
                "valid": bool(link_index.get("valid")),
                "edge_count": int(link_index.get("component", {}).get("edge_count", 0)),
            },
            "resolver": {
                "source_fragment": {
                    "status": source_resolver.get("status"),
                    "admitted": source_resolver.get("admission", {}).get("admitted"),
                },
                "claim": {
                    "status": claim_resolver.get("status"),
                    "admitted": claim_resolver.get("admission", {}).get("admitted"),
                },
                "statement_target": {
                    "status": statement_resolver.get("status"),
                    "reason": statement_resolver.get("admission", {}).get("reason"),
                    "gap": statement_resolver.get("receipt", {}).get("gap"),
                },
            },
        }
    finally:
        runtime.close()


def _read_facts(
    root: Path,
    *,
    source_id: str,
    source_revision_id: str,
    fragment_id: str,
    fragment_text: str,
    source_bytes: bytes,
    claim_page: str | None,
    source_page: str | None,
) -> dict[str, Any]:
    from deeplaw.read_services import SourceReadService

    source_read = SourceReadService(root).execute(
        action="fragment", fragment_id=fragment_id, max_sensitivity="private", max_chars=12_000
    )
    source_fragment = source_read["fragment"]
    with KnowledgeVault(root, read_only=True) as vault:
        stored_source = vault.source_file_path(source_id)
        source_bytes_exact_match = (
            stored_source.is_file()
            and not stored_source.is_symlink()
            and stored_source.read_bytes() == source_bytes
            and sha256_bytes(source_bytes) == vault.source_info(source_id)["content_sha256"]
        )
    exact_match = (
        source_fragment.get("source_revision_id") == source_revision_id
        and source_fragment.get("fragment_id") == fragment_id
        and source_fragment.get("text") == fragment_text
        and source_fragment.get("text_sha256") == sha256_bytes(fragment_text.encode("utf-8"))
        and source_fragment.get("content_truncated") is False
    )
    return {
        "source_fragment_exact_match": exact_match,
        "source_bytes_exact_match": source_bytes_exact_match,
        "source_read_write_performed": bool(source_read.get("write_performed", False)),
        "fragment_text_sha256": source_fragment.get("text_sha256"),
        "fragment_locator": source_fragment.get("locator"),
        "source_page_relative": source_page,
        "claim_page_relative": claim_page,
    }


def _query_facts(root: Path, *, task: str) -> tuple[dict[str, Any], dict[str, Any]]:
    with KnowledgeOS.open(root) as osys:
        query = osys.retrieval.query(
            task,
            purpose="answer",
            graph_hops=1,
            retrieval_mode="hybrid",
            force_canonical_lexical=True,
            limit=8,
            max_chars=8_000,
            max_tokens=6_000,
            max_sources=12,
        )
        context = osys.context.compile(
            task=task,
            purpose="answer",
            graph_hops=1,
            retrieval_mode="hybrid",
            limit=8,
            max_chars=8_000,
            max_tokens=6_000,
            max_sources=12,
            confirm_no_case_data=True,
        )
    query_plan = query.get("query_plan", {})
    context_plan = context.get("query_plan", {})
    provider = context.get("provider_capsule")
    provider_bytes = (
        len(canonical_json(provider).encode("utf-8"))
        if isinstance(provider, Mapping)
        else 0
    )
    query_is_v6 = query_plan.get("schema_version") == "deeplaw.knowledge-query-plan/v6"
    context_is_v6 = context_plan.get("schema_version") == "deeplaw.knowledge-query-plan/v6"
    return (
        {
            "schema_version": query.get("schema_version"),
            "query_plan_schema": query_plan.get("schema_version"),
            "query_plan_version": "6" if query_is_v6 else None,
            "retrieval_controls": query_plan.get("retrieval_controls", {}),
            "receipt_id": query.get("receipt_id"),
            "statement_count": (
                len(query.get("statements", []))
                if isinstance(query.get("statements"), list)
                else 0
            ),
            "evidence_count": (
                len(query.get("evidence", []))
                if isinstance(query.get("evidence"), list)
                else 0
            ),
            "provider_bytes": len(canonical_json(query.get("capsule", {})).encode("utf-8")),
            "write_performed": bool(query.get("write_performed", False)),
        },
        {
            "schema_version": context.get("schema_version"),
            "query_plan_schema": context_plan.get("schema_version"),
            "query_plan_version": "6" if context_is_v6 else None,
            "retrieval_controls": context_plan.get("retrieval_controls", {}),
            "receipt_id": context.get("receipt_id"),
            "statement_count": (
                len(context.get("statements", []))
                if isinstance(context.get("statements"), list)
                else 0
            ),
            "evidence_count": (
                len(context.get("evidence", []))
                if isinstance(context.get("evidence"), list)
                else 0
            ),
            "provider_bytes": provider_bytes,
            "write_performed": bool(context.get("write_performed", False)),
        },
    )


def run_candidate(source: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    """Run the source-only Evidence Wiki development candidate."""

    started = time.perf_counter()
    fixture, fixture_hash = _load_source(source)
    with tempfile.TemporaryDirectory(prefix="deeplaw-evidence-wiki-") as temporary:
        root = Path(temporary)
        initialize_knowledge_vault(root, name="evidence-wiki-development", scope="project")
        initialize_autonomous_core(root)
        source_path = root / fixture["source_filename"]
        # Source Revision bytes are evidence.  ``Path.write_text`` without an explicit
        # newline policy lets Windows translate ``\n`` to ``\r\n`` and changes the
        # ingested content hash.  Write the exact UTF-8 byte sequence instead.
        source_path.write_bytes(fixture["source_text"].encode("utf-8"))
        with KnowledgeVault(root, read_only=False) as vault:
            compiled = compile_source(
                vault,
                source_path,
                source_kind="document",
                confirm_no_case_data=True,
            )
            source_id = str(compiled["source"]["source_id"])
            review_manifest = vault.source_review_manifest(source_id)
            vault.approve_source_assets(
                source_id,
                confirm_reviewed=True,
                confirm_quarantined=True,
                review_manifest_sha256=review_manifest["review_manifest_sha256"],
                reviewer_id="evidence-wiki-development",
                review_reason=(
                    "The bounded synthetic development source was reviewed before "
                    "read qualification."
                ),
            )
        source_card = compiled["source"]
        source_revision_id = str(compiled["identity"]["source_revision_id"])
        profile = compiler_profile(version="3")
        with AutonomousKnowledgeStore(root, read_only=False) as store:
            compiler_grant = store.enable_grant(
                writer_id="evidence-wiki-compiler",
                operations=tuple(
                    {
                        "begin_compilation",
                        "stage_compilation_batch",
                        "validate_compilation",
                        "commit_compilation",
                        "freeze_semantic_inventory",
                        "stage_semantic_observations",
                        "finalize_semantic_compilation",
                    }
                ),
            )["grant_id"]
        with KnowledgeOS.open(root) as osys:
            run = osys.compilations.begin(
                grant_id=compiler_grant,
                source_revision_id=source_revision_id,
                compiler_profile=profile["compiler_profile"],
                compiler_profile_version="3",
                host_identity="evidence-wiki-development",
                model_identity=None,
                prompt_template_id=profile["prompt_template_id"],
                prompt_config_sha256=profile["prompt_config_sha256"],
                plan_configuration_sha256=profile["plan_configuration_sha256"],
                confirm_no_case_data=True,
            )
            packet = run.next_packet()
            if packet is None:
                raise RuntimeError("semantic compilation packet is unavailable")
            fragment = next(
                (
                    item
                    for item in packet["fragments"]
                    if fixture["statement_text"] in item["text"]
                ),
                None,
            )
            if fragment is None:
                raise ValueError("statement text is absent from every compiled fragment")
            source_ref = _source_ref(packet, fragment)
            semantic_key = "evidence-wiki:source-claim"
            # The coordinator's deterministic create identity is bound to the Vault ID, not to
            # any path.  Read it through the verified read plane instead of retaining a path.
            with AutonomousKnowledgeStore(root, read_only=True) as store:
                claim_id = stable_id(
                    "knowledge", store.vault_id, "source-compilation", "claim", semantic_key
                )
            observation = _observation(
                run_id=run.compilation_run_id,
                packet=packet,
                source_ref=source_ref,
                semantic_key=semantic_key,
                title="Evidence claim",
                body=fragment["text"],
            )
            observation_plan = {
                "schema_version": "deeplaw.source-compilation-observation-plan/v2",
                "compilation_run_id": run.compilation_run_id,
                "source_revision_id": source_revision_id,
                "packet_id": packet["packet_id"],
                "expected_audit_head": packet["input_audit_head"],
                "observations": [observation],
                "coverage": {
                    "packet_fragment_count": len(packet["fragments"]),
                    "covered_fragment_ids": [item["fragment_id"] for item in packet["fragments"]],
                    "omitted_fragments": [],
                    "ratio": 1.0,
                },
                "warnings": [],
            }
            run.stage_observations(observation_plan, confirm_no_case_data=True)
            source_plan = _source_plan(
                packet=packet,
                source_ref=source_ref,
                semantic_key=semantic_key,
                knowledge_id=claim_id,
                title="Evidence claim",
                body=fragment["text"],
            )
            run.stage(source_plan, confirm_no_case_data=True)
            inventory = run.semantic_inventory(confirm_no_case_data=True)
            finalization = run.finalization_packet()
            statement_plan = _statement_plan(
                packet=packet,
                source_ref=source_ref,
                statement_text=fixture["statement_text"],
                body=fragment["text"],
            )
            publication = _publication_plan(
                run=run,
                packet=packet,
                source_plan=source_plan,
                statement_plan=statement_plan,
                observation=observation,
                inventory=inventory,
                finalization=finalization,
            )
            run.stage_publication(publication, confirm_no_case_data=True)
            run.validate(confirm_no_case_data=True)
            run.commit(confirm_no_case_data=True)
        with AutonomousKnowledgeStore(root, read_only=True) as store:
            statement_revision_id = store.get_current(claim_id)["revision_id"]
        statement_value = {
            "statement_id": statement_id(
                statement_revision_id,
                1,
                statement_sha256(fixture["statement_text"]),
            ),
            "knowledge_revision_id": statement_revision_id,
        }
        with AutonomousKnowledgeStore(root, read_only=False) as store:
            owner_grant = store.enable_grant(
                writer_id="evidence-wiki-owner",
                operations=("add_relation", "remember"),
            )["grant_id"]
            interpretation = store.remember(
                grant_id=owner_grant,
                idempotency_key="evidence-wiki-interpretation",
                title="Agent interpretation",
                body=fixture["interpretation_text"],
                kind="claim",
                semantic_key="evidence-wiki:agent-interpretation",
                source_refs=[source_ref],
                confirm_no_case_data=True,
            )
            relation = store.add_relation(
                grant_id=owner_grant,
                idempotency_key="evidence-wiki-derived-from",
                subject_knowledge_id=interpretation["knowledge_id"],
                predicate="derived_from",
                object_knowledge_id=claim_id,
                evidence_refs=[source_ref],
                confirm_no_case_data=True,
            )
            audit_head_before = store.audit_head
            store.rebuild_derived()
        statement_store = StatementEvidenceStore(root)
        statement_receipt = statement_store.receipt(statement_value["statement_id"])
        statement_record = statement_store.statement(statement_value["statement_id"])
        with AutonomousKnowledgeStore(root, read_only=True) as store:
            claim_current = store.get_current(claim_id)
            interpretation_current = store.get_current(interpretation["knowledge_id"])
            audit_head_after = store.audit_head
            graph = store.graph(
                knowledge_id=interpretation["knowledge_id"],
                scope="project",
                max_sensitivity="private",
                limit=20,
            )
        relation_card = next(
            (
                item
                for item in graph.get("relations", [])
                if item.get("relation_revision_id") == relation["relation_revision_id"]
            ),
            {},
        )
        wiki_facts = _projection_facts(
            root,
            source_revision_id=source_revision_id,
            claim_id=claim_id,
            statement_id_value=statement_value["statement_id"],
            fragment_id=source_ref["fragment_id"],
        )
        from deeplaw.read_services import WikiReadService

        source_page = wiki_facts["paths"].get("source_page")
        claim_page = wiki_facts["paths"].get("claim_page")
        source_page_read = (
            WikiReadService(root).execute(action="page", wiki_path=source_page)
            if source_page
            else {}
        )
        claim_page_read = (
            WikiReadService(root).execute(action="page", wiki_path=claim_page)
            if claim_page
            else {}
        )
        links_read = (
            WikiReadService(root).execute(action="outlinks", wiki_path=claim_page, limit=20)
            if claim_page
            else {}
        )
        source_read_facts = _read_facts(
            root,
            source_id=source_id,
            source_revision_id=source_revision_id,
            fragment_id=source_ref["fragment_id"],
            fragment_text=str(fragment["text"]),
            source_bytes=fixture["source_text"].encode("utf-8"),
            claim_page=claim_page,
            source_page=source_page,
        )
        query_facts, context_facts = _query_facts(root, task=fixture["statement_text"])
        result: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "status": "executed",
            "case_id": fixture["case_id"],
            "claim_eligible": False,
            "competitive_claim_eligible": False,
            "input_source_sha256": fixture_hash,
            "source": {
                "source_revision_id": source_revision_id,
                "content_sha256": source_card.get("content_sha256"),
                "fragment_id": source_ref["fragment_id"],
                "locator": source_ref["locator"],
                "fragment_text_sha256": source_ref["quote_sha256"],
            },
            "statement": {
                "statement_id": statement_value["statement_id"],
                "knowledge_id": claim_id,
                "knowledge_revision_id": statement_revision_id,
                "statement_sha256": statement_record.get("statement_sha256"),
                "receipt_sha256": statement_receipt.get("receipt_sha256"),
                "receipt_status": statement_receipt.get("status"),
                "current_supported": statement_record.get("current_supported"),
                "source_refs": statement_record.get("source_refs", []),
            },
            "interpretation": {
                "knowledge_id": interpretation_current["knowledge_id"],
                "revision_id": interpretation_current["revision_id"],
                "origin": interpretation_current["origin"],
                "authority": interpretation_current["authority"],
                "legal_authority": False,
                "source_free": interpretation_current["source_free"],
            },
            "relation": {
                "relation_revision_id": relation_card.get("relation_revision_id"),
                "predicate": relation_card.get("predicate"),
                "subject_knowledge_id": relation_card.get("subject_knowledge_id"),
                "object_knowledge_id": relation_card.get("object_knowledge_id"),
                "evidence_refs": relation_card.get("evidence_refs", []),
                "origin": relation_card.get("origin"),
                "authority": relation_card.get("authority"),
                "legal_authority": False,
            },
            "ledger": {
                "claim_current_revision_id": claim_current["revision_id"],
                "interpretation_current_revision_id": interpretation_current["revision_id"],
                "relation_current_revision_id": relation_card.get("relation_revision_id"),
                "audit_head_before_read": audit_head_before,
                "audit_head_after_read": audit_head_after,
                "audit_head_unchanged": audit_head_before == audit_head_after,
            },
            "source_read": source_read_facts,
            "wiki": {
                **wiki_facts,
                "source_page_read_only": source_page_read.get("write_performed") is False,
                "claim_page_read_only": claim_page_read.get("write_performed") is False,
                "claim_page_sha256": claim_page_read.get("content_sha256"),
                "link_read_only": links_read.get("write_performed") is False,
                "link_index_used": links_read.get("index_used") is True,
                "link_count": links_read.get("total_count", 0),
            },
            "query": query_facts,
            "context": context_facts,
            "limits": {
                "provider_hard_limit_bytes": PROVIDER_HARD_LIMIT,
                "local_capsule_hard_limit_bytes": LOCAL_HARD_LIMIT,
                "query_provider_bytes": query_facts["provider_bytes"],
                "context_provider_bytes": context_facts["provider_bytes"],
            },
            "known_limitations": [],
            "not_executed": ["relation_path_query", "human_gold_review", "real_provider_host"],
            "write_performed": False,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }
        deferred = wiki_facts["resolver"]["statement_target"]
        if deferred.get("reason") == "statement_map_deferred":
            result["known_limitations"].append("statement_target_resolver_deferred")
        serialized = canonical_json(result)
        if len(serialized.encode("utf-8")) > LOCAL_HARD_LIMIT:
            raise RuntimeError("Evidence Wiki candidate exceeds its local hard limit")
        if (
            _ABSOLUTE_PATH.search(serialized)
            or _WINDOWS_PATH.search(serialized)
            or _SECRET_LIKE.search(serialized)
        ):
            raise RuntimeError(
                "Evidence Wiki candidate contains disallowed path or secret-like material"
            )
        return result


def build_candidate(source: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    """Compatibility alias used by benchmark tests."""

    return run_candidate(source)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the offline Evidence Wiki candidate.")
    parser.add_argument("source", type=Path, help="one development source JSON file")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    value = run_candidate(args.source)
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output is None:
        print(payload, end="")
    else:
        if args.output.is_symlink():
            raise ValueError("candidate output must not be a symbolic link")
        args.output.write_text(payload, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
