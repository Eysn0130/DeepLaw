# CTRL011 engineering checkpoint and continuation

Status: paused engineering checkpoint, not a qualified release. `release_ready=false`.
The owner requested committing and pushing the existing work on 2026-09-16.
This checkpoint does not authorize treating incomplete producers as formal evidence.

## Repository baseline

- Repository: `Eysn0130/DeepLaw`.
- Continue from the latest verified head of `codex/v013-kernel-release-candidate-v5`,
  PR #42, rather than silently starting from `main`.
- Prior source candidate: `f5d7b54ef56481998ee918e0739d2041c788686e`,
  tree `3f79f50380cec5017498c079ed1aea5175a02f63`.
- On 2026-09-16, remote `main` was `5990687918e7b017e40da00a2c258267bc41e7f5`;
  latest formal GitHub release was `v0.12.0`. Refresh these facts before continuing.
- Candidate Full run `34321430000` belongs to f5d7b54 and completed FAILURE.
  Its exact-wheel journey/10k and six POSIX cells passed; Windows calibration
  shard 1 failed, shards 2/3 passed, Windows full matrix and aggregation skipped.
  The first failure was the external-install test's closed Windows environment
  omitting SYSTEMROOT (WinError 10106). This checkpoint fixes that environment;
  it does not claim a successful Windows rerun. Do not replay the old run.

## Included work and evidence ceiling

The six governed capabilities were implemented in the prior source candidate:
source/revision dependency support sets, explicit evidence-duty gaps, bounded
external-action state, admitted selection explanations, selective withdrawal and
recovery, and a synthetic maintenance evaluation. These do not establish general
legal completeness, autonomous semantic dependency discovery, external action
success from Host reports alone, or superiority over named alternatives.

This checkpoint adds native isolation engineering, process/audit/loopback
observers, deterministic guest assembly, original fork-byte correlation, bounded
OpenCode lifecycle source patches and build identities, and a fixed real-task
maintenance harness. Read `benchmarks/hosts/NATIVE_SLOT_ENGINEERING.md` and
`benchmarks/hosts/upstream/README.md` for scope and exact engineering identities.
No credentials, native binary packages, private Host payloads or machine-specific
runtime evidence are included in Git.

Retained local engineering observations, not reproducible merely by cloning:

- Linux ARM64 guest under macOS Virtualization: no NIC, shared directory or disk;
  separate Host/MCP UID, namespaces, roots, runtime bindings and cgroups.
- Four entry-phase actual negative probes: both roles denied runtime-entry write
  with EACCES and non-loopback connect with ENETUNREACH; audit loss/backlog zero.
  This is not a complete runtime filesystem/Secret trace.
- `three-route-1`: only health/session/fork, 68 packets, zero kernel drops,
  three connections, network namespace binding, closed role process windows,
  Host/MCP exit 0, empty cgroups and normal VM stop.
- MCP in that run received root-owned EOF without an MCP request:
  `mcp_exercised=false`, not Host-to-MCP functional acceptance.
- Earlier duplicate-health and timeout attempts remain failures, not discarded.
  Waiting for the actual ready line resolved the observed later sequence; the
  causal link to upstream listen-before-handler ordering remains INFERRED.
- These runs used the prior f5d7b54 wheel plus changing engineering modules.
  They cannot be relabelled as final-candidate or formal-slot evidence.

The latest `GuestSlotControl.adapt_fork_source` consumes original private bytes
through the existing native adapter and clears references. It has local tests,
including rejection of a killed Host and byte mismatch. It is not wired to a
complete installed broker/context in bootstrap and has not had a new native run.
Its barrier is the broker's control reply after the child event, not a claim that
upstream HTTP itself delayed its response. No final formal admission is implied.
The proposed `linux_host_execution_observer.py` was interrupted before delivery
and is not present; inspect the current tree rather than assuming it exists.

## Required next work, in dependency order

