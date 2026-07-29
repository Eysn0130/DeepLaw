# DeepLaw 2.0 visual system

The public visual system uses three coordinated levels:

- light frosted glass for the wordmark and the five-second product flow;
- a restrained dark technical diagram for the deeper knowledge lifecycle;
- detailed architecture diagrams only where the README text needs them.

The tracked images are product illustrations, not screenshots of a runtime UI. Check every
visible word, label, arrow, and relationship at full resolution before publishing an update.

## Files

- `brand/deeplaw-2-glass.png`: primary DeepLaw 2.0 wordmark.
- `brand/deeplaw-knowledge-os-hero-v0.6.png`: Image2-generated v0.6 hero showing
  an earlier source-to-Capsule concept. It remains a historical visual and is not the current
  README introduction. Its generation record and alt text live in the adjacent `.prompt.md` file.
- `readme/agent-knowledge-flow-v0.7.png`: current five-second product explanation. It preserves
  the light v0.5 composition while updating the sequence to Sources → Knowledge Vault → Review /
  Recall / Explain → Knowledge Capsule → Agent. Its Image2 edit record and alt text live in the
  adjacent `.prompt.md` file.
- `readme/agent-knowledge-cycle-v0.7.png`: current lifecycle diagram. It preserves the restrained
  v0.5 dark composition while showing Ingest / Review / Recall / Explain / Verify / Deliver around
  the Knowledge Vault. Its Image2 edit record and alt text live in the adjacent `.prompt.md` file.
- `readme/agent-knowledge-vault-v0.7.png`: current inside-the-Vault diagram. It preserves the
  restrained dark Evidence Core composition while showing Sources & Revisions, Knowledge Assets,
  Knowledge Duties, Limits & Gaps, and Receipts & Replay around the Knowledge Vault. Its Image2
  edit record and alt text live in the adjacent `.prompt.md` file.
- `readme/product-flow-glass.png`: Files → DeepLaw Knowledge Base → Locate / Connect /
  Explain → Evidence Pack → Agent. This is the historical v0.5 flow retained as a source asset.
- `readme/knowledge-cycle.png`: Ingest / Organize / Locate / Connect / Explain / Verify,
  with Deliver as the output action. This is the historical v0.5 cycle retained as a source asset.
- `readme/evidence-core.png`: historical Legal Pack-specific Evidence Core diagram showing Sources
  & Versions, Knowledge Map, Evidence Duties, Limits & Gaps, and Receipts & Replay.

## Brand rules

- Public product name: `DeepLaw 2.0`.
- Repository name: `DeepLaw`.
- Python package, CLI, and local configuration prefix: `deeplaw`.
- Describe the system as the `DeepLaw architecture`; do not give the architecture a version.
- Software release versions are independent of the product name and belong in release metadata.
- Core colors: deep ink `#071821`, proof mint `#36CDBB`, cloud white `#F4F7F6`, and one
  gap-amber accent.
- Avoid legal-industry clichés, national or official symbols, fake awards, unsupported
  ranking badges, and visuals that imply adjudication.
- Use the current light v0.7 flow once near the top of the README. Use the dark lifecycle and Vault
  diagrams only after the plain-language introduction, and prefer legibility over visual spectacle.

Apache License 2.0 applies to copyrightable project assets in this repository. It does not
grant rights in third-party source material or constitute trademark clearance.
