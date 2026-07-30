# Living Wiki Compiler acceptance report

Report date: 2026-07-30
Candidate scope: current `codex/living-wiki-compiler` working tree
Package version: unchanged at `0.10.0`
Release decision: **not yet eligible for a new release**

Status vocabulary:

- **verified**: executable local evidence exists and passed for this working tree;
- **implemented / final gate pending**: implementation and focused evidence pass; the final
  repository-wide command is recorded below when complete;
- **partial**: a meaningful subset is implemented/tested but the exact requirement is not fully
  proven;
- **not executed**: no result is claimed.

## 48 hard acceptance items

| # | Requirement | Status | Evidence or limitation |
| ---: | --- | --- | --- |
| 1 | PDF, DOCX, PPTX, XLSX, EPUB and Markdown preserve original bytes and create Source Revisions | verified by existing source suite | source adapter, extraction, stable first-party media identity and immutable-evidence tests; no format was reimplemented in the compiler |
| 2 | Compilation uses persisted Source IR without needless original reparse | verified | Run binds `source_ir_compilation_id` and `source_ir_sha256`; fake-Agent E2E reads packets from persisted fragments |
| 3 | Every active Source Revision has a status page | verified | rich projection enumerates admitted active revisions; projection tests inspect source pages |
| 4 | Uncompiled source is explicit and has no invented summary | verified | source page and `list_uncompiled` tests |
| 5 | Same Source Revision/profile is idempotent | verified | deterministic Run unique key and replay assertions |
| 6 | More than 32 objects can stage in batches and commit as one set | verified | 40-object multi-packet test; deterministic 35-object fake-Agent test |
| 7 | Crash at every Run stage can recover or safely abort | verified | restart/fault evidence covers planned, staging, validating, ready-to-commit, committed, projection-pending and succeeded; pre-commit rollback, post-commit recovery, projection retry and pre-commit abort are explicit |
| 8 | Staging is invisible before canonical commit | verified | recall and object inventory remain unchanged across staged batches |
| 9 | Knowledge and relation revisions become visible together | verified | one transaction publishes both output kinds; relation integration test |
| 10 | Projection failure does not corrupt canonical knowledge | verified | injected projection failure leaves `projection_pending` and canonical receipt |
| 11 | Projection retry completes correctly | verified | `resume(project=True)` recovery test |
| 12 | Aliases do not create duplicate active canonical identities | verified | exact semantic identity is revised; normalized alias collision fails closed |
| 13 | Ambiguous entities are not auto-merged | verified | `possible_duplicate` test preserves two IDs and a visible ambiguous candidate |
| 14 | Merge and split history is auditable | verified by existing autonomous suite | immutable `knowledge_identity_resolved` events and integrity replay |
| 15 | A new source augments an existing Concept rather than duplicating it | verified by shared identity path | deterministic exact semantic-key create resolves to revise; Entity test exercises the same resolver |
| 16 | A successor affects only dependent objects | verified | changed/unchanged/moved structural diff and selective freshness test |
| 17 | Withdrawal removes related knowledge from current admission | verified | removal, recall and rebuilt-Wiki test |
| 18 | Multi-source Synthesis becomes stale after one input changes | verified | complete Synthesis input-set and transitive freshness test |
| 19 | A Claim with remaining evidence is not wholly deleted | verified with bounded semantics | revisions/history are never deleted; worst dependency marks current revision stale rather than erasing it |
| 20 | Overview and index have distinct responsibilities | verified | projection test and explicit missing-overview-Synthesis gap |
| 21 | Entity, Concept, Claim, Procedure and Synthesis pages are rich | verified | typed renderers and projection assertions |
| 22 | Relation targets show title and stable ID | verified | rich relation renderer test |
| 23 | More than 300 objects remain discoverable through shards | verified | 305-object projection test, five exact-fragment shards and full manifest inventory |
| 24 | Deleting derived FTS/dense/Wiki/graph/Canvas/cache permits full rebuild | verified by existing rebuild suite plus new projection | all layers are audit-bound derived state |
| 25 | Rebuild does not call a model | verified | deterministic local builder; no model/network dependency in rebuild path |
| 26 | Default answer query prefers compiled knowledge | verified | purpose-aware read-only query test |
| 27 | Verify, quote and legal prefer evidence | verified | versioned purpose-policy contract and tests |
| 28 | Source fallback is visible in the Query Plan | verified | retrieval contract exposes channel order, fallback reason and counts |
| 29 | Uncompiled source and stale knowledge create explicit gaps | verified | query and Wiki compilation-gap output |
| 30 | Key answers drill down to Source Revision, fragment and locator | verified | compiled result retains exact source refs and quote digest |
| 31 | Query is read-only | verified | event count remains unchanged |
| 32 | Backfill begins as a draft | verified | draft path/table and E2E test |
| 33 | Non-durable/non-reusable/non-novel answer cannot promote | verified | validation gate negative paths |
| 34 | Promoted backfill stays Agent-derived and non-legal | verified | promotion receipt and stored revision assertions |
| 35 | Authoritative legal originals cannot be mutated by an Agent | verified by existing Legal Pack isolation suite | compiler rejects Authority elevation and uses a separate general-knowledge store |
| 36 | Agent cannot self-grant | verified | no MCP grant creation operation; owner CLI capability path only |
| 37 | Compiler grant cannot call ordinary unauthorized mutation | verified | exact operation allowlist and Sink negative test |
| 38 | Arbitrary path, secret, absolute path and case data are rejected | verified | compiler/bridge/Sink contract and negative tests |
| 39 | Python API, CLI and MCP produce one domain result | verified | same Run exercised across all three surfaces |
| 40 | Codex, Claude Code and OpenCode no-model loop passes | not executed for changed tree | shared Skill, manifests, tool schemas and lifecycle harness are present; this machine does not have all three pinned host CLIs, so no pass is inferred |
| 41 | Deterministic fake Agent performs a real compile | verified | 35 objects, seven packets, commit, projection, verify and compiled query |
| 42 | Unrun real hosts are reported `not_executed` | verified | opt-in harness contract/test |
| 43 | Fresh Vault, old migration, snapshot, restore and rollback pass | verified | compiler migration round-trip integration test |
| 44 | Linux, macOS and Windows gates pass | not executed for changed tree | local macOS evidence only; prior release CI cannot prove this branch |
| 45 | Installed wheel executes the core loop | verified on local macOS | exact local wheel/fresh-environment result is recorded in the final verification section |
| 46 | Full pytest, Ruff and diff check pass | verified on local macOS | exact commands/results are recorded below |
| 47 | Release report binds exact wheel, commit, schemas and migration | not satisfied | no release is declared; the feature branch commit and local wheel are not a published release manifest |
| 48 | Documentation does not claim unimplemented behavior | verified for working tree | current/target/released labels are separated; external/cross-platform/comparative gaps remain explicit |

