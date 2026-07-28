# Upstream capability and adoption matrix

Reviewed: 2026-07-28

This matrix fixes the upstream material used to design the DeepLaw v0.7
construction line. Commits are immutable review coordinates, not runtime
dependency pins. Repository metadata, READMEs, manifests and the listed source
areas were read at those commits. Upstream self-reported benchmark results are
recorded only as upstream evidence; they do not transfer to DeepLaw.

No file or substantial source fragment from the projects below is currently
copied into DeepLaw. The adoption mode is therefore `protocol/concept
reimplementation` unless a closed subprocess is already identified. Any later
copy or vendoring requires a file-level notice, exact license text, SBOM entry,
security review and regression evidence in the same change.

## Product systems

| System | Fixed commit or release | License at review | Strong capability | Material limitation / dependency boundary | DeepLaw adoption decision |
| --- | --- | --- | --- | --- | --- |
| [RAGFlow](https://github.com/infiniflow/ragflow/tree/76acd499d4fc74cc6e72ce8c71fcb2262b3cab13) | `76acd499d4fc74cc6e72ce8c71fcb2262b3cab13`; latest release `v0.26.4` | Apache-2.0 | Broad parsers, visible chunking, ingestion jobs, hybrid retrieval and reranking | Large server/Docker/storage/model surface; network model providers are common; chunks remain central retrieval units | Reimplement the Source Adapter/job/fusion contracts; do not adopt the service runtime |
| [Microsoft GraphRAG](https://github.com/microsoft/graphrag/tree/14a00ad88fc33cf2b52f4f113f25807556f8e25e) | `14a00ad88fc33cf2b52f4f113f25807556f8e25e` / `v3.1.1` | MIT | Entity/relationship/community indexing, local and global search, community reports | Indexing and prompt/model cost can be high; generated graph/community text depends on model quality | Reimplement bounded graph/global channels as removable source-linked sidecars |
| [LightRAG](https://github.com/HKUDS/LightRAG/tree/bbebdd64272d9c9cd71cf93c7446b1054a00388c) | `bbebdd64272d9c9cd71cf93c7446b1054a00388c` / `v1.5.5rc1` | MIT | Local/global/hybrid/naive/mix modes, incremental graph merge, reranking and delete-time rebuild | Requires LLM/VLM roles plus embedding; production guidance uses PostgreSQL/MongoDB/OpenSearch or vector/graph services; embedding changes require re-embedding | Reimplement query modes, RRF and rebuild semantics; no runtime/storage dependency |
| [Graphiti](https://github.com/getzep/graphiti/tree/9140123a7282d44efc077a0af09179919f3defdf) | `9140123a7282d44efc077a0af09179919f3defdf` / `v0.29.2` | Apache-2.0 | Episode provenance, valid-time facts, incremental entity resolution and hybrid graph search | Structured LLM extraction and external graph backends are core assumptions | Reimplement Episode/time contracts and SQLite adjacency; generated facts remain proposals |
| [Mem0](https://github.com/mem0ai/mem0/tree/b357a5a1b03c299ec8229c268e63cfac0f7c6566) | `b357a5a1b03c299ec8229c268e63cfac0f7c6566`; Python `v2.0.14` | Apache-2.0 | Small add/search API, vector/graph memory, filters and feedback-oriented long-term memory | Model, embedding and vector-store providers are normal dependencies; automatic memory mutation conflicts with DeepLaw review gates | Reimplement low-friction Inbox and recall UX; never map `add` directly to active knowledge |
| [Letta](https://github.com/letta-ai/letta/tree/b76da9092518cbaa2d09042e52fdcbde69243e18) | `b76da9092518cbaa2d09042e52fdcbde69243e18` / `0.16.8` | Apache-2.0 | Persistent agent state and memory blocks exposed through an Agent SDK | Agent/server state and self-editing semantics are broader than a read-only knowledge leaf | Reimplement bounded Project/User/Skill proposal types; no server dependency |
| [Letta Code](https://github.com/letta-ai/letta-code/tree/bd06074da707b4660ce151cf66446b73071c4091) | `bd06074da707b4660ce151cf66446b73071c4091` / `v0.29.4` | Apache-2.0 | Long-running coding-agent context, skills/mods and context rewriting | Agent may rewrite its own durable context; Node/provider runtime and account flows are outside the product boundary | Use only as host-acceptance and Skill Bundle protocol reference |
| [Cognee](https://github.com/topoteretes/cognee/tree/325acf356a81545b9892f19ab1ea7b61c51a776b) | `325acf356a81545b9892f19ab1ea7b61c51a776b` / `v1.4.0.dev0` | Apache-2.0 | `add/cognify/search`, graph/vector pipelines, tasks and agent hooks | Broad database/model/provider matrix; generated graph mutations are not human-reviewed authority | Reimplement session artifact capture and pipeline/job concepts into the isolated Inbox |
| [MemOS](https://github.com/MemTensor/MemOS/tree/344cab73c2d04b44d5a10f4bfed0d7e51af9c91c) | `344cab73c2d04b44d5a10f4bfed0d7e51af9c91c`; local plugin `v2.0.11` | Apache-2.0 | Unified memory API, memory cubes, feedback/correction, async ingestion and multimodal extensions | Multiple services/providers and automatic memory operations exceed the local canonical-core boundary | Reimplement versioned Ranking Profiles, correction and async Inbox jobs only |
| [PageIndex](https://github.com/VectifyAI/PageIndex/tree/39121c4d3479edeb049fb1e37045f3227bf50355) | `39121c4d3479edeb049fb1e37045f3227bf50355` / `v0.3.0.dev3` | MIT | Hierarchical long-document tree and reasoned node selection | LLM-built trees/summaries and provider calls cannot be canonical structure | Deterministic Source Tree first; optional closed adapter may add a derived tree candidate |
| [OpenKB](https://github.com/VectifyAI/OpenKB/tree/ff54396e575ee6feb0113b631a34caa082b441cc) | `ff54396e575ee6feb0113b631a34caa082b441cc` / `v0.4.5` | Apache-2.0 | Source compilation into concepts/wiki, workbench and Obsidian-oriented output | LLM compilation and mutable Wiki UX can create an untracked second truth | Reimplement deterministic, source-linked derived projections and Skill manifests |
| [WikiGraph](https://github.com/oomol-lab/wiki-graph/tree/8dc2b2e0642b4d6e67462739deaaf2ac0f6bb666) | `8dc2b2e0642b4d6e67462739deaaf2ac0f6bb666` | Apache-2.0 | `.wikg` archive, URI grammar, chapter tree, reading/search/graph views and jobs | LLM-derived entities/links and a write-capable Node runtime cannot become DeepLaw truth | Reimplement URI/archive/view contracts; keep `.dlk` trust and lifecycle semantics distinct |
| [Obsidian Help](https://github.com/obsidianmd/obsidian-help/tree/a97de34c1a9f2381586f4f51070aeb9207c8a457) | `a97de34c1a9f2381586f4f51070aeb9207c8a457` | No repository-wide SPDX license asserted | Local Markdown, properties, backlinks, search, graph, Canvas and URI user experience | Obsidian application code is proprietary; help content is reference material, not reusable product code | Use only documented formats/URI behavior; generate a one-way projection |
| [JSON Canvas](https://github.com/obsidianmd/jsoncanvas/tree/456f843cb293df4f4ab1763e22ccb46a80b307c8) | `456f843cb293df4f4ab1763e22ccb46a80b307c8` | MIT | Open `.canvas` nodes/edges/groups format | A Canvas is a view and has no authority/lifecycle semantics | Directly implement the public format with deterministic IDs and source-linked metadata |

## Parser candidates

| Parser | Fixed version/commit | License | Runtime/network boundary | Decision |
| --- | --- | --- | --- | --- |
| [MinerU](https://github.com/opendatalab/MinerU/tree/79d6d8d79fb8f3ddba5cc34c07a16f0ec36f56c7) | `3.4.4`, `79d6d8d79fb8f3ddba5cc34c07a16f0ec36f56c7` | MinerU Open Source License; exact redistribution terms require release review | Large optional local model/runtime; models are provisioned explicitly | Retain the existing fixed closed subprocess only; output is a Source IR candidate |
| [Docling](https://github.com/docling-project/docling/tree/873f990203ac3195b0142f5564eea13e59c1a312) | `873f990203ac3195b0142f5564eea13e59c1a312` / `v2.115.0` | MIT | Broad parser/model dependency set; can run locally after model preparation | Evaluate as an optional closed adapter against fixed fixtures before adoption |
| [MarkItDown](https://github.com/microsoft/markitdown/tree/2e42a01c404629b06892a1bdb5e7bf5261770c40) | `2e42a01c404629b06892a1bdb5e7bf5261770c40` / `v0.1.6` | MIT | Format extras and optional model/provider features vary | Evaluate deterministic local converters; Markdown output never becomes canonical Source IR |
| [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR/tree/2661c7c0ef5c613e8f93c6e93b2e052399f0f854) | `2661c7c0ef5c613e8f93c6e93b2e052399f0f854` / `v3.7.0` | Apache-2.0 | Large local OCR/layout model stack; explicit model preparation required | Candidate second OCR/layout witness, not a default dependency or authority source |
| [Tree-sitter Python binding and official grammars](https://github.com/tree-sitter/py-tree-sitter) | core `0.26.0`; JavaScript `0.25.0`; TypeScript `0.23.2`; Java `0.23.5`; Go `0.25.0`; Rust `0.24.2` | MIT | Exact-pinned in-process native parsers; no network/model; source bytes, syntax nodes, structural symbols, imports, and references are bounded | Adopt as base-runtime dependencies for compiler-grade JavaScript/JSX, TypeScript/TSX, Java, Go, and Rust Source IR; bind exact versions into compilation identity and treat all output as untrusted derived structure |
| [SQLGlot](https://github.com/tobymao/sqlglot) | `30.13.0` | MIT | Pure-Python in-process parser; no network/model; SQL bytes, AST nodes, statements, and symbols are bounded | Adopt as an exact-pinned base-runtime dependency for statement/CTE/table/column Source IR; bind version and generic dialect into compilation identity, never execute SQL, and mark bounded lexical fallback explicitly |

## Data and authority boundary

All adopted ideas are downstream of immutable source bytes and Source IR.
External LLM calls are operator-only, must disclose the selected source scope,
and may create only quarantined proposals or removable sidecars. Dense, graph,
tree, reranker, community, Wiki and feedback profiles are rebuildable. None may
change source identity, governance, human review, legal authority or lifecycle.

## Benchmark evidence and fair comparison

Several upstream repositories publish their own benchmark results. They use
different corpora, languages, model prompts, generation judges, token budgets,
hardware and network policies. DeepLaw will not normalize those README numbers
into a comparison. A named baseline is eligible only when its pinned official
configuration can emit raw candidates/context for the same frozen corpus,
query set, model set, token budget and resource recorder. Unsupported or
failed baseline runs remain failures in the report; they are not replaced by a
toy implementation.

The first fair harness must include BM25, dense retrieval, BM25+dense+reranker,
the named systems above where their official interfaces permit automation,
Obsidian's scripted human workflow, and DeepLaw lexical/hybrid/full profiles.
It must retain per-case output, ingestion/query cost, provenance, stale and
cross-vault checks, paired bootstrap intervals and corrected hypothesis tests.
