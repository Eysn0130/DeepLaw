from __future__ import annotations

import argparse
from pathlib import Path

if __package__:
    from .benchlib import read_jsonl, score_metrics, sha256_file, write_json
else:
    from benchlib import read_jsonl, score_metrics, sha256_file, write_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize exact per-case upstream metrics before paired comparison. "
            "The caller must convert upstream output without changing case values."
        )
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--suite-id", required=True)
    parser.add_argument("--system-id", required=True)
    parser.add_argument(
        "--cases-sha256",
        required=True,
        help="SHA-256 of the frozen upstream question/case manifest.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--claim-eligible", action="store_true")
    parser.add_argument("--claim-ineligibility-reason")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.claim_eligible and args.claim_ineligibility_reason:
        raise ValueError("claim-eligible runs cannot have an ineligibility reason")
    if not args.claim_eligible and not args.claim_ineligibility_reason:
        raise ValueError("development runs must state why they are claim-ineligible")
    report = score_metrics(
        read_jsonl(args.input),
        suite_id=args.suite_id,
        system_id=args.system_id,
        input_sha256=sha256_file(args.input),
        cases_sha256=args.cases_sha256,
        claim_eligible=args.claim_eligible,
        claim_ineligibility_reason=args.claim_ineligibility_reason,
    )
    write_json(args.output, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
