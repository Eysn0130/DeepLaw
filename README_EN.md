<p align="center">
  <a href="README.md">简体中文</a> · <strong>English</strong>
</p>

<h1 align="center">DeepLaw 2.0</h1>

<p align="center">
  <img src="assets/brand/deeplaw-2-glass.png" width="820" alt="DeepLaw 2.0 wordmark" />
</p>

<p align="center">
  <strong>A local, single-user Agent Knowledge OS.</strong><br />
  Verifiable sources · Identity v2 · multi-channel retrieval · Knowledge Capsules · human governance
</p>

> **v0.7.0 commercial GA.** Commercial release qualification and competitive leadership claims
> are separate. The formal manifest fixes `commercial_release_eligible=true` and
> `competitive_claim_eligible=false`. Real model-task E2E, all 17 named baselines, secret held-outs,
> and independent evaluator signatures remain incomplete, so this release makes no best, SOTA,
> overall-leadership, or all-baselines-surpassed claim. No-model host lifecycle is not model-task
> acceptance.

DeepLaw is permanently scoped to one local OS user. Multi-tenancy, team RBAC, remote databases,
central services, and enterprise SaaS are not future-product assumptions. Canonical state stays in
the owner's SQLite database, content-addressed source fragments, and append-only audit chain.
Telemetry is disabled by design because no telemetry path exists.

## One-command install and five-step Golden Path

Install the signed wheel from GitHub Release in one command:

```bash
uv tool install https://github.com/Eysn0130/DeepLaw/releases/download/v0.7.0/deeplaw-0.7.0-py3-none-any.whl
```

The normal workflow requires no JSON parsing or copied internal IDs:

```bash
# 1. Initialize a local Vault
deeplaw init ./vault --name my-project

# 2. Ingest a file or directory through a resumable job
deeplaw add ./docs --vault ./vault --confirm-no-case-data

# 3. Review proposals locally
deeplaw review --vault ./vault --interactive

# 4. Build a Query Plan, Retrieval Trace, and bounded Capsule, then verify it
deeplaw recall "Which constraints govern this release?" \
  --vault ./vault --confirm-no-case-data --output capsule.json

# 5. Inspect the last explain trace
deeplaw explain --vault ./vault --last
```

`recall` returns `capsule_verification` in the same result. Advanced
`deeplaw knowledge ...` commands retain stable `human`, `json`, and `jsonl` surfaces.

## Product loop

```text
local files / directories / structured data
  → Source Adapter → Source IR / Source Tree
  → immutable Source Revision → many-to-many Compiler
  → quarantined / proposed Knowledge Revision
  → human Review Receipt → active Knowledge Asset
  → Query Plan → multi-channel fusion → Admission / Selection
  → token-aware Knowledge Capsule → Agent
  → Capsule-bound Run Record → structured feedback → Proposal Inbox
```

A retrieval score, model output, graph edge, or embedding never grants authority. Admission still
requires exact evidence bindings, a valid lifecycle, policy permission, and human review.

```mermaid
flowchart LR
  S["Local sources"] --> A["Source Adapters"]
  A --> IR["Source IR / Tree"]
  IR --> C["Many-to-Many Compiler"]
  C --> R["Human Review"]
  R --> V["Identity v2 Vault"]
  V --> Q["Evidence-Governed Retrieval Fabric"]
  Q --> K["Knowledge Capsule"]
  K --> M["read-only knowledge_support"]
  M --> G["Codex · Claude Code · OpenCode"]
  G -. "Run Record / feedback artifact" .-> I["Isolated Proposal Inbox"]
  I -. "operator review only" .-> R
```

The general Knowledge OS and Chinese Legal Pack use separate processes, stores, and optional
plugins. `knowledge_support` and `law_support` remain independently activated and permanently
read-only.

## Identity, source structure, and retrieval

Identity v2 separates stable source location, immutable source revision, compilation identity,
proposal-set identity, Knowledge Revision, and Governance Revision. It supports many-to-many
evidence bindings, split/merge/modified/deleted/ambiguous lineage, and bitemporal relation
revisions without allowing generated pages or scores to replace source text.

