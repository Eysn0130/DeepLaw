# Typed Compiler benchmark scorer

This directory provides a closed, source-reference-aware scoring contract for typed compiler
outputs. It reports precision, recall, F1, hallucinated/unsupported claim rates, exact source-span
correctness, duplicate rate, review acceptance, and cross-document synthesis correctness.

The evaluator supplies explicit claim-equivalence, support, review, and source-reference labels.
The scorer does not infer semantic equivalence from string similarity and does not treat a model
confidence as a gold label.

```bash
uv run python -m benchmarks.typed_compiler.score \
  --suite benchmarks/typed_compiler/dev-fixture-v1.json \
  --output /tmp/typed-compiler-report.json
```

`dev-fixture-v1.json` exists only to exercise metric semantics. Its checked report is permanently
`claim_eligible=false`; predictions and labels are embedded and are neither a deterministic-v2
quality result nor independent evidence. A release-quality suite must freeze real sources, source
spans, gold claims, raw compiler outputs, reviewer decisions, evaluator identity, and failures
before scoring.
