# Third-Party Notices And Research References

Reviewed: 2026-08-11

DeepLaw is licensed separately under the license declared by this repository.
This document records upstream systems reviewed during architecture work and
the optional external parser/OCR integrations. It is not a substitute for the
dependency lockfile, package metadata, generated SBOM, or the complete notices
required for a particular release artifact.

## Current Source-Reuse Status

No source file or substantial code fragment from OpenWiki, Tolaria, or the
other research projects listed below has been copied, modified, vendored, or
redistributed in the current DeepLaw source tree. Pass 8 does exercise
Owner-authorized focused `behavioral` and `reference` reuse for OpenWiki and
Tolaria. Their algorithms, fixtures, and architecture informed independently
authored DeepLaw tests and development probes documented in the
[upstream reuse audit](https://github.com/Eysn0130/DeepLaw/blob/main/docs/UPSTREAM_REUSE.md).

The actual Pass 8 inventory is:

- independently re-authored DeepLaw behavior tests for Wikilink tables, code
  fences, aliases, and Traditional Chinese text, informed by Tolaria's exact
  frozen test files;
- independently authored comparison documentation for OpenWiki link
  validation, snapshot/no-op, and managed-content behavior;
- an independently authored, development-only interoperability probe that
  imports Tolaria's own tool service from a separate exact AGPL checkout to
  open, read, and update one synthetic allowed note.

The external Tolaria checkout, Node dependencies, and tool-service source are
not included in DeepLaw's source distribution, wheel, sdist, runtime dependency
graph, or SBOM. Its dependency audit reported six known high findings; those
dependencies are external and not redistributed by DeepLaw. Because no MIT or
AGPL source/substantial fragment is incorporated, this repository does not add
an incorporated-code MIT or AGPL license notice for these rows. If a future
artifact copies or derives from upstream source, the exact copyright and
license/NOTICE obligations must be added before distribution.

Owner authorization is frozen to the individually named files/symbols and target
paths in that audit's unified reuse manifest. Whole-repository vendor trees,
large upstream runtimes, and product-control-plane code remain out of scope.
Only when code is actually copied into a release artifact must this file add the
MIT, AGPL-3.0-or-later, or separate-grant notice required for that exact code;
behavioral/reference review and separate external execution are not by
themselves incorporated-code license notices.

If that status changes, this file must be updated in the same change with:

- the exact repository commit and copied file paths;
- copyright and full required license/NOTICE text;
- a description of modifications;
- dependency and model-weight licenses where applicable;
- tests and an SBOM entry.

Claims of separate permission are not relied upon for repository distribution
unless the grant and its scope have been verified through the project's release
process. An unrecorded oral authorization is not a sufficient sole basis for a
commercial artifact.

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

## Obsidian Bridge Build Dependencies

The optional desktop plugin under `adapters/obsidian/plugin` uses pinned
development-only packages: `obsidian==1.13.1` (MIT), `esbuild==0.28.1` (MIT),
`typescript==5.9.3` (Apache-2.0), `tsx==4.23.1` (MIT), and
`@types/node==22.20.1` (MIT). Obsidian is external at bundle/runtime; DeepLaw
does not redistribute the Obsidian application. The plugin bundle contains
DeepLaw bridge code and excludes the Obsidian API module.

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

## Optional Linux CUDA Runtime Dependencies

On Linux, the pinned `torch==2.13.0` dependency selected only by the optional
`deeplaw[document-engine]` extra resolves the following NVIDIA packages:

- `cuda-bindings==13.3.1` and `cuda-toolkit==13.0.3.0`;
- `nvidia-cublas==13.1.1.3`, `nvidia-cuda-cupti==13.0.85`,
  `nvidia-cuda-nvrtc==13.0.88`, and `nvidia-cuda-runtime==13.0.96`;
- `nvidia-cudnn-cu13==9.20.0.48`, `nvidia-cufft==12.0.0.61`,
  `nvidia-cufile==1.15.1.6`, and `nvidia-curand==10.4.0.35`;
- `nvidia-cusolver==12.0.4.66`, `nvidia-cusparse==12.6.3.3`,
  `nvidia-cusparselt-cu13==0.8.1`, and `nvidia-nccl-cu13==2.29.7`;
- `nvidia-nvjitlink==13.3.33` and `nvidia-nvshmem-cu13==3.4.5`.

The installed distributions identify NVIDIA software or proprietary terms;
`cuda-toolkit==13.0.3.0` publishes no license field in its installed metadata.
They are exact-version reviewed exceptions in the generated license inventory,
not open-source approvals. NVIDIA publishes the governing
[CUDA Toolkit EULA](https://docs.nvidia.com/cuda/eula/index.html), including
use, redistribution, notice, and export-control conditions.

DeepLaw's wheel, sdist, and default OCI image do not contain or redistribute
these NVIDIA package bytes. A package installer downloads them separately only
when an operator explicitly installs the `document-engine` extra on Linux. The
operator is responsible for accepting and complying with NVIDIA's current terms;
any version, metadata, bundling, or distribution change returns the release
license gate to `review_required`.

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

The current v0.13 Kernel compatibility baseline binds OpenWiki released v0.3.1
to peeled commit `630eb9ec3fa22a4bed2d347fc3ea3a6a3bd22abc`. The reviewed
coordinate retained in this notice is `7531d615216e8cbccf464f66cfbbae3668871c84`,
solely as a package-version-0.3.1 review snapshot.

| Project | Commit reviewed | Published license at review | Use in DeepLaw |
| --- | --- | --- | --- |
| [oomol-lab/wiki-graph](https://github.com/oomol-lab/wiki-graph) | `7f916f63cfb9` | Apache-2.0 | Source hierarchy, URI, public-entity grounding, job-control, and schema-upgrade reference |
| [langchain-ai/openwiki](https://github.com/langchain-ai/openwiki) | `7531d615216e8cbccf464f66cfbbae3668871c84` (package-version-0.3.1 review snapshot) | MIT | Owner-authorized behavioral/reference review under frozen manifest; no source copied or redistributed |
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
| [obsidianmd/obsidian-api](https://github.com/obsidianmd/obsidian-api) | `cc1744324150` | MIT | Plugin lifecycle, command, workspace, and event API reference |
| [obsidianmd/obsidian-sample-plugin](https://github.com/obsidianmd/obsidian-sample-plugin) | `23c165fd362d` | 0BSD | Official build layout and external API bundling reference |
| [refactoringhq/tolaria](https://github.com/refactoringhq/tolaria) | `ab01faa6773136a58285d04cb81e2587c11bac85` | AGPL-3.0-or-later | Owner-authorized behavioral/reference reuse and separate external-source probe; no source copied or redistributed |
| [zeroentropy-ai/legalbenchrag](https://github.com/zeroentropy-ai/legalbenchrag) | `431bc8f2488a` | MIT | Character-span retrieval metric reference |
| [hoorangyee/LRAGE](https://github.com/hoorangyee/LRAGE) | `a3c6d06db347` | MIT | External legal retrieval benchmark reference |

Published licenses are identified only to explain the reuse review. Because no
code from these projects is currently distributed by DeepLaw, this table does
not assert that their full license texts are incorporated into DeepLaw. If a
future release includes actual copied code, the exact file inventory, preserved
headers, applicable MIT/AGPL/separate-grant notices, tests, and SBOM entry must
be added before distribution.

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
