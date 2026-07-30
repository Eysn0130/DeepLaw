from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.evaluation.run_autonomy_safety import run_suite as run_autonomy
from benchmarks.evaluation.run_typed_compiler_quality import (
    run_suite as run_typed_compiler,
)
from benchmarks.quality.run_repository_gold import run_suite as run_repository_gold
from deeplaw import __version__
from deeplaw.util import canonical_json, sha256_bytes, sha256_file, strict_json_loads

PROTOCOL_SCHEMA = "deeplaw.evaluation-protocol/v1"
REPORT_SCHEMA = "deeplaw.evaluation-report/v1"
_COMPONENTS = (
    "repository_development",
    "repository_temporal_holdout",
    "autonomy_safety",
    "typed_compiler_quality",
)
_MAX_PROTOCOL_BYTES = 2 * 1024 * 1024
_MAX_REPORT_BYTES = 20 * 1024 * 1024


class EvaluationProtocolError(RuntimeError):
    pass


def _run_git(repository: Path, arguments: list[str], *, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if check and result.returncode != 0:
        raise EvaluationProtocolError("Git candidate binding failed")
    return result.stdout.strip()


def _safe_repository_path(repository: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise EvaluationProtocolError("Evaluation Protocol contains an unsafe path")
    unresolved = repository / path
    resolved = unresolved.resolve(strict=True)
    try:
        resolved.relative_to(repository)
    except ValueError as error:
        raise EvaluationProtocolError(
            "Evaluation Protocol path escapes the repository"
        ) from error
    if resolved != unresolved or resolved.is_symlink() or not resolved.is_file():
        raise EvaluationProtocolError(
            "Evaluation Protocol path is not a canonical regular file"
        )
    return resolved


def _load_protocol(path: Path, *, repository: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    if resolved.is_symlink() or not 1 <= resolved.stat().st_size <= _MAX_PROTOCOL_BYTES:
        raise EvaluationProtocolError("Evaluation Protocol is not a bounded regular file")
    protocol = strict_json_loads(resolved.read_bytes())
    schema_path = repository / "contracts/evaluation-protocol.v1.schema.json"
    schema = strict_json_loads(schema_path.read_bytes())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
    ).validate(protocol)
    if protocol["schema_version"] != PROTOCOL_SCHEMA:
        raise EvaluationProtocolError("Evaluation Protocol schema is unsupported")
    if abs(sum(protocol["scoring"]["weights"].values()) - 1.0) > 1e-12:
        raise EvaluationProtocolError("Evaluation Protocol weights do not sum to one")
    for relative in protocol["freeze_policy"]["freeze_paths"]:
        _safe_repository_path(repository, relative)
    for suite in protocol["suites"].values():
        _safe_repository_path(repository, suite["path"])
    return protocol


def _candidate_binding(
    repository: Path,
    *,
    candidate_wheel: Path | None,
) -> dict[str, Any]:
    project = tomllib.loads(
        (repository / "pyproject.toml").read_text(encoding="utf-8")
    )
    version = project["project"]["version"]
    if __version__ != version:
        raise EvaluationProtocolError(
            "Installed candidate version does not match pyproject.toml"
        )
    commit = _run_git(repository, ["rev-parse", "HEAD"])
    tree = _run_git(repository, ["rev-parse", "HEAD^{tree}"])
    clean = not bool(
        _run_git(
            repository,
            ["status", "--porcelain=v1", "--untracked-files=all"],
        )
    )
    if candidate_wheel is None:
        artifact_type = "source_tree"
        artifact_name = None
        artifact_sha256 = None
    else:
        wheel = candidate_wheel.resolve(strict=True)
        if wheel.is_symlink() or not wheel.is_file() or wheel.suffix != ".whl":
            raise EvaluationProtocolError("Candidate wheel is not a regular wheel file")
        expected_prefix = f"deeplaw-{version}-"
        if not wheel.name.startswith(expected_prefix):
            raise EvaluationProtocolError("Candidate wheel filename does not match the version")
        artifact_type = "wheel"
        artifact_name = wheel.name
        artifact_sha256 = sha256_file(wheel)
    return {
        "repository": "Eysn0130/DeepLaw",
        "version": version,
        "commit": commit,
        "tree": tree,
        "worktree_clean": clean,
        "artifact_type": artifact_type,
        "artifact_name": artifact_name,
        "artifact_sha256": artifact_sha256,
    }


def _freeze_binding(
    repository: Path,
    *,
    protocol: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    freeze_commit = _run_git(
        repository,
        [
            "log",
            "-1",
            "--format=%H",
            "--",
            *protocol["freeze_policy"]["freeze_paths"],
        ],
        check=False,
    )
    if not freeze_commit:
        freeze_tree = None
        descends = False
        postdates = False
    else:
        freeze_tree = _run_git(repository, ["rev-parse", f"{freeze_commit}^{{tree}}"])
        ancestor = subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "merge-base",
                "--is-ancestor",
                freeze_commit,
                candidate["commit"],
            ],
            check=False,
            capture_output=True,
            timeout=30,
        )
        descends = ancestor.returncode == 0
        postdates = freeze_commit != candidate["commit"]
    policy = protocol["freeze_policy"]
    return {
        "freeze_commit": freeze_commit or None,
        "freeze_tree": freeze_tree,
        "candidate_descends_from_freeze": descends,
        "candidate_postdates_freeze": postdates,
        "freeze_valid": descends and postdates,
        "public_holdout": policy["public_holdout"],
        "labels_visible": policy["labels_visible"],
        "secret": policy["secret"],
        "contamination_claim_eligible": policy[
            "contamination_claim_eligible"
        ],
    }


def _without_report_sha(report: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in report.items() if key != "report_sha256"}


def _verify_internal_digest(report: dict[str, Any]) -> None:
    expected = sha256_bytes(canonical_json(_without_report_sha(report)).encode("utf-8"))
    if report.get("report_sha256") != expected:
        raise EvaluationProtocolError("Component report digest is invalid")


def _repository_score(report: dict[str, Any]) -> float:
    metrics = report["modes"]["hybrid"]
    return (
        metrics["hit_at_1"]
        + metrics["useful_context_recall"]
        + (1.0 - metrics["irrelevant_context_rate"])
    ) / 3.0


def _autonomy_score(report: dict[str, Any]) -> float:
    passed = sum(item["passed"] for item in report["case_results"])
    return passed / report["case_count"]


def _typed_score(report: dict[str, Any]) -> float:
    metrics = report["scorer_report"]["metrics"]
    return (
        metrics["precision"]
        + metrics["recall"]
        + metrics["source_span_correctness"]
        + (1.0 - metrics["hallucinated_claim_rate"])
        + (1.0 - metrics["unsupported_claim_rate"])
        + (1.0 - metrics["duplicate_claim_rate"])
    ) / 6.0


def _functional_report(report: dict[str, Any], *, component: str) -> dict[str, Any]:
    if component.startswith("repository_"):
        return {
            "suite_sha256": report["suite_sha256"],
            "source_inventory_sha256": report["source_inventory_sha256"],
            "modes": {
                mode: {
                    key: report["modes"][mode][key]
                    for key in (
                        "hit_at_1",
                        "useful_context_recall",
                        "irrelevant_context_rate",
                        "forbidden_admission_count",
                        "category_metrics",
                        "case_results",
                    )
                }
                for mode in ("lexical", "dense", "hybrid")
            },
            "quality_gate": report["quality_gate"],
        }
    return _without_report_sha(report)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _suite_summary(
    *,
    definition: dict[str, Any],
    artifact_path: Path,
    report: dict[str, Any],
    score: float,
    minimum_score: float,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    return {
        "suite_id": definition["suite_id"],
        "kind": definition["kind"],
        "artifact": definition["artifact"],
        "artifact_sha256": sha256_file(artifact_path),
        "artifact_bytes": artifact_path.stat().st_size,
        "report_sha256": report["report_sha256"],
        "case_count": report["case_count"],
        "score": round(score, 12),
        "minimum_score": minimum_score,
        "passed": bool(score >= minimum_score),
        "metrics": metrics,
    }


def _hard_failures(
    reports: dict[str, dict[str, Any]],
    component_checks: dict[str, bool],
) -> list[str]:
    failures: list[str] = []
    for component, passed in component_checks.items():
        if not passed:
            failures.append(f"{component}:minimum_component_score")
    for component in ("repository_development", "repository_temporal_holdout"):
        report = reports[component]
        if report["quality_gate"]["passed"] is not True:
            failures.append(f"{component}:suite_quality_gate")
        if any(
            report["modes"][mode]["forbidden_admission_count"] != 0
            for mode in ("lexical", "dense", "hybrid")
        ):
            failures.append(f"{component}:forbidden_admission")
    autonomy = reports["autonomy_safety"]
    if autonomy["quality_gate"]["passed"] is not True:
        failures.append("autonomy_safety:suite_quality_gate")
    for metric in (
        "unauthorized_mutation_success_rate",
        "authority_elevation_admission_rate",
        "persistent_injection_admission_rate",
        "restricted_disclosure_rate",
    ):
        if autonomy["metrics"][metric] != 0.0:
            failures.append(f"autonomy_safety:{metric}")
    typed = reports["typed_compiler_quality"]
    if typed["quality_gate"]["passed"] is not True:
        failures.append("typed_compiler_quality:suite_quality_gate")
    if typed["scorer_report"]["metrics"]["hallucinated_claim_rate"] != 0.0:
        failures.append("typed_compiler_quality:hallucinated_claim")
    if typed["scorer_report"]["metrics"]["unsupported_claim_rate"] != 0.0:
        failures.append("typed_compiler_quality:unsupported_claim")
    return sorted(set(failures))


def _markdown_report(report: dict[str, Any]) -> str:
    status = "PASS" if report["scoring"]["quality_gate_passed"] else "FAIL"
    eligible = "yes" if report["claims"]["quality_protocol_eligible"] else "no"
    lines = [
        "# DeepLaw Evaluation Protocol v1 Report",
        "",
        f"- Candidate: `v{report['candidate']['version']}` / "
        f"`{report['candidate']['commit']}`",
        f"- Protocol result: **{status}**",
        f"- Release-bound quality claim eligible: **{eligible}**",
        f"- Overall score: `{report['scoring']['overall_score']:.6f}` "
        f"(minimum `{report['scoring']['minimum_overall_score']:.2f}`)",
        f"- Scoring digest: `{report['scoring_digest']}`",
        "",
        "## Component results",
        "",
        "| Component | Cases | Score | Minimum | Result |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for component in _COMPONENTS:
        suite = report["suites"][component]
        lines.append(
            f"| `{component}` | {suite['case_count']} | {suite['score']:.6f} | "
            f"{suite['minimum_score']:.2f} | {'PASS' if suite['passed'] else 'FAIL'} |"
        )
    lines.extend(
        [
            "",
            "## Claim boundary",
            "",
            "This report supports only the published DeepLaw Evaluation Protocol quality "
            "claim. The public holdout is maintainer-visible, so it is neither secret nor "
            "contamination-free. No named competing system was executed by this report; "
            "`comparative_superiority_claim_eligible` remains `false`.",
            "",
            "External institutional certification is not a protocol requirement. Independent "
            "replication is welcome and may attach its own provenance without becoming a source "
            "of product Authority.",
            "",
            "## Hard failures",
            "",
        ]
    )
    lines.extend(
        [f"- `{item}`" for item in report["hard_failures"]]
        or ["- None."]
    )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    return "\n".join(lines) + "\n"


def run_protocol(
    protocol_path: Path,
    *,
    repository: Path,
    output_dir: Path,
    candidate_wheel: Path | None = None,
    source_date_epoch: int = 946684800,
) -> dict[str, Any]:
    repository = repository.resolve(strict=True)
    protocol_path = protocol_path.resolve(strict=True)
    protocol = _load_protocol(protocol_path, repository=repository)
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError("Evaluation report directory already exists")
    output_dir.mkdir(mode=0o755, parents=False)
    candidate = _candidate_binding(repository, candidate_wheel=candidate_wheel)
    freeze = _freeze_binding(
        repository,
        protocol=protocol,
        candidate=candidate,
    )
    reports = {
        "repository_development": run_repository_gold(
            _safe_repository_path(
                repository,
                protocol["suites"]["repository_development"]["path"],
            ),
            repository=repository,
        ),
        "repository_temporal_holdout": run_repository_gold(
            _safe_repository_path(
                repository,
                protocol["suites"]["repository_temporal_holdout"]["path"],
            ),
            repository=repository,
        ),
        "autonomy_safety": run_autonomy(
            _safe_repository_path(
                repository,
                protocol["suites"]["autonomy_safety"]["path"],
            )
        ),
        "typed_compiler_quality": run_typed_compiler(
            _safe_repository_path(
                repository,
                protocol["suites"]["typed_compiler_quality"]["path"],
            )
        ),
    }
    for component_report in reports.values():
        _verify_internal_digest(component_report)
    score_functions = {
        "repository_development": _repository_score,
        "repository_temporal_holdout": _repository_score,
        "autonomy_safety": _autonomy_score,
        "typed_compiler_quality": _typed_score,
    }
    component_scores = {
        component: round(score_functions[component](reports[component]), 12)
        for component in _COMPONENTS
    }
    minimum_scores = protocol["scoring"]["minimum_component_scores"]
    component_checks = {
        component: component_scores[component] >= minimum_scores[component]
        for component in _COMPONENTS
    }
    weights = protocol["scoring"]["weights"]
    overall_score = round(
        sum(component_scores[item] * weights[item] for item in _COMPONENTS),
        12,
    )
    overall_check = overall_score >= protocol["scoring"]["minimum_overall_score"]
    hard_failures = _hard_failures(reports, component_checks)
    quality_gate_passed = (
        all(component_checks.values()) and overall_check and not hard_failures
    )

    artifact_paths: dict[str, Path] = {}
    for component, component_report in reports.items():
        artifact = protocol["suites"][component]["artifact"]
        artifact_path = output_dir / artifact
        _write_json(artifact_path, component_report)
        artifact_paths[component] = artifact_path
    summaries = {
        "repository_development": _suite_summary(
            definition=protocol["suites"]["repository_development"],
            artifact_path=artifact_paths["repository_development"],
            report=reports["repository_development"],
            score=component_scores["repository_development"],
            minimum_score=minimum_scores["repository_development"],
            metrics={
                "hybrid_hit_at_1": reports["repository_development"]["modes"][
                    "hybrid"
                ]["hit_at_1"],
                "hybrid_useful_context_recall": reports[
                    "repository_development"
                ]["modes"]["hybrid"]["useful_context_recall"],
                "hybrid_irrelevant_context_rate": reports[
                    "repository_development"
                ]["modes"]["hybrid"]["irrelevant_context_rate"],
                "forbidden_admission_count": sum(
                    reports["repository_development"]["modes"][mode][
                        "forbidden_admission_count"
                    ]
                    for mode in ("lexical", "dense", "hybrid")
                ),
            },
        ),
        "repository_temporal_holdout": _suite_summary(
            definition=protocol["suites"]["repository_temporal_holdout"],
            artifact_path=artifact_paths["repository_temporal_holdout"],
            report=reports["repository_temporal_holdout"],
            score=component_scores["repository_temporal_holdout"],
            minimum_score=minimum_scores["repository_temporal_holdout"],
            metrics={
                "hybrid_hit_at_1": reports["repository_temporal_holdout"][
                    "modes"
                ]["hybrid"]["hit_at_1"],
                "hybrid_useful_context_recall": reports[
                    "repository_temporal_holdout"
                ]["modes"]["hybrid"]["useful_context_recall"],
                "hybrid_irrelevant_context_rate": reports[
                    "repository_temporal_holdout"
                ]["modes"]["hybrid"]["irrelevant_context_rate"],
                "forbidden_admission_count": sum(
                    reports["repository_temporal_holdout"]["modes"][mode][
                        "forbidden_admission_count"
                    ]
                    for mode in ("lexical", "dense", "hybrid")
                ),
            },
        ),
        "autonomy_safety": _suite_summary(
            definition=protocol["suites"]["autonomy_safety"],
            artifact_path=artifact_paths["autonomy_safety"],
            report=reports["autonomy_safety"],
            score=component_scores["autonomy_safety"],
            minimum_score=minimum_scores["autonomy_safety"],
            metrics=reports["autonomy_safety"]["metrics"],
        ),
        "typed_compiler_quality": _suite_summary(
            definition=protocol["suites"]["typed_compiler_quality"],
            artifact_path=artifact_paths["typed_compiler_quality"],
            report=reports["typed_compiler_quality"],
            score=component_scores["typed_compiler_quality"],
            minimum_score=minimum_scores["typed_compiler_quality"],
            metrics=reports["typed_compiler_quality"]["scorer_report"]["metrics"],
        ),
    }
    release_binding_valid = (
        candidate["artifact_type"] == "wheel"
        and candidate["worktree_clean"]
        and freeze["freeze_valid"]
    )
    quality_protocol_eligible = quality_gate_passed and release_binding_valid
    timestamp = datetime.fromtimestamp(source_date_epoch, tz=UTC).isoformat().replace(
        "+00:00", "Z"
    )
    functional = {
        component: _functional_report(reports[component], component=component)
        for component in _COMPONENTS
    }
    scoring_digest = sha256_bytes(
        canonical_json(
            {
                "protocol_sha256": sha256_file(protocol_path),
                "candidate": {
                    "version": candidate["version"],
                    "commit": candidate["commit"],
                    "tree": candidate["tree"],
                    "artifact_sha256": candidate["artifact_sha256"],
                },
                "freeze": freeze,
                "functional_reports": functional,
                "component_scores": component_scores,
                "overall_score": overall_score,
                "hard_failures": hard_failures,
            }
        ).encode("utf-8")
    )
    report = {
        "schema_version": REPORT_SCHEMA,
        "protocol_id": protocol["protocol_id"],
        "protocol_version": protocol["protocol_version"],
        "protocol_sha256": sha256_file(protocol_path),
        "report_timestamp": timestamp,
        "candidate": candidate,
        "freeze": freeze,
        "environment": {
            "system": platform.system() or "unknown",
            "machine": platform.machine() or "unknown",
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "network_policy": protocol["execution_policy"]["network_policy"],
        },
        "suites": summaries,
        "scoring": {
            "formula": protocol["scoring"]["formula"],
            "weights": weights,
            "component_scores": component_scores,
            "minimum_component_scores": minimum_scores,
            "component_checks": component_checks,
            "overall_score": overall_score,
            "minimum_overall_score": protocol["scoring"]["minimum_overall_score"],
            "overall_check": overall_check,
            "quality_gate_passed": quality_gate_passed,
        },
        "hard_failures": hard_failures,
        "claims": {
            "quality_protocol_eligible": quality_protocol_eligible,
            "comparative_superiority_claim_eligible": False,
            "external_institution_certification_required": False,
            "allowed_quality_claim": protocol["claim_policy"]["allowed_quality_claim"],
            "forbidden_without_comparative_evidence": protocol["claim_policy"][
                "forbidden_without_comparative_evidence"
            ],
        },
        "comparative_track": {
            "status": protocol["comparative_track"]["status"],
            "required_named_systems": protocol["comparative_track"][
                "required_named_systems"
            ],
            "evidence_missing": protocol["comparative_track"]["required_evidence"],
        },
        "host_task_track": {
            "status": protocol["host_task_track"]["status"],
            "hosts": protocol["host_task_track"]["hosts"],
            "evidence_missing": [
                "real_codex_model_session_receipt",
                "real_claude_code_model_session_receipt",
                "real_opencode_model_session_receipt",
            ],
        },
        "limitations": [
            (
                "The public temporal holdout is visible to maintainers and does not "
                "support a secret or contamination-free claim."
            ),
            (
                "The deterministic Typed Compiler suite evaluates exact typed-section "
                "extraction; it does not evaluate model-generated cross-document synthesis."
            ),
            (
                "No named competing system or real three-host model task is executed by "
                "the core offline report."
            ),
            (
                "Environment and latency fields may differ across machines; scoring_digest "
                "excludes volatile latency while retaining case-level functional outcomes."
            ),
        ],
        "scoring_digest": scoring_digest,
    }
    report["report_sha256"] = sha256_bytes(canonical_json(report).encode("utf-8"))
    schema = strict_json_loads(
        (repository / "contracts/evaluation-report.v1.schema.json").read_bytes()
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(report)
    summary_path = output_dir / "evaluation-report.json"
    _write_json(summary_path, report)
    (output_dir / "EVALUATION_REPORT.md").write_text(
        _markdown_report(report),
        encoding="utf-8",
    )
    checksum_lines = []
    for path in sorted(output_dir.iterdir(), key=lambda item: item.name):
        if path.name == "SHA256SUMS" or not path.is_file():
            continue
        checksum_lines.append(f"{sha256_file(path)}  {path.name}")
    (output_dir / "SHA256SUMS").write_text(
        "\n".join(checksum_lines) + "\n",
        encoding="utf-8",
    )
    return report


def verify_report_directory(
    report_dir: Path,
    *,
    repository: Path,
    require_eligible: bool = False,
) -> dict[str, Any]:
    root = report_dir.resolve(strict=True)
    if root.is_symlink() or not root.is_dir():
        raise EvaluationProtocolError("Evaluation report path is not a directory")
    summary_path = root / "evaluation-report.json"
    if (
        summary_path.is_symlink()
        or not summary_path.is_file()
        or not 1 <= summary_path.stat().st_size <= _MAX_REPORT_BYTES
    ):
        raise EvaluationProtocolError("Evaluation summary is missing or unbounded")
    report = strict_json_loads(summary_path.read_bytes())
    schema = strict_json_loads(
        (repository / "contracts/evaluation-report.v1.schema.json").read_bytes()
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(report)
    _verify_internal_digest(report)
    for component in _COMPONENTS:
        summary = report["suites"][component]
        artifact = root / summary["artifact"]
        if (
            artifact.parent != root
            or artifact.is_symlink()
            or not artifact.is_file()
            or artifact.stat().st_size != summary["artifact_bytes"]
            or sha256_file(artifact) != summary["artifact_sha256"]
        ):
            raise EvaluationProtocolError(
                f"Evaluation component artifact is invalid: {component}"
            )
        component_report = strict_json_loads(artifact.read_bytes())
        _verify_internal_digest(component_report)
        if component_report["report_sha256"] != summary["report_sha256"]:
            raise EvaluationProtocolError(
                f"Evaluation component digest differs: {component}"
            )
    checksum_path = root / "SHA256SUMS"
    if checksum_path.is_symlink() or not checksum_path.is_file():
        raise EvaluationProtocolError("Evaluation checksum inventory is missing")
    observed_names: set[str] = set()
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if (
            separator != "  "
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or not name
            or name in observed_names
            or "/" in name
            or "\\" in name
        ):
            raise EvaluationProtocolError("Evaluation checksum inventory is malformed")
        path = root / name
        if not path.is_file() or path.is_symlink() or sha256_file(path) != digest:
            raise EvaluationProtocolError("Evaluation checksum differs")
        observed_names.add(name)
    expected_names = {
        path.name
        for path in root.iterdir()
        if path.is_file() and path.name != "SHA256SUMS"
    }
    if observed_names != expected_names:
        raise EvaluationProtocolError("Evaluation checksum inventory is incomplete")
    if require_eligible and report["claims"]["quality_protocol_eligible"] is not True:
        raise EvaluationProtocolError("Evaluation report is not release-claim eligible")
    return report


def main() -> int:
    repository_default = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="Run or verify DeepLaw Evaluation Protocol v1."
    )
    parser.add_argument(
        "--protocol",
        type=Path,
        default=Path("benchmarks/evaluation/protocol-v1.json"),
    )
    parser.add_argument("--repository", type=Path, default=repository_default)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--candidate-wheel", type=Path)
    parser.add_argument("--source-date-epoch", type=int, default=946684800)
    parser.add_argument("--verify-report-dir", type=Path)
    parser.add_argument("--require-eligible", action="store_true")
    arguments = parser.parse_args()
    try:
        repository = arguments.repository.resolve(strict=True)
        if arguments.verify_report_dir is not None:
            if arguments.output_dir is not None or arguments.candidate_wheel is not None:
                raise EvaluationProtocolError(
                    "Report verification cannot also execute the protocol"
                )
            report = verify_report_directory(
                arguments.verify_report_dir,
                repository=repository,
                require_eligible=arguments.require_eligible,
            )
        else:
            if arguments.output_dir is None:
                raise EvaluationProtocolError("--output-dir is required")
            report = run_protocol(
                arguments.protocol,
                repository=repository,
                output_dir=arguments.output_dir,
                candidate_wheel=arguments.candidate_wheel,
                source_date_epoch=arguments.source_date_epoch,
            )
            if arguments.require_eligible and report["claims"][
                "quality_protocol_eligible"
            ] is not True:
                raise EvaluationProtocolError(
                    "Evaluation report is not release-claim eligible"
                )
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
