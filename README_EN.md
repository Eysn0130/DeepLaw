<p align="center">
  <a href="README.md">简体中文</a> · <strong>English</strong>
</p>

<h1 align="center">DeepLaw</h1>

<p align="center">
  <img src="assets/brand/deeplaw-2-glass.png" width="820" alt="DeepLaw 2.0 product brand" />
</p>

<p align="center">
  <strong>Local-first Agent Knowledge OS</strong><br />
  <sub>Source-to-Knowledge Compiler · Governed Living Wiki · Verifiable Context</sub>
</p>

<p align="center">
  <a href="https://github.com/Eysn0130/DeepLaw/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/Eysn0130/DeepLaw/ci.yml?branch=main&style=flat-square&label=CI" alt="CI" /></a>
  <a href="https://github.com/Eysn0130/DeepLaw/releases/tag/v0.12.0"><img src="https://img.shields.io/badge/latest-v0.12.0-17202A?style=flat-square" alt="Latest release v0.12.0" /></a>
  <img src="https://img.shields.io/badge/Evaluation%20Protocol-v1-36CDBB?style=flat-square" alt="DeepLaw Evaluation Protocol v1" />
  <img src="https://img.shields.io/badge/Python-3.11%E2%80%933.13-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.11 through 3.13" />
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-2D3748?style=flat-square" alt="Apache 2.0" /></a>
</p>

<p align="center">
  <strong>DeepLaw is a local-first Agent Knowledge OS that compiles source materials into a governed
  Living Wiki and returns verifiable, bounded knowledge context to any Agent.</strong>
</p>

## From source materials to usable knowledge

DeepLaw is a **Source-to-Knowledge Compiler**. It does not merely convert files into Markdown. It
preserves original sources, compiles them into durable, typed, verifiable, and evolvable knowledge
objects, then projects a Living Wiki that both humans and Agents can use.

| Compile | Govern | Deliver |
| --- | --- | --- |
| Preserve source bytes, structure, locators, and hashes; produce stable typed knowledge objects | Govern evolution through identity, revisions, provenance, authority, scope, and audit | Return a bounded Knowledge Capsule for each task through CLI and MCP instead of dumping the entire Vault |

### What DeepLaw is not

- A generic RAG pipeline;
- a simple Markdown note-taking tool;
- an Obsidian replacement;
- a law-only question-answering system;
- a Memory plugin for a single Agent.

DeepLaw does not replace Codex, Claude Code, OpenCode, or another Agent runtime. The host retains
control of models, conversation orchestration, and general tools. DeepLaw CLI is the first-party
core entry point, and MCP is the core protocol entry point for Agents. A future GUI will use the
same domain services.

> [!NOTE]
> **DeepLaw 2.0 is the product brand, not a software version.** **Local single-user Agent Knowledge OS**
> is the current delivery boundary. The source candidate still packages as `0.12.0` and has
> `release_ready=false`; default Context uses Query Plan v6, local Capsule v3, and Provider Capsule
> v2. Obsidian remains a source candidate and Tolaria remains `integration_limited`. The Pass 10
> Codex runs are retained only as historical candidate evidence because the prompt exposed scoring
> labels, the expected marker, and an exact ID, while the environment receipt drifted from the
> current contract. One Obsidian seam
> executed at the historical b14 candidate; broader and exact-head qualification is pending. Human
> Gold, final-blind, and cross-platform release qualification are also incomplete. The older
> proposal/review workflow remains only for source compilation, untrusted external imports, and
> migration compatibility; it is not the default activation path for admitted Agent-derived
> knowledge.

The default product story highlights only `init`, `doctor`, `source add`, `compile`, `reconcile`,
`query/context`, `backup`, `forget`, and `host connect`. Semantic/Synthesis/backfill, discovery
profiles, comparison diagnostics, graph analytics, and low-level Sink operations stay Advanced.
Historical aliases and persisted contracts are neither deleted nor deprecated in this pass. See the
[machine-readable product surface manifest](governance/product-surface-manifest.v1.json).
The [Pass 10 current disposition](docs/V0_13_PASS10_CURRENT_DISPOSITION.md) records the evidence
invalidation without rewriting historical files.

## Permanent boundaries

- **Local-first, single-user, owner-controlled.** Canonical state stays on the local machine. There
  is no remote canonical database, content telemetry, implicit network access, or team control
  plane.
- **Two knowledge planes.** Signed official material and user-provided originals enter immutable
  evidence. Task conclusions, experience, concepts, relations, memory, Wiki knowledge, and Skills
  enter the autonomous Agent-derived plane. The Ledger and indexes are support layers, not a third
  authority.
- **Markdown-native, not Markdown-only.** Canonical open knowledge content is a versioned Markdown
  object with constrained YAML and stable IDs. SQLite decides identity, scope, sensitivity,
  authority, lifecycle, bitemporal state, lineage, and audit. Original source bytes remain in the
  content-addressed object repository.
