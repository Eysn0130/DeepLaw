# DeepLaw v0.7 named-baseline kit

This directory is an execution contract, not a results directory. It pins the
official upstream revisions, shared local model revisions, resource/fairness
policy, and adapter boundary required by the v0.7 construction brief. Every
entry remains `pending_execution`; no local planning artifact is claim evidence.

Validate the closed registry:

```bash
uv run python -m benchmarks.baselines.registry
```

Only after a clean candidate commit exists, the evaluator can create a new
external registry copy without editing the source-tree registry or advancing
the package version:

```bash
uv run python -m benchmarks.baselines.registry \
  --freeze-candidate-commit 0123456789abcdef0123456789abcdef01234567 \
  --reviewed-at 2026-07-28 \
  --output /absolute/evaluator/frozen-registry.json
```

This transition binds all three DeepLaw profiles to the exact commit and marks
them `candidate_frozen_execution_pending`; every result and claim field remains
pending/false. The evaluator must separately bind wheel/sdist/SBOM and sign the
complete commitment.

For a server or graph system, install the exact official revision in an
isolated checkout and provide a thin wrapper from its official API to the
existing `deeplaw.external-run/v1` JSONL protocol. The runner verifies the Git
worktree root, clean state, revision and recursive submodule inventory; binds
the exact registry bytes, corpus/query bytes and query case-ID inventory; and
hashes both the command executable and evaluator-owned wrapper. It rechecks
every binding immediately before launch, invokes argv without a shell from a
minimal hash-bound environment, and requires exact output coverage:

```bash
uv run python -m benchmarks.baselines.official_adapter \
  --system-id ragflow \
  --checkout /absolute/pinned/ragflow \
  --corpus /absolute/frozen-corpus.jsonl \
  --queries /absolute/frozen-queries.jsonl \
  --evaluation-environment /absolute/evaluator/evaluation-environment.json \
  --output /absolute/new/raw-output.jsonl \
  --resource-record /absolute/new/resource-record.json \
  --stdout-log /absolute/new/stdout.log \
  --stderr-log /absolute/new/stderr.log \
  --receipt /absolute/new/execution-receipt.json \
  --wrapper /absolute/evaluator-owned/ragflow-wrapper \
  --plan /absolute/new/execution-plan.json \
  --execute \
  -- /absolute/evaluator-owned/ragflow-wrapper
```

The wrapper must use the official system and recommended mode fixed in the
registry. A simplified local reimplementation is prohibited. Before planning,
the evaluator must provide a content-digested evaluation-environment record
covering the fixed host, OS/CPU/memory/storage, exact software artifacts,
system models, common reader model, network control and measurement protocol.
The runner does not claim to implement an operating-system network sandbox:
the evaluator must enforce the registered query-offline policy externally,
with fixed loopback-only model services where needed. Additional environment
variables must be named explicitly with repeatable `--inherit-env`; their
values are not written into the plan, but their canonical digest is bound and
rechecked.

Environment, execution-plan, resource-record and receipt schemas are
`baseline-evaluation-environment.v1.schema.json`,
`official-baseline-execution-plan.v2.schema.json` and
`official-baseline-resource-record.v1.schema.json`, and
`official-baseline-execution-receipt.v2.schema.json`. Output, resource record,
stdout, stderr, and receipt paths must all be new. Bounded stdout/stderr are retained for
success, non-zero exit, timeout, and output-limit failure. A zero exit is still
an `output_invalid` failure unless every strict JSONL row is finite and closed,
and output case IDs exactly cover the frozen query inventory. It is separately
`resource_invalid` unless the hash-bound record reports finite build/query
time, peak memory, index/workspace bytes, model calls/tokens/cost, and a closed
failure inventory for the same registry, corpus, queries and environment.

After runs are retained, an evaluator-owned collection manifest can be checked
without turning it into a performance claim:

```bash
uv run python -m benchmarks.baselines.collection_gate \
  --registry /absolute/frozen-registry.json \
  --collection /absolute/evidence-collection.json \
  --report /absolute/new/collection-report.json
```

The collection gate reopens every plan, receipt, frozen input, checkout and
artifact, detects post-receipt drift, and requires all 17 systems to share the
same corpus, queries, case inventory, hardware, reader, measurement protocol,
Token budget, top-k and evaluator run. It also requires raw output, resource
record and failure inventory retention. Its input/report contracts are
`baseline-evidence-collection.v1.schema.json` and
`baseline-evidence-collection-report.v1.schema.json`. Even a complete report
keeps `claim_eligible=false`: paired statistics, frozen secret held-out suites
and independent signatures remain separate gates. Plan/receipt/report digests
provide content integrity only; they are not evaluator signatures or claim
evidence.

