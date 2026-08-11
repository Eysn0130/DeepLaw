# DeepLaw v0.13 Pass 11 Codex token-attribution disposition

Status: **three failed/partial source-candidate workflows; no profile admitted** (2026-08-11).

This is development failure evidence, not qualification, a competitive claim, or release evidence.
The three authorized Codex workflows are exhausted. Their candidate observations, sanitized JSONL,
closed MCP environment receipts, and post-run evaluator reports are retained under
[`../benchmarks/hosts/evidence/pass11-token-attribution-2026-08-11/`](../benchmarks/hosts/evidence/pass11-token-attribution-2026-08-11/).
No raw Host result, transcript, hidden reasoning, account value, environment value, credential,
Secret, or absolute path is retained.

## Frozen construction facts

- Host: `codex-cli 0.147.0-alpha.1.2`; binary SHA-256
  `9f6748b4ab10ffc92c28b9ccedae89e61a302bbc011df7d276ee38f55906e481`.
- Model/reasoning: `gpt-5.6-luna` / `max`, using the Host's existing ChatGPT login without reading
  an auth file or token.
- Exact wheel: `deeplaw-0.12.0-py3-none-any.whl`; SHA-256
  `9e146069fba536fb5db47ea8a687aef9bb96dc2831b297768ab14e80d9aa764d`.
- A: no MCP/dynamic tool. B: one Context-only dynamic schema. C: current 19-operation dynamic
  schema. D: exact-wheel `knowledge_support` MCP through a verified closed environment.
- Context-only advertised schema: 3,797 bytes. Full dynamic schema: 24,713 bytes. Exact MCP tool
  envelope: 26,157 bytes. The full-minus-Context advertised delta is 20,916 bytes.
- All conditions reported provider usage and kept the Ledger unchanged. All D attempts produced the
  exact `runtime/bin/python`, `runtime/bin/deeplaw`, `knowledge mcp --stdio --vault vault` receipt.

## Executed results

| Attempt | Commit / tree | Condition status A/B/C/D | C−B input tokens | Current disposition |
| --- | --- | --- | ---: | --- |
| 1 | `275594213b3c` / `eccde0c00f2e` | pass / pass / fail / fail | -795 | partial candidate; C dynamic call and D MCP result failed |
| 2 | `6e9b16a5ffd3` / `e3261365de8c` | pass / fail / fail / fail | +1,450 | partial candidate; B missing required input, C `oneOf` mismatch, D failed |
| 3 | `7aa391ac50ab` / `a48fbf4dbd22` | pass* / fail / fail / fail | -286 | partial candidate; same B/C/D failures; no further retry authorized |

The asterisk records a harness defect rather than upgrading attempt 3: A emitted an App Server
`error` notification and 1,271 stderr bytes, but that runner revision did not yet classify either as
a condition failure. The current runner treats both as hard failures. The retained attempt is not
rewritten.

The C−B delta is directionally unstable across three workflows. Attempt 2 exceeds the pre-frozen
absolute and relative token thresholds, but B, C, and D all failed their tool conditions. The
protocol requires successful controlled conditions, so neither that delta nor the schema byte delta
admits a product profile. `profile_change_admitted=false` in every retained observation.

## Independent evaluator result

The evaluator-only scorer ran after candidate execution. Of 12 conditions, only attempt 1 B had a
Provider Capsule and was scoreable:

| Metric | Attempt 1 B |
| --- | ---: |
| First Correct Action | 0.0 |
| Decision Preservation | 0.5 |
| Wrong-State Admission | 0 |
| Useful Context Recall | 0.666667 |
| RelevantChars / ContextChars | 0.222018 |
| Duty Coverage | 0.333333 |
| Gap Correctness | 1.0 |
| Duplicate Evidence | 0 |
| Redundancy | 0.0 |

The other 11 conditions remain `not_scored`, not zero, because no Provider Capsule was available.
Distractor Answer Delta was not executed. No Human Gold, qualification holdout, or final blind was
used or claimed.

## Product and release disposition

- Do not add an operation profile from these results. The existing full operator surface remains
  unchanged; no default narrow profile is claimed.
- The 19-operation App Server/MCP task-call reliability failure is a release blocker and requires a
  newly frozen, independently justified protocol before another real Codex run.
- The requested new/resume/fork/compaction/concurrent-worktree/stale/wrong-route/forget/conflict
  continuity workflow was not executed because all three authorized Codex workflows were consumed
  by the failed token-attribution gate.
- OpenCode/DeepSeek, Living Wiki/Desktop, professional Evidence, Legal Human Gold, final artifact,
  cross-platform, signature, public redownload, and final-blind gates remain separate pending or
  blocked work.

```text
release_gate_passed=false
claim_eligible=false
competitive_claim_eligible=false
profile_change_admitted=false
release_ready=false
package_version=0.12.0
source_candidate_remains_not_released
```
