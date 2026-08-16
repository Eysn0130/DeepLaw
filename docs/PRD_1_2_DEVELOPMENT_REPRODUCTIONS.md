# PRD 1.2 development reproductions

Status: **development evidence only**, 2026-08-08. These reproductions are derived from the
Owner-submitted PRD 1.2 task scenarios and use synthetic temporary Vaults. They are not a
qualification holdout, final blind holdout, independent Human Gold result, or release evidence.
The reproduction phase made no runtime change. The reproduced task-line defect subsequently
admitted the narrow remediation recorded below; package version, credentials, Provider calls, and
release state remain unchanged.

## Frozen boundary

- Candidate commit: `8465b99af31467a744db0814b5df2c39d51aa2fb` with the pre-existing dirty
  PRD/research documentation work preserved.
- PRD 1.2 SHA-256:
  `daa524d62471801ca79699948ebca52ab194e14adcdf0bc1d332850fd7a12fb8`.
- Traceability matrix covers 107/107 unique PRD IDs and has no `Qualified` or `Released` row.
- Baseline before the additive characterization tests:
  `uv run --frozen pytest --strict-markers` → `1177 passed, 9 skipped`.
- Baseline with the first three additive characterization tests:
  `uv run --frozen pytest --strict-markers` → `1180 passed, 9 skipped`. The subsequently added
  linked-worktree test passes independently and will be included in the next full run.
- The new tests create only temporary synthetic Vaults and Git worktrees. They do not read `.env`, Host auth,
  repository-external Gold, private material, or Provider secrets.

## PRD12-REPRO-CONT-010-001 — wrong task-line checkpoint admission

Result: **reproduced**.

Owner task represented by the development fixture:

> Resume the feature worktree's deployment task without admitting the main worktree's current
> checkpoint, even when both runs have the same task text.

Public seam: `KnowledgeOS.context.compile` using the default Query Plan v6 and Provider Capsule v2
projection. Setup mutations use only an owner-created temporary `knowledge_sink` grant.

The fixture writes two succeeded Run Records with the same task text and two bounded, run-bound,
seven-field working checkpoints representing logical `main` and `feature` task lines. A Context
request explicitly asks to continue the feature worktree. The pre-remediation Provider projection admits
both checkpoints and emits no lineage/worktree mismatch Gap:

```text
requested_logical_line=feature
wrong_line_admitted=true
right_line_admitted=true
selected_checkpoint_count=2
gap_codes=[]
provider_bytes_within_limit=true
```

The original failing characterization was captured by the pre-remediation versions of
`tests/test_prd12_task_lineage_reproduction.py` and
`tests/test_prd12_task_lineage_worktree_reproduction.py`.

The second fixture creates two real linked Git worktrees in pytest-owned temporary storage. Their
branches, base revisions, and dirty-state digests differ, but the pre-remediation sink and Context
contracts could not record or select by those facts. It reproduced the same wrong-line admission without saving
or projecting the worktree paths, branch name, base revisions, or dirty-state digests.

An externalized exact-wheel check independently installed the candidate into an isolated temporary
Python 3.13 environment outside the repository and reran the first characterization successfully:

```text
package_version=0.12.0
wheel_sha256=c7767ce5703d0ec496527aaef2898a1796d00840a20f63f87d0c6b250152f36c
result=1 passed
```

This closes source-tree import ambiguity for the reproduced behavior. The copied development
fixture is still repository-visible and is not independent Human Gold, qualification, or a release
artifact gate.

Root cause:

```text
Run identity != task-line identity != repository/worktree state
```

Before remediation, `knowledge_run_records_v4` bound a Run to task hash, Host, writer, scope, sensitivity, status,
time, and bounded digests. It has no canonical task-line/fork parent, repository/worktree identity,
base revision, or dirty-state digest. The stable Python Context API had no current-line/workspace
binding input, and v6 working-memory admission verified run binding, lifecycle, scope,
sensitivity, and query relevance but could not compare the selected checkpoint with a current task
line.

Impact and risk:

- a wrong-thread or stale-worktree decision can become Provider-visible current context;
- First Correct Action and tool/parameter selection can be grounded in the wrong branch state;
- a later, separately authorized mutation could apply a correct operation to the wrong worktree;
- the checkpoint remains Agent-derived and does not gain Source or legal Authority, but the
  cross-task-line admission is still release-blocking correctness and isolation risk.

Current reusable primitives: Vault ID, Run ID/task hash, immutable Knowledge Revision, semantic
key, CAS update, scope/sensitivity admission, audit head, Capsule selected revisions, Agent Context
repository/workspace identities, and explicit Gap/Receipt surfaces.

### Minimum remediation applied

The public-seam failure, real linked-worktree reproduction, and isolated exact-wheel reproduction
admitted one narrowly scoped source-candidate contract change:

- additive closed `deeplaw.task-context-binding/v1`, containing only project/task-line and optional
  parent/repository/worktree/base/dirty-state digests plus its canonical hash;
- the existing Run Record metadata/receipt/event binds that object without a new table or database
  migration;
- new working memory requires a task-bound Run Record;
- Query Plan v6 and local Capsule v3 bind the exact selector or explicit absence;
- exact-match working checkpoints alone are admitted; missing or legacy-unbound selectors fail
  closed;
- mismatched task-line candidates remain local rejections and do not disclose their existence or
  binding through Provider-visible mismatch Gaps;
- Provider v2 stays unchanged and excludes all binding fields; its 65,536-byte limit is unchanged;
- Python, CLI, and MCP v6 query/context share the same admission path, while v4/v5 reject rather
  than silently discard a binding.

