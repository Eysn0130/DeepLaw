# DeepLaw v0.13 real-host qualification disposition

Status: **Track A Codex not executed; Track B OpenCode/DeepSeek blocked and not executed**
(2026-08-08). Deterministic harness tests are not substituted for model, Human Gold or blind-host
evidence.

## Shared isolation gate

The real-host harnesses now use a closed environment allowlist and private per-invocation
HOME/XDG/temp/Codex/OpenCode directories. The old whole-environment inheritance was reproduced and
removed. The canary suite proves an ambient secret is absent from the fake Host, its normally
inherited fake MCP child, argv, prompt, stdout, stderr, report and artifacts. A Provider Secret is
not accepted through the shared Host/MCP environment at all.

| Bound artifact | SHA-256 |
| --- | --- |
| `benchmarks/hosts/run_semantic_host_harness.py` | `812b24547c7562f7f5c57de602bb07dd5f9f2680e623942863ecc691b975ce21` |
| `benchmarks/hosts/run_living_wiki_host_harness.py` | `582c9bf3a0e7cd50aa9106b4e2ef5c1ed70895084931caf26154d538b1a467b3` |
| `contracts/real-semantic-host-report.v2.schema.json` | `9bba9bb4f01e5493531180bb2e47c831a614d42e9eb8d9886d6c2d37ad7cb1bd` |

The detailed result and credential boundary are in
`docs/V0_13_CREDENTIAL_HOST_ISOLATION_REPORT.md`.

## Track A — isolated Codex

The local application contains `codex-cli 0.147.0-alpha.1.2`. It was inspected only for version;
no model invocation was made. The current Desktop authentication state is not an admissible
evaluation credential and was not copied or shared. The required evaluation-only API project/key,
repository-external qualification/final-blind corpus and Human-confirmed Gold were not provided.

| Requirement | Status |
| --- | --- |
| Exact candidate wheel mounted without repository source | `not_executed` |
| `codex exec --ephemeral` with isolated HOME/XDG/temp | `not_executed` |
| Exact GPT-5.6 Luna model identity | `not_executed` |
| Shell/edit/Web/extra MCP disabled | `not_executed` |
| Human Gold deterministic scoring | `not_executed` |
| Three independent invocations | `not_executed` |
| Secret-canary observation on real provider path | `not_executed` |

The retained command shape is:

```bash
uv run --frozen python -m benchmarks.hosts.run_semantic_host_harness \
  --host codex \
  --host-version <exact-pinned-version> \
  --model-identity <exact-gpt-5.6-luna-id> \
  --grant-id <owner-created-bounded-grant> \
  --gold <external-human-confirmed-gold.json> \
  --corpus <external-final-blind-corpus-directory> \
  --vault <new-empty-temporary-vault> \
  --baseline-query-vault <new-empty-baseline-vault> \
  --command <isolated-codex-exec-command> \
  --execute --output <run-1.json>
```

The same frozen candidate/protocol/corpus must be run three times. Placeholder inputs are
intentional hard prerequisites.

## Track B — isolated OpenCode with DeepSeek

`opencode` is not installed in the qualification environment. No previously exposed DeepSeek
credential was used, copied, printed, searched for or persisted. Owner-side revocation of that
credential is not observable from this repository, and no new repository-external owner-only
evaluation secret file was supplied. Therefore even provider preflight is blocked.

The future Track B run must additionally prove all of the following before its first model task:

- exact OpenCode version/hash and an isolated OS user or container;
- isolated HOME plus XDG_CONFIG_HOME/XDG_DATA_HOME/XDG_CACHE_HOME;
- captured resolved configuration showing no globally merged provider, plugin, MCP or tool state;
- sharing disabled, snapshots disabled, autoupdate disabled and subagent depth zero;
- no shell/edit/Web/arbitrary MCP; only exact DeepLaw read tools;
- egress restricted to `api.deepseek.com:443`;
- a read-only owner secret file referenced by OpenCode file configuration, never inherited by
  DeepLaw MCP;
- exact `deepseek-v4-flash` model identity, using an OpenAI-compatible custom provider only if the
  pinned OpenCode build does not expose that model;
- model-list, tool-call, JSON, timeout, rate-limit, receipt and secret-canary preflights;
- three independent runs scored by Human Gold and a deterministic evaluator.

Current official design references are the DeepSeek API updates/model documentation and OpenCode
provider/configuration documentation. Their mutable current behavior must be rechecked at the time
an exact pinned host is installed; this source-candidate report is not a substitute for that
capture.

## Decision

`real_codex_three_runs=not_executed`, `opencode_deepseek_three_runs=not_executed`,
`human_gold=review_pending`, `real_host_gate_passed=false`, `claim_eligible=false`, and
`competitive_claim_eligible=false`. The missing external authority cannot be repaired by using
Desktop credentials, repository development fixtures or model-generated Gold. No RC or GA is
authorized.
