# DeepLaw v0.7.0 — commercial GA

DeepLaw v0.7.0 is the first commercial release of the local, single-user Agent Knowledge OS. It
keeps canonical sources, Source IR, Knowledge Assets, indexes, graphs, feedback, and audit state on
the owner's machine. It requires no cloud account, remote database, telemetry service, or model API
key for its default workflow.

## Release decision

The Owner has separated commercial qualification from competitive leadership:

```text
commercial_release_eligible=true
competitive_claim_eligible=false
```

Real model-task E2E, 17 named-baseline results, secret held-out suites, and independent evaluator
signatures are not complete. This release therefore makes no best, SOTA, overall-leadership, or
all-baselines-surpassed claim. Official-CLI configuration/plugin lifecycle and MCP handshake tests
are no-model acceptance only; they are not presented as real Agent task results.

## Product highlights

- Identity v2 separates stable logical source identity, immutable Source Revision, compilation,
  proposal set, Knowledge Revision, and governance revision.
- Multi-format Source Adapters preserve deterministic Source IR and Source Tree structure before
  review-gated many-to-many Knowledge compilation.
- The Evidence-Governed Retrieval Fabric combines exact, BM25, Source Tree, reviewed graph,
  temporal, feedback, optional Dense, and pinned local reranking behind one admission boundary.
- Query Plans, Explain Traces, token-aware Knowledge Capsules, Capsule-bound Run Records, feedback,
  and Proposal Inbox artifacts remain source- and audit-bound.
- Golden CLI, local curses Workbench, Obsidian/Markdown/JSON Canvas projection, Skill Factory,
  resumable jobs, snapshot/restore, migration/rollback, GC, doctor, and selective forgetting form a
  complete local operator lifecycle.
- The general Knowledge OS and Chinese Legal Pack remain different plugins, processes, stores, and
  read-only MCP surfaces.

## Distribution and verification

The release includes a byte-reproducible wheel and sdist, an OCI layout archive that runs non-root
and exposes no port, CycloneDX SBOM, installed-license inventory, dependency audit reports,
OpenVEX, SHA-256 checksums, Sigstore/OIDC bundles, GitHub provenance/SBOM attestations, three-OS
test/lifecycle reports, no-model host acceptance, and the commercial release manifest.
The published Sigstore bundles use the exact workflow identity
`https://github.com/Eysn0130/DeepLaw/.github/workflows/release.yml@refs/heads/main`.

The tagged workflow publishes the already verified exact bytes. A final job downloads the public
GitHub Release assets, verifies checksums, Sigstore identity and GitHub provenance, and performs a
fresh wheel/sdist install, v0.6→v0.7 upgrade, CLI check, and uninstall. See
`docs/INSTALL_UPGRADE_ROLLBACK.md` and `docs/V0_7_ACCEPTANCE_MATRIX.md`.
