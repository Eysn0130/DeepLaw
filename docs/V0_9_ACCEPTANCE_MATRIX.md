# DeepLaw v0.9 acceptance matrix

Status: **release contract**, 2026-07-30. This matrix evaluates the supplied DeepLaw project brief
and its phase order against runtime code, closed contracts, migrations, recovery, tests, and
release gates. It does not convert externally pending evidence into a completed result.

Machine release decision:

```text
commercial_release_eligible=true
competitive_claim_eligible=false
```

The first flag means the software and supply-chain release gates are required to pass for the exact
tag. The second remains false because real three-host model tasks, actual named competitor runs,
evaluator-secret held-outs, and two independent evaluator signatures are external facts that do not
yet exist.

## 0.8 Autonomous Knowledge Core

| Requirement | Acceptance criterion | Runtime/contract evidence | Status |
| --- | --- | --- | --- |
| Markdown Knowledge Revision | Stable ID survives rename; exact UTF-8 Markdown bytes are hashed, stored in CAS, and paired with immutable Ledger governance | `knowledge_autonomy.py`; `knowledge-object.v2`, `knowledge-revision.v2`; reconcile/tamper tests | Passed |
| Knowledge Commit Coordinator | One domain service performs capability/schema/provenance checks, CAS publication, Ledger transaction, audit, recovery intent, and materialization; failure cannot partially switch current state | `AutonomousKnowledgeStore`; canonical lease, staging/recovery, CAS tests | Passed |
| `knowledge_sink` | Separate process and leaf; owner token, writer, exact scope, max sensitivity, operation allowlist, size/rate/capacity, idempotency, audit; no source/Legal/authority/admin write | Sink input/output v2; stdio MCP and grant tests | Passed |
| Agent Memory automatic activation | Policy-admitted Agent knowledge is immediately active; risk enters quarantine; activation never means verified or official | activation contract, authority/injection/provenance tests | Passed |
| Authority and epistemic state | Origin, verification, lifecycle, scope/sensitivity, provenance, valid/transaction time, and epistemic state remain independent | Knowledge Revision schema, Ledger checks, retrieval assertions | Passed |
| Typed objects | Claim, Concept, Entity, Event plus Decision, Procedure, Experience, Preference, Comparison, Synthesis, Memory, and Skill have closed operation/kind contracts | Sink v2 one-of branches and typed mutation tests | Passed |
| Watcher | Explicit bounded foreground loop calls the same reconcile/coordinator path and drains pending derived maintenance | CLI Watcher implementation and real cycle test | Passed |
| Remove universal Agent review | Ordinary Agent-derived writes bypass the legacy proposal queue; source-derived and external import review remains isolated | autonomous/legacy partitions and migration tests | Passed |
| Legal Pack update protocol | Exact-byte Ed25519 verification before parse/download, public trust root, revocation, catalog identity, monotonic sequence, rollback protection, immutable release, atomic pointer | official catalog/update tests and security contracts | Passed |

Additional 0.8 gates pass for immutable Run Records, bounded capture, exact dedup receipts,
high-precision contradiction preservation, alias/same-as/merge/split decisions, evidence-required
bitemporal relations, compare-and-swap, explicit conflicts, TTL, forget, owner GC, snapshot/restore,
and v0.7 rollback. Memory consolidation validates its relation sub-capability and commits every
evidence-bound lineage edge before archiving an input.

## 0.9 Living Wiki and Knowledge Intelligence

| Requirement | Acceptance criterion | Runtime/contract evidence | Status |
| --- | --- | --- | --- |
| Living Overview | Rebuild produces hash-bound Overview/index views marked derived and authority-free | derived manifest and Wiki tests | Passed |
| Entity/Concept pages | Typed canonical objects project to bounded pages and backlinks without replacing source evidence | Wiki generator and typed-page tests | Passed |
| Synthesis | Dedicated capability and kind; normal `remember` cannot smuggle it | Sink schema and operation tests | Passed |
| Semantic Lint | Detects provenance, missing evidence, uncompiled relation hints, duplicates, conflicts, links, orphan, and alias issues; scope/sensitivity and scan/output limits apply | Lint implementation and boundary tests | Passed |
| Community detection | Deterministic weighted local communities are disposable and never affect Authority | graph/community manifest tests | Passed |
| Gap discovery | Read-only bounded report; CLI/MCP request boundary cannot expose another scope or sensitivity | output v3 and gap isolation regression | Passed |
| Memory consolidation | New summary revision and evidence relations commit before input archival; deterministic retry/recovery | consolidation saga tests | Passed |
| Obsidian/Tolaria interoperability | Files-first Markdown/YAML/Wikilink, identity-safe rename/move, external edit reconcile, conflict preservation, JSON Canvas | reconcile/Watcher/Canvas tests | Passed |
| Skill Factory | Checkable Procedure steps compile to a governed draft; vague steps abstain; promotion needs owner-granted user/external evaluation | Skill schema/factory/promotion tests | Passed |

