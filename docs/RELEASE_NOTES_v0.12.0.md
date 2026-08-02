# DeepLaw v0.12.0 release notes

Status: **formal release contract**. These notes describe the `v0.12.0` tag only after the public
Release exists. Before that point, exact-commit, platform, real-model, artifact, signature,
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
- Release evaluation adds frozen Semantic Gold, phased successor/withdrawal real-host execution,
  first-party CLI query scoring, provider-reported build token capture, deterministic query-cost
  accounting, and explicit maintainer review binding.
- The corrected Semantic Gold adds real multi-Packet identity fusion, target-scoped Entity/Concept
  precision with separate extraction completeness and source coverage, claim-level Concept and
  Synthesis assertions, structured typed-relation endpoints and valid time for contradictions,
  exact `production`/`ordinary` applicability and restricted-payload exclusions for retention
  claims, explicit non-mergeable contradiction endpoints, an explicit withdrawal Gap, fully
  specified per-field freeze commitment algorithms, one-event-per-valid-time timeline labels,
  and a scheduled-publication multi-format Event.
- Credential-free pre-review now compiles the entire public corpus through the real governed
  transaction using a deterministic no-model Agent, executes all 15 first-party CLI retrieval
  cases and five adversarial challenges, and exports a source-free Human Review Packet. This does
  not count as external-model evidence or human confirmation.

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

## Externally verified

The formal Release is permitted only after its manifest binds all of the following exact-tag
artifacts:

- Linux, macOS, and Windows on Python 3.11, 3.12, and 3.13 with zero mandatory-suite skips;
- one real Codex CLI/model execution against maintainer-confirmed Semantic Gold, including both
  baseline and successor phases, withdrawal, verified Compilation Runs, first-party query results,
  provider-reported build tokens, and the maintainer correction record;
- exact fresh-wheel quality, byte-reproducible wheel/sdist, OCI, dependency audit, SBOM, license,
  OpenVEX, Sigstore, and GitHub provenance evidence;
- public GitHub Release re-download, digest/signature/provenance verification, isolated install,
  Semantic v2 smoke, rollback, and uninstall.

The published `commercial-release-manifest.json` and `post-release-verification.json`, not this
precomputed prose, are the evidence that these gates actually ran.

## Not verified

- The corrected Semantic Gold is not maintainer-confirmed until the independent audit and owner
  decision bind the exact freeze digest. Before that decision, no paid real-model semantic task,
  merge, tag, or Release is permitted.
- Claude Code and OpenCode real-model semantic execution is `not_executed`. Their pre-review reports
  bind exact CLI version, discovery, authentication, model-access, and non-execution reason; host
  discovery/no-model lifecycle is not real-model verification.
- Unknown Agent hosts, models, editor versions, and plugins have not been tested. The supported
  interoperability claim is limited to Agents that implement DeepLaw's versioned CLI/MCP/Python
  contracts and receive an explicit owner grant.
- The pending legal held-out candidate is not `expert_reviewed` until an independent qualified
  reviewer records a digest-bound decision. Synthetic policy fixtures do not establish legal
  advice quality.
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
- A deterministic fake Agent proves transaction mechanics only. It is not real semantic quality
  evidence.
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
