# Upstream Reuse Review

Reviewed: 2026-08-11

The full v0.7 product-system and parser comparison, including dependency,
network, data-boundary and benchmark fields, is maintained in
[`UPSTREAM_CAPABILITY_MATRIX.md`](UPSTREAM_CAPABILITY_MATRIX.md). The detailed
historical decisions below remain useful file-level review notes.

This document records the upstream systems examined for DeepLaw 2.0 and the
technical decision for each. It distinguishes a runtime dependency, an
optional external build adapter/tool, possible future code extraction,
architectural reference, and rejection. It does not assert that repository
popularity or an upstream self-reported benchmark transfers to Chinese legal
retrieval.

## Decision Rules

DeepLaw accepts upstream work only when it preserves all of these invariants:

- official-source and version metadata remain more authoritative than a rank,
  embedding, graph edge, or generated page;
- public corpus access remains read-only;
- case-private data remains outside the service;
- provider-visible results remain bounded;
- the core works offline with a small dependency and resource footprint;
- every copied file or substantial code fragment has a compatible license,
  pinned source commit, attribution, tests, and a recorded reason;
- derived data is replaceable and cannot change legal validity.

Current decision: none of the reviewed knowledge platforms is a DeepLaw runtime
authority. Pass 8 records a bounded Owner-authorized sibling-reuse path for
OpenWiki and Tolaria; this supersedes the prior blanket no-code-reuse decision
only for the frozen manifest below. No source code from either repository has
been copied into the current source tree or any release artifact. Any future
copy must name the exact file/symbol and target in the manifest before work
starts. Whole-repository vendoring, large upstream runtimes, and product-control
plane adoption remain prohibited; sibling material may not introduce another
Authority, Ledger, Agent runtime, telemetry, or Secret model. The base MCP
runtime stays lightweight. Offline builders may use separately installed OCR/PDF
tools, the optional `document-engine` dependency, and the optional local
Discovery runtime; every derived output remains a candidate subject to
DeepLaw's own source, lifecycle, evidence, and admission policy.

## Reviewed Snapshot

