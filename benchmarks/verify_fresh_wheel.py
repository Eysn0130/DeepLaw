from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from deeplaw import __version__
from deeplaw.util import sha256_file


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


def _compilation_plan(
    packet: dict[str, Any],
    *,
    input_audit_head: str,
) -> dict[str, Any]:
    actions = []
    fragment_ids = []
    for fragment in packet["fragments"]:
        fragment_ids.append(fragment["fragment_id"])
        actions.append(
            {
                "action": "create",
                "kind": "claim",
                "semantic_key": f"fresh-wheel:{fragment['fragment_id']}",
                "knowledge_id": None,
                "expected_revision_id": None,
                "title": f"Fresh wheel claim {fragment['ordinal']}",
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
                "tags": ["fresh-wheel"],
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
    return {
        "schema_version": "deeplaw.source-compilation-plan/v1",
        "source_revision_id": packet["source_revision_id"],
        "packet_id": packet["packet_id"],
        "expected_audit_head": input_audit_head,
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
            "compiler",
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
            "--confirm-no-case-data",
        )
        run_id = compilation["compilation_run_id"]
        packet_count = 0
        while packet_count < 10_000:
            packet = _run(
                interpreter,
                "knowledge",
                "compile",
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
            plan_path = root / f"compilation-plan-{packet_count + 1}.json"
            plan_path.write_text(
                json.dumps(
                    _compilation_plan(
                        packet,
                        input_audit_head=compilation["input_audit_head"],
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
                "compile",
                "stage",
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
            packet_count += 1
        else:
            raise RuntimeError("fresh-wheel compilation exceeded its packet bound")
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
        "compilation_object_count": compilation_receipt["committed_object_count"],
        "compilation_status": compilation_completed["status"],
        "living_wiki_manifest_sha256": compilation_completed["projection"][
            "living_wiki"
        ]["manifest_sha256"],
        "compiled_query_hit": compiled_query["metrics"]["compiled_hit"],
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
            and compilation_receipt["committed_object_count"] > 0
            and compilation_completed["status"] == "succeeded"
            and compiled_query["metrics"]["compiled_hit"]
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
