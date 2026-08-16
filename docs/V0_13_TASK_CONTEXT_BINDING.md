# v0.13 task-context binding source-candidate contract

Status: **Implemented in the source candidate; not qualified or released.** Package version remains
`0.12.0`. This contract is the minimum remediation for the reproduced same-Vault wrong-task-line
working-checkpoint admission defect. It is not a Host credential, capability token, Git authority,
or permission to widen Vault scope or sensitivity.

## Public contract

`deeplaw.task-context-binding/v1` is a closed, hash-bound object containing only opaque digests and
an optional Git base revision:

- `project_sha256` and `task_lineage_sha256` are required;
- `parent_task_lineage_sha256` is optional and cannot equal the current lineage;
- `repository_sha256`, `worktree_sha256`, `base_revision`, and `dirty_state_sha256` are either all
  present or all `null`;
- `binding_sha256` hashes the canonical object without the hash field;
- paths, branch names, task text, diffs, file content, Host session state, and credentials are not
  fields and fail closed as additional properties.

The Host or owner integration derives those opaque identities. DeepLaw validates and compares
them; it does not infer authorization from the current directory, a Host thread ID, similarity, or
the binding itself. Vault selection plus the existing grant, scope, sensitivity, lifecycle, and
temporal admission rules remain authoritative for access.

## Write and read behavior

- A new `memory_type=working` write requires an admitted Run Record whose closed
  `run_metadata.task_binding` is valid and integrity-bound. Other memory types retain their
  existing rules.
- Query Plan v6 and local Knowledge Capsule v3 always record either the normalized binding or
  explicit `null`. Their hashes therefore bind the selector used for the read.
- A working checkpoint is admitted only when the request binding resolves the same task route and
  current base/dirty snapshot as its Run Record. A request without a binding may use only the
  compatibility path for a unique task-text route; otherwise it returns `task_binding_required` or
  `task_line_ambiguous`. That compatibility path cannot attest current workspace freshness. A
  legacy unbound Run is withheld with `task_binding_unbound`.
- A checkpoint from another valid task line is rejected locally. Its existence, binding, branch,
  base, dirty state, path, and content are not projected as a Provider-visible mismatch Gap.
- Provider Capsule v2 remains unchanged and receives only admitted bounded context, safe Gap data,
  and the opaque `receipt_id`. The 65,536-byte hard limit is unchanged.
- Ordinary query and Context calls remain read-only and do not append the Canonical Ledger.

The binding is accepted by Python retrieval/context, both Knowledge CLI query/context surfaces,
and autonomous MCP `query`/`context`. CLI accepts one canonical JSON object through
`--task-binding`. Explicit Query Plan v4/v5 calls reject a binding; v5 compatibility does not
silently discard it.

Host Connect Plan v2 can embed the canonical binding in a path-free fixed closed-launcher
configuration. Static configurations accept the same value as `DEEPLAW_TASK_BINDING`. The launcher
injects it into omitted MCP `query`/`context` arguments and rejects an attempt to replace the fixed
binding. It also binds the owner-selected Vault by expected Vault identity while keeping the local
path outside generated configuration and provider-visible output.

## Compatibility, migration, and recovery

No table, database, Knowledge kind, Relation predicate, or Authority dimension was added. The
binding is stored in the existing closed Run Record `metadata_json`, covered by the Run receipt
and `knowledge_run_recorded` event digest. Consequently there is no physical schema migration:

- legacy Run Records with no binding remain integrity-verifiable as legacy-unbound records;
- they are not treated as the current task line, and their working checkpoints are withheld;
- new working checkpoints cannot be attached to an unbound or tampered Run;
- snapshots and restores already include Run metadata and the append-only event chain;
- a missing, non-canonical, hash-mismatched, receipt-mismatched, or event-mismatched binding fails
  closed; `verify` reports integrity failure for persisted tampering;
- downgrade follows the existing verified-snapshot policy. An older runtime may ignore the
  additive metadata field and therefore must not be used to claim task-line-safe admission.

Fork/merge conflict reconciliation, a searchable Run Timeline, semantic single-Revision restore,
and cross-Vault imports are not implemented by this remediation. `parent_task_lineage_sha256`
preserves an opaque parent reference only; it does not create a scheduler or merge policy.

## Development evidence and remaining gates

Repository development regressions cover missing-binding fail-closed behavior, exact selection of
one of two same-key current checkpoints, real linked Git worktrees with different base/dirty
states, public Python Context, MCP schema closure, receipt/event integrity, tampering, and Provider
redaction. The original defect was also reproduced from an isolated installation of the exact
pre-remediation wheel.

This evidence is not independent Human Gold, a fresh qualification/final-blind holdout, or a real
Codex/Claude/OpenCode result. It does not qualify concurrent fork lifecycle, stale-head recovery,
cross-Vault authorization, scale, portability, or release artifacts. The disposition remains:

```text
release_gate_passed=false
claim_eligible=false
competitive_claim_eligible=false
package_version=0.12.0
source_candidate_remains_not_released
```
