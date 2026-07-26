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

## Closed JSONL corpus adapter

`jsonl_corpus_deeplaw.py` is the evaluator-facing bridge for retrieval suites
that can export a frozen source corpus and query set without changing their
official scorer.

Corpus JSONL uses one closed record per source document:

```json
{"id":"document-id","title":"Source title","text":"Exact source text"}
```

Query JSONL uses:

```json
{"case_id":"case-id","query":"Unmodified benchmark query"}
```

Run it in a fresh workspace:

```bash
uv run python benchmarks/external/adapters/jsonl_corpus_deeplaw.py \
  --suite-id evaluator-suite-id \
  --corpus /absolute/path/to/corpus.jsonl \
  --queries /absolute/path/to/queries.jsonl \
  --workspace /absolute/new/path/to/workspace \
  --output /absolute/new/path/to/deeplaw-run.jsonl \
  --receipt /absolute/new/path/to/deeplaw-ingest-receipt.json \
  --max-items 5 \
  --max-chars 5000 \
  --frozen-fixture-approved
```

The adapter:

- verifies exact corpus bytes, unique IDs, closed fields, and explicit size
  bounds;
- preserves evaluator document IDs while storing exact source fragments,
  locators, hashes, and source-bound Assets;
- uses the shipped Context Compiler rather than a benchmark-only retrieval
  shortcut;
- returns bounded records compatible with the repository scorer;
- keeps instruction-like corpus content quarantined unless the independent
  evaluator additionally passes `--approve-quarantined-fixture`.

`--frozen-fixture-approved` authorizes only an isolated benchmark fixture.
It is not a product trust label, does not make benchmark content legal
authority, and must never be reused for an end-user vault. The complete
corpus/query SHA-256, upstream revision, reader configuration, Token budget,
raw output, resource measurements, and official score still belong in the
signed suite evidence manifest.
