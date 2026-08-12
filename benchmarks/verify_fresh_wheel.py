from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

_POSIX_ABSOLUTE_PATH = re.compile(
    r"/(?:Users|home|private|tmp|var)(?:[\\/][^\s,;:()<>]+)*"
)
_WINDOWS_ABSOLUTE_PATH = re.compile(
    r"(?i)(?:[A-Za-z]:[\\/]|\\\\)(?:[^\s,;:()<>]+[\\/])*[^\s,;:()<>]*"
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _stable_id(prefix: str, *parts: str, length: int = 24) -> str:
    payload = "\x00".join(parts).encode("utf-8")
    return f"{prefix}_{_sha256_bytes(payload)[:length]}"


def _build_input_set_sha256(
    *,
    source_refs: list[dict[str, Any]],
    statement_type: str,
    support_status: str,
) -> str:
    payload = {
        "source_refs": sorted(source_refs, key=_canonical_json),
        "knowledge_revision_refs": [],
        "relation_revision_refs": [],
        "valid_from": None,
        "valid_to": None,
        "statement_type": statement_type,
        "support_status": support_status,
        "limitation": None,
        "gaps": [],
    }
    return _sha256_bytes(_canonical_json(payload).encode("utf-8"))


def _sanitize_diagnostic(value: str, *, roots: tuple[Path, ...]) -> str:
    message = value
    for path in roots:
        for rendered in {
            str(path),
            str(path.resolve()),
            path.as_posix(),
            str(path).replace("/", "\\"),
            str(path.resolve()).replace("/", "\\"),
        }:
            message = message.replace(rendered, "<redacted-path>")
    message = _POSIX_ABSOLUTE_PATH.sub("<redacted-path>", message)
    message = _WINDOWS_ABSOLUTE_PATH.sub("<redacted-path>", message)
    return " ".join(message.split())[:500].replace("\\", "/")


def _isolated_environment(interpreter: Path) -> tuple[Path, dict[str, str]]:
    root = interpreter.parents[2]
    executable = (
        interpreter.parent / "deeplaw.exe"
        if os.name == "nt"
        else interpreter.parent / "deeplaw"
    )
    runtime = root / "runtime"
    user_home = root / "home"
    xdg_config = root / "xdg-config"
    for directory in (runtime, user_home, xdg_config):
        directory.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(user_home),
            "USERPROFILE": str(user_home),
            "XDG_CONFIG_HOME": str(xdg_config),
            "PYTHONPATH": "",
            "PYTHONNOUSERSITE": "1",
        }
    )
    return executable, environment


