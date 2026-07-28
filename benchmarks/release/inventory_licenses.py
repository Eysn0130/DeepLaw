from __future__ import annotations

import argparse
import json
import re
import sys
from importlib.metadata import distributions
from pathlib import Path
from typing import Any

from benchmarks.release.evidence import (
    canonical_json,
    environment_manifest,
    repository_binding,
    sha256_bytes,
)

_NORMALIZE_NAME = re.compile(r"[-_.]+")


def _normalized_name(value: str) -> str:
    return _NORMALIZE_NAME.sub("-", value).lower()


def _load_policy(path: Path) -> dict[str, Any]:
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"release license policy is unavailable or invalid: {error}") from error
    if not isinstance(policy, dict) or policy.get("schema_version") != (
        "deeplaw.release-license-policy/v1"
    ):
        raise RuntimeError("unsupported release license policy")
    for field in ("approved_markers", "forbidden_markers"):
        if not isinstance(policy.get(field), list) or not all(
            isinstance(item, str) and item for item in policy[field]
        ):
            raise RuntimeError(f"license policy {field} is invalid")
    if not isinstance(policy.get("reviewed_exceptions"), dict):
        raise RuntimeError("license policy reviewed_exceptions is invalid")
    for name, exception in policy["reviewed_exceptions"].items():
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(exception, dict)
            or set(exception) != {"version", "license_evidence", "notice_marker"}
            or not isinstance(exception.get("version"), str)
            or not exception["version"]
            or not (
                exception.get("license_evidence") is None
                or (
                    isinstance(exception.get("license_evidence"), str)
                    and exception["license_evidence"]
                )
            )
            or not isinstance(exception.get("notice_marker"), str)
            or not exception["notice_marker"]
        ):
            raise RuntimeError(f"license policy exception for {name!r} is invalid")
    return policy


def _reviewed_exception_matches(
    exception: dict[str, Any], *, version: str, evidence: str, notices: str
) -> bool:
    expected_evidence = exception["license_evidence"]
    evidence_matches = (
        not evidence if expected_evidence is None else expected_evidence in evidence
    )
    return (
        exception["version"] == version
        and evidence_matches
        and exception["notice_marker"] in notices
    )


def inventory(*, policy_path: Path, notices_path: Path) -> dict[str, Any]:
    policy = _load_policy(policy_path)
    notices = notices_path.read_text(encoding="utf-8")
    packages: list[dict[str, Any]] = []
    blocked: list[str] = []
    review_required: list[str] = []
    for distribution in sorted(
        distributions(), key=lambda item: _normalized_name(item.metadata.get("Name") or "")
    ):
        name = distribution.metadata.get("Name")
        if not isinstance(name, str) or not name:
            continue
        normalized = _normalized_name(name)
        expression = distribution.metadata.get("License-Expression")
        declared = distribution.metadata.get("License")
        classifiers = sorted(
            item.removeprefix("License :: ")
            for item in distribution.metadata.get_all("Classifier") or []
            if item.startswith("License :: ")
        )
        evidence = " | ".join(
            item for item in (expression, declared, *classifiers) if isinstance(item, str)
        )
        exception = policy["reviewed_exceptions"].get(normalized)
        status = "approved"
        reason = "approved_license_marker"
        forbidden = [
            item
            for item in policy["forbidden_markers"]
            if item.lower() in evidence.lower()
        ]
        if forbidden:
            status = "blocked"
            reason = "forbidden_license_marker:" + ",".join(forbidden)
        elif isinstance(exception, dict):
            if _reviewed_exception_matches(
                exception,
                version=distribution.version,
                evidence=evidence,
                notices=notices,
            ):
                status = "reviewed_exception"
                reason = "exact_version_notice_review"
            else:
                status = "review_required"
                reason = "reviewed_exception_no_longer_matches"
        elif not any(
            item.lower() in evidence.lower() for item in policy["approved_markers"]
        ):
            status = "review_required"
            reason = "no_approved_license_marker"
        record = {
            "name": name,
            "normalized_name": normalized,
            "version": distribution.version,
            "license_expression": expression,
            "declared_license": declared,
            "license_classifiers": classifiers,
            "status": status,
            "reason": reason,
        }
        packages.append(record)
        if status == "blocked":
            blocked.append(f"{normalized}=={distribution.version}")
        elif status == "review_required":
            review_required.append(f"{normalized}=={distribution.version}")
    return {
        "schema_version": "deeplaw.installed-license-inventory/v1",
        "policy_schema_version": policy["schema_version"],
        "package_count": len(packages),
        "status": "blocked" if blocked else "review_required" if review_required else "passed",
        "blocked": blocked,
        "review_required": review_required,
        "packages": packages,
    }


def bound_inventory(
    *, repository: Path, policy_path: Path, notices_path: Path
) -> dict[str, Any]:
    report = {
        **inventory(policy_path=policy_path, notices_path=notices_path),
        "binding": repository_binding(repository),
        "environment": environment_manifest(),
    }
    report["record_sha256"] = sha256_bytes(canonical_json(report).encode("utf-8"))
    return report


def main() -> int:
    repository = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="Inventory installed distribution licenses against the release policy."
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=repository / "benchmarks" / "release" / "license-policy-v1.json",
    )
    parser.add_argument(
        "--notices", type=Path, default=repository / "THIRD_PARTY_NOTICES.md"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = bound_inventory(
            repository=repository,
            policy_path=args.policy.resolve(),
            notices_path=args.notices.resolve(),
        )
    except (OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
