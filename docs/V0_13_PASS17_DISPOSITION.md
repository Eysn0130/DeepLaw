# DeepLaw v0.13 Pass 17 disposition

Status: **Host-specific receipt v2 implemented; one real claim-ineligible development diagnostic
executed on each of Codex and OpenCode; qualification blocked by missing external Human Gold**
(2026-08-13).

This is a current-fix and development-diagnostic disposition. It is not qualification evidence,
does not satisfy a Core gate, and does not authorize a tag or release. Package version remains
`0.12.0`, `release_ready=false`, and `claim_eligible=false`.

## Root causes and fail-before/pass-after

| Root cause | Fail-before reproduction | Minimum correction | Pass-after evidence |
| --- | --- | --- | --- |
| OpenCode native lifecycle was recorded with Codex vocabulary | The Pass 16 OpenCode runner invoked native CLI/session seams but populated `thread/start`, `thread/resume`, `thread/fork`, `thread/compact/start`, `item/started`, and `item/completed`; the shared validator treated those Codex names as cross-Host vocabulary. Semantic scenario names could therefore be mistaken for observed native events. | Keep the semantic task family separate from transport, request seam, requested native operation, observed native event/response, sanitized byte digest, lineage, and actual Provider usage. Pin OpenCode behavior to source commit `a3647eb025c7615159d417dcc49fc39fdaeba65b`: CLI new/resume/fork, HTTP `session.get`, `session.summarize`, and `session.messages`. OpenCode 1.18.16 does not persist `parentID` on the forked `session.get`; the CLI `--session … --fork` request binds the predecessor and the GET receipt leaves native parent absent. | `test_opencode_runner_and_shared_validator_use_native_not_codex_vocabulary`, `test_single_diagnostic_covers_each_native_lifecycle_seam`, `test_opencode_each_cli_session_transition_requires_its_native_get`, and the retained OpenCode v2 receipt pass. Its observed method set is exactly `cli.run.json`, `session.get`, `session.summarize`, and `session.messages`. |
| Diagnostic-first protocol was unreachable without Human Gold | The protocol required a Host diagnostic before qualification, but both Host entry points loaded Human Gold before candidate preparation and Host/model start. With no external Gold, there was no executable diagnostic seam. | Add `diagnostic` mode to the existing runner/orchestrator. It uses the source-free development fixture, does not accept or read Gold or qualification task cases, emits no qualification metric, and cannot enter the scorer or active gate. Qualification fixtures are loaded lazily only inside qualification mode. Keep qualification mode fail closed by loading external frozen Gold before candidate preparation and any Host/model process. | `test_diagnostic_mode_is_reachable_before_external_human_gold`, `test_diagnostic_reaches_candidate_preparation_without_gold`, `test_importing_diagnostic_runners_does_not_read_qualification_cases`, `test_diagnostic_pre_host_path_does_not_read_qualification_cases`, `test_development_diagnostic_is_not_scorer_or_gate_eligible`, and the two retained real reports pass. Qualification without Gold still fails before candidate/model work. |

The OpenCode real diagnostic also exposed two bounded model/protocol failures while exercising the
new seam: an invalid public-v6 call shape, followed after correction by an over-bound resume
response. Both runs remained `claim_eligible=false`. The minimum correction disclosed the complete
exact argument object and the existing response bounds to the Host; the argument validator and
response contract were not weakened. Provider usage attribution was then separated so the
compaction `session.messages` usage is retained once in native receipts while the following turn
and aggregate still include the complete cost. The shared validator now requires OpenCode native
usage totals to reconcile with turn evidence.

## Receipt contract migration and historical disposition

`deeplaw.host-continuity-qualification/v1` remains byte-for-byte historical and is
`invalidated-for-current-qualification`:

- v1 SHA-256:
  `6208741ee2a438ece8a7424c05c6f9d1057ab81af0da5791fc4d4809ff9fa369`;
- current v2 SHA-256:
  `c34c59800f14b11c6a760f8451e6e9361c4bdd7c6dac4ea3acb7458bdf2022f9`;
- historical active-classification v4 SHA-256:
  `07079b9f00021753426db7a98eb2ada4be05a50af96e8c6fc6565b94128d7c58`;