The Obsidian entry uses the fixed human workflow in
`obsidian-workflow-v1.md`. It is not forced through a fake subprocess. The
two-phase manual runner writes a pre-task plan and later seals the exact raw
output, common resource record and scripted-human record:

```bash
uv run python -m benchmarks.baselines.manual_adapter plan \
  --corpus /absolute/frozen-corpus.jsonl \
  --queries /absolute/frozen-queries.jsonl \
  --evaluation-environment /absolute/evaluator/obsidian-environment.json \
  --output /absolute/new/obsidian-output.jsonl \
  --resource-record /absolute/new/obsidian-resource.json \
  --manual-record /absolute/new/obsidian-manual-record.json \
  --receipt /absolute/new/obsidian-receipt.json \
  --plan /absolute/new/obsidian-plan.json

# Perform the frozen workflow and create the three planned evidence files.
uv run python -m benchmarks.baselines.manual_adapter seal \
  --plan /absolute/new/obsidian-plan.json
```

The manual record binds per-case outcome/quality/provenance/staleness/Token and
timing fields plus exact screen-recording and before/after vault archives. A
retained task failure is valid benchmark data when it appears consistently in
the raw output, manual cases and resource failure inventory. A successful
seal means evidence consistency, not that every task succeeded. Contracts are
`manual-baseline-execution-plan.v1.schema.json`,
`manual-baseline-execution-receipt.v1.schema.json`, and
`obsidian-manual-run.v1.schema.json`.

DeepLaw profiles use
`benchmarks/external/adapters/jsonl_corpus_deeplaw_v070.py`. Final candidate
commit/artifact hashes, evaluator commitments, actual raw outputs, and two
independent attestations remain release blockers.

## External Evaluator Kit freeze

`benchmarks.release.evaluator_candidate` is the final fail-closed freezer and
portable verifier. It does not manufacture evidence. Before candidate delivery,
the evaluator can hash the exact corpus/query bytes and every local model file:

```bash
uv run python -m benchmarks.release.evaluator_candidate corpus-commitment --help
uv run python -m benchmarks.release.evaluator_candidate model-manifest --help
```

After all 17 official/manual runs succeed and the evaluator-owned internal gate
records case-level results, 10,000 paired-bootstrap iterations, 95% confidence,
Holm–Bonferroni correction, retained failures, and frozen thresholds, `freeze`
requires all of the following at once:

- an exact clean HEAD with no untracked files or omitted submodule contents;
- a frozen registry whose three DeepLaw operating points bind that commit;
- a freshly revalidated complete collection and all raw output/resource/log/manual
  evidence;
- model manifests whose canonical digests match every run environment;
- a pre-delivery corpus commitment and the passed internal statistical gate;
- exact protocol, per-case result, and comparison manifests;
- a clean reproducible-build report, wheel, sdist, `uv.lock`, CycloneDX SBOM,
  passed license inventory, and an OCI image archive whose descriptor/config
  labels bind the same commit, package version, wheel, sdist, and lock digests.

The output is a new `0700` content-addressed directory with exact source-tree,
contract, tokenizer/fusion/index-profile, release, commitment, model, evidence,
and signature-tool bindings. No absolute source path is used as a portable blob
identity. Verify it independently with:

```bash
uv run python -m benchmarks.release.evaluator_candidate freeze --help
uv run python -m benchmarks.release.evaluator_candidate verify \
  --kit /absolute/path/to/evaluator-kit
```

An organization may sign the exact `manifest.json` bytes with Ed25519. Verification
requires a separately trusted public key; a public key embedded only in the
attestation is never trusted automatically:

```bash
uv run python -m benchmarks.release.evaluator_candidate verify-attestation \
  --kit /absolute/path/to/evaluator-kit \
  --attestation /absolute/path/to/attestation.json \
  --trusted-public-key-hex 64_LOWERCASE_HEX_CHARACTERS
```

Kit integrity and one valid signature still return `claim_eligible=false` and do
not prove organizational identity or independence. Two real secret held-outs and
two independently trusted organizations remain mandatory. The current dirty,
unexecuted construction tree cannot produce a final kit by design.
