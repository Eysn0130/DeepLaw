# DeepLaw v0.13 real Codex blind-execution disposition

Status: **not_executed**. This is the required negative report; it is not a deterministic
substitute, a model run, or a release acceptance result.

## Required gate

The v0.13 GA gate requires all of the following in one evidence package:

1. an exact Codex host and model identity;
2. a repository-external blind corpus whose bytes are unavailable to the compiler before run;
3. a human-confirmed Gold manifest and scorer isolated from the compiling Agent;
4. three independent executions with exact prompts, tool permissions, budgets and environment;
5. compiler isolation and evaluator read-only receipts;
6. zero invented source, invalid quote/locator, unsupported factual statement, Authority elevation,
   unauthorized mutation, restricted disclosure, silent fallback, stale admission and false
   completeness hard failures.

No such corpus/Gold/reviewer package was supplied or discovered in the authorized repository. The
source-free deterministic Semantic fixtures are public development fixtures and therefore cannot
be relabelled blind. The current task itself is also not a valid blind run because the Agent can
read the implementation, tests, prompt and fixture labels.

## Bound local tooling

| Item | Exact identity |
| --- | --- |
| Host runner | `benchmarks/hosts/run_semantic_host_harness.py` |
| Runner SHA-256 | `d5fde3906a8d28e612778f57ff766523e6cae5a6ec331626ded81686bb2f5858` |
| Report contract | `deeplaw.real-semantic-host-report/v2` |
| Contract SHA-256 | `9bba9bb4f01e5493531180bb2e47c831a614d42e9eb8d9886d6c2d37ad7cb1bd` |
| Existing candidate Gold SHA-256 | `d3e85a1233ef2acccabf279dd7955733eddb3cfe53c5990c5ed42c50236386c3` |
| Existing freeze SHA-256 | `3682f30716c8a0ead139aa09e41dcd715783648c17948b0aae63daffeb2edb67` |

The existing runner is retained for an externally prepared real-host package. Its current report
contract predates Profile v3, so a formal v0.13 run must also bind the v3 Profile, Statement
Evidence and Query Plan v6 artifacts; merely executing the old command would not close G01-G03.

## Exact future command shape

```bash
uv run --frozen python -m benchmarks.hosts.run_semantic_host_harness \
  --host codex \
  --host-version <exact-version> \
  --model-identity <exact-model> \
  --grant-id <owner-created-bounded-grant> \
  --gold <external-human-confirmed-gold.json> \
  --corpus <external-blind-corpus-directory> \
  --vault <new-empty-temporary-vault> \
  --baseline-query-vault <new-empty-baseline-vault> \
  --command <exact-codex-command> \
  --execute \
  --output <run-1.json>
```

The same frozen inputs must be run three times and evaluated outside the compiler environment.
Placeholder values above are intentionally unresolved; running against repository fixtures or
inventing a reviewer would invalidate the gate.

## Decision

`real_codex_blind_execution=not_executed`, `claim_eligible=false`, and
`competitive_claim_eligible=false`. G01, G02 and the real-model portion of G03 remain unmet, so
v0.13.0 GA is forbidden.
