from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from benchmarks.release.evidence import environment_manifest, repository_binding, write_report

_ADVISORY = re.compile(r"^- ([A-Z][A-Z0-9-]+):", re.MULTILINE)
_PROFILES = {
    "default": ("dev", "discovery", "document-engine"),
    "build": ("discovery", "document-engine"),
    "discovery": ("dev", "document-engine"),
    "document-engine": ("dev", "discovery"),
}


def _load_vex(path: Path) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"OpenVEX document is unavailable or invalid: {error}") from error
    statements = payload.get("statements") if isinstance(payload, dict) else None
    if not isinstance(statements, list):
        raise RuntimeError("OpenVEX statements are unavailable")
    accepted: dict[str, dict[str, Any]] = {}
    for statement in statements:
        if not isinstance(statement, dict) or statement.get("status") != "not_affected":
            continue
        vulnerability = statement.get("vulnerability")
        name = vulnerability.get("name") if isinstance(vulnerability, dict) else None
        impact = statement.get("impact_statement")
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(impact, str)
            or len(impact.strip()) < 80
            or statement.get("justification") != "vulnerable_code_not_in_execute_path"
        ):
            raise RuntimeError("OpenVEX not_affected statement lacks bounded justification")
        accepted[name] = statement
    return accepted


def _audit_command(profile: str, *, ignored: tuple[str, ...] = ()) -> list[str]:
    command = ["uv", "--preview-features", "audit", "audit", "--frozen"]
    for extra in _PROFILES[profile]:
        command.extend(("--no-extra", extra))
    for advisory in ignored:
        command.extend(("--ignore", advisory))
    return command


def audit(profile: str, *, repository: Path, output_path: Path | None = None) -> int:
    vex = _load_vex(repository / "security" / "openvex.json")
    first = subprocess.run(
        _audit_command(profile),
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    output = first.stdout + first.stderr
    advisories = tuple(sorted(set(_ADVISORY.findall(output))))
    if first.returncode == 0:
        sys.stdout.write(output)
        if output_path is not None:
            write_report(
                output_path,
                {
                    "schema_version": "deeplaw.dependency-audit/v1",
                    "binding": repository_binding(repository),
                    "environment": environment_manifest(),
                    "profile": profile,
                    "status": "passed",
                    "advisories": [],
                    "openvex_covered": [],
                },
            )
        return 0
    uncovered = tuple(item for item in advisories if item not in vex)
    if not advisories or uncovered or profile != "document-engine":
        sys.stderr.write(output)
        if uncovered:
            sys.stderr.write(
                "Uncovered dependency advisories: " + ", ".join(uncovered) + "\n"
            )
        if output_path is not None:
            write_report(
                output_path,
                {
                    "schema_version": "deeplaw.dependency-audit/v1",
                    "binding": repository_binding(repository),
                    "environment": environment_manifest(),
                    "profile": profile,
                    "status": "failed",
                    "advisories": list(advisories),
                    "openvex_covered": [],
                    "uncovered": list(uncovered),
                },
            )
        return first.returncode or 1
    second = subprocess.run(
        _audit_command(profile, ignored=advisories),
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    sys.stdout.write(second.stdout)
    sys.stderr.write(second.stderr)
    if second.returncode == 0:
        sys.stdout.write(
            "OpenVEX-covered advisories (not affected by the closed document-engine path): "
            + ", ".join(advisories)
            + "\n"
        )
    if output_path is not None:
        write_report(
            output_path,
            {
                "schema_version": "deeplaw.dependency-audit/v1",
                "binding": repository_binding(repository),
                "environment": environment_manifest(),
                "profile": profile,
                "status": "passed" if second.returncode == 0 else "failed",
                "advisories": list(advisories),
                "openvex_covered": list(advisories) if second.returncode == 0 else [],
            },
        )
    return second.returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit one locked dependency profile and enforce OpenVEX coverage."
    )
    parser.add_argument("--profile", choices=sorted(_PROFILES), required=True)
    parser.add_argument(
        "--repository",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    return audit(
        args.profile,
        repository=args.repository.resolve(),
        output_path=args.output.resolve() if args.output is not None else None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