A follow-on public coordinator regression reproduced that `failed`, `partial`, and `aborted` Run
Records were still accepted as working-memory provenance because the existing Run admission check
compared writer/scope/sensitivity but omitted status. The minimum correction requires
`status=succeeded` both when a working checkpoint is written and when its task binding is resolved
for current Context. Run Records of other statuses remain valid history; they cannot ground current
working state. The parameterized regression observed three failures before this correction and
three passes afterward.

The current versions of the two reproduction tests are post-fix development regressions. They use
the same semantic key on two current lines, and the real-worktree lane binds different base and
dirty-state digests. Missing binding admits neither checkpoint; exact feature binding admits only
the feature checkpoint. Contract, receipt/event integrity, null/partial/tampered input, MCP schema
closure, and Provider redaction are covered by
`tests/test_prd12_task_context_binding.py`. Compatibility and recovery boundaries are recorded in
`docs/V0_13_TASK_CONTEXT_BINDING.md`.

This is `Implemented`, not `Qualified`. Fork/merge reconciliation, independent Human Gold, fresh
holdout, real Hosts, cross-platform execution, and release artifacts remain required. The corpus
used to diagnose the defect remains development-only and cannot be reused as blind evidence.

## PRD12-REPRO-CONT-012-001 — Run Timeline public seam

Result: **reproduced_missing_public_seam**.

Owner task represented by the development fixture:

> Find an older Orion Run by task meaning, time, outcome status, and Artifact without knowing its
> Run ID and without loading a transcript.

The fixture records a successful, older Orion release Run and a later failed Orion rollback Run
through `knowledge_sink.record_run`. The returned receipts contain task hashes, bounded status/time
metadata, and opaque Artifact IDs, not the task plaintext.

The stable `KnowledgeOS` facade exposes no `runs`, `run_receipts`, `timeline`, or `search_runs`
surface. The autonomous `knowledge_support` input schema rejects `run_timeline` and `run_list`.
The legacy CLI `run-receipt list` is a bounded latest-N view without semantic/status/time/Artifact
filters or cursor pagination; `show` and `verify` require the caller to know the Run ID. Internal
SQL replay and the ephemeral Query Trace are not product Timeline seams.

Frozen characterization: `tests/test_prd12_run_timeline_reproduction.py`.

Root cause: content-minimized Run Records, legacy Run Receipts, Ledger events, inbox Artifacts, and
Query Traces are separate primitives with no unified owner-only projection, lookup contract,
filter/index policy, cursor, or forget/lifecycle linkage.

Impact and risk: the Owner cannot complete the PRD continuity/time-to-locate task through a stable
public interface. This is an availability/usability gap, not evidence of transcript or Secret
leakage. A future implementation would create a new disclosure surface, so task plaintext, Host
session data, hidden reasoning, raw logs, paths, and Provider secrets must remain excluded.

Minimum safe next action: freeze an owner-only task lookup vocabulary, status/time/Artifact
filters, pagination and integrity binding using the existing Run/Ledger primitives. Provider-facing
Context must receive only selected bounded checkpoint content and an opaque receipt, never the full
Timeline. No implementation is admitted until the external time-to-locate Gold and deletion/
forget expectations are frozen.

## PRD12-REPRO-KNOW-010-001 — default physical cross-Vault isolation

Result: **not_reproduced_default_physical_isolation**.

The development fixture creates two physical Vault roots with identical checkpoint title, alias,
semantic key, and task text but different Vault IDs, Run IDs, content, and sensitivity. Through
public Python Context and MCP `identity_lookup` reads:

- each Vault returns only its local Knowledge ID;
- identical semantic keys do not collapse across Vault IDs;
- changing the process CWD to Vault B does not change an explicitly opened Vault A;
- querying Vault A with Vault B's Knowledge ID returns no Statements and an explicit Gap.

Frozen negative reproduction:
`tests/test_prd12_cross_vault_isolation_reproduction.py`.

The current single-root architecture therefore did not reproduce an automatic cross-Vault read,
merge, or disclosure. No fix is justified for that default physical isolation path.

Not covered: an explicitly authorized cross-Vault projection/import, Host configuration selecting
the wrong Vault path, task-line/worktree identity inside one Vault, a real Host, backup/export/
forget independence, or cross-Vault disambiguation UI. These remain `Target` or `not_executed` as
recorded in the traceability matrix.

## Commands

```bash
uv run --frozen pytest -q \
  tests/test_prd12_task_context_binding.py \
  tests/test_prd12_task_lineage_reproduction.py \
  tests/test_prd12_task_lineage_worktree_reproduction.py \
  tests/test_prd12_run_timeline_reproduction.py \
  tests/test_prd12_cross_vault_isolation_reproduction.py

uv run --frozen ruff check \
  tests/test_prd12_task_context_binding.py \
  tests/test_prd12_task_lineage_reproduction.py \
  tests/test_prd12_task_lineage_worktree_reproduction.py \
  tests/test_prd12_run_timeline_reproduction.py \
  tests/test_prd12_cross_vault_isolation_reproduction.py

git diff --check
```

## Qualification boundary

Still `not_executed`:

- repository-external independent Human Gold and fresh qualification/final-blind holdouts;
- fork/merge/reconciliation lifecycle across task lines and independently authored external Gold;
- Owner Run Timeline time-to-locate study and selective deletion/forget lifecycle;
- explicit authorized cross-Vault reference/import/export behavior;
- real Codex, Claude Code, and OpenCode model tasks;
- exact signed/verified legal Pack and independent legal Gold;
- current-candidate 10k/100k, RSS/concurrency, 3-OS, wheel, SBOM/provenance, and public-redownload
  gates.

Current disposition remains:

```text
release_gate_passed=false
claim_eligible=false
competitive_claim_eligible=false
package_version=0.12.0
source_candidate_remains_not_released
```
