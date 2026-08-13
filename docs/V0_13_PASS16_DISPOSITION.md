# DeepLaw v0.13 Pass 16 disposition

Status: **Host prerequisites and fail-closed qualification harness verified; six real Host runs and
independent Human Gold not executed; release remains blocked** (2026-08-13).

This is a qualification disposition, not a Host result or release report. No model availability
probe, candidate task, Human Gold score, release assembly, RC, GA, tag, or publication was
performed. Package version remains `0.12.0`, `release_ready=false`, and `claim_eligible=false`.

## Candidate boundary

The exact qualification candidate before this disposition commit is:

- Pass 15 starting HEAD: `9f958fc4fbaf6220784592e3596e74e45ffd2a81`;
- branch: `codex/v013-pass16-real-host-human-gold`;
- candidate commit: `67a8694a296149852bf4a28ea6eab5a74309137f`;
- candidate tree: `98f6c8203f6beb2d6d5c389cc697173e2b1cc0b8`;
- package: `0.12.0`;
- platform: macOS `26.5.2` (`25F84`), Darwin/arm64, Python `3.11.15`;
- release readiness: false.

The disposition commit containing this document is not substituted for the candidate commit/tree.
No tag points at the candidate.

## Exact Host, tool, model, and configuration bindings

These are prerequisite receipts only. They do not satisfy either Host gate and do not contain
authentication material or a model result.

| Boundary | Exact binding | Static status |
| --- | --- | --- |
| Shared Host contract | `deeplaw.host-continuity-qualification/v1`; SHA-256 `6208741ee2a438ece8a7424c05c6f9d1057ab81af0da5791fc4d4809ff9fa369` | `passed` prerequisite |
| Frozen task/rubric input | `pass16-continuity-task-cases-v1.json`; frozen `2026-08-13T06:04:31Z`; SHA-256 `cc3862a0ad78c7e9e9855eca11329d450bb2110da00b88ed58fa02ad3692037b`; no model output seen before freeze | `passed` prerequisite |
| Codex Host | public `codex app-server --stdio`; runner SHA-256 `d203cd678c99e98aff2c9538b4adb32fb045afcad44990aea98f5eac006335d1` | invocation frozen; not executed |
| Codex tool | `codex-cli 0.147.0-alpha.1.2`; binary SHA-256 `9f6748b4ab10ffc92c28b9ccedae89e61a302bbc011df7d276ee38f55906e481` | `passed` prerequisite |
| Codex authentication | repository-external dedicated profile; official ChatGPT login status receipt SHA-256 `16118df3eb3595e44a4721878cef0f79910e6564e10ff0e78e451e7c4e478947`, 24 bytes; `auth_file_read=false` | `passed` prerequisite |
| Codex model | `gpt-5.6-luna`, reasoning effort `max` | selected; model call not executed |
| OpenCode Host/tool | official `opencode-ai@1.18.16`; source commit `a3647eb025c7615159d417dcc49fc39fdaeba65b`; runner SHA-256 `ec22eb27138404bc21d8973bf62a9fb0efd2edba5c18eec2388d8cda3c0b9375` | `passed` prerequisite |
| OpenCode install | package tarball SHA-256 `1e0ac00a7dafd5e7c22d468ce7e088ae329dc02abb48b52581cf1c63fb2c3ffd`; binary SHA-256 `a41776bf64c75786d6baf531b840ffb873c090d7c44793ae2dd4b1896de56a1f`; package lock SHA-256 `7de9ffbc1cd7123e774c0d28e68088eb03e001cafa538372ff40bfb862e4cbda` | `passed` prerequisite |
| OpenCode isolation | repository-external qualification-only configuration SHA-256 `404eef34fb5a09f7ba2d77a53a3fa0b9bc4f966d024ab40cef586e4a9e111ad2`, 1,143 bytes; separate dotenv mode `0600`, 53 bytes; no value retained | `passed` prerequisite |
| OpenCode model | `deepseek/deepseek-v4-flash`, variant `max`; static inventory SHA-256 `5f94c4c288371e55170945b1bd77964bbcaa635d2b888180ef9156a99e97380b`, 102 bytes; resolved-config SHA-256 `3d1dc8db12df673a4c7b7e68e4addba33ec50df69857929ea85f767fa90d5454`, 1,233 bytes | selected; availability/model call not executed |

The Codex runner does not read authentication files. The OpenCode runner accepts exactly one
owner-controlled `DEEPSEEK_API_KEY` assignment from a repository-external regular file owned by the
current user with no group/other permission. It rejects additional dotenv assignments, symlinks,
hard links, unsafe session identities, resolved-config Secret values, and Secret values in model
inventory or retained evidence. Codex and OpenCode configuration and authentication boundaries are
not shared.

## Human Gold freeze boundary

The repository contains only the frozen task cases, scoring rules, and closed structural contracts:

| Input/contract | SHA-256 | Status |
| --- | --- | --- |
| Task-case schema | `f1028244bd0a0e3650da3ccc31afed5dfb143a38bf6975e46cf0486f01e28b55` | frozen |
| External Human Gold schema | `de32ad74480b5e808bd38071dc0a6a2e6b7900ba836df77591267ed6867f0130` | ready; no Gold supplied |
| Blind-review schema | `dccbf7468d792c938ef49605086a344ccb3af0dc02585e4ffeab0ec27ff8b7dd` | ready; no reviews supplied |
| Per-run score schema | `c33edac49be148590907e9e65455465fa726e2dade87f973d6c9b6aea5a63983` | ready; no scores emitted |
| Fail-closed scorer | `17c849784f9e4285895d0fcb258f39469d70269ea7ef634fdf53bb2f5d952692` | structural validation only; authenticity never inferred |

