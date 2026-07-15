# Upstream Reuse Review

Reviewed: 2026-07-16

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
authority. No source code from these repositories has been copied into DeepLaw.
The base MCP runtime stays lightweight. Offline builders may use separately
installed OCR/PDF tools and the optional `document-engine` dependency; every
output remains a candidate subject to DeepLaw's own page evidence and admission
policy.

## Reviewed Snapshot

| Project | Commit reviewed | Published license | DeepLaw decision |
| --- | --- | --- | --- |
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
| [opendatalab/MinerU](https://github.com/opendatalab/MinerU) | `79d6d8d79fb8` | MinerU Open Source License | Optional structured PDF candidate behind the build-only document engine |
| [PaddlePaddle/PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) | `211989f046cc` | Apache-2.0 | Strong candidate for a second Chinese OCR/layout witness; not yet integrated |
| [docling-project/docling](https://github.com/docling-project/docling) | `e548307e8d32` | MIT | Document IR and provenance reference; not a runtime dependency |
| [Unstructured-IO/unstructured](https://github.com/Unstructured-IO/unstructured) | `c38745b32f53` | Apache-2.0 | Broad ETL reference; not selected as legal canonical representation |
| [datalab-to/marker](https://github.com/datalab-to/marker) | `ef16c2caa29d` | GPL-3.0 | Not selected for the default Apache-distributed build path |
| [datalab-to/surya](https://github.com/datalab-to/surya) | `fe8e2d968462` | GPL-3.0 code; separate model terms | Not selected for default redistribution |

Commit pins identify the material reviewed; they are not dependency pins
because these projects are not imported into the DeepLaw runtime.

## Detailed Decisions

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

## Reuse Classification

### Current runtime dependency

None of the reviewed platforms.

### Current optional external build tools

- Tesseract OCR plus Poppler `pdftoppm`, only through the explicit first-party
  PDF evidence pipeline; no raw OCR bypass is exposed.
- MinerU through the optional `deeplaw[document-engine]` build extra and a
  bounded page-range adapter. It is not imported by, or required for, MCP query
  runtime. Structured JSON is treated as a candidate; generated Markdown is not
  accepted as source truth.

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

1. Record repository, exact commit, file path, copyright, and license.
2. Confirm compatibility with DeepLaw's Apache-2.0 distribution and intended
   deployment.
3. Add the required notice and preserve source headers.
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
