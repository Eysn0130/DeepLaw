# Third-Party Notices And Research References

Reviewed: 2026-07-28

DeepLaw is licensed separately under the license declared by this repository.
This document records upstream systems reviewed during architecture work and
the optional external parser/OCR integrations. It is not a substitute for the
dependency lockfile, package metadata, generated SBOM, or the complete notices
required for a particular release artifact.

## Current Source-Reuse Status

No source file or substantial code fragment from the research projects listed
below has been copied, modified, vendored, or redistributed in the current
DeepLaw source tree. Their algorithms and architecture informed design
decisions documented in the
[upstream reuse audit](https://github.com/Eysn0130/DeepLaw/blob/main/docs/UPSTREAM_REUSE.md).

If that status changes, this file must be updated in the same change with:

- the exact repository commit and copied file paths;
- copyright and full required license/NOTICE text;
- a description of modifications;
- dependency and model-weight licenses where applicable;
- tests and an SBOM entry.

Claims of separate permission are not relied upon for repository distribution
unless the grant and its scope have been verified through the project's
release process.

## Safe OOXML XML Parser

- Project: [defusedxml](https://github.com/tiran/defusedxml)
- Version pinned by the current runtime: `0.7.1`
- Published terms: Python Software Foundation License
- Integration form: Python dependency used to reject DTD declarations, entity
  expansion, and external references while parsing untrusted DOCX XML members
- Vendored source redistributed by this repository: no

DeepLaw still applies an independent bounded whole-archive inventory, member and
expanded-size limits, compression-ratio limits, duplicate/path/encryption/symlink
rejection, and footnote/endnote budgets. A successful safe XML parse is only
extraction input; it does not establish source authority or activate a Knowledge
Asset.

## Compiler-Grade Code Grammar Dependencies

DeepLaw's base runtime uses the official Tree-sitter Python binding and five
language-grammar distributions for local Source IR construction:

- `tree-sitter==0.26.0`;
- `tree-sitter-javascript==0.25.0` for JavaScript and JSX;
- `tree-sitter-typescript==0.23.2` for TypeScript and TSX;
- `tree-sitter-java==0.23.5`;
- `tree-sitter-go==0.25.0`;
- `tree-sitter-rust==0.24.2`.

Each listed distribution includes MIT license metadata and its own license file.
They are installed as ordinary pinned dependencies; their source is not copied
into this repository. DeepLaw binds the exact core and grammar versions into
`adapter_version`, caps input and syntax-tree inventories, records syntax recovery
as a quality flag, and uses a bounded lexical fallback only when the explicit
size or node limit is exceeded. Parser output remains untrusted derived structure:
it cannot establish source trust, approval, authority, or permission to execute
source code.

## Compiler-Grade SQL Parser Dependency

- Project: [SQLGlot](https://github.com/tobymao/sqlglot)
- Exact runtime version: `30.13.0`
- Published terms: MIT
- Integration form: in-process local parsing through the published Python API
- Network or model dependency: none
- Vendored source redistributed by this repository: no

DeepLaw binds the exact SQLGlot version and generic dialect profile into
`adapter_version`. It emits bounded statement, CTE, table, column, and line-span
Source IR and records an explicit bounded lexical fallback when the closed parser
rejects input or an input/tree limit is exceeded. SQL text and parser output remain
untrusted data and are never executed by the Source Adapter.

## Optional External OCR Tools: Tesseract And Poppler

- OCR project: [tesseract-ocr/tesseract](https://github.com/tesseract-ocr/tesseract)
- Historical candidate version recorded: `5.5.2`
- Tesseract 5.5.2 license:
  [Apache License 2.0](https://github.com/tesseract-ocr/tesseract/blob/5.5.2/LICENSE)
- PDF renderer project: [Poppler](https://gitlab.freedesktop.org/poppler/poppler)
- Poppler license notices:
  [COPYING](https://gitlab.freedesktop.org/poppler/poppler/-/blob/master/COPYING)
- Integration form: optional, separately installed `tesseract` and `pdftoppm`
  executables used only by the offline builder
- Bundled by DeepLaw: no
- Executables or Tesseract language data redistributed by DeepLaw: no

When an operator explicitly selects `vision-consensus`, DeepLaw uses Poppler's
`pdftoppm` to create temporary 300-DPI PNG pages and invokes Tesseract with
`chi_sim+eng` and page segmentation mode 3 only for pages whose native text
fails the quality gate. Current code records both executable versions, page
image/native/OCR/selected-text hashes, weighted OCR confidence, native/OCR
consistency, risk flags, page association, warnings, and the final text hash.

The historical `deeplaw.sqlite/v2` candidate mentioned in the source audit
recorded Tesseract 5.5.2 but did not record the `pdftoppm` version or the full
OCR configuration. Poppler 26.05.0 in that audit describes the separate PDF
inspection environment; it must not be attributed to the historical OCR build.
The current provenance fields are not retroactively added to that immutable
candidate.

Tesseract 5.5.2 publishes Apache-2.0 terms. Poppler's `COPYING` and relevant
source headers publish GPL terms, and a packaged Poppler distribution can
contain components with additional notices. Tesseract language-data packages
also require review for the exact files installed. Invoking separately
installed executables does not mean DeepLaw bundles them; conversely, any
future release artifact that bundles or redistributes the executables or data
must include the exact versions in its SBOM and satisfy all applicable license,
notice, source, and redistribution obligations.

## Optional Structured Document Engine

- Project: [OpenDataLab/MinerU](https://github.com/opendatalab/MinerU)
- Version pinned by the current document-engine extra: `3.4.4`
- Published terms: [MinerU Open Source License](https://github.com/opendatalab/MinerU/blob/master/LICENSE.md)
- Integration form: optional build-time dependency behind the
  `deeplaw[document-engine]` extra and a bounded subprocess adapter
- Bundled by the base DeepLaw runtime: no
- Model weights redistributed by this repository: no
- Model provisioning: an explicit DeepLaw administrative command downloads one
  pinned upstream revision and accepts it only after the exact file set, sizes,
  and SHA-256 values pass; parsing itself is local-only and never downloads
  models

The adapter reads only structured content-list JSON for explicitly requested PDF
page ranges. It rejects symlinks, duplicate JSON keys, non-finite numbers,
oversized output trees, excessive nesting, out-of-range pages, invalid bounding
boxes, process timeouts, and unbounded stdout/stderr. Generated Markdown is not
used as DeepLaw source truth.

DeepLaw treats this engine as one extraction candidate. Its output does not clear
an extraction gate merely because the process succeeded: admission requires an
independent OCR candidate, whole-text agreement, lexical and legal-punctuation
equality, no unresolved table-structure risk, and the page quality policy, or a
separately bound human review. Operators and
distributors must review the exact license, model-weight terms, attribution,
service-use conditions, and any separately granted permission applicable to
their release. This notice is intentionally retained even where a project team
has a separate permission grant.

## Optional Local Candidate Discovery

The `deeplaw[discovery]` extra uses:

- [ONNX Runtime](https://github.com/microsoft/onnxruntime), pinned to `1.27.0`,
  under the MIT License;
- [Hugging Face Tokenizers](https://github.com/huggingface/tokenizers), pinned
  to `0.22.2`, under Apache License 2.0;
- [huggingface_hub](https://github.com/huggingface/huggingface_hub), pinned to
  `0.36.2`, under Apache License 2.0;
- [fsspec](https://github.com/fsspec/filesystem_spec), pinned by platform
  markers, under its BSD-3-Clause license;
- [NumPy](https://github.com/numpy/numpy), pinned by platform markers, under
  its BSD-3-Clause license.

The two selectable model profiles are:

- `xenova/jina-embeddings-v2-small-en` at
  `523cadcb9c2e71c7153fc46016e1fe79acb4f58f`;
- `jinaai/jina-embeddings-v2-base-zh` at
  `c1ff9086a89a1123d7b5eff58055a665db4fb4b9`.

Their model cards publish Apache License 2.0. Model weights are not stored in
this repository or bundled by the base package. An operator must run the
explicit model setup command; DeepLaw then copies only the five pinned files
whose byte sizes and SHA-256 values are compiled into the software. Indexing
and querying execute locally, and the derived index cannot establish source
trust, human approval, legal authority, or case applicability.

## Optional Legacy DOC Converter

- Project: [LibreOffice](https://www.libreoffice.org/)
- Published licensing:
  [MPL 2.0 / LGPLv3+](https://www.libreoffice.org/about-us/licenses)
- Integration form: optional, separately installed `soffice` or `libreoffice`
  executable used for explicit legacy `.doc` ingestion
- Bundled or redistributed by DeepLaw: no

DeepLaw invokes the converter without a shell, with a timeout, an isolated
temporary HOME and user profile, and safe/headless flags. It records the
executable version and converted DOCX SHA-256 before OOXML extraction. A
successful conversion is not human approval. Deployments processing hostile
legacy documents should additionally isolate the converter at the operating
system or container boundary and keep LibreOffice patched; process flags are
not a substitute for a security sandbox.

## Architecture And Algorithm References

The following projects were reviewed but are not current DeepLaw dependencies
and have not contributed copied source code:

| Project | Commit reviewed | Published license at review | Use in DeepLaw |
| --- | --- | --- | --- |
| [oomol-lab/wiki-graph](https://github.com/oomol-lab/wiki-graph) | `7f916f63cfb9` | Apache-2.0 | Source hierarchy, URI, public-entity grounding, job-control, and schema-upgrade reference |
| [garrytan/gbrain](https://github.com/garrytan/gbrain) | `5008b287e47b` | MIT | Hybrid retrieval, evidence, result-budget, and evaluation reference |
| [Open-Source-Legal/OpenContracts](https://github.com/Open-Source-Legal/OpenContracts) | `4896de1ef4fb` | MIT | Authority-source, annotation-coordinate, and bounded-MCP reference |
| [QuantLaw/legal-data-preprocessing](https://github.com/QuantLaw/legal-data-preprocessing) | `d0952593ce0b` | BSD-2-Clause | Statute hierarchy and snapshot-lineage reference |
| [VectifyAI/PageIndex](https://github.com/VectifyAI/PageIndex) | `f413c66fee0b` | MIT | Long-document tree-retrieval research reference |
| [OpenSPG/KAG](https://github.com/OpenSPG/KAG) | `fdab15b3929d` | Apache-2.0 | Query planning and schema-constrained graph reference |
| [XMUDeepLIT/LegalGraphRAG](https://github.com/XMUDeepLIT/LegalGraphRAG) | `ded4f4e66176` | No LICENSE found in the reviewed repository | Rejected for code reuse and runtime adoption |
| [infiniflow/ragflow](https://github.com/infiniflow/ragflow) | `14d361aa5116` | Apache-2.0 | Parser-adapter and legal-heading research reference |
| [microsoft/graphrag](https://github.com/microsoft/graphrag) | `dac4f721ddc1` | MIT | Derived broad-topic graph research reference |
| [VectifyAI/OpenKB](https://github.com/VectifyAI/OpenKB) | `0d905e40afa6` | Apache-2.0 | Derived LLM Wiki and Obsidian export reference |
| [HKUDS/LightRAG](https://github.com/HKUDS/LightRAG) | `bbebdd64272d` | MIT | Local/global/hybrid graph-retrieval and rebuild reference |
| [getzep/graphiti](https://github.com/getzep/graphiti) | `9140123a7282` | Apache-2.0 | Episode, valid-time fact, and incremental graph reference |
| [mem0ai/mem0](https://github.com/mem0ai/mem0) | `b357a5a1b03c` | Apache-2.0 | Low-friction memory API and feedback reference |
| [letta-ai/letta](https://github.com/letta-ai/letta) | `b76da9092518` | Apache-2.0 | Persistent memory-block reference |
| [letta-ai/letta-code](https://github.com/letta-ai/letta-code) | `bd06074da707` | Apache-2.0 | Coding-Agent context and skill host reference |
| [topoteretes/cognee](https://github.com/topoteretes/cognee) | `325acf356a81` | Apache-2.0 | Agent-hook and memory-pipeline reference |
| [MemTensor/MemOS](https://github.com/MemTensor/MemOS) | `344cab73c2d0` | Apache-2.0 | Unified memory, correction, and asynchronous-ingest reference |
| [obsidianmd/jsoncanvas](https://github.com/obsidianmd/jsoncanvas) | `456f843cb293` | MIT | Public JSON Canvas format |
| [zeroentropy-ai/legalbenchrag](https://github.com/zeroentropy-ai/legalbenchrag) | `431bc8f2488a` | MIT | Character-span retrieval metric reference |
| [hoorangyee/LRAGE](https://github.com/hoorangyee/LRAGE) | `a3c6d06db347` | MIT | External legal retrieval benchmark reference |

Published licenses are identified only to explain the reuse review. Because no
code from these projects is currently distributed by DeepLaw, this table does
not assert that their full license texts are incorporated into DeepLaw.

## Benchmark And Marketing Notice

Results published by an upstream project may use different languages,
corpora, labels, retrieval budgets, models, hardware, and cost assumptions.
DeepLaw does not claim to outperform gbrain, PageIndex, KAG, RAGFlow,
GraphRAG, LightRAG, Graphiti, Mem0, Letta, Cognee, MemOS, OpenKB,
WikiGraph, Obsidian, LegalBench-RAG, LRAGE, or all RAG/LLM Wiki systems.

Any future comparative claim must be supported by a reproducible held-out
Chinese legal benchmark that reports source/version correctness, citation-span
precision and recall, context budget, latency, resource use, model/API cost,
and failure cases under equivalent conditions.

## Legal Source Materials

Legal source DOCX/PDF files, downloaded corpora, and generated release
databases are not distributed merely because DeepLaw can parse them. Source
authenticity, copyright, database rights, terms of use, redistribution rights,
and official publication status require a separate review for each corpus
release. GitHub mirrors and local collections do not become authoritative or
redistributable by inclusion in a build manifest.
