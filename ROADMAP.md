# DeepLaw roadmap

Status: `v0.7.0` commercial GA, updated 2026-07-28. Runtime truth is
`src/deeplaw`, tests, contracts, and `uv.lock`; this file never upgrades a planned capability into
an implementation claim.

## Permanent product boundary

DeepLaw is a local, single-OS-user Agent Knowledge OS. This is the intended product, not a temporary
stage before a hosted service.

The roadmap does not include:

- multi-tenant SaaS;
- team RBAC or a remote control plane;
- a remote canonical database or distributed index;
- cross-organization Knowledge Marketplace;
- default upload, telemetry, or implicit web retrieval;
- Agent-driven automatic activation of memory;
- graph, embedding, model output, confidence, or rank becoming authority.

The separate general Knowledge OS and Chinese Legal Pack remain local, explicitly activated, and
reachable through different read-only MCP plugins.

## v0.7.0 commercial implementation

- Identity v2 with stable logical-source identity, immutable source/compilation/proposal/governance
  revisions, many-to-many references, lineage, temporal relation revisions, and selective
  forgetting.
- Multi-format Source Adapter, Source IR, Source Tree, deterministic-v2 compiler, explicit local
  and external compiler modes, resumable ingest/sync/watch jobs, and atomic source replacement.
- One-shot owner-only Source Snapshots for explicitly authorized public HTTPS and exact commits in
  existing local Git repositories, with origin commitments, bounded fetch/plumbing, no silent
  fallback, and the same proposal/review lifecycle.
- Evidence-Governed Retrieval Fabric with Query Plan, exact/BM25/tree/graph/temporal/feedback
  channels, optional Dense and pinned local reranker paths, rank fusion, admission, Knowledge
  Duties, source diversity, token budgets, Explain Trace, and Capsule verification.
- Golden CLI, curses Operator Workbench, rich Markdown/Obsidian/JSON Canvas projection, reverse
  edit-to-quarantine workflow, isolated Proposal Inbox, and source-bound Skill Factory.
- Snapshot/restore, GC/orphan detection, derived rebuild, migration/rollback, corruption doctor,
  backup validation, POSIX permissions, and native Windows ACL evaluation/hardening code.
- Named baseline registry and official-adapter protocol, Retrieval Fabric scale runner, a
  fail-closed content-addressed External Evaluator Kit freezer/verifier, dependency audits,
  OpenVEX enforcement, CycloneDX SBOM, license/package inventory,
  byte-reproducible wheel/sdist check, fresh-wheel verification, and signed/attested release
  workflow.

The signed commercial manifest and post-release report bind these items to exact release bytes.

## Competitive validation program

| Gate | Required evidence | Current state |
| --- | --- | --- |
| Commercial artifact freeze | Clean final commit, lock, contracts, identical wheel/sdist, OCI, SBOM, licenses, audits, OpenVEX, signatures and provenance | required commercial GA gate; published in the v0.7.0 manifest |
| Native Windows isolation | Real Windows run proves owner SID and rejects Users, Everyone, inherited broad grants, reparse points and junctions across Vault sources, model files, and index files | required commercial GA gate; the `windows-latest` report is published |
| OS install matrix | Clean install/upgrade/uninstall, CLI/MCP/migration/rollback/snapshot and mandatory tests on Linux, macOS and Windows with zero skips | required commercial GA gate; reports are published |
| Formal 100k support | Actual 100,000-Asset run records build/index cost, cold CLI/integrity, memory, database size, warm latency, quality, provenance, update, forgetting, and no-answer | 100k construction diagnostic passed the complete listed workload; clean frozen-candidate rerun remains pending |
| One-million diagnostic | Actual 1,000,000-Asset run with the same resource accounting; no extrapolated claim | construction diagnostic passed the complete workload and all three latency gates; clean frozen-candidate rerun remains pending |
| Named baseline execution | Every preregistered third-party system runs its official implementation/config at the pinned commit/model with the same corpus, tasks, token budget, hardware, and network policy | registry, environment/resource contracts, subprocess receipts, and 17-system collection gate ready; all real results pending |
| Internal superiority gate | Per-system non-inferiority plus one win, aggregate metrics, paired bootstrap, confidence intervals, Holm–Bonferroni, failures, costs, and raw outputs | pending execution |
| Host model-task end-to-end | Codex, Claude Code and OpenCode complete real model tasks for recall/context/verify/explain/restricted exclusion and inactive-zero-impact | no-model official-CLI lifecycle passes commercial GA; real model-task evidence remains competitive-only and pending |
| Independent held-out | Two genuine independent organizations commit secret suites before candidate access, run the frozen artifact, retain failures/resources, and sign complete manifests | pending external execution |

The development team cannot create the independent organizations, secret data, signatures, or
independence. Until authentic evidence exists, `competitive_claim_eligible=false` and no
best/SOTA/leadership statement is permitted. That status does not revoke commercial GA.

## P2 product completion after the candidate core

- Exercise the HTTPS/Git snapshot adapters against a frozen public-endpoint/certificate/redirect
  matrix and native Git implementations on every supported OS; retain fail-closed SSRF, protocol,
  size, hash, origin, and tamper evidence.
- Exercise rename/move/split/merge/parser-change/source-update cases across the full real-file
  format matrix, including damaged and adversarial inputs.
- Complete natural-language retrieval suites for typo, synonym, abbreviation, mixed CJK/English,
  multi-entity, multi-hop, global, temporal, contradiction/counterevidence, source swamp,
  preference, procedure, experience, incident review, and no-answer behavior.
- Run training/evaluation/activation/rollback gates for versioned Ranking Profiles using only
  Run/Capsule/Feedback-bound data; task success remains explicit human/evaluator evidence.
- Expand TUI keyboard/accessibility tests and large-queue usability studies without introducing a
  remote service or second business-logic layer.
- Complete publisher signing for `.dlk`; until then packages provide content integrity only and
  every import remains untrusted quarantine.

## Release rule

Commercial version advancement requires the engineering, packaging, security, platform and
post-release gates in `docs/V0_7_ACCEPTANCE_MATRIX.md`. Competitive leadership requires the separate
external evidence program above. Planned work remains `Planned`; a generated confidence score is
never an approval or a release gate result.
