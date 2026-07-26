# External benchmark adapters

These adapters exercise the shipped DeepLaw runtime through upstream-owned
evaluation interfaces. They are evaluation glue, not product shortcuts.

## LongMemEval-V2

Pinned upstream: `xiaowu0162/LongMemEval-V2` at
`6f020ac2fc3275e46c706d3406e02c3ed79b7be2`.

1. Install DeepLaw 0.4.0 into the benchmark's Python 3.11 environment.
2. Copy `longmemeval_v2_deeplaw.py` into upstream `memory_modules/`.
3. Add `from .longmemeval_v2_deeplaw import DeepLawMemory` to the pinned
   upstream `memory_modules/memory.py`.
4. Copy `longmemeval-v2-memory-config.json`, replace `workspace_dir` with a
   fresh absolute path, and pass it to upstream `evaluation/harness.py` via
   `--memory-config-path`.
5. Run both `web` and `enterprise` for the complete `small` tier with the
   upstream fixed reader, judge and memory-context token budget.
6. Preserve `runtime_inputs`, `per_question.jsonl`, `aggregated_metrics.json`,
   the saved memory, software lock files, host profile and raw logs.

This first adapter is a declared **text operating point**. It compiles the
trajectory goal, outcome, URL, action, thought and accessibility-tree text.
It records whether a query image was ignored. A result from this operating
point must not be described as a multimodal DeepLaw result.

`frozen_fixture_approved=true` means the benchmark operator has authorized the
published test fixture as evidence for this isolated evaluation vault. It does
not turn benchmark content into trusted product knowledge and must never be
copied into an end-user configuration.

LongMemEval-V2 requires a single submitted code artifact. This adapter is
self-contained except for the installed DeepLaw package and the documented
upstream `Memory` interface.
