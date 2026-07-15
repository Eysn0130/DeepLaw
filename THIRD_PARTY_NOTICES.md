# Third-Party Notices And Research References

Reviewed: 2026-07-15

DeepLaw is licensed separately under the license declared by this repository.
This document records upstream systems reviewed during architecture work and
the optional external parser/OCR integrations. It is not a substitute for the
dependency lockfile, package metadata, generated SBOM, or the complete notices
required for a particular release artifact.

## Current Source-Reuse Status

No source file or substantial code fragment from the research projects listed
below has been copied, modified, vendored, or redistributed in the current
DeepLaw source tree. Their algorithms and architecture informed design
decisions documented in [`docs/UPSTREAM_REUSE.md`](docs/UPSTREAM_REUSE.md).

If that status changes, this file must be updated in the same change with:

- the exact repository commit and copied file paths;
- copyright and full required license/NOTICE text;
- a description of modifications;
- dependency and model-weight licenses where applicable;
- tests and an SBOM entry.

Claims of separate permission are not relied upon for repository distribution
unless the grant and its scope have been verified through the project's
release process.

## Optional External Adapter: MinerU

- Project: [opendatalab/MinerU](https://github.com/opendatalab/MinerU)
- Commit reviewed: `79d6d8d79fb8`
- Version reviewed: `3.4.4`
- License text:
  [MinerU Open Source License](https://github.com/opendatalab/MinerU/blob/79d6d8d79fb8/LICENSE.md)
- Integration form: optional, separately installed local `mineru` executable
- Bundled by DeepLaw: no
- MinerU source or model weights redistributed by DeepLaw: no

DeepLaw invokes MinerU only after an operator explicitly chooses the MinerU
PDF fallback, has preinstalled its models, and sets
`MINERU_MODEL_SOURCE=local`. The adapter reasserts that value in the child
environment, passes the local input path and an isolated temporary output
directory as subprocess arguments, and fixes MinerU's `pipeline` backend. It
prefers a generated `content_list.json` derivative and falls back to Markdown,
records the resolved MinerU version for either path, then deletes the temporary
directory. The core DeepLaw installation does not download MinerU, its models,
or a cloud parser.

This local-mode check prevents DeepLaw's adapter from relying on MinerU's
normal model-download path; it is not an OS-level network sandbox. Operators
that require stronger egress isolation must provide it around the offline
build process.

The reviewed MinerU license applies Apache License 2.0 plus additional terms.
Those terms include:

- a separate commercial-license requirement when the stated consolidated MAU
  or monthly-revenue threshold is reached;
- a clear and prominent attribution obligation for online third-party
  services based on MinerU;
- automatic termination conditions for specified violations.

Accordingly, MinerU must not be described as an Apache-2.0-only component.
Operators and distributors are responsible for reviewing the complete current
license, model licenses, and deployment obligations. DeepLaw's architecture
keeps MinerU optional and external so its dependency and license surface does
not silently become part of the core runtime.

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

When an operator explicitly selects the Tesseract fallback, DeepLaw uses
Poppler's `pdftoppm` to create temporary 300-DPI PNG pages and invokes
Tesseract with `chi_sim+eng` and page segmentation mode 3. Current code records
the resolved Tesseract and `pdftoppm` version strings, those settings, page
association, warnings, and an extracted-text hash in a newly built release. It
does not record word-level OCR coordinates or confidence values.

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

## Architecture And Algorithm References

The following projects were reviewed but are not current DeepLaw dependencies
and have not contributed copied source code:

| Project | Commit reviewed | Published license at review | Use in DeepLaw |
| --- | --- | --- | --- |
| [garrytan/gbrain](https://github.com/garrytan/gbrain) | `5008b287e47b` | MIT | Hybrid retrieval, evidence, result-budget, and evaluation reference |
| [Open-Source-Legal/OpenContracts](https://github.com/Open-Source-Legal/OpenContracts) | `4896de1ef4fb` | MIT | Authority-source, annotation-coordinate, and bounded-MCP reference |
| [QuantLaw/legal-data-preprocessing](https://github.com/QuantLaw/legal-data-preprocessing) | `d0952593ce0b` | BSD-2-Clause | Statute hierarchy and snapshot-lineage reference |
| [VectifyAI/PageIndex](https://github.com/VectifyAI/PageIndex) | `f413c66fee0b` | MIT | Long-document tree-retrieval research reference |
| [OpenSPG/KAG](https://github.com/OpenSPG/KAG) | `fdab15b3929d` | Apache-2.0 | Query planning and schema-constrained graph reference |
| [XMUDeepLIT/LegalGraphRAG](https://github.com/XMUDeepLIT/LegalGraphRAG) | `ded4f4e66176` | No LICENSE found in the reviewed repository | Rejected for code reuse and runtime adoption |
| [infiniflow/ragflow](https://github.com/infiniflow/ragflow) | `14d361aa5116` | Apache-2.0 | Parser-adapter and legal-heading research reference |
| [microsoft/graphrag](https://github.com/microsoft/graphrag) | `dac4f721ddc1` | MIT | Derived broad-topic graph research reference |
| [VectifyAI/OpenKB](https://github.com/VectifyAI/OpenKB) | `0d905e40afa6` | Apache-2.0 | Derived LLM Wiki and Obsidian export reference |
| [zeroentropy-ai/legalbenchrag](https://github.com/zeroentropy-ai/legalbenchrag) | `431bc8f2488a` | MIT | Character-span retrieval metric reference |
| [hoorangyee/LRAGE](https://github.com/hoorangyee/LRAGE) | `a3c6d06db347` | MIT | External legal retrieval benchmark reference |

Published licenses are identified only to explain the reuse review. Because no
code from these projects is currently distributed by DeepLaw, this table does
not assert that their full license texts are incorporated into DeepLaw.

## Benchmark And Marketing Notice

Results published by an upstream project may use different languages,
corpora, labels, retrieval budgets, models, hardware, and cost assumptions.
DeepLaw does not claim to outperform gbrain, MinerU, PageIndex, KAG, RAGFlow,
GraphRAG, OpenKB, LegalBench-RAG, LRAGE, or all RAG/LLM Wiki systems.

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
