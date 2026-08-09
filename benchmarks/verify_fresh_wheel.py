from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from deeplaw import __version__
from deeplaw.compilation import CompilationCoordinator
from deeplaw.evidence import build_input_set_sha256
from deeplaw.util import canonical_json, sha256_bytes, sha256_file, stable_id

_POSIX_ABSOLUTE_PATH = re.compile(
    r"/(?:Users|home|private|tmp|var)(?:[\\/][^\s,;:()<>]+)*"
)
_WINDOWS_ABSOLUTE_PATH = re.compile(
    r"(?i)(?:[A-Za-z]:[\\/]|\\\\)(?:[^\s,;:()<>]+[\\/])*[^\s,;:()<>]*"
)


def _run(interpreter: Path, *arguments: str) -> dict[str, Any]:
    completed = subprocess.run(
        [str(interpreter), "-m", "deeplaw", *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"fresh-wheel command failed ({completed.returncode}): "
            f"{' '.join(arguments)}\n{completed.stderr[:2000]}"
        )
    return json.loads(completed.stdout)


def _source_reference(packet: dict[str, Any], fragment: dict[str, Any]) -> dict[str, str]:
    return {
        "source_revision_id": packet["source_revision_id"],
        "fragment_id": fragment["fragment_id"],
        "locator": fragment["locator"],
        "quote_sha256": fragment["text_sha256"],
    }


def _semantic_packet_plan(
    packet: dict[str, Any],
    *,
    compilation_run_id: str,
) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    statements: list[dict[str, Any]] = []
    for fragment in packet["fragments"]:
        body = str(fragment["text"])
        source_refs = [_source_reference(packet, fragment)]
        title = f"Fresh wheel claim {fragment['ordinal']}"
        observation = {
            "packet_id": packet["packet_id"],
            "semantic_key_candidate": f"fresh-wheel:{fragment['fragment_id']}",
            "kind": "claim",
            "title_candidate": title,
            "body_candidate": body,
            "aliases": [],
            "source_refs": source_refs,
            "assertion": None,
            "applicability": None,
            "tags": ["fresh-wheel"],
            "reason": "Verify installed-wheel Source-to-Knowledge publication.",
        }
        observation["observation_id"] = stable_id(
            "observation",
            compilation_run_id,
            packet["packet_id"],
            canonical_json(observation),
        )
        observations.append(observation)
        actions.append(
            {
                "action": "create",
                "kind": observation["kind"],
                "semantic_key": observation["semantic_key_candidate"],
                "knowledge_id": None,
                "expected_revision_id": None,
                "title": title,
                "body": body,
                "aliases": observation["aliases"],
                "epistemic_state": "supported",
                "source_refs": source_refs,
                "assertion": observation["assertion"],
                "tags": observation["tags"],
                "valid_from": None,
                "valid_to": None,
                "applicability": {
                    "description": "Fresh-wheel Source Revision.",
                    "scopes": [],
                    "conditions": [],
                    "exclusions": [],
                },
                "synthesis_inputs": None,
                "reason": "Verify installed-wheel Source-to-Knowledge publication.",
            }
        )
        text_sha256 = sha256_bytes(body.encode("utf-8"))
        statements.append(
            {
                "packet_id": packet["packet_id"],
                "object_action_ordinal": len(actions),
                "statements": [
                    {
                        "ordinal": 1,
                        "char_start": 0,
                        "char_end": len(body),
                        "statement_text": body,
                        "statement_sha256": text_sha256,
                        "statement_type": "factual",
                        "support_status": "supported",
                        "source_refs": source_refs,
                        "knowledge_revision_refs": [],
                        "relation_revision_refs": [],
                        "valid_from": None,
                        "valid_to": None,
                        "limitation": None,
                        "gaps": [],
                        "input_set_sha256": build_input_set_sha256(
                            source_refs=source_refs,
                            knowledge_revision_refs=[],
                            relation_revision_refs=[],
                            valid_from=None,
                            valid_to=None,
                            statement_type="factual",
                            support_status="supported",
                            limitation=None,
                            gaps=[],
                        ),
                    }
                ],
            }
        )
    fragment_ids = [fragment["fragment_id"] for fragment in packet["fragments"]]
    return {
        "observation_plan": {
            "schema_version": "deeplaw.source-compilation-observation-plan/v2",
            "compilation_run_id": compilation_run_id,
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
        },
        "packet_plan": {
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
        },
        "observations": observations,
        "statement_plans": statements,
    }


def _semantic_publication_plan(
    *,
    compilation: dict[str, Any],
    finalization: dict[str, Any],
    packet_plans: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    statement_plans: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": "deeplaw.semantic-publication-plan/v3",
        "compiler_profile_version": "3",
        "compilation_run_id": compilation["compilation_run_id"],
        "source_revision_id": compilation["source_revision_id"],
        "expected_audit_head": compilation["input_audit_head"],
        "inventory_sha256": finalization["inventory_sha256"],
        "finalization_packet_id": finalization["finalization_packet_id"],
        "applicability_policy_sha256": finalization["applicability_policy_sha256"],
        "applicability_digest": finalization["applicability_digest"],
        "packet_plans": packet_plans,
        "statement_plans": statement_plans,
        "observation_dispositions": [
            {
                "observation_id": observation["observation_id"],
                "disposition": "published",
                "target_ref": observation["semantic_key_candidate"],
                "reason": "Publish the source-bound fresh-wheel claim.",
            }
            for observation in observations
        ],
        "duty_reports": finalization["duties"],
        "semantic_status": "partial",
        "warnings": [],
    }


def _compiled_query_hit(query: dict[str, Any]) -> bool:
    """Return whether a Query v6 result contains a real compiled hit.

    Query v6 exposes the discovery/admission/selection counts in both its
    Query Plan and metrics surfaces.  Keep the smoke check bound to those
    public fields and to the selected Statement's source binding instead of
    relying on the removed Query v5 hit flag.
    """

    plan = query.get("query_plan")
    metrics = query.get("metrics")
    statements = query.get("statements")
    if not isinstance(plan, dict) or plan.get("schema_version") != (
        "deeplaw.knowledge-query-plan/v6"
    ):
        return False
    if not isinstance(metrics, dict) or not isinstance(statements, list):
        return False

    count_fields = (
        "compiled_candidate_count",
        "admitted_statement_count",
        "selected_statement_count",
    )
    counts: dict[str, int] = {}
    for field in count_fields:
        plan_count = plan.get(field)
        metric_count = metrics.get(field)
        if (
            isinstance(plan_count, bool)
            or not isinstance(plan_count, int)
            or plan_count < 0
            or isinstance(metric_count, bool)
            or not isinstance(metric_count, int)
            or metric_count < 0
            or metric_count != plan_count
        ):
            return False
        counts[field] = plan_count

    if not (
        0 < counts["selected_statement_count"]
        <= counts["admitted_statement_count"]
        <= counts["compiled_candidate_count"]
        and counts["selected_statement_count"] == len(statements)
    ):
        return False

    return any(
        isinstance(statement, dict)
        and statement.get("current_supported") is True
        and isinstance(statement.get("source_refs"), list)
        and bool(statement["source_refs"])
        for statement in statements
    )


def _resume_failure_diagnostic(
    vault: Path,
    *,
    grant_id: str,
    compilation_run_id: str,
) -> str:
    """Reproduce a failed installed-CLI resume without exposing local paths.

    This path runs only after the public installed-wheel command has already
    failed.  It cannot turn the smoke result into a pass; it preserves the raw
    domain exception needed to diagnose platform-specific projection failures.
    """

    try:
        CompilationCoordinator(vault).resume(
            grant_id=grant_id,
            compilation_run_id=compilation_run_id,
            project=True,
            confirm_no_case_data=True,
        )
    except BaseException as error:
        message = str(error)
        for path in (vault, vault.parent):
            for rendered in {
                str(path),
                str(path.resolve()),
                path.as_posix(),
                str(path).replace("/", "\\"),
                str(path.resolve()).replace("/", "\\"),
            }:
                message = message.replace(rendered, "<redacted-path>")
        # Redact both path syntaxes before normalising separators.  The second pass
        # covers paths that are not rooted at the temporary Vault (for example a
        # Windows runner's checkout path) without retaining an absolute prefix.
        message = _POSIX_ABSOLUTE_PATH.sub("<redacted-path>", message)
        message = _WINDOWS_ABSOLUTE_PATH.sub("<redacted-path>", message)
        message = " ".join(message.split())[:500]
        message = message.replace("\\", "/")
        return f"{type(error).__name__}:{message or '<no-message>'}"
    return "direct_resume_passed_after_cli_failure"


def verify_fresh_wheel(dist: Path) -> dict[str, Any]:
    wheels = sorted(dist.glob(f"deeplaw-{__version__}-*.whl"))
    if len(wheels) != 1 or wheels[0].is_symlink() or not wheels[0].is_file():
        raise RuntimeError("fresh-wheel verification requires exactly one current-version wheel")
    wheel = wheels[0].resolve()
    with tempfile.TemporaryDirectory(prefix="deeplaw-wheel-smoke-") as temporary:
        root = Path(temporary)
        environment = root / "venv"
        subprocess.run(
            ["uv", "venv", "--python", sys.executable, str(environment)],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        interpreter = (
            environment / "Scripts" / "python.exe"
            if os.name == "nt"
            else environment / "bin" / "python"
        )
        subprocess.run(
            ["uv", "pip", "install", "--python", str(interpreter), str(wheel)],
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
        version = subprocess.run(
            [str(interpreter), "-m", "deeplaw", "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        vault = root / "vault"
        source = root / "project.md"
        capsule = root / "capsule.json"
        source.write_text(
            "# Decision\nThe fresh wheel uses SQLite as its canonical local store.\n",
            encoding="utf-8",
            newline="\n",
        )
        initialized = _run(
            interpreter,
            "knowledge",
            "init",
            "--vault",
            str(vault),
            "--name",
            "fresh-wheel",
            "--scope",
            "project",
        )
        compiled = _run(
            interpreter,
            "knowledge",
            "source",
            "add",
            "--vault",
            str(vault),
            "--source",
            str(source),
            "--typed-extraction",
            "deterministic-v1",
            "--confirm-no-case-data",
        )
        source_id = compiled["source"]["source_id"]
        manifest = _run(
            interpreter,
            "knowledge",
            "review",
            "manifest",
            "--vault",
            str(vault),
            "--source-id",
            source_id,
        )
        approval = _run(
            interpreter,
            "knowledge",
            "review",
            "approve-source",
            "--vault",
            str(vault),
            "--source-id",
            source_id,
            "--review-manifest-sha256",
            manifest["review_manifest_sha256"],
            "--reviewer-id",
            "wheel-smoke",
            "--reason",
            "Fresh wheel exact-source acceptance.",
            "--confirm-reviewed",
        )
        profile = _run(
            interpreter,
            "knowledge",
            "compile",
            "profile",
            "--vault",
            str(vault),
            "--compiler-profile-version",
            "3",
        )
        compiler_grant = _run(
            interpreter,
            "knowledge",
            "sink",
            "enable",
            "--vault",
            str(vault),
            "--writer-id",
            "fresh-wheel-compiler",
            "--scope",
            "project",
            "--profile",
            "semantic-compiler",
        )
        compilation = _run(
            interpreter,
            "knowledge",
            "compile",
            "begin",
            "--vault",
            str(vault),
            "--grant-id",
            compiler_grant["grant_id"],
            "--source-revision-id",
            compiled["identity"]["source_revision_id"],
            "--host-identity",
            "fresh-wheel-no-model",
            "--compiler-profile-version",
            "3",
            "--confirm-no-case-data",
        )
        run_id = compilation["compilation_run_id"]
        packet_count = 0
        packet_plans: list[dict[str, Any]] = []
        observations: list[dict[str, Any]] = []
        statement_plans: list[dict[str, Any]] = []
        while packet_count < 10_000:
            packet = _run(
                interpreter,
                "knowledge",
                "semantic",
                "packet",
                "--vault",
                str(vault),
                "--grant-id",
                compiler_grant["grant_id"],
                "--run-id",
                run_id,
            )
            if packet.get("complete") is True:
                break
            packet_plan = _semantic_packet_plan(
                packet,
                compilation_run_id=run_id,
            )
            plan_path = root / f"semantic-observation-plan-{packet_count + 1}.json"
            plan_path.write_text(
                json.dumps(
                    packet_plan["observation_plan"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            _run(
                interpreter,
                "knowledge",
                "semantic",
                "observe",
                "--vault",
                str(vault),
                "--grant-id",
                compiler_grant["grant_id"],
                "--run-id",
                run_id,
                "--plan",
                str(plan_path),
                "--confirm-no-case-data",
            )
            packet_plans.append(packet_plan["packet_plan"])
            observations.extend(packet_plan["observations"])
            statement_plans.extend(packet_plan["statement_plans"])
            packet_count += 1
        else:
            raise RuntimeError("fresh-wheel compilation exceeded its packet bound")
        _run(
            interpreter,
            "knowledge",
            "semantic",
            "inventory",
            "--vault",
            str(vault),
            "--grant-id",
            compiler_grant["grant_id"],
            "--run-id",
            run_id,
            "--confirm-no-case-data",
        )
        finalization = _run(
            interpreter,
            "knowledge",
            "semantic",
            "finalization",
            "--vault",
            str(vault),
            "--grant-id",
            compiler_grant["grant_id"],
            "--run-id",
            run_id,
        )
        publication_path = root / "semantic-publication-plan.json"
        publication_path.write_text(
            json.dumps(
                _semantic_publication_plan(
                    compilation=compilation,
                    finalization=finalization,
                    packet_plans=packet_plans,
                    observations=observations,
                    statement_plans=statement_plans,
                ),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        _run(
            interpreter,
            "knowledge",
            "semantic",
            "finalize",
            "--vault",
            str(vault),
            "--grant-id",
            compiler_grant["grant_id"],
            "--run-id",
            run_id,
            "--plan",
            str(publication_path),
            "--confirm-no-case-data",
        )
        compilation_validation = _run(
            interpreter,
            "knowledge",
            "compile",
            "validate",
            "--vault",
            str(vault),
            "--grant-id",
            compiler_grant["grant_id"],
            "--run-id",
            run_id,
            "--confirm-no-case-data",
        )
        compilation_receipt = _run(
            interpreter,
            "knowledge",
            "compile",
            "commit",
            "--vault",
            str(vault),
            "--grant-id",
            compiler_grant["grant_id"],
            "--run-id",
            run_id,
            "--confirm-no-case-data",
        )
        try:
            compilation_completed = _run(
                interpreter,
                "knowledge",
                "compile",
                "resume",
                "--vault",
                str(vault),
                "--grant-id",
                compiler_grant["grant_id"],
                "--run-id",
                run_id,
                "--project",
                "--confirm-no-case-data",
            )
        except RuntimeError:
            diagnostic = _resume_failure_diagnostic(
                vault,
                grant_id=compiler_grant["grant_id"],
                compilation_run_id=run_id,
            )
            raise RuntimeError(
                f"fresh-wheel installed CLI resume failed; diagnostic={diagnostic}"
            ) from None
        compiled_query = _run(
            interpreter,
            "knowledge",
            "query",
            "--vault",
            str(vault),
            "--query",
            "Which canonical local store does the fresh wheel use?",
            "--purpose",
            "answer",
        )
        knowledge_verification = _run(
            interpreter,
            "knowledge",
            "autonomy",
            "verify",
            "--vault",
            str(vault),
        )
        https_preflight = _run(
            interpreter,
            "add",
            "--url",
            "https://example.com/source.md",
            "--dry-run",
            "--vault",
            str(vault),
            "--confirm-no-case-data",
            "--format",
            "json",
        )
        compiled_capsule = _run(
            interpreter,
            "knowledge",
            "context",
            "--vault",
            str(vault),
            "--task",
            "Which canonical local store must the fresh wheel use?",
            "--confirm-no-case-data",
            "--output",
            str(capsule),
        )
        verification = _run(
            interpreter,
            "knowledge",
            "verify-capsule",
            "--vault",
            str(vault),
            "--capsule",
            str(capsule),
        )
        compiled_query_hit = _compiled_query_hit(compiled_query)
    return {
        "schema_version": "deeplaw.fresh-wheel-smoke/v1",
        "package_version": __version__,
        "wheel_name": wheel.name,
        "wheel_sha256": sha256_file(wheel),
        "reported_version": version,
        "vault_id": initialized["vault_id"],
        "source_id": source_id,
        "review_receipt_valid": approval["review_receipt"]["review_receipt_id"].startswith(
            "review_"
        ),
        "compiler_profile": profile["compiler_profile"],
        "compilation_run_id": run_id,
        "compilation_packet_count": packet_count,
        "compilation_validation_valid": compilation_validation["valid"],
        "compilation_object_count": compilation_receipt["observation_count"],
        "compilation_status": compilation_completed["status"],
        "living_wiki_manifest_sha256": compilation_completed["projection"][
            "living_wiki"
        ]["manifest_sha256"],
        "compiled_query_hit": compiled_query_hit,
        "knowledge_verification_valid": knowledge_verification["valid"],
        "https_preflight_valid": (
            https_preflight["network_performed"] is False
            and https_preflight["canonical_requested_url"]
            == "https://example.com/source.md"
        ),
        "capsule_id": compiled_capsule["capsule_id"],
        "capsule_valid": verification["valid"],
        "valid": (
            verification["valid"]
            and compilation_validation["valid"]
            and compilation_receipt["observation_count"] > 0
            and compilation_completed["status"] == "succeeded"
            and compiled_query_hit
            and knowledge_verification["valid"]
            and https_preflight["network_performed"] is False
            and version == f"deeplaw {__version__}"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    args = parser.parse_args()
    result = verify_fresh_wheel(args.dist.expanduser().absolute())
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if not result["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
