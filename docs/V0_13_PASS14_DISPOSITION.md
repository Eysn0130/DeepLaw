# DeepLaw v0.13 Pass 14 disposition

Status: **current-fix implementation locally verified; real Host diagnostic and qualification
not executed; release remains blocked** (2026-08-13).

This is a Pass 13 correction record, not a release report. Package version remains `0.12.0`,
`release_ready=false`, and every retained or described result remains `claim_eligible=false`.
No RC, GA, tag, merge, publication, or context-only Host profile was created.

## Candidate boundary

The implementation candidate before this evidence-documentation commit is:

- branch: `codex/v013-pass13-host-continuity-qualification`;
- commit: `e81e9c87e4215de2d26d354051a20678fd9a4ca8`;
- tree: `3ff5c44fdbcf0fb1e698f422fa4533e4fee83443`;
- package: `0.12.0`;
- release readiness: false.

The documentation commit that contains this report is not silently substituted for that exact
implementation/artifact binding.

## Root causes and minimum corrections

| Area | Root cause | Minimum correction |
| --- | --- | --- |
| Codex compaction | The Pass 13 client treated deprecated `thread/compacted` as the success signal and did not model the current compaction item lifecycle | `thread/compact/start` accepts `{}`; qualification waits for paired `item/started` and `item/completed` events whose item type is `contextCompaction`; the legacy event is compatibility input only and cannot satisfy qualification |
| Codex isolation | The Pass 13 runner inherited ambient HOME/config/plugin state while describing the run as isolated | Build a closed temporary HOME, `CODEX_HOME`, XDG and temp environment, reject ambient plugin/app/hook state, and retain only a path-free isolation receipt |
| Codex plugin packaging | Bundled MCP configuration was placed below `.codex-plugin/` | Keep only `plugin.json` in `.codex-plugin/`, place `.mcp.json` at the plugin root, and bind it from the manifest as `./.mcp.json`; both plugins, `host connect`, contracts and tests use the same format |
| Compilation mixed history | `source_knowledge_status` and compilation handoff independently reduced history, so a later failed attempt could appear as `stale_or_blocked` in one seam and plain `compiled` in the other | Both public seams use one status reducer and separately report committed canonical success, current admissibility, latest attempt status and Wiki projection status; an older successful revision is never deleted or rewritten by a later failure |
| Golden sync | Golden synchronization called the lower-level sync path without the auto-aware/status coordinator | Golden sync opens the existing auto-aware Vault/coordinator and returns the same per-source status used by add/reconcile; no second mutation implementation was added |
| Host runners | Codex and OpenCode duplicated Git/tree, wheel/install, contract-digest, report aggregation and bundle-manifest logic | Both runners use `QualificationOrchestrator`; Host-specific code is limited to protocol, configuration, isolation and event interpretation. The consolidation removed more runner code than it added |
| Product status | Surface role, visibility and maturity were conflated in `category`/`disposition`/`status` | Existing surfaces only were migrated to `product_role` (`Core`, `Driver`, `Compatibility`, `Experiment`) and `lifecycle` (`Active`, `Hidden`, `Deprecated`, `Deferred`, `Retired`) |
| Windows CI | A Codex argv test passed `Path("/opt/codex")` but asserted a literal POSIX string | Assert the platform-native `str(Path(...))`; runtime behavior and security policy are unchanged |

No database, Ledger, retrieval engine, Knowledge kind, relation predicate, page family, GUI,
connector platform, telemetry, Secret manager, mixed read/write MCP, filesystem scan or private bulk
API was added.

## Fail-before and pass-after evidence

Before implementation, focused regressions demonstrated:

- five Codex compaction failures, including timeout on the current `contextCompaction` events and
  false success from deprecated `thread/compacted`;
- six isolation/plugin failures, including ambient Host-state inheritance, missing isolation
  receipt fields, obsolete plugin layout and stale `host connect` output;
- a mixed-history divergence: success followed by failure returned `stale_or_blocked` from status
  but `compiled` from handoff;