Source Adapters cover Markdown/TXT, HTML, PDF, DOCX, PPTX, XLSX, EPUB, code,
JSON/JSONL/YAML/TOML, CSV/TSV, SQL, conversations, and tool results. Python uses its AST;
JavaScript/JSX, TypeScript/TSX, Java, Go, and Rust use pinned official Tree-sitter grammars whose
exact versions enter compilation identity. SQL uses an exact-pinned SQLGlot AST for statements,
CTEs, tables, columns, and line spans. Parser versions, recovery, and bounded lexical fallback after
an explicit limit or parse failure remain quality data. Heading, page, table, cell, symbol, path,
SQL structure, locator, order, and hash data become Source IR rather than model-generated summaries.
OOXML and EPUB validate the complete archive and relationship inventory before content extraction,
bound XML bytes/nodes/depth, and reject invalid XLSX cell, shared-string, row-order, and merged-range
inventories.

Explicit connectors create one-shot, owner-only, hash-bound Source Snapshots; they do not register
background synchronization. HTTPS accepts only public-DNS TLS on port 443 without credentials,
query, or fragment, reapplies SSRF checks at every redirect, pins the resolved endpoint to TLS SNI,
rejects compressed or over-64-MiB responses, and can require a caller-supplied SHA-256. Remote
bytes always enter as `untrusted`. Git reads only a full 40- or 64-hex commit from an existing local
repository, performs no clone or checkout, disables lazy fetch, and keeps the local repository path
out of canonical Source Identity. Neither path activates knowledge or adds an MCP write surface.

```bash
# HTTPS dry-run performs no network request and writes no snapshot
deeplaw add --url https://example.org/guide.md --expected-sha256 SHA256_REPLACE \
  --vault ./vault --confirm-network --confirm-no-case-data

# exact revision from an existing local repository
deeplaw add --git-repository ./repo --git-revision FULL_COMMIT_REPLACE \
  --git-repository-id product-docs --include '*.md' --vault ./vault \
  --confirm-local-repository --confirm-no-case-data
```

Advanced source commands accept a normalized logical-path `--alias`. Historical paths continue to
resolve the same Source Identity after a reviewed rename or move. `--active` selects the reviewed
version, while `--latest` can inspect a pending successor. Alias collisions and multiple parallel
pending successors fail closed rather than relying on timestamp or internal-ID ordering.

The Retrieval Fabric compiles a stable Query Plan and can use exact, fielded BM25, Source Tree,
reviewed graph, temporal, feedback, explicitly supplied Dense, and pinned local-reranker channels.
Explain Trace records channel ranks, exclusions, source and Knowledge Duty coverage, gaps, and
token budgets. Lexical retrieval may use bounded one-edit ASCII typo repair only after an ordinary
lexical miss; reviewed graph expansion is capped at two hops and the same evidence admission and
channel budget. Ranking remains candidate-only and cannot change trust or approval.

```bash
deeplaw recall "What was current as of 2026-07-01?" \
  --vault ./vault --mode hybrid --as-of 2026-07-01T23:59:59Z \
  --max-tokens 4096 --confirm-no-case-data
deeplaw explain --vault ./vault --last --format json
deeplaw knowledge lineage --vault ./vault --asset-id asset_REPLACE_WITH_EXACT_ID
deeplaw knowledge lineage --vault ./vault --map-status split \
  --from-asset-id asset_PREDECESSOR --to-asset-id asset_SUCCESSOR_A \
  --to-asset-id asset_SUCCESSOR_B --reason 'Reviewed source-bound split.' \
  --confirm-reviewed
deeplaw knowledge relation carry-forward --vault ./vault
```

The advanced Lineage command accepts only exact source-bound Identity v2 revisions. A reviewed
split/merged/ambiguous mapping is recorded under every involved Knowledge Key, creates or activates
no knowledge, and inherits no approval. The Workbench offers the same action by visible row number
so the normal operator path does not require copied IDs.

After a source update, unchanged relation endpoints can only produce an inactive carry-forward
candidate. Modified, renamed, or moved endpoints require full review; deleted, split, merged, or
ambiguous endpoints remain outside the current graph. Golden `review` and the local Workbench expose
the queue without inheriting approval or requiring IDs in the regular workflow.

An explicitly verified local Dense sidecar can be supplied to `recall` or `explain` with
`--discovery-index`, `--model-root`, and `--threads`. Without those inputs DeepLaw does not
silently fetch a model, use the network, or pretend that a semantic channel ran.

An optional reranker manifest pins the executable, closed argv, model revision, exact model-file
hashes, bounds, and timeout. It may only permute existing candidates. An offline declaration is not
an OS network sandbox; deployments needing mechanical egress prevention must supply one.

## Local operator experience