No repository-external independent Human Gold exists for this candidate. No Agent assertion can
prove a human author, independence, blindness, randomized ordering, or a freeze that preceded
candidate output. Both Host runners therefore validate external Gold before candidate preparation
or any Provider/model process starts. The scorer reports `authenticity_proven=false` and never
turns structurally valid JSON into release eligibility.

## Six real Host runs

No true task was started. Deliberately, an unexecuted task receives neither a synthetic receipt nor
a placeholder `run_id`; Provider bytes/tokens are not guessed or replaced by static/no-model data.

| Host | Task case | Scenario | run_id | Real receipt | Actual Provider bytes/tokens | Status |
| --- | --- | --- | --- | --- | --- | --- |
| Codex | `continuity_cold_new_v1` | cold/new | not assigned | absent | not observed | `not_executed` |
| Codex | `continuity_resume_fork_concurrent_worktree_v1` | resume/fork/concurrent worktree | not assigned | absent | not observed | `not_executed` |
| Codex | `continuity_compaction_forget_v1` | compaction/forget plus stale/wrong-task/wrong-worktree challenge | not assigned | absent | not observed | `not_executed` |
| OpenCode | `continuity_cold_new_v1` | cold/new | not assigned | absent | not observed | `not_executed` |
| OpenCode | `continuity_resume_fork_concurrent_worktree_v1` | resume/fork/concurrent worktree | not assigned | absent | not observed | `not_executed` |
| OpenCode | `continuity_compaction_forget_v1` | compaction/forget plus stale/wrong-task/wrong-worktree challenge | not assigned | absent | not observed | `not_executed` |

Consequently First Correct Action, Decision Preservation, Wrong-State Admission, stale rejection,
forget admission, gap correctness, blind-review failure cases, and hard-failure details have no
observations or scores. Login/tool success is not extrapolated to either Host gate, and neither
Host is extrapolated to overall Kernel parity.

## Gate disposition

`passed` means a gate was actually executed against this exact candidate and met every frozen
threshold; `failed` means it executed and missed a threshold or incurred a hard failure;
`not_executed` means no complete admissible evidence exists. Under those meanings, every active
classification-v4 gate is `not_executed` for this candidate:

| Gate | Category | Required | Status |
| --- | --- | --- | --- |
| `canonical_integrity` | Core | yes | `not_executed` |
| `migration_recovery` | Core | yes | `not_executed` |
| `secret_host_isolation` | Core | yes | `not_executed` |
| `bounded_context` | Core | yes | `not_executed` |
| `legal_evidence` | Core | yes | `not_executed` |
| `source_citation_locator` | Core | yes | `not_executed` |
| `scale_performance` | Core | yes | `not_executed` |
| `supported_platforms` | Core | yes | `not_executed` |
| `reproducible_supply_chain` | Core | yes | `not_executed` |
| `human_gold_isolation` | Core | yes | `not_executed` |
| `codex` | Core | yes | `not_executed` |
| `selective_forget` | Core | yes | `not_executed` |
| `timeline` | Capability | no/not claimed | `not_executed` |
| `semantic_restore` | Capability | no/not claimed | `not_executed` |
| `claude` | Capability | no/not claimed | `not_executed` |
| `opencode` | Core | yes | `not_executed` |
| `comparative_incremental_benefit` | Competitive Claim | no/not claimed | `not_executed` |
| `superiority` | Competitive Claim | no/not claimed | `not_executed` |
| `sota` | Competitive Claim | no/not claimed | `not_executed` |

Active classification is `deeplaw-v013-commercial-gates-v4`, SHA-256
`07079b9f00021753426db7a98eb2ada4be05a50af96e8c6fc6565b94128d7c58`. It only rotates the
historical v3 bytes to bind exact OpenCode `1.18.16`; categories, thresholds, and gate strength are
unchanged. Assembly remains `assembly_enabled=false` with reason `blocked_missing_validator`.

## Verification

The exact staged tree later committed as the candidate passed:

```text
uv lock --check
  Resolved 140 packages in 21ms
uv run pytest
  1654 passed, 6 skipped in 526.72s
uv run ruff check .
  All checks passed!
git diff --cached --check
  passed with no output
```

The six skips remain explicit non-results. A Secret-value and local-path scan of the staged diff
also passed without retaining or printing the Secret. These engineering checks do not substitute
for any release gate listed above.

## Disposition

Keep package `0.12.0` and `release_ready=false`. Do not create a tag, RC, GA, publication, or Kernel
parity claim. The next admissible sequence is: an independent human freezes repository-external
Gold bound to the exact task-case digest; only then the two runners execute six real isolated tasks
against the candidate; a different independent human blindly scores six anonymized packets; only
then may the existing release gates be executed. Any wrong-state admission, Secret/path leak,
hidden write, wrong Authority, model/tool substitution, missing actual Provider accounting, or
threshold miss fails closed.