def _run(interpreter: Path, *arguments: str) -> dict[str, Any]:
    executable, environment = _isolated_environment(interpreter)
    completed = subprocess.run(
        [str(executable), *arguments],
        cwd=interpreter.parents[2] / "runtime",
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        diagnostic = _sanitize_diagnostic(
            completed.stderr,
            roots=(interpreter.parents[2],),
        )
        raise RuntimeError(
            f"fresh-wheel command failed ({completed.returncode}): "
            f"{' '.join(arguments)}; diagnostic={diagnostic}"
        )
    return json.loads(completed.stdout)


def _git_identity(repository: Path) -> tuple[str, str]:
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if status.stdout:
        raise RuntimeError("fresh-wheel verification requires a clean exact-HEAD worktree")
    values: list[str] = []
    for revision in ("HEAD", "HEAD^{tree}"):
        completed = subprocess.run(
            ["git", "rev-parse", revision],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        value = completed.stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{40}", value):
            raise RuntimeError("fresh-wheel Git identity is invalid")
        values.append(value)
    return values[0], values[1]


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
        observation["observation_id"] = _stable_id(
            "observation",
            compilation_run_id,
            packet["packet_id"],
            _canonical_json(observation),
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
        text_sha256 = _sha256_bytes(body.encode("utf-8"))
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
                        "input_set_sha256": _build_input_set_sha256(
                            source_refs=source_refs,
                            statement_type="factual",
                            support_status="supported",
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


def verify_fresh_wheel(
    dist: Path,
    *,
    repository: Path | None = None,
) -> dict[str, Any]:
    wheels = sorted(dist.glob("deeplaw-*.whl"))
    if len(wheels) != 1 or wheels[0].is_symlink() or not wheels[0].is_file():
        raise RuntimeError("fresh-wheel verification requires exactly one wheel")
    wheel = wheels[0].resolve()
    wheel_match = re.fullmatch(r"deeplaw-([0-9][^-]*)-.*\.whl", wheel.name)
    if wheel_match is None:
        raise RuntimeError("fresh-wheel filename is invalid")
    package_version = wheel_match.group(1)
    repository_root = (repository or Path.cwd()).expanduser().absolute()
    commit_sha, tree_sha = _git_identity(repository_root)
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
        executable, isolated_environment = _isolated_environment(interpreter)
        runtime = root / "runtime"
        version = subprocess.run(
            [str(executable), "--version"],
            cwd=runtime,
            env=isolated_environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        runtime_facts_process = subprocess.run(
            [
                str(interpreter),
                "-c",
                (
                    "import hashlib,importlib.resources,json,os,platform,deeplaw;"
                    "root=importlib.resources.files('deeplaw').joinpath('contracts');"
                    "names=['compilation-handoff.v1.schema.json',"
                    "'fresh-wheel-journey.v1.schema.json',"
                    "'provider-knowledge-capsule.v2.schema.json',"
                    "'source-knowledge-status.v1.schema.json'];"
                    "digests={name:hashlib.sha256(root.joinpath(name).read_bytes()).hexdigest()"
                    " for name in names};"
                    "inventory=sorted((entry.name,hashlib.sha256(entry.read_bytes()).hexdigest())"
                    " for entry in root.iterdir() if entry.name.endswith('.json'));"
                    "print(json.dumps({'import_file':deeplaw.__file__,"
                    "'version':deeplaw.__version__,'contract_digests':digests,"
                    "'contract_inventory_count':len(inventory),"
                    "'contract_inventory_sha256':hashlib.sha256(json.dumps(inventory,"
                    "sort_keys=True,separators=(',',':')).encode()).hexdigest(),"
                    "'python_version':platform.python_version(),"
                    "'platform':platform.platform(),"
                    "'cwd':os.getcwd(),'pythonpath':os.environ.get('PYTHONPATH')}))"
                ),
            ],
            cwd=runtime,
            env=isolated_environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        runtime_facts = json.loads(runtime_facts_process.stdout)
        import_file = Path(runtime_facts.pop("import_file")).resolve()
        environment_root = environment.resolve()
        if environment_root not in import_file.parents:
            raise RuntimeError("fresh-wheel import did not resolve inside the isolated environment")
        if Path(runtime_facts.pop("cwd")).resolve() != runtime.resolve():
            raise RuntimeError("fresh-wheel runtime cwd escaped the isolated directory")
        runtime_facts["import_path_class"] = "isolated_site_packages"
        runtime_facts["import_file_relative_to_environment"] = import_file.relative_to(
            environment_root
        ).as_posix()
        runtime_facts["cwd_class"] = "external_isolated_runtime"
        runtime_facts["executable_used"] = True
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
        doctor = _run(interpreter, "knowledge", "doctor", "--vault", str(vault))
        audit_before = _run(
            interpreter,
            "knowledge",
            "autonomy",
            "status",
            "--vault",
            str(vault),
        )
        added = _run(
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
        source_id = added["source"]["source_id"]
        source_revision_id = added["identity"]["source_revision_id"]
        honest_gap = _run(
            interpreter,
            "knowledge",
            "context",
            "--vault",
            str(vault),
            "--task",
            "Which canonical local store must the fresh wheel use?",
            "--confirm-no-case-data",
        )
        handoff = _run(
            interpreter,
            "knowledge",
            "compile",
            "handoff",
            "--vault",
            str(vault),
            "--source-revision-id",
            source_revision_id,
        )
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
            source_revision_id,
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
        provider_capsule = compiled_capsule["provider_capsule"]
        provider_content = provider_capsule["capsule"]
        provider_bytes = len(_canonical_json(provider_content).encode("utf-8"))
        provider_sha256 = _sha256_bytes(
            _canonical_json(provider_content).encode("utf-8")
        )
        if provider_capsule["delivery"]["provider_content_bytes"] != provider_bytes:
            raise RuntimeError("fresh-wheel Provider Capsule byte count is not exact")
        if not compiled_capsule["statements"]:
            raise RuntimeError("fresh-wheel compiled Context has no Statement")
        selected_statement = compiled_capsule["statements"][0]
        source_reference = selected_statement["source_refs"][0]
        wiki_page = _run(
            interpreter,
            "knowledge",
            "wiki",
            "page",
            "--vault",
            str(vault),
            "--wiki-path",
            f"wiki/claims/{selected_statement['knowledge_id']}.md",
        )
        source_fragment = _run(
            interpreter,
            "knowledge",
            "source",
            "fragment",
            "--vault",
            str(vault),
            "--fragment-id",
            source_reference["fragment_id"],
        )
        snapshot = root / "snapshot"
        snapshot_created = _run(
            interpreter,
            "knowledge",
            "snapshot",
            "create",
            "--vault",
            str(vault),
            "--output",
            str(snapshot),
        )
        snapshot_verified = _run(
            interpreter,
            "knowledge",
            "snapshot",
            "verify",
            "--snapshot",
            str(snapshot),
        )
        forget_grant = _run(
            interpreter,
            "knowledge",
            "sink",
            "enable",
            "--vault",
            str(vault),
            "--writer-id",
            "fresh-wheel-forget-owner",
            "--scope",
            "project",
            "--operation",
            "forget",
        )
        forgotten = _run(
            interpreter,
            "knowledge",
            "forget",
            "--vault",
            str(vault),
            "--knowledge-id",
            selected_statement["knowledge_id"],
            "--expected-revision-id",
            selected_statement["knowledge_revision_id"],
            "--grant-id",
            forget_grant["grant_id"],
            "--idempotency-key",
            "fresh-wheel-forget-knowledge",
            "--reason",
            "Verify installed-wheel governed Knowledge forgetting.",
            "--confirm",
            "--confirm-no-case-data",
        )
        after_forget = _run(
            interpreter,
            "knowledge",
            "context",
            "--vault",
            str(vault),
            "--task",
            "Which canonical local store must the fresh wheel use?",
            "--confirm-no-case-data",
        )
        audit_after = _run(
            interpreter,
            "knowledge",
            "autonomy",
            "status",
            "--vault",
            str(vault),
        )
        final_verification = _run(
            interpreter,
            "knowledge",
            "autonomy",
            "verify",
            "--vault",
            str(vault),
        )
        compiled_query_hit = _compiled_query_hit(compiled_query)
        honest_gap_codes = sorted(
            {
                gap["code"]
                for gap in honest_gap["gaps"]
                if isinstance(gap, dict)
            }
        )
        forgotten_excluded = all(
            value not in _canonical_json(after_forget)
            for value in (
                selected_statement["knowledge_id"],
                selected_statement["knowledge_revision_id"],
                selected_statement["statement_text"],
            )
        )
        valid = bool(
            doctor["ready"]
            and runtime_facts["version"] == package_version
            and runtime_facts["pythonpath"] == ""
            and version == f"deeplaw {package_version}"
            and added["source_knowledge_status"]["state"] == "compilation_required"
            and "uncompiled_source" in honest_gap_codes
            and handoff["source_status"] == "compilation_required"
            and verification["valid"]
            and compilation_validation["valid"]
            and compilation_receipt["observation_count"] > 0
            and compilation_completed["status"] == "succeeded"
            and compiled_query_hit
            and knowledge_verification["valid"]
            and final_verification["valid"]
            and https_preflight["network_performed"] is False
            and selected_statement["knowledge_id"] in wiki_page["content"]
            and source_fragment["fragment"]["source_revision_id"] == source_revision_id
            and snapshot_created["valid"]
            and snapshot_verified["valid"]
            and forgotten["lifecycle"] == "forgotten"
            and forgotten_excluded
            and audit_before["audit_head"] != audit_after["audit_head"]
        )
    result = {
        "schema_version": "deeplaw.fresh-wheel-journey/v1",
        "commit_sha": commit_sha,
        "tree_sha": tree_sha,
        "worktree_clean": True,
        "package_version": package_version,
        "wheel": {
            "name": wheel.name,
            "sha256": _sha256_file(wheel),
            "size_bytes": wheel.stat().st_size,
        },
        "runtime": runtime_facts,
        "journey": {
            "vault_id": initialized["vault_id"],
            "source_id": source_id,
            "source_revision_id": source_revision_id,
            "doctor_ready": doctor["ready"],
            "source_status_before_compile": {
                key: added["source_knowledge_status"][key]
                for key in (
                    "state",
                    "source_registered",
                    "compilation_required",
                    "compiled",
                    "canonical_knowledge_committed",
                    "canonical_knowledge_admissible",
                    "wiki_projection_status",
                    "wiki_projection_pending",
                    "wiki_projection_ready",
                )
            },
            "honest_gap_codes": honest_gap_codes,
            "handoff_source_status": handoff["source_status"],
            "review_receipt_valid": approval["review_receipt"][
                "review_receipt_id"
            ].startswith("review_"),
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
            "wiki_drill_down_valid": (
                selected_statement["knowledge_id"] in wiki_page["content"]
            ),
            "source_drill_down_valid": (
                source_fragment["fragment"]["source_revision_id"] == source_revision_id
            ),
            "snapshot_valid": snapshot_created["valid"] and snapshot_verified["valid"],
            "knowledge_forget_lifecycle": forgotten["lifecycle"],
            "forgotten_knowledge_excluded": forgotten_excluded,
            "final_verification_valid": final_verification["valid"],
            "https_preflight_valid": (
                https_preflight["network_performed"] is False
                and https_preflight["canonical_requested_url"]
                == "https://example.com/source.md"
            ),
        },
        "provider": {
            "capsule_id": compiled_capsule["capsule_id"],
            "capsule_valid": verification["valid"],
            "provider_bytes": provider_bytes,
            "provider_content_sha256": provider_sha256,
            "delivery_bytes_match": True,
            "receipt_excluded_from_provider_content": True,
        },
        "audit": {
            "before": audit_before["audit_head"],
            "after": audit_after["audit_head"],
            "changed_for_explicit_writes": audit_before["audit_head"]
            != audit_after["audit_head"],
        },
        "write_boundaries": {
            "read_leaf": "knowledge_support",
            "write_leaf": "knowledge_sink",
            "grant_required": True,
            "hidden_read_write": False,
            "explicit_owner_writes": [
                "init",
                "source_add",
                "source_review",
                "sink_grant_enable",
                "semantic_compile",
                "snapshot_create",
                "knowledge_forget",
            ],
        },
        "valid": valid,
    }
    encoded = _canonical_json(result)
    if _POSIX_ABSOLUTE_PATH.search(encoded) or _WINDOWS_ABSOLUTE_PATH.search(encoded):
        raise RuntimeError("fresh-wheel receipt contains an absolute path")
    with zipfile.ZipFile(wheel) as archive:
        schema = json.loads(
            archive.read("deeplaw/contracts/fresh-wheel-journey.v1.schema.json")
        )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    args = parser.parse_args()
    result = verify_fresh_wheel(
        args.dist.expanduser().absolute(),
        repository=args.repository.expanduser().absolute(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if not result["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