```bash
# curses TUI; non-TTY environments receive the same bounded snapshot as JSON
deeplaw open --vault ./vault

# rich, derived Markdown and JSON Canvas projection
deeplaw open --vault ./vault --obsidian --print-uri
```

The Workbench exposes Source List/Tree/Diff, side-by-side review,
approve/reject/edit/split/merge, visible-row cross-key Lineage review, Recall, Explain, Lineage,
current and historical relations, Capsules, feedback, health, and benchmark boundaries through the
same service layer as the CLI. Multi-Asset approve/reject decisions are atomic, and approval of a
quarantined proposal requires a separate risk confirmation.
Projection edits can only create quarantined, source-bound proposals; SQLite remains canonical.

The isolated Proposal Inbox accepts bounded `.dlproposal`, `.dlfeedback`, `.dlrun`, and `.dleval`
artifacts. Agent MCP tools still cannot write canonical state. Skill Factory emits source-bound,
budgeted, read-only skills; imported skills enter quarantine.

## Capability status

| Capability | Status | Boundary |
| --- | --- | --- |
| Identity v2, many-to-many bindings, lineage, temporal relations | **Supported** | Legacy source-free manual proposals remain explicitly unbound |
| Multi-format Source Adapter / IR / Tree | **Supported local-only** | Closed base adapters are tested; complex PDF OCR/table/figure/multilingual work still needs the Operator-only engine and frozen evidence; every output remains bounded, hashed, and review-gated |
| Explicit HTTPS / local exact-Git Source Snapshot | **Operator-only** | One-shot and review-gated; no polling, clone, checkout, authenticated URL, private-network access, or silent fallback |
| Deterministic v2 compiler | **Supported** | Proposal-only; human review is the sole activation path |
| Local/external model compiler | **Operator-only** | Exact manifest; external disclosure requires explicit confirmation |
| Retrieval Fabric, Query Plan, Explain Trace, token-aware Capsule | **Supported** | Dense is outside the default Context/MCP path |
| Pinned local reranker | **Operator-only** | Candidate-only, rank-only; OS egress policy remains operator-owned |
| Golden CLI, resumable sync, shell completion | **Supported local-only** | Advanced stable JSON/JSONL commands remain available |
| curses Operator Workbench | **Supported local-only** | No remote listener, duplicated business logic, or telemetry |
| Markdown/Obsidian/JSON Canvas proposal workflow | **Supported local-only** | Reverse edits never overwrite active knowledge |
| Inbox, Skill Factory, snapshot/restore/GC/doctor | **Supported local-only** | Import/install defaults to quarantine |
| POSIX owner-only isolation | **Supported local-only** | Full-disk encryption is still recommended |
| Native Windows ACL and junction gates | **Supported local-only** | The `windows-latest` commercial gate requires zero skips and exercises real ACL, junction, and reparse-point behavior |
| Discovery Index | **Experimental** | Removable derived sidecar, outside default Context/MCP |
| `knowledge_support` and `law_support` | **Supported** | Separate, explicit, read-only, and bounded |
| Codex / Claude Code / OpenCode no-model host lifecycle | **Supported local-only** | Official CLIs validate manifests/config, discovery, install, enable/disable, upgrade, removal, MCP handshake, and dual-product isolation; this is not model-task acceptance |
| Cross-system leadership | **External verification pending** | Frozen held-out results and two independent signatures are mandatory |

## Benchmarks and claims

The registry pins official configurations for BM25, Dense, BM25+Dense+Reranker, RAGFlow,
Microsoft GraphRAG, LightRAG, Graphiti, Mem0, Cognee, MemOS, PageIndex, OpenKB, WikiGraph,
Obsidian workflow, and DeepLaw lexical/hybrid/full. It does not substitute toy in-house
implementations for third-party systems.

The closed execution-plan/receipt v2 binds the exact registry, clean Git revision and submodule
state, corpus/query and case-ID inventory, wrapper/executable, and a fixed hardware/software/model,
common-reader, network, and measurement environment record. Five new paths retain raw output, a
resource/failure record, stdout, stderr, and the receipt. The resource record binds build/query
time, peak memory, index/workspace bytes, model calls/tokens/cost, and failures. A 17-system
collection gate reopens every input and artifact and checks common corpus, queries, hardware,
reader, Token budget, and retained evidence; even a complete collection remains
`claim_eligible=false`. Query-offline isolation remains an evaluator-enforced OS sandbox; plan,
receipt, and report hashes are not independent signatures.