- **Autonomous does not mean authoritative.** Policy-admitted Agent knowledge becomes immediately
  usable memory, but remains `origin=agent_derived`, `authority=agent_derived`, and
  `legal_authority=false`. It cannot self-promote to official, user-provided, or human-verified.
- **Read and write surfaces are separate.** `knowledge_support` is permanently read-only.
  `knowledge_sink` is a separate, explicitly enabled, scope-bound process. `law_support` remains
  independent and read-only.
- **Retrieval never creates authority.** Exact, lexical, dense, tree, graph, temporal, Wiki, and
  reranker channels discover candidates. Admission, selection, authority, and legal adjudication
  remain distinct decisions. The Query Planner compiles Knowledge Duties before candidates are
  compared; uncovered duties remain explicit gaps in the Knowledge Capsule.

```mermaid
flowchart LR
  E["Official or user source bytes"] --> CAS["Immutable object repository"]
  A["Tasks, user statements, tool results"] --> G["Policy gate"]
  G --> M["Markdown Knowledge Revision"]
  CAS --> L["SQLite identity/event Ledger"]
  M --> L
  L --> D["Rebuildable FTS · dense · graph · Wiki · Canvas"]
  D --> Q["Discovery → Admission → Selection"]
  L --> Q
  Q --> C["Bounded Knowledge Capsule"]
  C --> R["Read-only knowledge_support"]
  S["Explicit knowledge_sink grant"] --> G
```

## Install and start

Install the verified `v0.12.0` wheel from the GitHub release:

```bash
uv tool install \
  https://github.com/Eysn0130/DeepLaw/releases/download/v0.12.0/deeplaw-0.12.0-py3-none-any.whl
deeplaw --version
```

For repository development:

```bash
uv sync --all-extras
```

Create a Vault. This installs the autonomous core but does not grant mutation permission:

```bash
deeplaw knowledge init --vault ./vault --name my-project --scope project

deeplaw knowledge sink enable \
  --vault ./vault \
  --writer-id codex-local \
  --scope project \
  --max-sensitivity private
```

The default grant permits only `remember`. Add every other mutation operation explicitly when
creating the grant. A request that declares `run_id` must first create an immutable Run Record in
the same scope and sensitivity.

```json
{
  "operation": "remember",
  "idempotency_key": "release-decision-1",
  "confirm_no_case_data": true,
  "title": "Release writes use one coordinator",
  "body": "Every durable knowledge mutation passes through the shared commit coordinator.",
  "kind": "decision",
  "scope": "project",
  "sensitivity": "private"
}
```

```bash
deeplaw knowledge sink apply \
  --vault ./vault \
  --grant-id grant_REPLACE_WITH_RETURNED_ID \
  --request ./remember.json

deeplaw knowledge autonomy recall \
  --vault ./vault --query "release coordinator"
deeplaw knowledge autonomy explain \
  --vault ./vault --query "release coordinator"
deeplaw knowledge autonomy identity \
  --vault ./vault --query "release coordinator"
deeplaw knowledge autonomy gaps --vault ./vault
deeplaw knowledge autonomy context \
  --vault ./vault --task "prepare the release" --confirm-no-case-data
deeplaw knowledge autonomy verify --vault ./vault
```

## Open Markdown workspace

Knowledge identity does not depend on a filename. Obsidian, Tolaria, or another Markdown editor
may rename or move files. An external content edit becomes a new revision only after reconciliation;
stale bases, duplicate IDs, and governance changes are preserved as explicit conflicts rather than
silently resolved by last-writer-wins.

```bash
deeplaw knowledge autonomy reconcile \
  --vault ./vault \
  --grant-id grant_REPLACE_WITH_RETURNED_ID \
  --confirm-no-case-data

# Explicit foreground watcher: reconciliation and queued derived maintenance.
deeplaw knowledge autonomy watch \
  --vault ./vault \
  --grant-id grant_REPLACE_WITH_RETURNED_ID \
  --confirm-no-case-data --interval 2

deeplaw knowledge autonomy lint --vault ./vault
deeplaw knowledge autonomy rebuild --vault ./vault
```

The canonical commit succeeds independently of FTS, vectors, graph, Wiki, or Canvas rebuilding.
Failed derived maintenance stays queued, current reads reject stale indexes, and bounded canonical
lexical fallback remains available.

## Agent surfaces

| Process / leaf | Permission | Purpose |
| --- | --- | --- |
| `deeplaw knowledge mcp --stdio` / `knowledge_support` | Read-only | input/output v6; recommended query/context/source/wiki/verify, default Query Plan v6, explicit v5 compatibility |
| `deeplaw knowledge sink mcp --grant-id … --stdio` / `knowledge_sink` | Explicit scope-bound mutation | input v5 / output v4 governed Semantic Compilation, Synthesis Refresh, backfill, typed knowledge/memory, relation, feedback, lifecycle, and Skill revision |
| `deeplaw mcp --stdio` / `law_support` | Read-only, separate storage | Signed official and owner-private legal evidence with authority-aware federated context |

