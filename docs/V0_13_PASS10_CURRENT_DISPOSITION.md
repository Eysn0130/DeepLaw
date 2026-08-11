# DeepLaw v0.13 Pass 10 current evidence disposition

Status: **historical candidate evidence only; current qualification invalidated** (2026-08-11).

Current Pass 11 Host dispositions moved to
[`V0_13_PASS11_TOKEN_ATTRIBUTION_DISPOSITION.md`](V0_13_PASS11_TOKEN_ATTRIBUTION_DISPOSITION.md)
and [`V0_13_PASS11_OPENCODE_DISPOSITION.md`](V0_13_PASS11_OPENCODE_DISPOSITION.md): three later
Codex App Server workflows and one OpenCode/DeepSeek workflow executed as partial/failed candidate
evidence. No operation profile, continuity qualification, or release claim was admitted. The Pass
10 facts below remain unchanged.

This disposition preserves the Pass 10 facts while preventing historical candidate artifacts from
being presented as uncontaminated or exact-head qualification. It does not rewrite the retained
Statement, Codex, or Obsidian files, and it does not permit commit/tree/hash-only rebinding.

## Frozen reproduction

The reproduction ran from commit `977ddd314e6d1e0a2615d98416a72463001d0ac9`, tree
`a96749b2bbc891a9b5e85df6da6e530a8f2e68e6`, package `0.12.0`, before any Pass 11 real-model run.
The machine-readable observation is
[`../benchmarks/v013/pass10-evidence-invalidation-v1.json`](../benchmarks/v013/pass10-evidence-invalidation-v1.json).

| Evidence | Observation | Current disposition |
| --- | --- | --- |
| Statement scale artifact from the b14 candidate chain | Current `verify_report` returns `Gold byte binding mismatch` | historical candidate evidence; not qualification |
| Codex 3-run report at `b14c90e` / tree `9fa784c` | The retained environment receipt lacks the current `runtime/bin/python` child prefix | historical candidate evidence; current verifier rejects it |
| Codex candidate prompt | It exposes scoring instructions for the expected first action and confirmed decision, the expected marker value, and an exact `knowledge_id`; the first-action and decision literal values are not directly interpolated | contaminated development evidence; not holdout/final blind |
| Obsidian Desktop receipt at `b14c90e` / tree `9fa784c` | One macOS load/verify/rename/edit/reconcile seam executed | historical candidate evidence; broader and exact-head qualification pending |

The Codex report's recorded usage and tool events remain historical observations. The invalidation
does not claim that the Host did not run; it means the run cannot establish uncontaminated task
success or current-candidate qualification.

## Current qualification boundary

- Gold, expected labels, and scorers must live in evaluator-only storage.
- Candidate prompts, Vaults, Host configuration, and Provider Capsules must not contain expected
  answers, expected markers, or exact Knowledge IDs.
- Candidate execution must not be able to read evaluator storage.
- Discovery must begin from a natural-language task plus non-secret project/task binding, and
  admission must reject wrong repository, worktree, task line, and stale state.
- No real Codex or OpenCode/DeepSeek qualification may run until those boundaries have executable
  fail-closed tests.

## Pass 11 harness status at the Phase 0 boundary

The development harness now has executable separation tests, but it is not yet a frozen real-Host
qualification suite:

- candidate source material is under `benchmarks/v013/qualification/candidate/`, while Gold and the
  scorer are under `benchmarks/evaluator/`;
- the Host response schema and candidate observation contract use neutral task-output fields and do
  not contain evaluator labels;
- prompt construction uses a natural-language task plus non-secret task binding, with no exact
  Knowledge ID or query target;
- the candidate working directory must be outside the repository/evaluator tree, and the Host
  configuration exposes only read-only `knowledge_support`;
- deterministic preflight proves current-state delivery, stale-snapshot Gap behavior, and exclusion
  of wrong repository, worktree, task-line, and historical-revision state;
- the old mixed `codex-continuity-qualification-report/v1` contract is retained only for historical
  receipts.

At this Phase 0 boundary these checks did not authorize a real-model run. Later Pass 11 commits
froze the App Server token-attribution harness and executed three exact-wheel workflows. Those
later observations did not pass and are governed by the separate current Pass 11 disposition; they
do not retroactively validate the Pass 10 artifacts.

## Release decision

```text
release_gate_passed=false
claim_eligible=false
competitive_claim_eligible=false
release_ready=false
package_version=0.12.0
source_candidate_remains_not_released
```

Human Gold, physically isolated qualification/final-blind data, exact-head real Host tasks, Legal
qualification, Wiki/Relation scale, cross-platform artifact evidence, signature, and public
redownload remain pending or blocked. No version change, tag, publication, or readiness promotion
is authorized by Pass 10 evidence.
