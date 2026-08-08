# DeepLaw v0.13 cross-thread continuity development report

Status: **partial development evidence**. This is a deterministic local comparison, not Human
Gold, a real Host result, or a claim that cross-thread continuity is solved.

Implementation freeze: `450e79e66a30399385ab4afd2d137414e78b7119`.

## Evaluation boundary

Thread A contains one structured Task Checkpoint with the current goal, confirmed decisions,
constraints, verified fact, open gaps, next actions, and stable commit reference. It also creates a
superseded Revision and an unrelated checkpoint. Thread B contains only the cold-start task.

The Host-only lane receives Thread B only. The Host+DeepLaw lane stores Thread A through an
owner-granted `knowledge_sink`, closes that write phase, opens a new read-only `KnowledgeOS`, and
uses default v6 `knowledge context` with Thread B's task text and no `query_target`. The candidate
keeps write-time identities only for post-read scoring and never supplies them to Context. Neither
lane calls a model.

The candidate process is denied network access, Gold, and the scorer. The scorer is separately
denied the source corpus and candidate implementation.

Repository-external development inputs were frozen before the final run:

| Input | SHA-256 | Status |
| --- | --- | --- |
| Thread A | `4b582108c2ef4cd7bb48e8c5640072016a3ed1c845700cd11e81feaeac2278e4` | development source |
| Thread B only | `3b4743065549cf4488221c000ee9b4b2976199596f4384fd079aa0b187bd296a` | development source |
| Owner-task Gold | `434d167b8f88b1741f3ab24536ac875a8a13c48ff6e64a0d59c64584b732ee81` | second human review not executed |

## Result

| Metric | Host-only | Host+DeepLaw | Frozen gate |
| --- | ---: | ---: | ---: |
| First Correct Action | 0.0 | 1.0 | 1.0 |
| Decision Preservation | 0.0 | 1.0 | 1.0 |
| Stale Decision Inclusion | 0.0 | 0.0 | maximum 0.0 |
| Useful Context Recall | 0.0 | 1.0 | minimum 0.9 |
| RelevantChars / ContextChars | 0.0 | **0.760628** | minimum **0.8** |
| False Memory Admission | 0.0 | 0.0 | maximum 0.0 |
| Contradiction/Gap Coverage | 0.0 | 1.0 | minimum 1.0 |
| Provider bytes | 0 | 2,697 | maximum 65,536 |
| Local latency | 0.154 ms | 813.500 ms | maximum 2,000 ms |

The current Revision was selected; the superseded Revision and distractor were not selected. The
read phase did not change the audit head. Python and MCP expose the same bounded Statement
semantics, although independently executed receipts retain their own receipt identities.

The development gate is **not passed** because `RelevantChars / ContextChars=0.760628` is below
the frozen `0.8` threshold. The score definition counts the frozen Gold value strings against the
entire selected checkpoint, including structural labels and delimiters. The result was not retuned
after observation.

Final-run artifact hashes:

| Artifact | SHA-256 |
| --- | --- |
| Host-only candidate | `990d717ed842cf4e1de1c451a8293e9a0462c01fe5f247f42c2f09ca7c941545` |
| Host+DeepLaw candidate | `97924092dc6536573b5354b045e6f9beaeb9146f083af6aefcd3086702c3e319` |
| Deterministic score | `d66cb35033b4a9b122c3f83fb85aa3f6aebec51c0ece96010413f9f0613f5b46` |

## Interpretation

The comparison proves a narrow implementation fact: the current, bounded Task Checkpoint can
survive a cold local read and yields a correct next action where the Thread-B-only deterministic
lane has no state. It does not prove model behavior, user-perceived continuity, answer quality, or
cross-host portability. The Host-only baseline is deliberately model-free and therefore cannot be
used as a competitive baseline.

Not executed: independent Human Gold review, real Codex x3, model answer scoring, multi-case blind
holdout, OpenCode/DeepSeek, cross-platform runs, and reproducible release artifacts.

`development_thresholds_passed=false`

`release_gate_passed=false`

`claim_eligible=false`

`competitive_claim_eligible=false`