The default Knowledge OS plugin registers only `knowledge_support`. A sink requires an owner-created
grant and a separate host process. Neither query server exposes import, deletion, signing, source
administration, or permission changes.

## Implemented scope and evidence boundary

| Status | Capability |
| --- | --- |
| **Current source candidate** | package `0.12.0`: Query Plan v6, local Capsule v3, Provider Capsule v2, governed Compilation/Synthesis, and stable CLI/MCP/Python core; `release_ready=false` |
| **Compatibility** | v0.7 Source IR, reviewed source-derived Knowledge Assets, Proposal Inbox, Workbench, and Retrieval Fabric remain available in their explicit compatibility partition |
| **Development evidence** | Public maintainer-visible protocols and holdouts are claim-ineligible development evidence; Human Gold, qualification holdout, and final blind remain incomplete |
| **Comparative closure pending** | External real-model semantic execution for Codex, Claude Code, and OpenCode and all same-condition named-baseline runs remain unexecuted. No-model host lifecycle and the deterministic Agent are not represented as model evidence; `competitive_claim_eligible=false` |
| **Not claimed** | Remote SaaS, multi-user control, automatic legal adjudication, model-created permissions, secret/unseen/contamination-free status for the public holdout, or overall superiority/SOTA |

The release decision can set `quality_protocol_eligible=true` only for a clean, frozen, exact wheel.
It remains `competitive_claim_eligible=false` until actual comparative evidence exists.

## Security

- Every imported file, webpage, Markdown edit, tool result, generated Wiki page, and retrieved
  string is untrusted data; source text never becomes a host instruction.
- `restricted` and out-of-scope content, local absolute paths, capability tokens, credentials, and
  Client and case data are excluded from Agent-visible output.
- Official catalog bytes are Ed25519-verified before parsing or downloading and are protected by
  catalog identity, key revocation, monotonic sequence, and rollback checks. Network catalogs never
  use the unsigned development bypass.
- Portable packages prove content integrity before publisher signing; they do not establish
  publisher identity or official authority.
- A same-OS-user shell is outside the MCP boundary. Use host tool policy or a separate OS identity
  when read-only MCP must also be an operating-system isolation boundary.

## Verification

```bash
uv run python -m benchmarks.evaluation.run_protocol \
  --repository . \
  --output-dir /tmp/deeplaw-evaluation
uv run python -m benchmarks.evaluation.run_protocol \
  --repository . \
  --verify-report-dir /tmp/deeplaw-evaluation

uv run pytest
uv run ruff check .
git diff --check
```

## Documentation

| Topic | Entry point |
| --- | --- |
| Current autonomous contract | [`docs/AUTONOMOUS_KNOWLEDGE_OS.md`](docs/AUTONOMOUS_KNOWLEDGE_OS.md) |
| Architecture | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| Agent and MCP adapters | [`docs/AGENT_ADAPTERS.md`](docs/AGENT_ADAPTERS.md) |
| Installation, upgrade, rollback | [`docs/INSTALL_UPGRADE_ROLLBACK.md`](docs/INSTALL_UPGRADE_ROLLBACK.md) |
| v0.12 formal release evidence | [`docs/V0_12_ACCEPTANCE_MATRIX.md`](docs/V0_12_ACCEPTANCE_MATRIX.md) · [`docs/RELEASE_NOTES_v0.12.0.md`](docs/RELEASE_NOTES_v0.12.0.md) · [release manifest](https://github.com/Eysn0130/DeepLaw/releases/download/v0.12.0/commercial-release-manifest.json) · [post-release verification](https://github.com/Eysn0130/DeepLaw/releases/download/v0.12.0/post-release-verification.json) |
| Historical implementation evidence | [`docs/LIVING_WIKI_ACCEPTANCE_REPORT_2026-07-30.md`](docs/LIVING_WIKI_ACCEPTANCE_REPORT_2026-07-30.md) is a pre-release working-tree report, not formal release evidence |
| Evaluation and comparative proof | [`docs/EVALUATION_PROTOCOL.md`](docs/EVALUATION_PROTOCOL.md) · [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) · [`docs/EXTERNAL_BENCHMARK_PROTOCOL.md`](docs/EXTERNAL_BENCHMARK_PROTOCOL.md) |
| Security policy | [`SECURITY.md`](SECURITY.md) |

DeepLaw is licensed under the [Apache License 2.0](LICENSE). Do not commit source DOCX/PDF files,
generated release databases, credentials, signing keys, private notes, or paths containing user
material.