1. First reconcile the current Platform Core test inventory using its existing
   classification rules; the checkpoint failures below are real and unresolved.
   Then finish one executable owner-external broker/collector and actual deployment.
   Reuse existing control-v2, host-process-receipt-v2, native adapter and identity
   freezing tools. Bind candidate commit/tree/lock/wheel/sdist, run identifiers,
   actual Host executable/package, installed broker source/instance, and a fresh
   challenge. Observe actual executed bytes and process identity, not just an
   expected path or configured digest. Close real OS/process/filesystem/network/
   credential boundaries and bounded negative cases. Preserve unknown counters;
   loopback HTTP capture cannot prove all model/Provider/plugin counts are zero.
   Provider forwarding over the isolated boundary and full functional MCP remain
   incomplete. First obtain one admissible slot, then reuse for the remaining
   slots; do not spend six model runs on a producer that cannot be admitted.
2. Complete C6 with the frozen three configurations and at least five actual
   later Host tasks each: source update, wrong experience, independent support,
   unknown external action, and reuse after forgetting. Reuse maintenance files;
   equal budgets/order, public synthetic input, no answer oracle in Host context,
   independent state/event/hash scoring. The 15 real tasks have NOT been run.
   Report costs and unknowns honestly, including no improvement; no cherry-picking.
3. Stabilize behavior, run repository-required checks, then freeze the new exact
   candidate and packages. Only then dispatch one necessary new Candidate Full.
   Never combine old package/tree results into new-candidate success.
4. Complete the same final candidate's nine platform cells, exact-wheel journey
   and 10k, 13 Core gates and six real Codex/OpenCode Host slots; produce and
   offline-reopen Kernel/Commercial qualification bundles. Keep fixed acceptance
   model identities: Codex `gpt-5.6-luna`/`max`, OpenCode
   `deepseek/deepseek-v4-flash`. Construction model is a separate choice.
5. Once all actual release gates pass, review/merge PR #42, handle any merge-tree
   identity change, use existing authorized signing/release tooling and immutable
   tags, publish verified artifacts, and verify clean post-release installation.
   Do not overwrite tags, fabricate keys, purchase resources, or publish a
   source-only archive as a verified wheel.

## Execution rules for the next ChatGPT task

Connect to the repository with a writable coding environment. A read-only GitHub
connector can inspect and plan but cannot itself execute tests, edit/push code,
access the owner's local VM or perform signing. State that concrete limitation
if encountered; do not claim local artifacts are accessible from a cloud clone.
Complete independent code work and identify exact missing facilities/permissions
instead of asking generally for a broker path. Never upload owner-private runtime
inputs or retrieve credentials into conversation or logs.

Read AGENTS.md, current architecture and applicable contracts first. Preserve
unrelated worktrees, dirty files, stashes and retained evidence. Default to
end-to-end work by one principal implementer; delegate only bounded independent
work with explicit file ownership and review. Do not create another controller
or periodic automation. Keep OBSERVED, THREAD-REPORTED, INFERRED, executed,
failed and not_executed distinct. Stop when finite acceptance is met, or report
an exact non-solvable prerequisite with completed independent work and evidence.

## Checkpoint validation

Executed against this checkpoint's source on 2026-09-16:

- `uv lock --check`: passed.
- `uv run ruff check .`: passed.
- `git diff --cached --check`: passed after adding an exact-file whitespace
  attribute for the frozen upstream patch's legitimate empty context lines;
  original patch bytes and recorded SHA are unchanged.
- `uv run pytest -q`: FAILED, exit 1, two failures:
  `tests/test_v013_pass21_release_closure.py::test_platform_core_v2_is_complete_non_overlapping_and_active`
  and
  `tests/test_v013_platform_core_manifest.py::test_platform_inventory_candidate_receipt_is_closed_and_preserves_drift`.
  The frozen Platform Core manifest no longer matches the enlarged test set;
  its expected common count is 3076 versus current 3418. This remains an explicit
  integration gap; no assertion or acceptance gate was weakened to save this
  checkpoint. The remaining executed tests reported no failures. Preserve the
  existing classifications when reconciling; do not blindly substitute a count.

This is a source checkpoint with known failing integration checks, not a stable
candidate ready for Candidate Full or release.
No new native VM, model request, Candidate Full dispatch, merge, tag or release
is part of the checkpoint-save request.
