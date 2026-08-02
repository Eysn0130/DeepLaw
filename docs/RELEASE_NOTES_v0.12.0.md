# DeepLaw v0.12.0 release notes

Status: **formal release contract**. These notes describe the `v0.12.0` tag only after the public
Release exists. Before that point, exact-commit, platform, machine-consensus, artifact, signature,
provenance, and post-release rows remain release gates rather than completed historical facts.

## Implemented

- Semantic Compilation Profile v2 adds packet-local Observation Plans, a bounded run-wide Semantic
  Inventory, 15 explicit semantic duties, one closed Finalization Packet, and one atomic
  Publication Plan. Observation staging is not recallable and every observation must have exactly
  one final disposition.
- Run-wide identity resolution reuses stable Entity and Concept identities across packets, records
  aliases, and preserves same-name ambiguity rather than merging on model confidence.
- Source Summary is a canonical revision-bound Synthesis with exact evidence bindings. A successful
  transaction can truthfully retain `semantic_status=partial`; unsupported completeness and empty
  semantic output fail closed.
- Synthesis Refresh is a recoverable saga for successor, withdrawal, transitive staleness, Overview,
  and community refresh. Canonical revisions remain authoritative if derived projection fails;
  deterministic rebuild never calls a model.
- Query Plan v5 makes purpose duties, compiled-first/evidence-first selection, provenance
  partitions, stale/uncompiled gaps, and raw fallback explicit while retaining provider-visible
  UTF-8 byte limits and read-only query behavior.
- Bounded deterministic query-only cross-language expansion improves Chinese-to-English discovery
  without rewriting evidence or identity. Query Plan v5 binds the expansion profile, count, and
  digest and explicitly records that neither Authority nor stored evidence changed.
- Exact Source IR fragment revision identities are accepted by the first-party Source fragment
  read surface, so v2 evidence receipts remain directly verifiable without duplicating a legacy
  fragment identity in provider-visible fallback cards.
- CLI, MCP, and Python API use the same compilation/retrieval domain services. Source, Wiki,
  Editor, Synthesis, freshness, contradiction, gap, status, explain, and verify operations use
  closed contracts.
- The Obsidian bridge waits for layout readiness, exposes governed commands, and cannot treat paths,
  frontmatter, Wiki links, or Canvas as identity or Authority. The Tolaria bridge merges existing
  configuration into owner-only output (POSIX mode or verified native Windows ACL), uses ephemeral
  active-note context, and keeps canonical roots read-only.
- Authoritative Pack evidence now exposes capability types, deterministic Challenge Trace/replay,
  citation audit, held-out expert review state, and a reusable Pack Core contract while preserving
  the physically separate read-only `law_support` boundary.
- Release evaluation adds frozen Semantic Gold, deterministic successor/withdrawal lifecycle,
  first-party CLI query scoring, real cursor continuation, deterministic query-cost accounting,
  six isolated machine-review roles, and unanimous consensus binding.
- The corrected Semantic Gold adds real multi-Packet identity fusion, target-scoped Entity/Concept
  precision with separate extraction completeness and source coverage, claim-level Concept and
  Synthesis assertions, structured typed-relation endpoints and valid time for contradictions,
  exact `production`/`ordinary` applicability and restricted-payload exclusions for retention
  claims, explicit non-mergeable contradiction endpoints, an explicit withdrawal Gap, fully
  specified per-field freeze commitment algorithms, one-event-per-valid-time timeline labels, a
  scheduled-publication multi-format Event, and fourteen natural-Chinese frozen query variants
  scored against the same objects, claims, citations, coverage, safety rules, and budgets as their
  canonical cases.
- Credential-free evaluation now compiles the entire public corpus through the real governed
  transaction using a deterministic no-model Agent, executes all 15 first-party CLI retrieval
  cases and five adversarial challenges, and exports source-free bilingual derived Owner packets.
  This does not count as external-model evidence or human confirmation.
