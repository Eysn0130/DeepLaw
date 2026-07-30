from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from deeplaw.api import KnowledgeOS
from deeplaw.util import canonical_json

SCHEMA_VERSION = "deeplaw.deterministic-fake-agent-compile/v1"


def _plan(packet: dict[str, Any]) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    for fragment in packet["fragments"]:
        first_line = next(
            (
                line.lstrip("# ").strip()
                for line in fragment["text"].splitlines()
                if line.strip()
            ),
            f"Fragment {fragment['ordinal']}",
        )
        actions.append(
            {
                "action": "create",
                "kind": "claim",
                "semantic_key": (
                    f"deterministic-fake-agent:{packet['source_revision_id']}:"
                    f"{fragment['fragment_id']}"
                ),
                "knowledge_id": None,
                "expected_revision_id": None,
                "title": first_line[:500],
                "body": fragment["text"],
                "aliases": [],
                "epistemic_state": "supported",
                "source_refs": [
                    {
                        "source_revision_id": packet["source_revision_id"],
                        "fragment_id": fragment["fragment_id"],
                        "locator": fragment["locator"],
                        "quote_sha256": fragment["text_sha256"],
                    }
                ],
                "assertion": None,
                "tags": ["deterministic-fake-agent", "source-compiled"],
                "valid_from": None,
                "valid_to": None,
                "applicability": {
                    "description": "Bound to the exact compiled Source Revision.",
                    "scopes": [],
                    "conditions": [],
                    "exclusions": [],
                },
                "synthesis_inputs": None,
                "reason": "Deterministically compile one reusable source-bound claim.",
            }
        )
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


def compile_with_fake_agent(
    *,
    vault: str | Path,
    grant_id: str,
    source_revision_id: str,
    packet_max_fragments: int = 8,
) -> dict[str, Any]:
    knowledge_os = KnowledgeOS.open(vault)
    profile = knowledge_os.compilations.profile()
    run = knowledge_os.compilations.begin(
        grant_id=grant_id,
        source_revision_id=source_revision_id,
        compiler_profile=profile["compiler_profile"],
        compiler_profile_version=profile["compiler_profile_version"],
        host_identity="deeplaw-deterministic-fake-agent",
        model_identity=None,
        prompt_template_id=profile["prompt_template_id"],
        prompt_config_sha256=profile["prompt_config_sha256"],
        plan_configuration_sha256=profile["plan_configuration_sha256"],
        packet_max_fragments=packet_max_fragments,
        confirm_no_case_data=True,
    )
    begin = run.begin_receipt()
    packet_count = 0
    staged_object_count = 0
    query_text = ""
    while packet := run.next_packet():
        packet_count += 1
        if not query_text:
            query_text = packet["fragments"][0]["text"][:500]
        batch = run.stage(_plan(packet), confirm_no_case_data=True)
        staged_object_count += batch["object_count"]
    validation = run.validate(confirm_no_case_data=True)
    run.commit(confirm_no_case_data=True)
    completed = run.resume(project=True, confirm_no_case_data=True)
    verification = knowledge_os.verify()
    retrieval = knowledge_os.retrieval.query(
        query_text,
        purpose="answer",
        limit=8,
        max_chars=8_000,
        max_tokens=4_000,
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        "host_identity": "deeplaw-deterministic-fake-agent",
        "model_identity": None,
        "source_revision_id": source_revision_id,
        "compilation_run_id": begin["compilation_run_id"],
        "packet_count": packet_count,
        "staged_object_count": staged_object_count,
        "validation_sha256": validation["validation_sha256"],
        "receipt_sha256": completed["receipt_sha256"],
        "projection_manifest_sha256": completed["projection"]["living_wiki"][
            "manifest_sha256"
        ],
        "compiled_result_count": len(retrieval["compiled"]),
        "status": completed["status"],
        "verification_valid": verification["valid"],
        "network_used": False,
        "external_credentials_used": False,
    }
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "contracts"
        / "deterministic-fake-agent-compile.v1.schema.json"
    )
    Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8"))).validate(
        report
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the no-network deterministic Living Wiki compilation agent."
    )
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--grant-id", required=True)
    parser.add_argument("--source-revision-id", required=True)
    parser.add_argument("--packet-max-fragments", type=int, default=8)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = compile_with_fake_agent(
        vault=args.vault,
        grant_id=args.grant_id,
        source_revision_id=args.source_revision_id,
        packet_max_fragments=args.packet_max_fragments,
    )
    payload = canonical_json(report) + "\n"
    if args.output is not None:
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