The final External Evaluator Kit freezer rechecks a clean exact HEAD, frozen registry, all 17
successful runs, the case-level statistical gate, complete model-file manifests, pre-delivery
corpus commitment, wheel/sdist, OCI container, SBOM, lock, contracts, tokenizer/index profiles,
raw outputs, resource records, and signature tools before creating a content-addressed portable
kit. `verify-attestation` requires a public key trusted outside the attestation. Tooling or one
valid signature never changes `claim_eligible=false`; two secret held-outs and two genuinely
independent organizations are still required.

All cross-system results remain `pending_external_execution` and
`competitive_claim_eligible=false`; that does not block v0.7.0 commercial GA.
Development-generated exact-token scale reports diagnose mechanical scale, provenance, lifecycle,
and latency only. The repository now records actual 100,000- and one-million-Asset construction
runs; both bind a dirty worktree and are neither frozen release evidence nor competitive claims.

DeepLaw's engineering objective is to achieve the strongest aggregate result for local,
single-user Agent Knowledge under a frozen, fair benchmark. This is an objective, not a current
market claim. See [benchmark evidence](docs/BENCHMARKS.md), the
[named baseline registry](benchmarks/baselines/README.md), and the
[external protocol](docs/EXTERNAL_BENCHMARK_PROTOCOL.md).

## Security and release engineering

- No default telemetry, remote listener, implicit web retrieval, or silent model-memory fallback.
- Agent MCP has no learn, remember, write, approve, import, revoke, delete, or administration tool.
- Restricted knowledge never crosses Agent MCP; case-private material belongs outside DeepLaw.
- Imported text is untrusted data and cannot override host, repository, developer, or user rules.
- Tagged release jobs generate a CycloneDX SBOM, license and package inventories, byte-identical
  wheel/sdist, a non-root/no-listener OCI, three-OS zero-skip gates, Sigstore/OIDC signatures,
  GitHub provenance/SBOM attestations, and `commercial-release-manifest.json`; the exact GitHub
  Release bytes are downloaded and reinstalled after publication.

```bash
uv lock --check
uv run --frozen ruff check .
uv run --frozen pytest
uv run --frozen python -m benchmarks.release.audit_dependencies --profile default
uv run --frozen python -m benchmarks.release.audit_dependencies --profile build
uv run --frozen python -m benchmarks.release.audit_dependencies --profile discovery
uv run --frozen python -m benchmarks.release.audit_dependencies --profile document-engine
uv run --frozen python -m benchmarks.release.verify_reproducible_build \
  --artifact-dir dist --output dist/reproducible-build.json
uv run --frozen python -m benchmarks.verify_fresh_wheel --dist dist
uv run --frozen python -m benchmarks.release.evaluator_candidate --help
uv run --frozen python benchmarks/hosts/run_codex_plugin_smoke.py \
  --codex /absolute/path/to/codex --output dist/codex-plugin-smoke.json
git diff --check
```

Codex, Claude Code, OpenCode, and generic Skill configuration and acceptance status are recorded in
[Agent adapters](docs/AGENT_ADAPTERS.md). v0.7.0 runs all three official CLIs for no-model
lifecycle and MCP stdio handshake without requesting an API key. This evidence is not model/task
end-to-end acceptance; that remains a competitive-evidence gap.

Legal Pack text is authoritative only inside an immutable release with official URL, source hash,
locator, and release ID. User-private legal references never inherit official authority. Temporal
matching alone does not establish legal applicability.

## Documentation and license

- [Knowledge OS contract](docs/KNOWLEDGE_OS.md)
- [CLI lifecycle](docs/CLI_LIFECYCLE.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Benchmarks](docs/BENCHMARKS.md)
- [Upstream capability matrix](docs/UPSTREAM_CAPABILITY_MATRIX.md)
- [Install, upgrade, and rollback](docs/INSTALL_UPGRADE_ROLLBACK.md)
- [v0.7 acceptance matrix](docs/V0_7_ACCEPTANCE_MATRIX.md)
- [v0.7.0 release notes](docs/RELEASE_NOTES_v0.7.0.md)
- [Roadmap](ROADMAP.md)
- [Security policy](SECURITY.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)

DeepLaw is licensed under [Apache License 2.0](LICENSE). Do not commit legal source documents,
generated release databases, credentials, model weights, private notes, or local paths containing
user material.
