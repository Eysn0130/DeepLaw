from __future__ import annotations

import argparse
from pathlib import Path

if __package__:
    from .benchlib import read_json, sha256_file, write_json
    from .claim_gate import _RUN_FIELDS, _expected_run_manifest
else:
    from benchlib import read_json, sha256_file, write_json
    from claim_gate import _RUN_FIELDS, _expected_run_manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build the closed suite evidence manifest that an independent evaluator signs."
        )
    )
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument(
        "--candidate",
        type=Path,
        required=True,
        help="JSON object with system_id, version, git_commit, and artifact_sha256.",
    )
    parser.add_argument(
        "--run-draft",
        type=Path,
        required=True,
        help="Closed run object without evidence_manifest_artifact.",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    protocol = read_json(args.protocol)
    candidate = read_json(args.candidate)
    run = read_json(args.run_draft)
    expected_fields = _RUN_FIELDS - {"evidence_manifest_artifact"}
    if set(candidate) != {"system_id", "version", "git_commit", "artifact_sha256"}:
        raise ValueError("candidate JSON does not match the closed identity shape")
    if set(run) != expected_fields:
        missing = sorted(expected_fields - set(run))
        extra = sorted(set(run) - expected_fields)
        raise ValueError(f"run draft shape mismatch: missing={missing}, extra={extra}")
    manifest = _expected_run_manifest(
        run,
        protocol_id=protocol.get("protocol_id"),
        candidate=candidate,
    )
    write_json(args.output, manifest)
    print(sha256_file(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