| Project | Commit reviewed | Published license | DeepLaw decision |
| --- | --- | --- | --- |
| [oomol-lab/wiki-graph](https://github.com/oomol-lab/wiki-graph) | `7f916f63cfb9` | Apache-2.0 | Source hierarchy, URI protocol, public grounding, job control, and schema-upgrade reference |
| [langchain-ai/openwiki](https://github.com/langchain-ai/openwiki) | `7531d615216e8cbccf464f66cfbbae3668871c84` (`v0.3.1`) | MIT | Owner-authorized focused reuse under the frozen manifest; no whole vendor/runtime |
| [garrytan/gbrain](https://github.com/garrytan/gbrain) | `5008b287e47b` | MIT | Architectural and algorithm reference; no whole-system dependency |
| [Open-Source-Legal/OpenContracts](https://github.com/Open-Source-Legal/OpenContracts) | `4896de1ef4fb` | MIT | Authority-pack, provenance, annotation, and MCP reference |
| [QuantLaw/legal-data-preprocessing](https://github.com/QuantLaw/legal-data-preprocessing) | `d0952593ce0b` | BSD-2-Clause | Statute hierarchy and snapshot-lineage reference |
| [VectifyAI/PageIndex](https://github.com/VectifyAI/PageIndex) | `f413c66fee0b` | MIT | Optional future experiment for long unstructured documents |
| [OpenSPG/KAG](https://github.com/OpenSPG/KAG) | `fdab15b3929d` | Apache-2.0 | Query-plan and constrained-graph reference |
| [XMUDeepLIT/LegalGraphRAG](https://github.com/XMUDeepLIT/LegalGraphRAG) | `ded4f4e66176` | No repository LICENSE found | Reject code reuse and runtime adoption |
| [infiniflow/ragflow](https://github.com/infiniflow/ragflow) | `14d361aa5116` | Apache-2.0 | Parser-adapter and law-heading reference only |
| [microsoft/graphrag](https://github.com/microsoft/graphrag) | `dac4f721ddc1` | MIT | Future derived-topic research reference only |
| [VectifyAI/OpenKB](https://github.com/VectifyAI/OpenKB) | `0d905e40afa6` | Apache-2.0 | Derived Wiki and Obsidian export reference only |
| [zeroentropy-ai/legalbenchrag](https://github.com/zeroentropy-ai/legalbenchrag) | `431bc8f2488a` | MIT | Retrieval evaluation format and span metrics reference |
| [hoorangyee/LRAGE](https://github.com/hoorangyee/LRAGE) | `a3c6d06db347` | MIT | External research benchmark harness reference |
| [xiaowu0162/LongMemEval-V2](https://github.com/xiaowu0162/LongMemEval-V2) | `6f020ac2fc32` | Apache-2.0 | External long-horizon task/memory protocol and official adapter interface |
| [HUST-AI-HYZ/MemoryAgentBench](https://github.com/HUST-AI-HYZ/MemoryAgentBench) | `455306dcabc3` | MIT | External retrieval, learning, long-range understanding, and forgetting benchmark |
| [geniesinc/Memora](https://github.com/geniesinc/Memora) | `a6493188efc8` | Apache-2.0 | External long-duration remembering/forgetting and cost benchmark |
| [microsoft/STATE-Bench](https://github.com/microsoft/STATE-Bench) | `4efcbf2d4fe6` | MIT | External Agent Learning task-success benchmark |
| [agiresearch/ASB](https://github.com/agiresearch/ASB) | `1f561dccf92d` | MIT | External memory-poisoning and observation-injection benchmark |
| [vectorize-io/agent-memory-benchmark](https://github.com/vectorize-io/agent-memory-benchmark) | `aa9273ab9e34` | Recheck before execution | External Agent task, latency, ingestion, Token, and cost benchmark only |
| [isaacus-dev/legal-rag-bench](https://github.com/isaacus-dev/legal-rag-bench) | `9e30a36d1ef5` | Recheck before execution | External end-to-end legal retrieval/reasoning benchmark only |
| [opendatalab/MinerU](https://github.com/opendatalab/MinerU) | `79d6d8d79fb8` | MinerU Open Source License | Optional structured PDF candidate behind the build-only document engine |
| [PaddlePaddle/PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) | `211989f046cc` | Apache-2.0 | Strong candidate for a second Chinese OCR/layout witness; not yet integrated |
| [docling-project/docling](https://github.com/docling-project/docling) | `e548307e8d32` | MIT | Document IR and provenance reference; not a runtime dependency |
| [Unstructured-IO/unstructured](https://github.com/Unstructured-IO/unstructured) | `c38745b32f53` | Apache-2.0 | Broad ETL reference; not selected as legal canonical representation |
| [datalab-to/marker](https://github.com/datalab-to/marker) | `ef16c2caa29d` | GPL-3.0 | Not selected for the default Apache-distributed build path |
| [datalab-to/surya](https://github.com/datalab-to/surya) | `fe8e2d968462` | GPL-3.0 code; separate model terms | Not selected for default redistribution |
| [refactoringhq/tolaria](https://github.com/refactoringhq/tolaria) | `ab01faa6773136a58285d04cb81e2587c11bac85` | AGPL-3.0-or-later | Owner-authorized focused reuse under same-team authorization; release-time file/contributor-rights confirmation; no whole vendor/runtime |
| [microsoft/onnxruntime](https://github.com/microsoft/onnxruntime) | `v1.27.0` | MIT | Optional local execution dependency for fixed candidate-discovery models |
| [huggingface/tokenizers](https://github.com/huggingface/tokenizers) | `v0.22.2` | Apache-2.0 | Optional fixed-tokenizer execution dependency |
| [xenova/jina-embeddings-v2-small-en](https://huggingface.co/Xenova/jina-embeddings-v2-small-en) | `523cadcb9c2e` | Apache-2.0 model card | Fixed English Discovery profile; weights downloaded explicitly and not redistributed |
| [jinaai/jina-embeddings-v2-base-zh](https://huggingface.co/jinaai/jina-embeddings-v2-base-zh) | `c1ff9086a89a` | Apache-2.0 model card | Fixed Chinese-English Discovery profile; weights downloaded explicitly and not redistributed |
| [tree-sitter/py-tree-sitter](https://github.com/tree-sitter/py-tree-sitter) and official language grammars | core `0.26.0`; JavaScript `0.25.0`; TypeScript `0.23.2`; Java `0.23.5`; Go `0.25.0`; Rust `0.24.2` | MIT | Exact-pinned base-runtime dependencies for bounded compiler-grade code Source IR |
| [tobymao/sqlglot](https://github.com/tobymao/sqlglot) | `30.13.0` | MIT | Exact-pinned base-runtime dependency for bounded compiler-grade SQL Source IR |

Repository commit coordinates identify material reviewed and are not dependency
pins. The explicit Tree-sitter and SQLGlot version coordinates are different:
they are exact base-runtime dependency pins recorded in `uv.lock`.

## Owner-authorized sibling reuse (Pass 8)

Owner authorization permits only a focused, reviewable reuse decision for the two
named sibling repositories. It is not permission to vendor either repository or
adopt a large upstream runtime. The frozen scope is:

- each reuse must bind a concrete PRD outcome and name an exact commit, source
  file/symbol, target file, rights basis, attribution, tests, and security or
  dependency impact before implementation;
- `reuse_mode` is one of `verbatim`, `adapted`, `behavioral`, or `reference`;
- only the individually named files/symbols in the manifest may be considered;
  generated trees, transitive vendor directories, large runtimes, and unrelated
  product code are out of scope;
- reused material remains subordinate to DeepLaw's own source, provenance,
  Authority, Ledger, MCP, and security boundaries and cannot create a second
  Authority, Ledger, Agent runtime, telemetry, or Secret model.

### Unified reuse manifest

The following is the release-review manifest for reuse actually exercised in
Pass 8. `behavioral` means the public outcome and fixtures were independently
re-authored in DeepLaw; `reference` means design review or execution from a
separate exact checkout. Neither mode below incorporates an upstream source
fragment into a DeepLaw release artifact.

| repository | exact commit | source file/symbol | reuse_mode | rights_basis | target file | modifications | attribution | tests | security/dependency impact |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `langchain-ai/openwiki` | `7531d615216e8cbccf464f66cfbbae3668871c84` (`v0.3.1`) | `src/agent/wiki-link-validator.ts:validateWikiInternalLinks` | `reference` | `MIT at exact commit; Owner-authorized sibling reuse` | `docs/V0_13_PASS8_CAPABILITY_GAP_MATRIX.md` | Compared broken-link outcomes with DeepLaw's Registry/Link Index; retained stable Ledger identity and indexed reads; copied no implementation | This manifest and `THIRD_PARTY_NOTICES.md`; no MIT source fragment incorporated | `tests/test_v013_wiki_link_index.py` | No dependency, network, telemetry, runtime, or distributed upstream bytes |
| `langchain-ai/openwiki` | `7531d615216e8cbccf464f66cfbbae3668871c84` (`v0.3.1`) | `src/agent/utils.ts:getUpdateNoopStatus`; `src/agent/utils.ts:createOpenWikiContentSnapshot` | `reference` | `MIT at exact commit; Owner-authorized sibling reuse` | `docs/V0_13_PASS8_CAPABILITY_GAP_MATRIX.md` | Compared content-snapshot/no-op behavior; retained DeepLaw revision-bound projection manifests; copied no implementation | This manifest and `THIRD_PARTY_NOTICES.md`; no MIT source fragment incorporated | `tests/test_v013_projection_incremental.py` | No dependency or runtime impact; no upstream scheduler adopted |
| `langchain-ai/openwiki` | `7531d615216e8cbccf464f66cfbbae3668871c84` (`v0.3.1`) | `src/ingestion/code-mode.ts` managed-block behavior | `reference` | `MIT at exact commit; Owner-authorized sibling reuse` | `docs/V0_13_PASS8_CAPABILITY_GAP_MATRIX.md` | Compared user-content protection; retained DeepLaw canonical/editable/derived ownership separation; copied no implementation | This manifest and `THIRD_PARTY_NOTICES.md`; no MIT source fragment incorporated | `tests/test_knowledge_markdown.py`; `tests/test_v013_projection_ownership.py` | No dependency; did not adopt recursive directory sync, CI injection, or a second Authority model |
| `refactoringhq/tolaria` | `ab01faa6773136a58285d04cb81e2587c11bac85` | `src/utils/wikilinks.ts:blankFencedCodeLines`; `src/utils/wikilinks.ts:extractOutgoingLinks`; `src/utils/wikilinks.test.ts`; `src/utils/wikilinks.table.test.ts`; `tests/smoke/wikilink-traditional-chinese.spec.ts` | `behavioral` | `Owner-declared same-team reuse authorization; published AGPL-3.0-or-later; release-time file/contributor-rights confirmation remains required only if derived source is distributed` | `tests/test_v013_wiki_link_index.py`; `tests/test_v013_wiki_resolver.py` | Independently re-authored Python table/code-fence/alias/CJK fixtures against DeepLaw stable identity and ambiguity semantics; copied no TypeScript implementation or test text | This manifest and `THIRD_PARTY_NOTICES.md`; no AGPL source fragment incorporated | `tests/test_v013_wiki_link_index.py`; `tests/test_v013_wiki_resolver.py` | No Node/Tauri/React/Rust dependency; no runtime, network, telemetry, Secret, Authority, or Ledger change |
| `refactoringhq/tolaria` | `ab01faa6773136a58285d04cb81e2587c11bac85` | `mcp-server/tool-service.js:createMcpToolService`; `mcp-server/vault.js:getNote`; `mcp-server/vault.js:updateNote` | `reference` | `Owner-declared same-team reuse authorization; external checkout remains AGPL-3.0-or-later; no upstream bytes redistributed` | `benchmarks/hosts/run_tolaria_workspace_interop.py`; `benchmarks/hosts/tolaria_workspace_probe.mjs`; `contracts/tolaria-workspace-interop-report.v1.schema.json`; `tests/test_v013_tolaria_workspace_interop.py` | An independently authored development probe imports the exact external service to open/read/update one allowed synthetic note; DeepLaw policy denies protected paths before the call; `expectedMtime` is never treated as a Revision | This manifest, report provenance, and `THIRD_PARTY_NOTICES.md`; upstream remains a separately installed external checkout | `tests/test_v013_tolaria_workspace_interop.py`; exact external report `tolaria_interop_e9df12e58307d1da94ad995a` | External `npm audit` has six known high findings; none are redistributed. Closed child environment; no Secret; OS sandbox not proven; no Core dependency or canonical Ledger write |

### Actual copied/adapted inventory

- Verbatim upstream source in the DeepLaw tree or distribution: **none**.
- Adapted upstream implementation in the DeepLaw tree or distribution: **none**.
- Independently re-authored behavioral inventory:
  `tests/test_v013_wiki_link_index.py` and
  `tests/test_v013_wiki_resolver.py`.
- Independently authored external-execution inventory:
  `benchmarks/hosts/run_tolaria_workspace_interop.py`,
  `benchmarks/hosts/tolaria_workspace_probe.mjs`,
  `contracts/tolaria-workspace-interop-report.v1.schema.json`, and
  `tests/test_v013_tolaria_workspace_interop.py`.
- Exact external upstream bytes executed during the Development probe:
  Tolaria `mcp-server/tool-service.js` and its separately installed locked
  dependencies. They are not part of the DeepLaw source distribution, wheel,
  sdist, SBOM, or runtime dependency graph.

Tolaria's `rights_basis` is explicitly **Owner-declared same-team reuse
authorization**. Before an Apache-2.0 release, the Owner/release reviewer must
confirm that the authorization covers every actual copied file and the rights of
all relevant contributors, not merely the repository as a whole. If an
Apache-2.0 release depends on a separate grant for Tolaria-derived material,
record a grant reference or an irreversible summary in the Owner-managed legal or
release record; the underlying legal document may be kept outside this
repository. Unrecorded oral authorization must not be the sole basis for a
commercial artifact.

The manifest is an authorization and audit aid, not a new product subsystem:
OpenWiki/Tolaria code cannot become DeepLaw authority, a Ledger, an Agent runtime,
telemetry path, a secret manager, or an MCP capability.

## Detailed Decisions

### Wiki Graph

Relevant upstream areas:

- [`.wikg` archive standard](https://github.com/oomol-lab/wiki-graph/blob/7f916f63cfb9df1f5361001167c92a7a7fef2146/docs/en/wikg-standard.md)
- [schema upgrade policy](https://github.com/oomol-lab/wiki-graph/blob/7f916f63cfb9df1f5361001167c92a7a7fef2146/docs/schema-upgrade.md)
- [`knowledge-build`](https://github.com/oomol-lab/wiki-graph/tree/7f916f63cfb9df1f5361001167c92a7a7fef2146/packages/core/src/graph/knowledge-build)
- [`evidence-selection`](https://github.com/oomol-lab/wiki-graph/tree/7f916f63cfb9df1f5361001167c92a7a7fef2146/packages/core/src/graph/evidence-selection)
- [`archive-view/pack.ts`](https://github.com/oomol-lab/wiki-graph/blob/7f916f63cfb9df1f5361001167c92a7a7fef2146/packages/core/src/retrieval/query/archive-view/pack.ts)
- [`wikg-coordinator`](https://github.com/oomol-lab/wiki-graph/tree/7f916f63cfb9df1f5361001167c92a7a7fef2146/packages/core/src/storage/wikg/wikg-coordinator)
- [`runtime/jobs`](https://github.com/oomol-lab/wiki-graph/tree/7f916f63cfb9df1f5361001167c92a7a7fef2146/packages/core/src/runtime/jobs)

Its strongest engineering contributions are a chapter-preserving portable
archive, one URI grammar for scopes and objects, QID-grounded public entities,
source-linked triples, build cost/watch workflows, library membership, archive
coordination, and centralized adjacent schema upgrades.

DeepLaw does not adopt the runtime wholesale. Wiki Graph's mutation token is a
cache and mutation identity rather than a complete cryptographic content or
publisher proof. Entity disambiguation, triples, Reading Graphs, and summaries
can involve LLM output; quote matching may auto-select a candidate above
heuristic thresholds. Those results are useful discovery material but cannot
inherit DeepLaw `human_verified`, legal authority, or instruction status.
Its Node.js build jobs and write-capable CLI also do not fit DeepLaw's one-leaf,
read-only Agent boundary.

Decision: learn from the archive/URI/control-plane contracts. Future source
hierarchy, vault libraries, background compilation, public-entity grounding,
and graph projections remain separate changes gated by migration design,
quarantine, provenance, cost controls, and held-out evaluation. No Wiki Graph
source code is currently copied or distributed.

### gbrain

Relevant upstream files:

- [`src/core/search/hybrid.ts`](https://github.com/garrytan/gbrain/blob/5008b287e47b/src/core/search/hybrid.ts)
- [`src/core/search/return-policy.ts`](https://github.com/garrytan/gbrain/blob/5008b287e47b/src/core/search/return-policy.ts)
- [`src/core/search/token-budget.ts`](https://github.com/garrytan/gbrain/blob/5008b287e47b/src/core/search/token-budget.ts)
- [`src/core/search/evidence.ts`](https://github.com/garrytan/gbrain/blob/5008b287e47b/src/core/search/evidence.ts)
- [`src/core/search/dedup.ts`](https://github.com/garrytan/gbrain/blob/5008b287e47b/src/core/search/dedup.ts)
- [`src/core/operations.ts`](https://github.com/garrytan/gbrain/blob/5008b287e47b/src/core/operations.ts)
- [retrieval architecture](https://github.com/garrytan/gbrain/blob/5008b287e47b/docs/architecture/RETRIEVAL.md)
- [evaluation metric glossary](https://github.com/garrytan/gbrain/blob/5008b287e47b/docs/eval/METRIC_GLOSSARY.md)

Useful concepts:

- exact title and alias evidence;
- best-chunk-per-page max pooling and deduplication;
- hybrid candidate fusion and bounded reranking;
- adaptive result limits and token budgets;
- evidence labels instead of opaque raw similarity scores;
- captured-query replay, Top-1 stability, Jaccard stability, and paired
  bootstrap comparisons;
- one operation contract generating multiple client surfaces.

Reasons not to depend on gbrain directly:

- it is a Bun/TypeScript personal-knowledge system coupled to PGLite or
  Postgres/pgvector, background jobs, embeddings, model providers, and a large
  write-capable operation catalogue;
- Markdown and personal memory are its primary domain, not reviewed immutable
  legal versions;
- its full MCP surface is much larger than the one-tool read-only contract
  DeepLaw needs;
- its published BrainBench results use a small generated personal-knowledge
  corpus and are not evidence for Chinese statutes, temporal accuracy, or
  citation fidelity;
- its approximate `chars / 4` token accounting is not suitable for Chinese.

Decision: reimplement only the minimal ranking, budget, and evaluation ideas
that prove useful in DeepLaw's own types. If future work copies a pure module,
it requires a separate change recording the exact file, commit, MIT notice,
adaptation, and tests.

### Tesseract And Poppler

The first-party `deeplaw-vision-consensus` pipeline uses separately installed
[Tesseract OCR](https://github.com/tesseract-ocr/tesseract) and Poppler's
[`pdftoppm`](https://gitlab.freedesktop.org/poppler/poppler). The current
pipeline renders PDF pages to temporary PNG files at 300 DPI, then invokes
Tesseract with `chi_sim+eng` and page segmentation mode 3 only for suspicious
native pages. New builds retain page image/native/OCR/selected hashes, weighted
confidence, native/OCR consistency, risk flags and review status.

This is process integration, not copied upstream source. DeepLaw does not
bundle either executable or Tesseract language data. Tesseract 5.5.2 publishes
an [Apache-2.0 license](https://github.com/tesseract-ocr/tesseract/blob/5.5.2/LICENSE);
Poppler publishes its own
[GPL-family notices](https://gitlab.freedesktop.org/poppler/poppler/-/blob/master/COPYING),
and exact package contents may carry additional component notices. A release
that bundles or redistributes any executable or data must review the exact
version and satisfy its notices, source, and other distribution obligations.
See [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md).

### OpenContracts

Relevant upstream files:

- [authority-pack authoring](https://github.com/Open-Source-Legal/OpenContracts/blob/4896de1ef4fb/docs/guides/authoring-authority-packs.md)
- [`base_authority_source_provider.py`](https://github.com/Open-Source-Legal/OpenContracts/blob/4896de1ef4fb/opencontractserver/pipeline/base/base_authority_source_provider.py)
- [`authority_gate_service.py`](https://github.com/Open-Source-Legal/OpenContracts/blob/4896de1ef4fb/opencontractserver/enrichment/services/authority_gate_service.py)
- [PAWLS page-aware format](https://github.com/Open-Source-Legal/OpenContracts/blob/4896de1ef4fb/docs/architecture/pawls-format.md)
- [annotation JSON](https://github.com/Open-Source-Legal/OpenContracts/blob/4896de1ef4fb/docs/architecture/data_model/annotation_json.md)
- [MCP tools](https://github.com/Open-Source-Legal/OpenContracts/blob/4896de1ef4fb/opencontractserver/mcp/tools.py)
- [reference-web versioning](https://github.com/Open-Source-Legal/OpenContracts/blob/4896de1ef4fb/docs/architecture/reference-web-versioning.md)

Useful concepts include declarative authority packs, separation of locating a
source from fetching it, source/license gates, host allowlists, page/character
annotations, bounded reads, and corpus-scoped authorization.

Reasons not to adopt the platform are its Django, Postgres/pgvector, Redis,
Celery, GraphQL, React, annotation, and collaboration scope. Its own versioning
design also records that already-resolved references may continue pointing at
superseded documents after an authority update. DeepLaw needs explicit
current/pinned/as-of semantics before citation edges can be authoritative.

Decision: use its authority-provider and coordinate-preservation concepts, not
its service runtime or data model wholesale.

### QuantLaw legal-data-preprocessing

Relevant upstream files:

- [`xml-schema.xsd`](https://github.com/QuantLaw/legal-data-preprocessing/blob/d0952593ce0b/xml-schema.xsd)
- [`hierarchy_graph.py`](https://github.com/QuantLaw/legal-data-preprocessing/blob/d0952593ce0b/statutes_pipeline_steps/hierarchy_graph.py)
- [`snapshot_mapping_index.py`](https://github.com/QuantLaw/legal-data-preprocessing/blob/d0952593ce0b/statutes_pipeline_steps/snapshot_mapping_index.py)
- [`snapshot_mapping_edgelist.py`](https://github.com/QuantLaw/legal-data-preprocessing/blob/d0952593ce0b/statutes_pipeline_steps/snapshot_mapping_edgelist.py)

Its strongest contribution is a structure-first representation of statutes,
hierarchy edges, cross-references, dated snapshots, and mappings between
snapshots. Exact-text and same-citation matches can inspire deterministic
lineage candidates. Containment and fuzzy matches must remain proposals until
reviewed; they cannot automatically establish that two Chinese provisions are
the same legal version.

The repository targets older Python and dependency versions and source formats
for other jurisdictions. Decision: borrow the model and test cases, not the
installed pipeline. Any future port must preserve the BSD-2-Clause notice.

### PageIndex

Relevant upstream files:

- [`page_index.py`](https://github.com/VectifyAI/PageIndex/blob/f413c66fee0b/pageindex/page_index.py)
- [`retrieve.py`](https://github.com/VectifyAI/PageIndex/blob/f413c66fee0b/pageindex/retrieve.py)
- [`client.py`](https://github.com/VectifyAI/PageIndex/blob/f413c66fee0b/pageindex/client.py)

PageIndex uses LLM calls to derive a document tree, node summaries, and a
reasoned node selection. That may help navigate long judgments, reports, or
other documents without reliable headings. Statutes already have explicit
part/chapter/section/article structure and should be parsed deterministically.

Decision: no public-law runtime dependency. A future case-document experiment
must be opt-in, provider/privacy aware, evaluated against a deterministic
heading tree, and kept outside the public DeepLaw corpus.

### KAG / OpenSPG

Relevant upstream files:

- [`schema_constraint_extractor.py`](https://github.com/OpenSPG/KAG/blob/fdab15b3929d/kag/builder/component/extractor/schema_constraint_extractor.py)
- [`kag_retrieve_output_merger.py`](https://github.com/OpenSPG/KAG/blob/fdab15b3929d/kag/common/tools/algorithm_tool/kag_retrieve_output_merger.py)
- [`exact_one_hop_select.py`](https://github.com/OpenSPG/KAG/blob/fdab15b3929d/kag/common/tools/algorithm_tool/graph_retriever/path_select/exact_one_hop_select.py)
- [`mcp_server.py`](https://github.com/OpenSPG/KAG/blob/fdab15b3929d/kag/bin/commands/mcp_server.py)

DeepLaw can learn from schema-constrained graph construction and a query plan
that chooses exact, text, graph, and numeric operators. It does not need the
OpenSPG engine, Docker services, graph-store stack, agent solver, or broad
dependency set.

Decision: architectural reference only. Legal version and citation edges must
come from deterministic parsing and review, not unconstrained LLM OpenIE.

### LegalGraphRAG

Relevant upstream files:

- [`core/LegalGraphRAG.py`](https://github.com/XMUDeepLIT/LegalGraphRAG/blob/ded4f4e66176/core/LegalGraphRAG.py)
- [`feature_graph.py`](https://github.com/XMUDeepLIT/LegalGraphRAG/blob/ded4f4e66176/core/graph_construct/feature_graph.py)
- [`judge_law.py`](https://github.com/XMUDeepLIT/LegalGraphRAG/blob/ded4f4e66176/core/judge/judge_law.py)

The repository is a CAIL/CMDL judgment-prediction evaluation framework, not a
versioned public legal source. Its current design includes LLM-generated case
features, hard-coded embedding-service assumptions, in-memory NetworkX/pickle
artifacts, and prediction-oriented law/crime selection. It does not provide
official-source provenance, temporal authority, immutable releases, evidence
spans, public/private isolation, or a production read-only MCP contract.

No LICENSE file was present at the reviewed commit. Repository visibility is
not permission to redistribute source. Decision: do not copy code or adopt the
runtime. A separately documented grant from the actual rights holder would
change the licensing analysis, but not the technical decision.

### RAGFlow

Relevant upstream files:

- [`rag/app/laws.py`](https://github.com/infiniflow/ragflow/blob/14d361aa5116/rag/app/laws.py)
- [`rag/nlp/search.py`](https://github.com/infiniflow/ragflow/blob/14d361aa5116/rag/nlp/search.py)
- [`deepdoc/parser/pdf_parser.py`](https://github.com/infiniflow/ragflow/blob/14d361aa5116/deepdoc/parser/pdf_parser.py)
- [`mcp/server/server.py`](https://github.com/infiniflow/ragflow/blob/14d361aa5116/mcp/server/server.py)

Useful references are law-heading-aware parsing, interchangeable parser
backends, visible chunking, and hybrid retrieval evaluation. The full service
requires a much larger Docker and storage stack and exposes generic dataset and
agent capabilities outside DeepLaw's scope. Its broad candidate retrieval
defaults would also reintroduce the context-noise problem DeepLaw is designed
to bound.

Decision: no service or package dependency. Reimplement only small,
Chinese-law-specific heading heuristics if tests show a gap; record Apache-2.0
attribution if code is actually copied.

### Microsoft GraphRAG

Relevant upstream areas:

- [index operations](https://github.com/microsoft/graphrag/tree/dac4f721ddc1/packages/graphrag/graphrag/index/operations)
- [structured search](https://github.com/microsoft/graphrag/tree/dac4f721ddc1/packages/graphrag/graphrag/query/structured_search)
- [`hierarchical_leiden.py`](https://github.com/microsoft/graphrag/blob/dac4f721ddc1/packages/graphrag/graphrag/graphs/hierarchical_leiden.py)
- [Responsible AI transparency note](https://github.com/microsoft/graphrag/blob/dac4f721ddc1/RAI_TRANSPARENCY.md)

GraphRAG is relevant to broad thematic synthesis over large unstructured
corpora. Its standard graph is produced through LLM entity, relationship,
claim, and community-summary extraction; its indexing can be expensive and
requires corpus-specific prompt tuning. Those properties make it unsuitable
for determining Chinese statute structure, legal status, or version lineage.

Decision: future derived-topic benchmark only. Community summaries may inform
a disposable Wiki but cannot enter the authority layer.

### OpenKB

Relevant upstream files:

- [`openkb/agent/compiler.py`](https://github.com/VectifyAI/OpenKB/blob/0d905e40afa6/openkb/agent/compiler.py)
- [`openkb/schema.py`](https://github.com/VectifyAI/OpenKB/blob/0d905e40afa6/openkb/schema.py)
- [`skills/openkb/SKILL.md`](https://github.com/VectifyAI/OpenKB/blob/0d905e40afa6/skills/openkb/SKILL.md)

OpenKB is a closer implementation of the Karpathy-style “LLM Wiki” pattern:
source documents compile into summaries, concepts, entities, and Obsidian-
friendly Markdown. This is useful for a future read-only topic-navigation
export. Generated pages can combine and propagate mistakes across many files
and cannot establish legal text or effect.

Decision: reuse only the derived/rebuildable Wiki separation and export
conventions. Do not use generated Wiki pages as DeepLaw search truth.

### LegalBench-RAG And LRAGE

LegalBench-RAG's
[`run_benchmark.py`](https://github.com/zeroentropy-ai/legalbenchrag/blob/431bc8f2488a/legalbenchrag/run_benchmark.py)
computes character-overlap precision and recall between retrieved and gold
source spans. Its benchmark schema also maps queries to exact file paths and
character intervals. These ideas are well suited to a future Chinese legal
gold set, but its bundled English contract/privacy datasets and their source
licenses do not automatically transfer to DeepLaw.

LRAGE provides common BM25, dense, hybrid, reranker, and legal-dataset research
interfaces. It is useful for offline baseline comparison but brings Java,
Pyserini, model, and often GPU requirements that do not belong in the product
runtime.

Decision: evaluation references only. If metric code is copied, pin the exact
MIT-licensed file and add attribution at that time.

### Tree-sitter and official code grammars

DeepLaw directly installs the official Tree-sitter Python binding and the
official JavaScript, TypeScript, Java, Go, and Rust grammar distributions at
the exact versions listed in the reviewed snapshot. JavaScript and TypeScript
also provide the JSX and TSX grammars. This is dependency use through their
published Python APIs and wheel contents; no upstream source is copied into the
repository.

The parser versions are part of Source IR compilation identity. DeepLaw caps
source bytes, traversed syntax nodes, structural symbols, imports, and
references; syntax recovery is recorded explicitly, and only an explicit
bounded lexical fallback may handle inputs above those limits. Parsed
structure remains derived, untrusted data and never establishes source trust,
approval, legal authority, or permission to execute source code. Exact license
and distribution information is recorded in `THIRD_PARTY_NOTICES.md`, the
lockfile, generated license inventory, and release SBOM.

### SQLGlot

DeepLaw directly installs SQLGlot `30.13.0` and uses its published Python API
to build statement, CTE, table, column, and line-span Source IR. The exact
version and generic dialect profile are compilation identity. Source bytes,
AST nodes, statements, and symbols are bounded; rejected or over-limit input
uses an explicit bounded lexical fallback. DeepLaw never executes SQL, and all
parser output remains untrusted derived structure. No SQLGlot source is copied
or vendored in this repository.

## Reuse Classification

### Current runtime dependency

None of the reviewed knowledge platforms.

The base runtime directly depends on the exact-pinned MIT-licensed Tree-sitter
Python binding and five official language-grammar distributions listed above.
They implement local compiler-grade Source IR for JavaScript/JSX,
TypeScript/TSX, Java, Go, and Rust behind explicit size and inventory bounds.
The base runtime also directly depends on exact-pinned MIT-licensed SQLGlot for
bounded compiler-grade SQL Source IR without a network, model, or execution path.

The optional `deeplaw[discovery]` extra directly depends on ONNX Runtime,
Tokenizers, `huggingface_hub`, and NumPy. DeepLaw implements its own small,
closed local execution and index contract; it does not copy source from a
knowledge platform. Model setup is explicit, revisions and all five files are
hash-pinned, query execution is offline, and the feature remains outside the
base MCP runtime and default Context Compiler.

### Current optional external build tools

- Tesseract OCR plus Poppler `pdftoppm`, only through the explicit first-party
  PDF evidence pipeline; no raw OCR bypass is exposed.
- MinerU through the optional `deeplaw[document-engine]` build extra and a
  bounded page-range adapter. The DeepLaw entrypoint admits only the fixed local
  `pipeline` backend and rejects alternate backends, remote-model/checkpoint
  parameters, and unknown options before importing the optional package. A
  separate explicit setup command pins the model repository revision and verifies
  the exact 15-file size/SHA-256 manifest; ingestion strips upstream model/config
  overrides and runs local-only without auto-download. It is not imported by, or
  required for, MCP query runtime. Structured JSON is treated as a candidate;
  generated Markdown is not accepted as source truth. Dependency findings and
  their product-level reachability assessment are recorded in
  [`SECURITY.md`](../SECURITY.md) and [`security/openvex.json`](../security/openvex.json).

### Suitable for future focused extraction

- A small pure gbrain return-policy, evidence, or evaluation module, after
  adaptation to DeepLaw contracts.
- Exact-match portions of QuantLaw snapshot mapping.
- OpenContracts authority-provider validation concepts.
- RAGFlow legal-heading heuristics.
- LegalBench-RAG span-overlap metrics.

No such code has been copied yet.

### Architectural reference only

- KAG/OpenSPG query planning and constrained graph operators.
- Microsoft GraphRAG broad/global synthesis.
- OpenKB derived Wiki organization.
- PageIndex tree retrieval for long unstructured documents.

### Rejected

- Whole-system adoption of gbrain, OpenContracts, KAG/OpenSPG, RAGFlow, or
  Microsoft GraphRAG.
- LegalGraphRAG code reuse.
- Any LLM-generated or fuzzy-matched legal relationship promoted directly into
  the authority layer.

## Future Reuse Checklist

Before a future change imports or copies upstream code:

1. Complete every unified reuse-manifest field and bind the entry to a concrete
   PRD outcome before copying or adapting anything.
2. Confirm compatibility with DeepLaw's Apache-2.0 distribution and intended
   deployment, including any separate grant and contributor-rights review.
3. Add the required notice and preserve source headers only when the exact code
   is actually copied into the release artifact.
4. Isolate optional heavy dependencies from the core installation.
5. Add tests proving source/version integrity, bounded results, offline
   behavior, and public/private separation.
6. Benchmark against the current deterministic baseline on a held-out Chinese
   legal corpus.
7. Record latency, memory, disk, model/API cost, and failure modes.
8. Remove marketing comparisons not supported by the benchmark.

External authorization that is not committed or otherwise available to
release reviewers is not relied upon by this repository. If a separate license
grant is necessary, preserve it through the project's approved legal and
release process before copying code or redistributing assets.