- Provider-visible results are purpose-aware and deduplicated into a bounded Knowledge Capsule;
  discovery scores, duplicate revisions/aliases, unrelated graph/Wiki navigation, rejected or
  withdrawn bodies, internal paths, and unauthorized metadata remain audit-only. Exact evidence,
  Authority, lifecycle, temporal applicability, contradictions, gaps, fallback, and continuation
  receipts remain visible.
- The signed 28-source Authoritative Pack gate verifies each immutable source hash, parser/segment
  inventory, locator, lifecycle, active release, exact title/citation retrieval, deterministic
  rebuild, baseline non-regression, read-only `law_support`, and zero security failures without
  committing source bytes, titles, paths, or private payloads.

## Verified

- Contract, unit, integration, failure-path, recovery, MCP stdio, deterministic fake-Agent,
  migration, snapshot, restore, rollback, editor boundary, authoritative challenge, capability,
  citation, and repository quality tests are part of the mandatory suite.
- The Obsidian plugin has TypeScript checks, lifecycle tests, a production bundle build, and a
  bundle verifier. The Tolaria temporary-Vault harness verifies merge preservation, active-note
  context, open-note UI intent, and no canonical persistence.
- Three official CLIs run isolated no-model lifecycle checks for discovery, manifest/config
  validation, install, enable/disable, upgrade, remove/re-add, MCP stdio discovery, and two-product
  isolation. No lifecycle result is represented as a real-model result.
- Fresh-wheel, reproducible wheel/sdist, migration/rollback, derived rebuild, and bounded query
  runners are executable release gates.
- Read-only release verification now closes SQLite handles deterministically, so official/private
  update, deletion, uninstall, and temporary authoritative gates do not retain Windows file locks.
- An isolated source-free Authoritative evidence runner emits a digest-bound release report for
  capability predicates, Challenge Trace/replay, citation tamper rejection, temporal exclusion,
  read-only enforcement, and Authority failures. It explicitly keeps unreviewed legal Gold pending.
- Exact v0.11 autonomous Vaults are accepted by the v0.12 verifier, receive a verified autonomous
  snapshot before reconciliation, and can be restored without losing compiled Knowledge state.
- The public Knowledge Revision Detail contract admits the existing `revision_bound` verification
  state used by Source Summary and Synthesis revisions, and the synthesis lifecycle regression
  validates the emitted detail against that exact Schema.
- Retrieval source coverage is target-scoped to revisions that a passing query may admit;
  predecessor and withdrawn revisions retained as negative freshness controls cannot inflate or
  depress the coverage metric.
- Purpose-aware selection preserves exact identities in mixed Event/Concept requests, uses exact
  ISO dates as bounded structured anchors without admitting unrelated low-score candidates, and
  emits timeline Events in chronological valid-time order. Frozen Chinese variants are executed
  cold and warm by the first-party CLI; correct extra objects remain outside target-scoped
  precision, while every required object and claim still needs exact evidence.
- A profile-v2 cross-source Synthesis may bind exact admitted evidence only from Source Revisions
  present in its validated input set. The deterministic retention comparison binds both inputs
  directly, making its provider-visible evidence receipt complete even when no companion Claim or
  raw fragment is selected.
- All 15 Semantic Gold cases and five adversarial challenges must be independently confirmed by
  six isolated `gpt-5.6-sol` machine auditors. No majority vote is accepted, and any discrepancy
  invalidates all prior packets for that candidate.

## Externally verified

The formal Release is permitted only after its manifest binds all of the following exact-tag
artifacts:

- Linux, macOS, and Windows on Python 3.11, 3.12, and 3.13 with zero mandatory-suite skips;
- exact-commit deterministic semantic lifecycle/query/cost evidence, six unanimous independent
  machine-review packets, the consensus digest, and bilingual derived Owner packets with
  `human_gold_review.status=not_required`, `maintainer_confirmed=false`, and `reviewer_id=null`;
- the exact 28-source Authoritative Pack matrix with two byte-identical rebuilds, frozen baseline
  comparison, first-party CLI/MCP evidence, and all safety counters at zero;