## Local executable evidence

Focused implementation checks completed before the final repository gate:

```text
uv run pytest tests/test_source_compilation.py -q
24 passed

uv run python -m examples.living_wiki.run_demo --workspace <new-temp-dir>
status=succeeded; verification_valid=true; packet_count=2;
knowledge_revision_count=2; source_revision_count=1; canvas_count=5

JSON Schema Draft 2020-12 structural validation
137 schemas valid

compile-living-wiki Skill quick validation
valid

Python 3.11.15 focused compiler/editor suite
41 passed

Python 3.13.13 focused compiler/editor suite
41 passed
```

The final local commands and exact results are filled only after they have actually run:

```text
uv lock --check
Resolved 140 packages

uv run pytest
715 passed, 3 skipped in 139.27s

uv run ruff check .
All checks passed

git diff --check
passed

fresh wheel install and installed-package core loop
valid=true; compilation_status=succeeded; compilation_validation_valid=true;
compiled_query_hit=true; knowledge_verification_valid=true; capsule_valid=true;
review_receipt_valid=true; https_preflight_valid=true

deeplaw-0.10.0-py3-none-any.whl
sha256=e143f3b701019d52b8ca256ceb63bd25c8c36a4659eff43f17236b2b440f1a32
second clean build produced the same wheel SHA-256
```

## External evidence

The comparative protocol and fixtures are frozen but every named comparator remains
`not_executed`. Real Codex, Claude Code, OpenCode and optional Gemini tasks are also not executed
unless an authentic host command produces a new, host-bound, verified Run. Therefore:

```text
competitive_claim_eligible=false
```

No “best”, “leading”, “superior to Guanlan/Obsidian/Tolaria” or “SOTA” claim is supported.

## Release gate decision

The local Source-to-Knowledge functional loop is implemented. A new Living Wiki Compiler release
gate remains **NO** until at least items 40, 44 and 47 have the required exact evidence.
This does not block committing the implementation branch; it prevents mislabelling that branch as
a published, cross-platform, externally compared release.
