from __future__ import annotations

import argparse
from pathlib import Path

if __package__:
    from .benchlib import read_jsonl, score_retrieval, sha256_file, write_json
else:
    from benchlib import read_jsonl, score_retrieval, sha256_file, write_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Score a frozen external retrieval run with exact IDs."
    )
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--suite-id", required=True)
    parser.add_argument("--system-id", required=True)
    parser.add_argument("--k", type=int, default=5)
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
    report = score_retrieval(
        read_jsonl(args.cases),
        read_jsonl(args.run),
        k=args.k,
        suite_id=args.suite_id,
        system_id=args.system_id,
        cases_sha256=sha256_file(args.cases),
        run_sha256=sha256_file(args.run),
        claim_eligible=args.claim_eligible,
        claim_ineligibility_reason=args.claim_ineligibility_reason,
    )
    write_json(args.output, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