- exact fresh-wheel quality, byte-reproducible wheel/sdist, OCI, dependency audit, SBOM, license,
  OpenVEX, Sigstore, and GitHub provenance evidence;
- public GitHub Release re-download, digest/signature/provenance verification, isolated install,
  Semantic v2 smoke, rollback, and uninstall.

The published `commercial-release-manifest.json` and `post-release-verification.json`, not this
precomputed prose, are the evidence that these gates actually ran.

## Not verified

- Human Gold review is not part of the owner-approved v0.12 release scope:
  `human_gold_review.status=not_required`, `maintainer_confirmed=false`, and `reviewer_id=null`.
  No human review is claimed or implied.
- Codex, Claude Code, and OpenCode external real-model semantic execution is `not_executed`.
  Claude Code and OpenCode reports
  bind exact CLI version, discovery, authentication, model-access, and non-execution reason; host
  discovery/no-model lifecycle is not real-model verification.
- Unknown Agent hosts, models, editor versions, and plugins have not been tested. The supported
  interoperability claim is limited to Agents that implement DeepLaw's versioned CLI/MCP/Python
  contracts and receive an explicit owner grant.
- The pending legal held-out candidate is not `expert_reviewed` until an independent qualified
  reviewer records a digest-bound decision. Synthetic policy fixtures do not establish legal
  advice quality.
- Five of the 28 signed Authoritative Pack sources retain explicit parser review warnings under
  the catalog's signed `allowNeedsOcr` policy. They were re-parsed reproducibly and remain
  locator/hash-verifiable, but flagged-page quote correctness has not been independently human
  transcribed; the release matrix reports a parse-risk-free rate of `23/28` rather than hiding
  those warnings.
- Real-client/case material is intentionally absent. DeepLaw does not adjudicate facts, determine
  legal applicability, or provide a verdict.
- No named Guanlan, Traditional RAG, embedding, GraphRAG, Tolaria-Agent, Obsidian-plugin, or other
  competitive run is included.

## Deferred

- A large native desktop application, Web UI, remote canonical database, cloud SaaS, multi-tenant
  control plane, team RBAC, background compilation, mandatory built-in model, and general Agent
  runtime remain outside this release.
- Embedding discovery remains optional and derived. It does not replace semantic compilation,
  evidence, deterministic admission, or Authority.
- Universal editor writes, arbitrary Vault traversal, automatic conversation capture, and model
  execution during rebuild are not planned compatibility behavior.

## Not claimed

- No best, leading, strongest, superior, SOTA, or competitor-beating claim is made.
- Agent output is never official or legal Authority. Ranking, similarity, graph weight, model
  confidence, and maintainer verification cannot elevate source Authority or capabilities.
- Obsidian and Tolaria are work surfaces, not canonical stores, permission systems, or DeepLaw
  substitutes.
- A deterministic Agent proves governed transaction and deterministic retrieval quality only. It
  is not external real-model execution or human semantic review.
- The fixture corpus is intentionally small. Query cost and bytes-saved measurements must be
  reported as observed, including negative savings against tiny raw fixtures; they are not a claim
  about production-corpus compression.

`commercial_release_eligible=true` and `quality_protocol_eligible=true` are valid only in the exact
formal manifest. `competitive_claim_eligible=false` is unconditional for v0.12.0.

## Compatibility and rollback

The migration is additive to the v0.11 knowledge store and retains v1 Compilation Plan behavior.
Source Revision identity, Source IR, registered Markdown, Ledger history, grants, Authority,
scope/sensitivity, and Legal Pack isolation are preserved. Create and verify a snapshot before
upgrade. Rollback restores the verified pre-upgrade pointer/state; it never rewrites immutable
evidence or silently maps v2 observations into v1 knowledge.

See `docs/INSTALL_UPGRADE_ROLLBACK.md`, `docs/LIVING_WIKI_COMPATIBILITY.md`, and
`docs/V0_12_ACCEPTANCE_MATRIX.md` for the exact operator and evidence boundaries.
