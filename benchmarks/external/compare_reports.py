from __future__ import annotations

import argparse
from pathlib import Path

if __package__:
    from .benchlib import paired_comparison, read_json, write_json
else:
    from benchlib import paired_comparison, read_json, write_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compute a deterministic paired-bootstrap comparison."
    )
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--metric", required=True)
    parser.add_argument("--direction", choices=("higher", "lower"), required=True)
    parser.add_argument("--samples", type=int, default=10_000)
    parser.add_argument("--confidence", type=float, default=0.95)
    parser.add_argument("--seed", type=int, default=20260726)
    parser.add_argument("--noninferiority-margin", type=float, default=0.0)
    parser.add_argument("--minimum-effect", type=float, default=0.0)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    comparison = paired_comparison(
        read_json(args.candidate),
        read_json(args.baseline),
        metric=args.metric,
        direction=args.direction,
        samples=args.samples,
        confidence=args.confidence,
        seed=args.seed,
        noninferiority_margin=args.noninferiority_margin,
        minimum_effect=args.minimum_effect,
    )
    write_json(args.output, comparison)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