- Golden sync omitted the shared source knowledge status;
- the two Host runners exposed no common candidate/report orchestrator;
- the product manifest had no independent role/lifecycle dimensions;
- the OpenCode entry point returned success for partial/failed reports; and
- the retained-artifact scanner did not reject an arbitrary POSIX path such as an `/opt/...`
  value.

After correction, the focused Pass 13/14 Host, compilation, Golden sync, plugin, isolation,
artifact-scan and surface-manifest tests pass. The complete local suite on the implementation
candidate reported `1581 passed, 6 skipped`; the skips remain skips and are not release passes.
`uv lock --check`, `uv run ruff check .`, and `git diff --check` also passed.

## Exact local artifact and plugin preflight

The clean implementation candidate produced this external, uncommitted artifact evidence:

| Evidence | Result |
| --- | --- |
| Wheel | `deeplaw-0.12.0-py3-none-any.whl`; 1,265,613 bytes; SHA-256 `eb7e77c89a63ee5781c1b57714fcc8d0702e582f6e22dc1ead8611bb7aa08aad` |
| Fresh-wheel journey | valid; file SHA-256 `1f91299046a88796348146e78c86c2fbe769d66d5cf86997bc741accd26f4cbc`; manifest bundle SHA-256 `ccc3b692d910b16c8311e07beef3c76c95ef0726b3fc0d38a58ae1569369d2d6` |
| Installed runtime | isolated site-packages import, `deeplaw 0.12.0`, 287 packaged contracts, read/write leaves remained `knowledge_support`/`knowledge_sink` |
| Codex plugin lifecycle | current local marketplace discovery, install/remove/re-add and exact cache-copy checks passed with `codex-cli 0.147.0-alpha.1.2`; report record SHA-256 `0ef6f984fcf33513250ff7df6a30a0f52a12852cf088ee40ffae6e396cd6f234` |

The plugin observation started no model or MCP session, did not seed credentials, and explicitly
remained `full_host_acceptance=false` and `claim_eligible=false`.

## Real Host diagnostic and qualification status

| Host | Closed preflight | Diagnostic | A/B/C qualification | Canonical Host bundle |
| --- | --- | --- | --- | --- |
| Codex / `gpt-5.6-luna` / `max` | `failed_closed`: a fresh temporary profile did not report a ChatGPT login through official `codex login status` | `not_executed` | `not_executed` | absent |
| OpenCode / `deepseek-v4-flash` | `failed_closed`: no installed `opencode` binary was found, so version/config validation could not start; `.env` was not read | `not_executed` | `not_executed` | absent |

No authentication file, keychain item or Secret value was read, copied, printed, hashed or
retained. No model call occurred. Because no diagnostic task ran, there is no tool/schema burden
measurement and no evidence that the 19-operation read profile is a root cause. Consequently a
new default context-only Host profile was not implemented.

## CI status

The terminal CI run for the pre-Pass-14 HEAD (`7ef0565b...`) was audited rather than inferred:

- Linux and macOS candidate lanes for Python 3.11, 3.12 and 3.13 passed;
- the macOS and Windows smoke lanes passed;
- all three Windows full-regression lanes failed on the same platform-specific Codex argv test
  assertion after the rest of the suite ran; and
- the platform-neutral test correction is included in `e81e9c8`, but a current-HEAD three-OS CI
  result is pending until the branch is pushed and the new run completes.

Therefore 3-OS status is not recorded as passed.

## Remaining priorities

- **P0:** Owner-login precondition for the isolated Codex profile; an installed OpenCode 1.x
  binary; one distinct diagnostic per Host; only then the three distinct A/B/C continuity tasks;
  a schema-valid, path/Secret-free bundle for every executed Host scenario; and a terminal green
  current-HEAD three-OS CI run.
- **P1:** independent Human Gold, exact signed 28-source Legal Pack, qualification holdout, final
  blind run, provenance/signing/public-redownload artifact chain and all remaining Core gates.
- **P2:** caller/migration/rollback evidence for compatibility wrappers before any deprecation or
  retirement. No wrapper is removed by this pass.

## Disposition

Keep PR #29 draft. Do not merge or release while the P0/P1 gates above remain open. Keep package
version `0.12.0` and `release_ready=false`; do not create an RC, GA, tag or publication from this
candidate.