Retrieval acceptance additionally requires governance filters before bounded FTS/dense/temporal/
graph candidate windows, a second admission pass before reranking, evidence-bound graph traversal,
explicit resource gaps, item/character/token/source/hop/provider limits, historical lexical reads,
stale-index rejection, and a canonical fallback. Regression tests cover wrong-tag crowding,
scope/sensitivity isolation, relation evidence, and identity ambiguity.

## Storage, recovery, and isolation

| Invariant | Acceptance |
| --- | --- |
| Two semantic planes | Evidence bytes and Agent knowledge retain independent identity; Ledger/index do not form Authority |
| No dual-write fiction | Registered Markdown Object plus Ledger record is the revision; workspace is recoverable materialization |
| No CRDT or full Event Sourcing | Single writer/base revision/CAS/conflict plus current tables and append-only audit chain |
| Derived deletion safety | Removing FTS/vector/graph/community/Wiki/Canvas loses no canonical source, knowledge, or governance |
| Query/write separation | `knowledge_support` and `law_support` are read-only; sink is a separate opt-in process and grant |
| Legal partition isolation | Official, user-private, and Agent interpretation retain distinct origin, release/receipt, temporal state, and `legal_authority` |
| Owner deletion | Forgetting and private-source deletion remove eligibility/content under explicit policy without treating immutability as a ban on owner erasure |
| Snapshot/rollback | Consistent Ledger, Markdown, CAS, evidence, staging, manifest, and capability state verify before restore; derived state rebuilds |

## 1.0 Quality and Superiority Closure

| Requirement | Repository-controlled gate | External fact required | Status |
| --- | --- | --- | --- |
| Chinese/English/code/legal/long-document Gold | Real repository development set, frozen hashes and thresholds | evaluator-owned held-out labels | Development gate passed; external pending |
| Default local Dense/Reranker | Offline fixed identity, bytes/audit manifest, stale rejection, fair mode runner | optional independent replication | Passed |
| Typed Compiler quality | Closed scorer for precision/recall, hallucination/support, span, duplicate, review, synthesis | real external compiler outputs | Scorer passed; external pending |
| Codex/Claude/OpenCode real tasks | Thin adapters, plugin validation, no-model lifecycle and MCP handshake | model/session task outcomes on all three hosts | Pending external |
| Named-system comparison | 17-system frozen registry, plan/receipt/raw/resource/collection validation | actual competitor executions under common environment | Pending external |
| Secret held-out | Commitment, inventory, statistics, and anti-leak verifier | evaluator-secret corpus and labels | Pending external |
| Independent signatures | Portable kit and detached Ed25519 verification | two independent organizations and trusted keys | Pending external |

No unit test, synthetic fixture, feature matrix, self-evaluation, or release signature satisfies the
external column. The release manifest must list all four missing evidence classes and remain fail
closed for a superiority claim.

## Required release gates

The exact tagged commit must pass:

```bash
uv lock --check
uv run pytest
uv run ruff check .
git diff --check
```

The reusable release workflow additionally requires clean and byte-identical wheel/sdist builds,
fresh-wheel tests, Linux/macOS/Windows mandatory suites with zero mandatory skips, native Windows
ACL/reparse gates, v0.6.0 upgrade and rollback lifecycle, no-model three-host lifecycle, networkless
non-root OCI verification, dependency audits, OpenVEX, SBOM, license inventory, checksums, OIDC
signatures, GitHub provenance/SBOM attestations, and public download/reinstall verification.

Detailed requirement-to-code mapping is in
[`VNEXT_REQUIREMENT_TRACEABILITY.md`](VNEXT_REQUIREMENT_TRACEABILITY.md). The external protocol is
[`EXTERNAL_BENCHMARK_PROTOCOL.md`](EXTERNAL_BENCHMARK_PROTOCOL.md).
