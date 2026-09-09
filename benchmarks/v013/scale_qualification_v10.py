"""Machine-only v0.13 Kernel Release Core scale qualification v10.

The execution and report logic is shared with the v9 runner through an
immutable profile.  This entry point selects the current Query Plan v7,
Provider v3, inner projection v2, and query/context observation v2 profile;
it does not duplicate the 10,000-object workload.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .scale_qualification_v9 import (
    V10_PROFILE,
    _artifact_binding,
    _canonical_bytes,
)
from .scale_qualification_v9 import (
    _measure_query_context as _measure_query_context_shared,
)
from .scale_qualification_v9 import (
    _public_semantic_compile as _public_semantic_compile_shared,
)
from .scale_qualification_v9 import (
    _validate_query_context_observation as _validate_query_context_observation_shared,
)
from .scale_qualification_v9 import (
    build_scale_qualification_report as _build_scale_qualification_report_shared,
)
from .scale_qualification_v9 import (
    run_scale_qualification as _run_scale_qualification_shared,
)
from .scale_qualification_v9 import (
    verify_report as _verify_report_shared,
)

SCHEMA_VERSION = V10_PROFILE.report_schema_version
RUNNER_RELATIVE_PATH = V10_PROFILE.runner_relative_path
SCHEMA_RELATIVE_PATH = V10_PROFILE.schema_relative_path
QUERY_CONTEXT_OBSERVATION_SCHEMA = V10_PROFILE.query_context_observation_schema
QUERY_PLAN_SCHEMA_V7 = V10_PROFILE.query_plan_schema_version
PROVIDER_CAPSULE_SCHEMA_V3 = V10_PROFILE.provider_schema_version
PROVIDER_INNER_SCHEMA_V2 = V10_PROFILE.provider_inner_schema_version
SCALE_SEMANTIC_KEY = V10_PROFILE.semantic_key


def _validate_query_context_observation(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the v10 query/context observation contract."""

    return _validate_query_context_observation_shared(value, profile=V10_PROFILE)


def verify_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Fail-closed verification for the v10 report contract."""

    return _verify_report_shared(report, profile=V10_PROFILE)


def verify_scale_qualification_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Compatibility alias for callers that name the report explicitly."""

    return verify_report(report)


def build_scale_qualification_report(**kwargs: Any) -> dict[str, Any]:
    """Build a report using only the immutable v10 profile."""

    return _build_scale_qualification_report_shared(**kwargs, profile=V10_PROFILE)


def _measure_query_context(
    knowledge_os: Any,
    *,
    query_text: str,
    expected_semantic_key: str = SCALE_SEMANTIC_KEY,
) -> dict[str, Any]:
    """Measure the current v7 query/context surfaces through the public API."""

    return _measure_query_context_shared(
        knowledge_os,
        query_text=query_text,
        expected_semantic_key=expected_semantic_key,
        profile=V10_PROFILE,
    )


def _public_semantic_compile(
    vault: Path,
    source_result: Mapping[str, Any],
    *,
    target: int,
    global_offset: int = 0,
    batch_index: int = 0,
    knowledge_os_handle: Any | None = None,
) -> dict[str, Any]:
    """Publish one bounded Source IR batch under the v10 profile identity."""

    return _public_semantic_compile_shared(
        vault,
        source_result,
        target=target,
        global_offset=global_offset,
        batch_index=batch_index,
        knowledge_os_handle=knowledge_os_handle,
        profile=V10_PROFILE,
    )


def run_scale_qualification(
    *,
    candidate_binding: Mapping[str, Any],
    wheel_path: Path,
    sdist_path: Path,
    lock_sha256: str,
    workflow_run_id: int,
    workspace: Path | None = None,
    execute_10k: bool = False,
    command: str | None = None,
) -> dict[str, Any]:
    """Execute the exact 10k lane with the current v10 profile."""

    return _run_scale_qualification_shared(
        candidate_binding=candidate_binding,
        wheel_path=wheel_path,
        sdist_path=sdist_path,
        lock_sha256=lock_sha256,
        workflow_run_id=workflow_run_id,
        workspace=workspace,
        execute_10k=execute_10k,
        command=command,
        profile=V10_PROFILE,
        runner_path=Path(__file__).resolve(),
    )


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Run the exact v0.13 10k scale qualification v10.")
    parser.add_argument("--execute-10k", action="store_true")
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--sdist", type=Path)
    parser.add_argument("--lock-sha256", required=False)
    parser.add_argument("--candidate-commit", required=False)
    parser.add_argument("--candidate-tree", required=False)
    parser.add_argument("--candidate-version", required=False)
    parser.add_argument("--candidate-wheel-sha256", required=False)
    parser.add_argument("--candidate-sdist-sha256", required=False)
    parser.add_argument("--workflow-run-id", type=int, required=False)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not all(
        (
            args.execute_10k,
            args.wheel is not None,
            args.sdist is not None,
            bool(args.lock_sha256),
            bool(args.candidate_commit),
            bool(args.candidate_tree),
            bool(args.candidate_version),
            bool(args.candidate_wheel_sha256),
            bool(args.candidate_sdist_sha256),
            args.workflow_run_id is not None and args.workflow_run_id > 0,
        )
    ):
        raise SystemExit(
            "exact 10k execution and all exact candidate bindings are required"
        )
    wheel_binding = _artifact_binding(args.wheel)
    wheel_binding["sha256"] = args.candidate_wheel_sha256
    sdist_binding = _artifact_binding(args.sdist)
    sdist_binding["sha256"] = args.candidate_sdist_sha256
    report = run_scale_qualification(
        candidate_binding={
            "commit": args.candidate_commit,
            "tree": args.candidate_tree,
            "version": args.candidate_version,
            "lock_sha256": args.lock_sha256,
            "wheel": wheel_binding,
            "sdist": sdist_binding,
        },
        wheel_path=args.wheel,
        sdist_path=args.sdist,
        lock_sha256=args.lock_sha256,
        workflow_run_id=args.workflow_run_id,
        execute_10k=True,
        command=(
            "python -m benchmarks.v013.scale_qualification_v10 --execute-10k "
            "--exact-candidate-bindings"
        ),
    )
    result = verify_report(report)
    if not result["valid"]:
        raise SystemExit("scale report failed validation: " + "; ".join(result["errors"]))
    args.output.write_bytes(_canonical_bytes(report) + b"\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
