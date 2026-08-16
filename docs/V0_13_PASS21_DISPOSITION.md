# DeepLaw v0.13 Pass 21 product-route and commercial-candidate disposition

Status: **current implementation / blocked before exact v0.13 qualification candidate**. Package
remains `0.12.0`; `release_ready=false`; `claim_eligible=false`. This document is not a Host/model,
Human Gold, Wiki, Legal, Context, scale, RC, GA, parity, superiority, signing, or publication result.

## Product-route closure

Pass 21 closes the ordinary recovery gap without adding a second persistence engine. The existing
Ledger, Run, Checkpoint, Knowledge Store, and mutation coordinator now support bounded read-only
`task locate`, `task inspect`, and `task timeline` projections. Project + task text + explicitly
selected current workspace is the normal route; an opaque task handle remains optional. Ambiguous,
wrong-worktree, stale, forgotten, secret-unverifiable, and workspace-bound cases return structured
Gaps.

Workspace identity hashes bounded non-sensitive untracked content rather than trusting
size/mtime/inode. Secret-looking tracked or non-ignored untracked candidates are detected only from
Git path/status metadata: their contents are never opened, and the snapshot becomes unverifiable.
Ignored paths and ignored trees are never enumerated and never enter route or snapshot identity.
The capacity ceiling remains closed and reports `workspace_snapshot_bound`. Fork selects and
validates a separate child worktree in the same repository. Exact forget resolves the recorded
checkpoint identity rather than requiring the current dirty/base snapshot to remain unchanged.

Checkpoint completion is idempotent across `record_run -> remember`: a failed remember leaves an
explicit recoverable partial state and the same idempotency key completes it later. Timeline is a
content-minimized projection of task-scoped Run/Checkpoint/Ledger/Artifact identities, status, time,
and Gap only. It does not copy transcript, reasoning, raw log, authentication material, full diff,
or local path. Artifact references accept only opaque or bounded safe identifiers.

Static Host configuration remains task-neutral. The production launcher requires explicit Host
workspace metadata and does not use ambient `Path.cwd()`. Codex/OpenCode lifecycle IDs remain
untrusted hints that are rebound to the Vault/project/task/workspace identity; adapters do not
implement an Agent runtime or retain Host private memory.

## Windows boundary

The launcher recursively hardens its owned temporary root, HOME, TMP, and work directory with the
native owner-only ACL implementation before starting the child. PowerShell ACL processes receive a
minimal constructed environment and cannot inherit provider/authentication variables or
`CODEX_HOME`. Source-level macOS checks verify construction and fail-closed behavior only; native
Windows qualification requires the Windows sentinel and Candidate Full Windows jobs and is not
claimed by this document.

## Qualification and artifact chain

Historical qualification schemas, fixtures, Gate classifications v1-v5, and Platform Core v1 stay
unchanged. Current contracts are:

- `deeplaw.v013-active-qualification/v1` for one exact candidate and external-input binding;
- Gate classification v6 plus source-specific retained evidence, derived Gate Result,
  selective-forget raw receipt, and exact all-Core collection contracts; generic raw evidence is
  diagnostic-only, and v6 keeps assembly disabled until that
  reproducible collection proves every Core Gate passed with zero hard failures;
- Platform Core v2 for complete, disjoint current collection classification;
- one reproducible-build output whose wheel/sdist bytes are the only downstream artifact source.

Candidate Full builds twice from clean tracked material with one `SOURCE_DATE_EPOCH`, retains only
the byte-identical wheel/sdist plus their reports for 90 days, and makes every OS/Python consumer
download and verify those same hashes. Windows uses its complete Python 3.12 run as the executed
duration calibration, then applies deterministic duration-weighted, complete, non-overlapping
shards to 3.11 and 3.13. Together those lanes cover all three supported Python minors. Periodic
Scale and Release resolve a successful Candidate Full run and consume its bytes; neither rebuilds.
Release additionally requires the exact commercial qualification collection, 9-platform fresh
install, SBOM/OpenVEX/licenses/provenance/signature/public-redownload evidence and is not triggered by
ordinary push.

## Current stop condition

The external Human Gold, qualification holdout, final-blind holdout, and compiler/scorer-isolation
hashes are not available. Therefore the package has not been changed to `0.13.0`, the exact frozen
wheel/sdist does not exist, Candidate Full and every real Core Gate are not executed, and no real
Codex/OpenCode/model call is permitted. If the installed Codex version differs from the active
`0.147.0-alpha.1.2` constraint, qualification also fails closed until that exact version is obtained
or the protocol is formally re-frozen.

No compact Host schema is added. There is no executed token evidence in this pass showing that the
existing MCP schema crosses the frozen significant-overhead threshold; the current schema remains
the compatibility and production surface rather than introducing an unmeasured second surface.

```text
package_version=0.12.0
active_candidate=construction_candidate
blocker=release_version_binding_deadlock
external_blocker=blocked_external_qualification_input
release_ready=false
claim_eligible=false
merge_allowed=false
tag_allowed=false
publication_performed=false
```
