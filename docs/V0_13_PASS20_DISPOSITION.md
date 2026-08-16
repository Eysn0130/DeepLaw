# DeepLaw v0.13 Pass 20 production-seam disposition

Status: **current source-candidate / pre-qualification implementation; not a release or Host/model
qualification report**. Package remains `0.12.0`; `release_ready=false`; `claim_eligible=false`.

## Reproduced gaps and minimum corrections

| # | Before/root cause | Impact and gate | After/minimum correction |
| --- | --- | --- | --- |
| 1 | Host Connect normalized a linked ancestor while the closed launcher rejected it because the two surfaces owned duplicate path rules. | Production configuration could pass preflight and fail at launch; publication blocker. | Both call one resolver for ancestor symlink/junction/reparse, owner, mode and Vault identity checks. |
| 2 | Windows junction/reparse behavior was not bound to the Host Connect/launcher equivalence contract. | Platform-specific fail-open/fail-late risk; publication blocker. | Native Windows junction regression is part of the Windows CI preflight and exercises both callers. |
| 3 | Tolaria generated raw MCP commands containing `--vault` and an absolute path. | Local-path disclosure and production-seam bypass; publication blocker. | Its generator binds the Vault owner-locally and emits the shared path-free closed argv. |
| 4 | Current Codex/OpenCode qualification and token-attribution runners built their own raw MCP child wrappers. | Qualification exercised a different security boundary from the product; qualification blocker. | Current runners delegate the child to the production launcher; Pass 13 runners remain compatibility harnesses but do the same. |
| 5 | Pass 19 labelled repeated calls through one `_read_context` helper as new/resume/compaction. | Overstated native continuity evidence; claim blocker. | The fixture and documentation now say deterministic restart/data-plane recovery; native Host lifecycle remains unqualified. |
| 6 | A path-free custom-Vault Host Connect plan did not create any owner-local mapping that the launcher could later resolve. | Generated setup was independently unusable; product blocker. | Host Connect performs and reports one private owner-local Vault-ID binding write, while still not writing Host or canonical state. |
| 7 | Product-surface manifest kept Host Connect v1 current. | Machine-readable surface contradicted the current path-free contract. | Current binds v2; v1 is compatibility-only; task continuity is a separate current surface. |
| 8 | README defaults and the Obsidian display-only setting still showed raw MCP. | Users could copy the unsafe/obsolete seam; documentation blocker. | Default examples show `--closed-environment`; raw MCP is labelled owner diagnostic/compatibility only. |
| 9 | Only the launcher parent compared the selected Vault with `--expected-vault-id`. | Parent/child path swap could escape the identity check; security blocker. | The raw child receives and independently revalidates the expected ID before serving MCP. |

The before canary produced seven focused failures for the cross-platform-equivalent local cases;
the native Windows junction branch is exercised only on Windows. The correction adds no database,
table, Ledger semantic, Knowledge kind, relation predicate, page family, runtime, daemon, connector,
telemetry or GUI.

## Production MCP caller inventory

| Caller class | Current disposition |
| --- | --- |
| Static product manifests | Both Codex/Claude plugin pairs and all three OpenCode samples use the fixed closed launcher and contain no Vault path. |
| Generated owner config | Host Connect v2 and Tolaria use `host_runtime` for Vault resolution, private binding and closed argv construction. |
| Editor/integration surfaces | Obsidian's display-only MCP default, Tolaria, Codex plugin smoke and the editor integration harness use or show the production seam. |
| No-model registration | `run_production_launcher_registration.py` performs MCP initialize, `tools/list`, schema/Vault/bound checks and optional Host config registration without a model turn. |
| Current qualification runners | Codex continuity, OpenCode continuity and Codex token attribution delegate MCP launch to the production seam. |
| Historical qualification compatibility | Pass 13 Codex/OpenCode runners retain their caller contracts but launch through the same closed seam. |
| Raw CLI | Raw `law_support`, `knowledge_support` and explicitly granted `knowledge_sink` remain owner diagnostic/compatibility entries; production generators do not emit them. |

## Task continuity boundary

The user chooses a project, task and Git worktree once. DeepLaw returns one stable opaque handle
containing a Vault ID and digests only; it contains no raw project/task text, repository/worktree
path, transcript, reasoning, authentication or Host log. On every operation the driver revalidates
the Vault, repository/worktree and current base/dirty snapshot and reconstructs the existing task
binding. Wrong task, wrong worktree, stale state and a forgotten checkpoint fail closed or return a
bounded GAP. Fork is explicitly `continue-parent` or `child-task`; compaction performs a new
verified Context read and copies no transcript.

Checkpoint is never hidden in a read. It records a succeeded Run and working-memory checkpoint only
through the separate `knowledge_sink`, an owner grant, an idempotency key and explicit no-case-data
confirmation. Forget uses that same protected write boundary. Native Host thread/session metadata
is neither required nor promoted to Knowledge identity.

This establishes deterministic restart/data-plane recovery. It does **not** prove that Codex or
OpenCode natively supplies start/resume/fork/compaction metadata, nor semantic whole-session restore,
automatic checkpointing, transcript restore, fork merge/conflict resolution or background work.

## Acceptance and retained artifacts

The no-model registration check uses an isolated Host home and a separately sanitized DeepLaw child
environment. It never reads an existing Codex home/authentication cache, `.env` or provider Secret,
and executes no model/provider turn. Codex uses its official MCP config CLI when an installed binary
is available. OpenCode executes only when the already-installed binary is exactly `1.18.16`; absence
is `not_executed` and does not authorize a download.

CI retains one exact wheel, sdist and path-free identity manifest bound to commit, tree, `uv.lock`
hash and both artifact hashes. A downstream job must download those retained bytes, revalidate the
manifest, install that exact wheel into a fresh environment, and execute the production launcher
initialize/`tools/list`/bounded Context lifecycle. This is retained-artifact and no-model MCP
evidence only, not Host/model qualification.

Current-source CI keeps Linux and macOS on the full Python 3.11/3.12/3.13 matrix. Windows runs the
same tracked `tests/test_*.py` modules on all three Python versions in three deterministic,
non-overlapping shards per version. A separate aggregate gate recomputes the shard assignment from
the exact checkout and rejects missing, duplicate, drifted, failed or differently classified
receipts. The Windows sentinel runs the Host, ACL, task-continuity and projection fail-closed seams
before starting those shards. This changes scheduling and diagnostic latency only; it does not
replace native Windows behavior with a mock or reduce the retained test-module set.

Remaining gates include repository-external Human Gold, real Codex/OpenCode model qualification,
Legal Pack qualification, human Living Wiki tasks, 1k/10k/100k Wiki/Relation scale, final blind
review, signing and publication. No RC/GA, parity, superiority or complete-validation claim is
eligible.

```text
package_version=0.12.0
lifecycle=source_candidate
release_ready=false
claim_eligible=false
no_tag=true
publication_performed=false
```