- current active-classification v5 SHA-256:
  `d8f3e638f8f57c09adf55e274def67c3cabbc729b7e9cdd287cc9605eda6c7bb`.

No persistence migration applies: this is an evidence-contract rotation, not a Source, Knowledge,
Authority, Ledger, or storage change. Current qualification accepts v2 only. The validator rejects
a report whose self-declared v2 digest does not equal the current exact schema bytes. Human Gold
v2, blind-review v2, and per-run score v2 bind the same receipt-contract digest. Classification v5
changes only the two active Host input bindings from v1 to v2; gate categories, required status,
budgets, thresholds, and hard-failure rules are unchanged. Historical v1/v4 bytes were not edited.

## Exact development-diagnostic receipts

Both reports use `deeplaw.host-continuity-qualification/v2` and are retained under
`benchmarks/hosts/evidence/pass17-native-host-diagnostics-2026-08-13/`. Both satisfy:

```text
execution_mode=diagnostic
qualification_status=not_applicable
evidence_class=development_diagnostic
status=executed
claim_eligible=false
release_ready=false
```

| Field | Codex | OpenCode |
| --- | --- | --- |
| Report SHA-256 | `ecadcd9ad32efffc38219cee1a59d157e91b04ca5c064d6455e87f8f7d33f391` | `7d75179b43398d2c6a2d077e1a72c8b1a4d6c41436f83bf719a44fa96448e9eb` |
| Candidate commit/tree | `2b98f8a67c06894adeed4c6e92933f220f865652` / `91af53a154111376d4422e51f279ddc58d6fa849` | `23a7d6f23677398f730d09433c0607927549d726` / `657def9eb0dda81927bdafee6d5171982a5c794d` |
| Candidate wheel | `deeplaw-0.12.0-py3-none-any.whl`; SHA-256 `8d7dde262ecce64e5e4dfdfdf81005a79e4dd64462c4bb3adf35b9091728631e`; 1,289,188 bytes | same exact wheel bytes |
| Host/tool | `codex-cli 0.147.0-alpha.1.2`; binary SHA-256 `9f6748b4ab10ffc92c28b9ccedae89e61a302bbc011df7d276ee38f55906e481` | `opencode-ai@1.18.16`; binary SHA-256 `a41776bf64c75786d6baf531b840ffb873c090d7c44793ae2dd4b1896de56a1f` |
| Model | `gpt-5.6-luna`; reasoning `max`; existing ChatGPT login checked only through `codex login status` | `deepseek/deepseek-v4-flash`; variant `max`; provider key read only by the runner from the owner-only external file |
| Transport/request seams | Codex App Server JSON-RPC: `thread/start`, `thread/resume`, `thread/fork`, `thread/compact/start` | OpenCode CLI: `cli.run`, `cli.run.session`, `cli.run.fork`; loopback HTTP: `session.get`, `session.summarize`, `session.messages` |
| Actually observed native vocabulary | `thread/start`, `thread/resume`, `thread/fork`, `thread/compact/start`, `item/started`, `item/completed` | `cli.run.json`, `session.get`, `session.summarize`, `session.messages` |

The Codex receipt observes a native compaction response and the paired `contextCompaction`
`item/started` and `item/completed` events. It does not turn a context-size snapshot into Provider
usage. The OpenCode receipt records CLI invocation, HTTP request, and native response in separate
columns. Its `session.get` after fork correctly has no claimed native parent; lineage remains bound
to the preceding sanitized fork request.

Each runner seeded its source-free checkpoint through the existing owner CLI boundary before the
Host turns. Every Host `knowledge_support` turn left the combined Ledger audit head unchanged;
`read_mcp_write_performed=false`. Both reports record a closed DeepLaw MCP child, only
`knowledge_support`, cleanup complete, no retained transcript or hidden reasoning, and no Secret
or absolute-path canary hit. No `knowledge_sink` or `law_support` process was merged into the read
Host path.

## Provider, Capsule, and tools/list measurements

`tools/list` returned one self-contained `knowledge_support` input schema for each Host:

- canonical UTF-8 bytes: `24,539`;
- SHA-256: `dd8e8257dc2dbbe88f34c2a962222021dc8f4a69e2d060528e36449ad5338a20`;
- effective operation count: `19`.

These are byte measurements, not actual or estimated tokens. Provider usage is reported
independently:

| Measurement | Codex diagnostic | OpenCode diagnostic |
| --- | ---: | ---: |
| Provider Capsule calls | 4 | 8 (two bounded reads per turn) |
| Bytes per Capsule | 1,404 | 1,162 |
| Aggregate Provider Capsule bytes | 5,616 | 9,296 |
| Actual input tokens | 57,027 | 29,790 |
| Actual cached-input tokens | 40,192 | 143,872 |
| Actual cache-write-input tokens | 0 | 0 |
| Actual output tokens | 2,103 | 4,950 |
| Actual reasoning-output tokens | 1,458 | 1,638 |
| Actual total tokens | 59,130 | 180,250 |

Codex reports cached input as a subset of input and reasoning as a subset of output, so its total
is input plus output. OpenCode reports cache read and reasoning as additive Provider fields, so its
total is the sum of all five component fields. OpenCode also performed a separate sanitized model
availability probe: input `315`, cached input `0`, cache write `0`, output `3`, reasoning `58`,
total `376`; these numbers are not folded into the diagnostic-task aggregate above.

All Codex Capsules contained 0 Statements, 0 Evidence items, duplicate Evidence count 0,
RelevantChars/ContextChars `0/1404`, and explicit Gap codes `duty_unresolved`, `no_answer`, and
`task_binding_required`. All OpenCode Capsules contained 0 Statements, 0 Evidence items, duplicate
Evidence count 0, RelevantChars/ContextChars `0/1162`, and explicit Gap codes `duty_unresolved`
and `no_answer`. OpenCode's native event exposes the exact MCP text projection but not
`structuredContent`, so its structured-output bytes/hash remain null rather than reconstructed.
These development gaps are diagnostic observations, not qualification scores.

## Human Gold and qualification

The owner-provisioned repository-external Human Gold directory was inspected by metadata only and
was empty. There is therefore no admissible artifact proving an independent human author, freeze
before qualification model output, exact task/candidate/wheel/v2 binding, or a second independent
blind reviewer.

Disposition:

```text
human_gold_status=blocked_external_human_gold
codex_qualification_tasks=not_executed (3)
opencode_qualification_tasks=not_executed (3)
blind_scoring=not_executed
qualification_claim_eligible=false
```

No qualification `run_id`, placeholder score, synthetic receipt, or reviewer identity was
created. First Correct Action, Decision Preservation, Wrong-State Admission, stale/wrong-worktree
rejection, forget Gap, and qualification Provider cost remain unmeasured.

## Gate and release disposition

Implementation tests and development diagnostics are separate from qualification and Core gate
execution. Every required Core gate in active classification v5 remains `not_executed`, including
`canonical_integrity`, `migration_recovery`, `secret_host_isolation`, `bounded_context`,
`legal_evidence`, `source_citation_locator`, `scale_performance`, `supported_platforms`,
`reproducible_supply_chain`, `human_gold_isolation`, `codex`, `selective_forget`, and `opencode`.
Optional capability and competitive-claim rows are not claimed.

The final booleans are:

```text
release_ready=false
claim_eligible=false
```

Passing either development diagnostic cannot change those values. The next admissible dependency
is an independently authored, repository-external Human Gold artifact bound to the exact task
cases, final clean candidate wheel, and current v2 contract, plus a different independent blind
reviewer. Only after that dependency exists may the six qualification tasks run; all remaining
legal, Context, scale, three-OS, SBOM, provenance, signature, and public-redownload gates still
apply.

## Verification

The implementation and retained reports were checked with:

```text
uv lock --check
uv run pytest <focused Host/receipt/Gold tests>
uv run ruff check .
git diff --check
uv run pytest
```

Final full-suite result: `1697 passed, 6 skipped in 508.63s`. The six skips are unchanged
non-results: native Windows ACLs, native Windows junctions, unavailable exact historical v0.6
wheel, the 10,000- and 100,000-Statement full stress lanes, and the 500/5,000 relation-edge bulk
fixture.
