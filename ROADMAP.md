# DeepLaw roadmap

Status: active roadmap for `main`, updated 2026-07-27. Implemented behavior is defined by
`src/deeplaw`, tests, contracts, and the dependency lock—not by this file.

## Current release boundary

The v0.6.0 code line has a working local Knowledge Asset lifecycle, isolated Legal Pack,
read-only MCP plugins, logical source versions, exact review manifests, local Review Receipts,
Task Run Receipts, structured feedback, and deterministic replay. There are no known open P0
implementation defects after the current synthetic regression suite.

External evidence remains `pending_external_execution`. No cross-system leadership statement is
permitted until the frozen protocol receives secret held-out results and independent signatures.

## Next release blockers

| Priority | Work | Acceptance gate |
| --- | --- | --- |
| P1 | Native Windows Vault ACL verification | Real Windows test proves owner SID and rejects writable `Users`/`Everyone`; `knowledge doctor --permissions` can return `verified` |
| P1 | Fresh-install operating-system matrix | Clean wheel install and first Capsule pass on Linux, macOS, and Windows without repository-relative files |
| P1 | Candidate commit freeze | The refreshed source/lock snapshot, reproducible local wheel/sdist hashes, CLI acceptance, and `claim_eligible=false` diagnostic are rebound to the final committed candidate |

Until those gates pass, Windows owner-only isolation and a final cross-platform release candidate
must not be marked supported.

## Planned product work

### P2 — control plane and source understanding

- Native Source Adapter contract and richer Source IR for DOCX headings/tables/footnotes, PDF
  layout hierarchy, code symbols, JSON/YAML paths, CSV/Excel cells, SQL statements, conversation
  turns, tool executions, and Git objects.
- URL and Git adapters with offline snapshots, origin commitments, and explicit network policy.
- Resumable background ingest/update jobs with lease, retry, cancel, crash recovery, and JSONL
  progress events.
- General-purpose snapshot, restore, and garbage collection commands beyond the implemented
  migration-specific verified backup and rollback flow.
- Shell completion and command-specific tables beyond the stable generic human output mode.
- Review edit/split/merge workflows, richer risk/sensitivity/extractor/time filters, and signed
  team receipts built on the same service contracts as CLI/MCP.
- A physically isolated, append-only Proposal Inbox/Outbox for bounded `.dlproposal`,
  `.dlfeedback`, `.dlrun`, and `.dleval` artifacts. Import must remain operator-only and must
  never become an MCP write path.
- Generic Vault/Source Admission/Export/Retention policies so consumer-specific restrictions are
  policy profiles rather than permanent product-name checks in the core.

### P2 — retrieval and human projection

- Knowledge Duties for constraints, decisions, procedures, lessons, changes, exceptions,
  conflicts, open questions, and evidence gaps.
- Tokenizer-profile-aware Capsule accounting alongside the existing character and serialized
  payload hard limits; estimates must remain labeled when exact tokenization is unavailable.
- Deterministic exact/title/lexical/semantic/hierarchy/relation candidate fusion with source
  diversity and operator-only explain traces.
- Default-path promotion only after held-out task-success, noise, provenance, lifecycle,
  poisoning, latency, and cost gates all pass.
- Source/review/Capsule/feedback projection pages and JSON Canvas; any future Obsidian reverse
  sync must create proposals, never overwrite active Assets.

### P2 — trustworthy sharing

- `.dlk` publisher identity, Ed25519 signature, trust roots, rotation, revocation, sequence, and
  transparency checkpoint. Until then every import remains untrusted quarantine.
- Signed Review Receipts and team review policy. The current local v1 receipt intentionally has
  `signature=null`.
- Explicit Vault Registry and allowlisted federation. DeepLaw will not add an implicit
  “search every Vault” operation.

## Longer-term work

P3 includes authenticated team services, multi-tenant RBAC, encrypted remote storage,
distributed indexes, multimodal adapters, and a cross-organization Knowledge Asset exchange.
These are not current product capabilities and must not weaken local source, review, lifecycle,
or Agent read-only invariants.

## External dependencies

- Two genuinely independent evaluator organizations must commit secret data and baseline
  configurations before receiving the candidate artifact.
- Each evaluator must run the candidate and preregistered baselines, retain per-case failures and
  resources, and sign the complete suite manifest with its own key.
- The development team cannot manufacture those organizations, hidden data, signatures, or
  independence. The machine claim gate remains closed until authentic evidence is returned.
