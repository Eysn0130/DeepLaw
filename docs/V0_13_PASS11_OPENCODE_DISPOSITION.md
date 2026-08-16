# DeepLaw v0.13 Pass 11 OpenCode continuity disposition

Status: **one failed source-candidate workflow; continuity not qualified** (2026-08-11).

This is retained failure evidence, not qualification, a competitive claim, or release evidence.
The exact candidate observation, evaluator-only result, sanitized JSONL, closed MCP environment
receipt, isolated configuration, and artifact manifest are retained under
[`../benchmarks/hosts/evidence/pass11-opencode-continuity-2026-08-11/`](../benchmarks/hosts/evidence/pass11-opencode-continuity-2026-08-11/).
No raw Host transcript, reasoning text, account value, credential value, user configuration, Secret,
absolute path, or retained Host session state is included.

## Frozen construction facts

- Host: official `opencode-ai@1.18.16`; source tag commit
  `a3647eb025c7615159d417dcc49fc39fdaeba65b`; package tarball SHA-256
  `1e0ac00a7dafd5e7c22d468ce7e088ae329dc02abb48b52581cf1c63fb2c3ffd`;
  platform binary SHA-256
  `a41776bf64c75786d6baf531b840ffb873c090d7c44793ae2dd4b1896de56a1f`.
- Model/variant: `deepseek/deepseek-v4-flash` / `max`.
- DeepLaw candidate: commit `ab5d43c14370e51fbcc5dcd996ad1c159b45d167`, tree
  `aa0af3bbd4685b08cddc11490c3f14703f2f9152`, package `0.12.0`.
- Exact wheel: `deeplaw-0.12.0-py3-none-any.whl`; SHA-256
  `66b5beb130a0b9c23b23ad05edf4eedc1170c49b8698d01d0874d4300e93518e`.
- OpenCode was installed outside the repository into an isolated temporary prefix. Its `HOME`,
  XDG roots, configuration, and state were isolated; share, snapshots, autoupdate, plugins,
  subagents, shell/edit/write/web, and all tools except the exact read-only
  `deeplaw_knowledge_knowledge_support` leaf were denied.
- A bounded parser selected only `DEEPSEEK_API_KEY` from the repository credential file. The value,
  length, prefix, suffix, hash, and auth payload were not recorded. The MCP child received a
  separate closed environment without the provider credential.

## Executed result

One frozen natural-language owner task was sent to OpenCode. The Host process exited `0` and
reported:

| Observation | Value |
| --- | ---: |
| Input tokens | 2,476 |
| Cached input tokens | 0 |
| Cache-write tokens | 0 |
| Output tokens | 175 |
| Reasoning tokens | 958 |
| Total tokens | 3,609 |
| Provider-reported cost | USD 0.00066388 |
| Latency | 12,542 ms |
| Provider bytes | 0 |

The sanitized event stream contains only `step_start`, `text`, and `step_finish`. It contains no
`tool_use`; consequently `tool_calls=[]`, no Provider Capsule exists, and no neutral structured
Host output was proved. The candidate run therefore failed with
`single_knowledge_support_call_not_proven`, `provider_capsule_not_proven_clean`, and
`neutral_host_output_missing`.

The security boundary did hold: the Host and MCP child environments were closed, the MCP receipt
used the exact `runtime/bin/python` and `runtime/bin/deeplaw` prefix, Host state was removed after
execution, the Ledger head was unchanged, usage was present, and the retained artifacts report no
Secret, absolute-path, or internal-surface leak. These facts do not convert the task failure into a
qualification pass.

## Independent evaluator result

The evaluator-only scorer ran after Host execution against the separated development Gold. Because
the actual run produced neither a Provider Capsule nor neutral Host output, all required quality
metrics are `null` with `scoring_status=not_scored`, not zero. The evaluator hard failures are:

- `candidate_run_failed`;
- `host_output_missing`;
- `provider_capsule_missing`.

First Correct Action, Decision Preservation, Wrong-State Admission, Useful Context Recall,
RelevantChars/ContextChars, Duty Coverage, Gap Correctness, Duplicate Evidence, Redundancy, and
Distractor Answer Delta therefore establish no claim.

## Product and release disposition

The same frozen task is not rerun to manufacture continuity evidence. Resume, fork, compaction,
concurrent worktree, forget, conflict recovery, second and third distinct OpenCode workflows,
qualification holdout, and final blind remain `not_executed`. The deterministic preflight's correct
state and wrong/stale route admission checks remain development harness evidence only.

This result does not admit a narrow operation profile, Host support claim, version change, tag, or
release action. A future attempt requires a separately frozen, genuinely distinct workflow and may
not reuse this failed task as repeated continuity evidence.

```text
release_gate_passed=false
claim_eligible=false
competitive_claim_eligible=false
release_ready=false
package_version=0.12.0
source_candidate_remains_not_released
```
