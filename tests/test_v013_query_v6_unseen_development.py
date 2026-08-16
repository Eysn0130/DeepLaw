"""Repository-visible Query v6 development challenge (claim-ineligible).

This fixture was introduced as a fresh challenge, then used during remediation;
it is therefore tuning-used development material, not Human Gold, a holdout, a
benchmark, or qualification evidence.  The source is ingested and reviewed
through the public CLI, semantic knowledge is built by the public
observe/inventory/finalize/validate/commit path, and the assertions exercise
only the public Python and MCP context seams.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from deeplaw.api import KnowledgeOS
from deeplaw.compilation.semantic import SemanticCompilationService
from deeplaw.evidence.statements import build_input_set_sha256, statement_sha256
from deeplaw.knowledge_mcp_server import handle_knowledge_support
from deeplaw.subprocess_environment import _build_subprocess_environment
from deeplaw.util import canonical_json, sha256_bytes

_REPOSITORY = Path(__file__).resolve().parents[1]

# Development-only labels make the metric report deterministic without loading
# any repository Gold/holdout fixture.  The labels intentionally remain in the
# source text so the expected target set is inspectable from the public seam.
_SOURCE_V1 = "\n".join(
    [
        "# Alpha and Beta comparison",
        "ALPHA_BOTH: Policy Alpha and Policy Beta are both mentioned in this comparison. "
        "Policy Alpha requires 30 days for the Alpha archive, while Policy Beta requires "
        "60 days for the Beta archive.",
        "",
        "# Alpha exception",
        "ALPHA_EXCEPTION: Policy Alpha does not require 30 days for temporary drafts; "
        "this exception overrides the general Alpha archive rule.",
        "",
        "# Alpha multilingual proper noun",
        "ALPHA_MULTILINGUAL: 阿尔法政策 governs the 星河项目 (Xinghe Project), a multilingual "
        "proper noun used for the Alpha archive.",
        "",
        "# Alpha alias collision",
        "ALIAS_ALPHA: Alpha Archive registers a governed proper-name alias for the Alpha record.",
        "",
        "# Beta alias collision",
        "ALIAS_BETA: Beta Archive registers the same governed proper-name alias for a separate "
        "Beta record.",
        "",
        "# Alpha homonym",
        "HOMONYM_POLICY: Mercury is the codename for the Alpha policy archive, not a legal "
        "conclusion.",
        "",
        "# Unrelated homonym",
        "HOMONYM_PLANET: Mercury is a planet and an unrelated astronomy reference with no "
        "policy requirement.",
        "",
        "# Alphabet substring distractor",
        "ALPHABET_SUBSTRING: Alphabetical indexing is unrelated metadata and contains no "
        "retention rule.",
        "",
        "# Beta-only target",
        "BETA_ONLY: Policy Beta alone requires 60 days for Beta-only records.",
        "",
        "# Irrelevant translated-keyword distractor",
        "TRANSLATED_DISTRACTOR: 无关 Gamma 只讨论 unrelated translation-keyword material.",
        "",
        "# Contradiction and exception witness",
        "ALPHA_CONTRADICTION: Policy Alpha requires 30 days in the general archive; the "
        "temporary-draft exception says Alpha does not require 30 days there.",
    ]
) + "\n"

_SOURCE_V2 = """\
# Alpha new revision
ALPHA_NEW: Policy Alpha now requires 45 days in the new source revision.
"""

_ALPHA_TARGETS = {
    "ALPHA_BOTH",
    "ALPHA_EXCEPTION",
    "ALPHA_CONTRADICTION",
}
_ALPHA_NEGATIVE_TARGET = "ALPHA_EXCEPTION"
_ALPHA_FALSE_POSITIVES = {
    "ALPHABET_SUBSTRING",
    "BETA_ONLY",
    "TRANSLATED_DISTRACTOR",
    "ALIAS_BETA",
    "HOMONYM_PLANET",
}


def _run_cli(cli_home: Path, *arguments: str) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, "-m", "deeplaw", "knowledge", "--format", "json", *arguments],
        cwd=_REPOSITORY,
        capture_output=True,
        check=False,
        env=_build_subprocess_environment(
            overrides={
                "HOME": str(cli_home),
                "PYTHONPATH": str(_REPOSITORY / "src"),
            }
        ),
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr
    value = json.loads(completed.stdout)
    assert isinstance(value, dict)
    return value


def _source_ref(packet: dict[str, Any], fragment: dict[str, Any]) -> dict[str, str]:
    return {
        "source_revision_id": packet["source_revision_id"],
        "fragment_id": fragment["fragment_id"],
        "locator": fragment["locator"],
        "quote_sha256": fragment["text_sha256"],
    }


def _statement(*, body: str, source_refs: list[dict[str, str]]) -> dict[str, Any]:
    gaps: list[dict[str, str]] = []
    return {
        "ordinal": 1,
        "char_start": 0,
        "char_end": len(body),
        "statement_text": body,
        "statement_sha256": statement_sha256(body),
        "statement_type": "factual",
        "support_status": "supported",
        "source_refs": source_refs,
        "knowledge_revision_refs": [],
        "relation_revision_refs": [],
        "valid_from": None,
        "valid_to": None,
        "limitation": None,
        "gaps": gaps,
        "input_set_sha256": build_input_set_sha256(
            source_refs=source_refs,
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


def _observation_plan(packet: dict[str, Any], observations: list[dict[str, Any]]) -> dict[str, Any]:
    fragment_ids = [fragment["fragment_id"] for fragment in packet["fragments"]]
    return {
        "schema_version": "deeplaw.source-compilation-observation-plan/v2",
        "compilation_run_id": packet["compilation_run_id"],
        "source_revision_id": packet["source_revision_id"],
        "packet_id": packet["packet_id"],
        "expected_audit_head": packet["input_audit_head"],
        "observations": observations,
        "coverage": {
            "packet_fragment_count": len(fragment_ids),
            "covered_fragment_ids": fragment_ids,
            "omitted_fragments": [],
            "ratio": 1.0,
        },
        "warnings": [],
    }


def _object_action(
    *,
    packet: dict[str, Any],
    body: str,
    source_refs: list[dict[str, str]],
    semantic_key: str,
    kind: str,
    title: str,
    aliases: list[str],
    synthesis_inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "action": "create",
        "kind": kind,
        "semantic_key": semantic_key,
        "knowledge_id": None,
        "expected_revision_id": None,
        "title": title,
        "body": body,
        "aliases": aliases,
        "epistemic_state": "supported",
        "source_refs": source_refs,
        "assertion": None,
        "tags": ["unseen-query-v6-development"],
        "valid_from": None,
        "valid_to": None,
        "applicability": {
            "description": "Development-only source witness; not Human Gold or holdout.",
            "scopes": [],
            "conditions": [],
            "exclusions": [],
        },
        "synthesis_inputs": synthesis_inputs,
        "reason": "Freeze a deterministic public-seam development candidate.",
    }


def _packet_plan(packet: dict[str, Any], actions: list[dict[str, Any]]) -> dict[str, Any]:
    fragment_ids = [fragment["fragment_id"] for fragment in packet["fragments"]]
    return {
        "schema_version": "deeplaw.source-compilation-plan/v1",
        "source_revision_id": packet["source_revision_id"],
        "packet_id": packet["packet_id"],
        "expected_audit_head": packet["input_audit_head"],
        "object_actions": actions,
        "relation_actions": [],
        "identity_actions": [],
        "unresolved_identities": [],
        "contradictions": [
            {
                "subject": "ALPHA_CONTRADICTION",
                "reason": (
                    "The general Alpha rule and temporary-draft exception are both "
                    "retained as evidence."
                ),
            }
        ],
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


def _approve_source(
    root: Path,
    source_path: Path,
    *,
    title: str,
    cli_home: Path,
) -> dict[str, Any]:
    source = _run_cli(
        cli_home,
        "source",
        "add",
        "--vault",
        str(root),
        "--source",
        str(source_path),
        "--source-kind",
        "document",
        "--title",
        title,
        "--trust",
        "user_provided",
        "--sensitivity",
        "public",
        "--confirm-no-case-data",
    )["source"]
    manifest = _run_cli(
        cli_home,
        "review",
        "manifest",
        "--vault",
        str(root),
        "--source-id",
        source["source_id"],
    )
    approved = _run_cli(
        cli_home,
        "review",
        "approve-source",
        "--vault",
        str(root),
        "--source-id",
        source["source_id"],
        "--review-manifest-sha256",
        manifest["review_manifest_sha256"],
        "--reviewer-id",
        "unseen-query-v6-development",
        "--reason",
        "Approve deterministic development material; not Human Gold or holdout.",
        "--confirm-reviewed",
    )
    assert approved["source_activated"] is True
    return source


def _compile_semantic(
    root: Path,
    *,
    source: dict[str, Any],
    grant_id: str,
) -> dict[str, Any]:
    service = SemanticCompilationService(root)
    with KnowledgeOS.open(root) as knowledge_os:
        profile = knowledge_os.compilations.profile(version="3")
        run = knowledge_os.compilations.begin(
            grant_id=grant_id,
            source_revision_id=source["source_revision_id"],
            compiler_profile=profile["compiler_profile"],
            compiler_profile_version=profile["compiler_profile_version"],
            host_identity="unseen-query-v6-development",
            model_identity=None,
            prompt_template_id=profile["prompt_template_id"],
            prompt_config_sha256=profile["prompt_config_sha256"],
            plan_configuration_sha256=profile["plan_configuration_sha256"],
            packet_max_fragments=128,
            confirm_no_case_data=True,
        )

        packets: list[dict[str, Any]] = []
        observations: list[dict[str, Any]] = []
        action_specs: list[tuple[dict[str, Any], dict[str, Any], dict[str, str], str]] = []
        while packet := run.next_packet():
            packets.append(packet)
            packet_observations: list[dict[str, Any]] = []
            for fragment in packet["fragments"]:
                body = fragment["text"]
                marker = next(
                    (
                        token
                        for token in (
                            "ALPHA_BOTH",
                            "ALPHA_EXCEPTION",
                            "ALPHA_MULTILINGUAL",
                            "ALIAS_ALPHA",
                            "ALIAS_BETA",
                            "HOMONYM_POLICY",
                            "HOMONYM_PLANET",
                            "ALPHABET_SUBSTRING",
                            "BETA_ONLY",
                            "TRANSLATED_DISTRACTOR",
                            "ALPHA_CONTRADICTION",
                            "ALPHA_NEW",
                        )
                        if token in body
                    ),
                    "UNLABELED",
                )
                semantic_key = f"claim:unseen-query-v6:{marker.lower()}"
                source_ref = _source_ref(packet, fragment)
                aliases = [marker]
                if marker in {"ALIAS_ALPHA", "ALIAS_BETA"}:
                    aliases.append("North Star")
                observation = {
                    "packet_id": packet["packet_id"],
                    "semantic_key_candidate": semantic_key,
                    "kind": "claim",
                    "title_candidate": marker,
                    "body_candidate": body,
                    "aliases": aliases,
                    "source_refs": [source_ref],
                    "assertion": None,
                    "applicability": None,
                    "tags": ["unseen-query-v6-development"],
                    "reason": "Freeze one deterministic tuning-used development observation.",
                }
                observation["observation_id"] = service.observation_id(
                    compilation_run_id=packet["compilation_run_id"],
                    packet_id=packet["packet_id"],
                    observation=observation,
                )
                packet_observations.append(observation)
                observations.append(observation)
                action_specs.append((packet, observation, source_ref, marker))
            run.stage_observations(
                _observation_plan(packet, packet_observations),
                confirm_no_case_data=True,
            )

        assert packets, "the approved development source must produce a packet"
        inventory = run.semantic_inventory(confirm_no_case_data=True)
        finalization = run.finalization_packet()
        source_refs = [
            _source_ref(packet, fragment)
            for packet in packets
            for fragment in packet["fragments"]
        ]
        summary_inputs = {
            "source_revision_ids": [source["source_revision_id"]],
            "knowledge_revision_ids": [],
            "relation_revision_ids": [],
            "compilation_run_ids": [run.compilation_run_id],
        }
        summary_inputs["input_set_sha256"] = sha256_bytes(
            canonical_json(summary_inputs).encode("utf-8")
        )
        packet_actions: dict[str, list[dict[str, Any]]] = {
            packet["packet_id"]: [] for packet in packets
        }
        statement_plans: list[dict[str, Any]] = []
        observation_dispositions: list[dict[str, Any]] = []
        for packet, observation, source_ref, marker in action_specs:
            body = observation["body_candidate"]
            action = _object_action(
                packet=packet,
                body=body,
                source_refs=[source_ref],
                semantic_key=observation["semantic_key_candidate"],
                kind="claim",
                title=marker,
                aliases=list(observation["aliases"]),
            )
            packet_actions[packet["packet_id"]].append(action)
            statement_plans.append(
                {
                    "packet_id": packet["packet_id"],
                    "object_action_ordinal": len(packet_actions[packet["packet_id"]]),
                    "statements": [_statement(body=body, source_refs=[source_ref])],
                }
            )
            observation_dispositions.append(
                {
                    "observation_id": observation["observation_id"],
                    "disposition": "published",
                    "target_ref": observation["semantic_key_candidate"],
                    "reason": "Publish the deterministic development claim candidate.",
                }
            )

        first_packet = packets[0]
        summary_body = (
            "Unseen Query v6 development source summary. "
            "It contains policy names, exceptions, contradiction witnesses, aliases, "
            "homonyms, and translated distractors."
        )
        summary_action = _object_action(
            packet=first_packet,
            body=summary_body,
            source_refs=source_refs,
            semantic_key=f"source-summary:{source['source_revision_id']}",
            kind="synthesis",
            title="Unseen Query v6 development source summary",
            aliases=[],
            synthesis_inputs=summary_inputs,
        )
        packet_actions[first_packet["packet_id"]].insert(0, summary_action)
        # The first packet's existing claim action ordinals move by one after
        # the summary is inserted.
        for item in statement_plans:
            if item["packet_id"] == first_packet["packet_id"]:
                item["object_action_ordinal"] += 1
        statement_plans.append(
            {
                "packet_id": first_packet["packet_id"],
                "object_action_ordinal": 1,
                "statements": [_statement(body=summary_body, source_refs=source_refs)],
            }
        )

        packet_plans = [
            _packet_plan(packet, packet_actions[packet["packet_id"]]) for packet in packets
        ]
        claim_observation_ids = [item["observation_id"] for item in observations]
        duty_reports: list[dict[str, Any]] = []
        for duty in finalization["duties"]:
            duty_type = duty["duty_type"]
            applicability = duty["applicability"]
            if applicability == "applicable":
                status = "satisfied"
                unresolved_items: list[str] = []
                omission_reason = None
                if duty_type == "source_summary":
                    output_refs: list[str] = []
                    evidence_refs = source_refs
                elif duty_type == "key_claims":
                    output_refs = claim_observation_ids
                    evidence_refs = source_refs
                else:
                    output_refs = []
                    evidence_refs = []
            elif applicability == "unknown":
                status = "unresolved"
                output_refs = []
                evidence_refs = []
                unresolved_items = [
                    (
                        "Development fixture intentionally leaves this duty unresolved; "
                        "it is not qualification evidence."
                    )
                ]
                omission_reason = None
            else:
                status = "omitted_with_reason"
                output_refs = []
                evidence_refs = []
                unresolved_items = []
                omission_reason = "No deterministic witness in this development source."
            duty_reports.append(
                {
                    "duty_id": duty["duty_id"],
                    "duty_type": duty_type,
                    "required": duty["required"],
                    "applicability": applicability,
                    "status": status,
                    "output_refs": output_refs,
                    "evidence_refs": evidence_refs,
                    "reason": "Deterministic tuning-used development duty decision.",
                    "unresolved_items": unresolved_items,
                    "omission_reason": omission_reason,
                    "deterministic_basis": duty["deterministic_basis"],
                }
            )
        publication_plan = {
            "schema_version": "deeplaw.semantic-publication-plan/v3",
            "compiler_profile_version": "3",
            "compilation_run_id": run.compilation_run_id,
            "source_revision_id": source["source_revision_id"],
            "expected_audit_head": first_packet["input_audit_head"],
            "inventory_sha256": inventory["inventory_sha256"],
            "finalization_packet_id": finalization["finalization_packet_id"],
            "applicability_policy_sha256": finalization["applicability_policy_sha256"],
            "applicability_digest": finalization["applicability_digest"],
            "packet_plans": packet_plans,
            "statement_plans": statement_plans,
            "observation_dispositions": observation_dispositions,
            "duty_reports": duty_reports,
            "semantic_status": "partial",
            "warnings": ["Tuning-used development challenge; claim-ineligible."],
        }
        staged = run.stage_publication(publication_plan, confirm_no_case_data=True)
        validation = run.validate(confirm_no_case_data=True)
        assert validation["valid"] is True
        receipt = run.commit(confirm_no_case_data=True)
    return {
        "root": root,
        "source": source,
        "source_path": root.parent / "unseen-query-v6.md",
        "grant_id": grant_id,
        "compilation_run_id": run.compilation_run_id,
        "staged": staged,
        "receipt": receipt,
    }


def _build_case(base: Path, *, source_text: str = _SOURCE_V1) -> dict[str, Any]:
    root = base / "vault"
    source_path = base / "unseen-query-v6.md"
    base.mkdir(parents=True, exist_ok=True)
    cli_home = base / "cli-home"
    cli_home.mkdir(parents=True, exist_ok=True)
    source_path.write_text(source_text, encoding="utf-8", newline="\n")
    _run_cli(
        cli_home,
        "init",
        "--vault",
        str(root),
        "--name",
        "unseen-query-v6-development",
        "--scope",
        "project",
    )
    source = _approve_source(
        root,
        source_path,
        title="Unseen Query v6 development",
        cli_home=cli_home,
    )
    grant = _run_cli(
        cli_home,
        "sink",
        "enable",
        "--vault",
        str(root),
        "--writer-id",
        "unseen-query-v6-development",
        "--profile",
        "semantic-compiler",
        "--scope",
        "project",
        "--max-sensitivity",
        "public",
        "--max-request-bytes",
        "262144",
    )
    result = _compile_semantic(root, source=source, grant_id=grant["grant_id"])
    result["cli_home"] = cli_home
    result["source_path"] = source_path
    result["source_title"] = "Unseen Query v6 development"
    return result


def _labels(context: dict[str, Any]) -> set[str]:
    return {
        marker
        for marker in (
            "ALPHA_BOTH",
            "ALPHA_EXCEPTION",
            "ALPHA_MULTILINGUAL",
            "ALIAS_ALPHA",
            "ALIAS_BETA",
            "HOMONYM_POLICY",
            "HOMONYM_PLANET",
            "ALPHABET_SUBSTRING",
            "BETA_ONLY",
            "TRANSLATED_DISTRACTOR",
            "ALPHA_CONTRADICTION",
            "ALPHA_NEW",
        )
        if any(
            marker in str(item.get("statement_text", ""))
            for item in context.get("statements", [])
        )
    }


def _context(case: dict[str, Any], task: str) -> tuple[dict[str, Any], dict[str, Any]]:
    root = case["root"]
    with KnowledgeOS.open(root) as knowledge_os:
        local = knowledge_os.context.compile(
            task=task,
            purpose="verify",
            scope="project",
            max_sensitivity="public",
            limit=13,
            max_chars=8_000,
            max_tokens=6_000,
            max_sources=12,
            confirm_no_case_data=True,
        )
    mcp = handle_knowledge_support(
        operation="context",
        task=task,
        purpose="verify",
        limit=13,
        max_chars=8_000,
        max_tokens=6_000,
        max_sources=12,
        scope="project",
        max_sensitivity="public",
        confirm_no_case_data=True,
        vault_path=root,
    )
    assert len(canonical_json(local).encode("utf-8")) <= 65_536
    assert len(canonical_json(mcp["result"]).encode("utf-8")) <= 65_536
    return local, mcp


@pytest.fixture(scope="module")
def development_case(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    # This repository-visible fixture has been consumed by remediation.  It must
    # never be promoted to Human Gold, a holdout, or a qualification claim.
    return _build_case(tmp_path_factory.mktemp("unseen-query-v6"))


def test_v6_unseen_development_metrics_are_explicit_and_claim_ineligible(
    development_case: dict[str, Any],
) -> None:
    alpha, alpha_mcp = _context(development_case, "What does Policy Alpha require?")
    negative, _negative_mcp = _context(
        development_case,
        "What does Policy Alpha not require for temporary drafts?",
    )
    translated, _translated_mcp = _context(
        development_case,
        "Alpha 政策 archive requirements",
    )
    alias, _alias_mcp = _context(development_case, "North Star")
    homonym, _homonym_mcp = _context(development_case, "Mercury policy")

    alpha_labels = _labels(alpha)
    negative_labels = _labels(negative)
    translated_labels = _labels(translated)
    alias_labels = _labels(alias)
    homonym_labels = _labels(homonym)
    failures: list[str] = []

    # False Suppression: negative language must retain the explicit exception.
    if _ALPHA_NEGATIVE_TARGET not in negative_labels:
        failures.append(
            "False Suppression: the negative/exception query suppressed ALPHA_EXCEPTION."
        )
    # A normalized substring must not turn Alphabet into Alpha.
    if "ALPHABET_SUBSTRING" in alpha_labels:
        failures.append("Substring false positive: ALPHABET_SUBSTRING was admitted for Alpha.")
    # Candidate ALPHA_BOTH intentionally names A and B; it is still a valid A target.
    if "ALPHA_BOTH" not in alpha_labels:
        failures.append("Wrong-target admission: the A/B candidate ALPHA_BOTH was suppressed.")
    wrong_targets = sorted(alpha_labels & _ALPHA_FALSE_POSITIVES)
    if wrong_targets:
        failures.append(f"Wrong-target admission: Alpha query admitted {wrong_targets}.")
    # Adding an irrelevant translated keyword must not change target admission.
    distractor_delta = sorted((translated_labels - alpha_labels) & _ALPHA_FALSE_POSITIVES)
    if distractor_delta:
        failures.append(
            "Distractor-induced delta: translated keyword admitted "
            f"{distractor_delta} into the Alpha context."
        )
    # Useful Context Recall is measured over the frozen development target set.
    useful_recall = len(alpha_labels & _ALPHA_TARGETS) / len(_ALPHA_TARGETS)
    if useful_recall < 1.0:
        failures.append(
            f"Useful Context Recall: expected 1.0 for {_ALPHA_TARGETS}, got {useful_recall:.3f}."
        )
    # Alias collision and same-form homonym cases must remain visible/ambiguous rather than
    # being silently collapsed to one target.
    if not {"ALIAS_ALPHA", "ALIAS_BETA"} <= alias_labels:
        failures.append(
            "Alias collision recall: expected both aliases, "
            f"got {sorted(alias_labels)}."
        )
    if not {"HOMONYM_POLICY", "HOMONYM_PLANET"} <= homonym_labels:
        failures.append(
            "Homonym ambiguity recall: both Mercury meanings must remain visible until "
            f"disambiguated, got {sorted(homonym_labels)}."
        )

    selected_texts = [item["statement_text"] for item in alpha.get("statements", [])]
    relevant_chars = sum(
        len(text)
        for text in selected_texts
        if any(marker in text for marker in _ALPHA_TARGETS)
    )
    context_chars = sum(len(text) for text in selected_texts)
    redundancy = len(selected_texts) - len(set(selected_texts))
    provider_bytes = len(canonical_json(alpha_mcp["result"]).encode("utf-8"))
    assert relevant_chars <= context_chars
    assert context_chars <= 8_000
    assert redundancy == 0, "Query v6 provider selection duplicated a statement."
    assert provider_bytes <= 65_536

    expansion = alpha["query_plan"]["query_expansion"]
    required_receipt_fields = (
        "profile",
        "profile_sha256",
        "lexicon_sha256",
        "configuration_sha256",
        "terms_sha256",
    )
    missing_receipt_fields = [field for field in required_receipt_fields if field not in expansion]
    if missing_receipt_fields:
        failures.append(
            "Receipt expansion binding is incomplete: missing "
            f"{missing_receipt_fields}; profile/lexicon/config/term digests "
            "must be bound before this challenge can qualify."
        )
    assert not failures, "\n".join(failures)


def test_v6_unseen_development_stale_and_new_revision_are_measured(
    tmp_path: Path,
) -> None:
    case = _build_case(tmp_path / "stale-new")
    old_context, _ = _context(case, "Policy Alpha current requirement")
    old_labels = _labels(old_context)
    source_path = case["source_path"]
    source_path.write_text(_SOURCE_V2, encoding="utf-8", newline="\n")
    new_source = _approve_source(
        case["root"],
        source_path,
        title=case["source_title"],
        cli_home=case["cli_home"],
    )
    _compile_semantic(case["root"], source=new_source, grant_id=case["grant_id"])
    latest, latest_mcp = _context(case, "Policy Alpha current requirement")
    latest_labels = _labels(latest)
    stale_selected = sorted(old_labels & latest_labels & _ALPHA_TARGETS)
    assert "ALPHA_NEW" in latest_labels, (
        "Useful Context Recall: the new source revision was not admitted. "
        f"old={sorted(old_labels)} latest={sorted(latest_labels)}"
    )
    assert not stale_selected, (
        "stale/new revision admission selected old statements after a reviewed successor: "
        f"{stale_selected}"
    )
    assert len(canonical_json(latest_mcp["result"]).encode("utf-8")) <= 65_536
    freshness = {
        item.get("statement_text", ""): item.get("freshness")
        for item in latest.get("statements", [])
    }
    assert all(value in {None, "fresh"} for value in freshness.values())
