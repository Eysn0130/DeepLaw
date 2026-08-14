# DeepLaw v0.13 Pass 19 current-fix disposition

Status: **current source-candidate / pre-qualification fix; not a release or qualification
report**. Package remains `0.12.0`; `release_ready=false`; `claim_eligible=false`.

## Starting boundary

- base branch: `codex/v013-pass17-native-host-receipts`;
- base commit: `9c4a7c75d4270069c0067e3e6d76b995bb4807b6`;
- base tree: `6dd8acdd1bef53cdbbaa36e872aa4e864099f561`;
- active gate classification:
  `benchmarks/release/v013-gate-classification-v5.json`, SHA-256
  `d8f3e638f8f57c09adf55e274def67c3cabbc729b7e9cdd287cc9605eda6c7bb`;
- PR 31 and CI run `31767745226` were read-only starting evidence, not Pass 19 evidence.

Historical pass-specific reports and classifications were not rewritten.

## Root causes and current fix

Official static MCP configurations and Host Connect Plan v1 launched the raw MCP process directly.
The raw process correctly had no authority to clean its own already-inherited environment, so
ambient Host/plugin state, provider credentials, `.env`-derived variables, credential paths and
the real user profile could cross the process boundary. Generated configuration also embedded the
owner's absolute Vault path.

Pass 19 adds one fixed-target production launcher behind the existing three CLI entry points. It
can start only `law_support`, `knowledge_support`, or an explicitly granted `knowledge_sink`; it
does not accept a command, executable or arbitrary argument vector. Static Codex/OpenCode/plugin
configuration and additive Host Connect Plan v2 now use `--closed-environment`. Plan v2 retains v1
as a compatibility contract, removes the Vault path, records `value_included=false`, and binds the
runtime-selected data directory by opaque `--expected-vault-id`.

The second root cause was product binding, not checkpoint persistence. Existing successful Run,
working-memory checkpoint, route projection, Context, grant and forget primitives were already
present, but static/generated Host launch did not carry a stable binding and the read MCP had no
configured default. Pass 19 lets Host Connect Plan v2 embed a canonical
`deeplaw.task-context-binding/v1` or lets a static Host supply it as `DEEPLAW_TASK_BINDING`. The
fixed read process injects it only into omitted `query`/`context` arguments and rejects replacement
with another line. Query and Context remain read-only.

At an explicit successful task boundary the public write chain remains:

```text
owner-created grant
  -> separate knowledge_sink record_run(status=succeeded, task_binding=...)
  -> separate knowledge_sink remember(kind=memory, memory_type=working, run_id=...)
  -> existing rebuildable checkpoint route projection
  -> later closed knowledge_support context with the same exact binding
```

There is no hidden checkpoint, transcript crawler, background daemon, new database, Knowledge
kind, Relation predicate, page family, Host runtime or retrieval engine. Raw transcript, hidden
reasoning, complete logs and authentication state are not stored.

## Closed child process boundary

The launcher builds the child from a small portable process allowlist: `PATH`, locale and Python
encoding flags, and the Windows bootstrap names `SYSTEMROOT`, `WINDIR`, `COMSPEC`, `PATHEXT` when
present. It replaces `HOME`, temporary directories and all XDG roots with per-launch private
directories. On Windows, `USERPROFILE` is set to the same isolated home so `Path.home()` cannot
fall back to the ambient user; linked, junction/reparse and symlink-equivalent data paths fail
closed. macOS receives a fixed CoreFoundation text-encoding value rather than an ambient profile
value.

Only the selected surface's explicit DeepLaw data settings cross the boundary:

| Surface | Explicit child settings |
| --- | --- |
| `knowledge_support` | `DEEPLAW_KNOWLEDGE_VAULT`; optional validated `DEEPLAW_TASK_BINDING` |
| `knowledge_sink` | `DEEPLAW_KNOWLEDGE_VAULT`; exact grant ID remains a fixed CLI argument |
| `law_support` | `DEEPLAW_HOME`; explicit `DEEPLAW_DB` / `DEEPLAW_PRIVATE_DB`; optional explicitly enabled federated Knowledge Vault |

`CODEX_HOME`, OpenCode config/plugin/hook variables, Codex authentication, provider API keys,
DeepSeek/OpenAI credentials, `.env` variables and credential paths are neither copied nor
inspected. Secret values are not read, printed, logged or hashed. Absolute data paths exist only in
the closed child environment where needed; they are absent from generated configuration, receipts,
Provider Capsules and sanitized errors.

## No-model public-path acceptance

The public stdio fixture uses the closed launcher and real MCP clients. It creates two checkpoints
through the separate Sink and validates:

1. new read process;
2. resume through a new process;
3. fork with the same route/snapshot and an opaque parent lineage;
4. post-compaction process restart;
5. a separate concurrent worktree;
6. stale base/dirty snapshot;
7. wrong task line;
8. selective owner-granted forget;
9. unchanged Ledger audit head across all read MCP calls;
10. exact binding returns only the expected checkpoint.

It also verifies missing binding returns `task_binding_required` when no unique route can be
admitted and same-task-text multiple routes return `task_line_ambiguous`. Stale state returns a
bounded `workspace_diverged`/`stale_checkpoint` Gap. Forgotten state is not re-admitted, while the
other worktree remains available.

These regressions guarantee deterministic recovery only when the Host supplies the same registered
project/repository/worktree/task-line/base/dirty binding and the route is unique and current. A
unique task-text-only compatibility route cannot attest the caller's live workspace. Fork merge or
conflict reconciliation, automatic Host thread discovery, transcript restore and semantic
whole-session restore remain unavailable.

## Surface and complexity measurement

No surface was split or refactored for this measurement.

| Surface | Current measurement |
| --- | --- |
| Root CLI parser | 29 internal commands: 3 Basic, 15 Advanced, 7 Admin, 4 Compatibility |
| Nested Knowledge parser | 58 internal commands: 10 Basic, 16 Advanced, 13 Admin, 14 Compatibility, 5 untiered helpers |
| Compile saga | 13 subcommands |
| `knowledge_support` | 19 operations; canonical input schema 24,539 bytes, SHA-256 `dd8e8257dc2dbbe88f34c2a962222021dc8f4a69e2d060528e36449ad5338a20`; output 1,181 bytes |
| `knowledge_sink` | 35 operations; input/output 54,684 / 9,883 canonical bytes |
| `law_support` | 13 operations; input/output 13,486 / 28,969 canonical bytes |
| Contract files | 302 JSON Schema files after additive Host Connect Plan v2; v1 retained |
| Largest module | `src/deeplaw/knowledge_autonomy.py`: 14,250 lines / 637,306 bytes |
| Host runners | 10 `benchmarks/hosts/run_*.py` files / 13,911 lines; measured Codex/OpenCode continuity-runner nonblank/noncomment LCS 247 and sequence similarity 0.2391 |

Canonical schema bytes are serialization size, not Provider tokens. The fixed cost of the stable
19-operation schema remains a next-qualification A/B hypothesis. It will not be changed without
same-task success-rate evidence and actual Provider usage split into tool schema, system, user,
Capsule, cache, output and reasoning components.

## Evidence and remaining gates

The launcher canary first produced `4 failed`, proving direct static launch, the missing production
launcher/link boundary and missing Windows profile/XDG isolation. After the minimum fix, the closed
launcher/config regression, Host v2 regression and public continuity fixture pass locally. Full
focused route/Host/Sink/provenance regression was `96 passed`. The final pre-commit local run was
`1709 passed, 6 skipped` in 550.42 seconds; `uv lock --check`, `uv run ruff check .`, and both staged
and unstaged `git diff --check` passed. The six explicit skips remain non-results. Stacked-PR CI is
recorded in the PR and final engineering handoff, not promoted into qualification evidence here.

No real Codex, OpenCode or DeepSeek model ran. Repository-external Human Gold v2, qualification
holdout, final blind, independent blind review, exact Legal Pack, human Living Wiki tasks, remaining
Relation/scale lanes, frozen wheel/redownload/signature chain and commercial publication gates are
`not_executed` or unresolved. A future real run still requires the exact frozen candidate/wheel,
pre-frozen independent Gold and cases, raw validators, isolated Codex profile, owner-only OpenCode
provider environment, and explicit Owner authorization for cost and external calls.

The frozen future Host boundary remains exact: Codex uses the official Host seam, the owner's
existing login state without an Agent reading authentication files, model `gpt-5.6-luna`, reasoning
`max`, and an isolated qualification `HOME`/`CODEX_HOME`; OpenCode is exactly `1.18.16`, selector
`deepseek/deepseek-v4-flash`, API model ID `deepseek-v4-flash`. The owner-provided `.env` is parsed
only for the provider/Host process. Its values and path never enter the DeepLaw MCP child. Each Host
requires three distinct qualification tasks plus new/resume/fork/compaction/stale/wrong-line/forget,
First Correct Action, Decision Preservation, Wrong-State Admission=0, Forgotten-State Admission=0,
expected include/exclude, Duty Coverage, Useful Context Recall, actual Provider usage decomposition,
unchanged read Ledger, clean Provider Capsules, and a same-task no-DeepLaw A/B baseline.

```text
package_version=0.12.0
lifecycle=source_candidate
release_ready=false
claim_eligible=false
competitive_claim_eligible=false
no_tag=true
no_rc_or_ga=true
publication_performed=false
```
