# DeepLaw v0.7.0 acceptance matrix

This matrix implements the Owner's release policy: commercial release qualification is independent
from competitive leadership. The formal result must be exactly:

```text
commercial_release_eligible=true
competitive_claim_eligible=false
```

No large-model API key is requested or used by the commercial gate. Static compatibility, plugin
lifecycle, and MCP handshake evidence are never described as a real model-task acceptance result.

## Commercial GA gates

| Gate | Required evidence | Release asset |
| --- | --- | --- |
| Exact version and commit | package, Python runtime, Claude/Codex plugins, Claude marketplace, and OpenCode adapter are exactly `0.7.0`; clean tag commit, tree, `uv.lock`, `pyproject.toml`, and contract inventory are bound | `commercial-release-manifest.json` |
| Linux | full mandatory suite with zero skip plus wheel/sdist install, v0.6→v0.7 upgrade, uninstall and CLI checks | `platform-linux.json`, `pytest-linux.xml`, `distribution-lifecycle-linux.json` |
| macOS | same mandatory and distribution lifecycle gates with zero skip | `platform-darwin.json`, `pytest-darwin.xml`, `distribution-lifecycle-darwin.json` |
| Windows | same gates plus native owner SID, Users/Everyone, inherited ACL, source/model/index files, junction and reparse-point tests; zero skip | `platform-windows.json`, `pytest-windows.xml`, `distribution-lifecycle-windows.json` |
| CLI and data lifecycle | configuration, migration, rollback, snapshot/restore, corruption, file locking, permissions, Golden CLI, and operator surfaces are part of each mandatory suite | three platform reports |
| MCP | stdio framing, one-tool read-only surfaces, exact tool schemas, restricted exclusion, and no write tools are mandatory tests | three platform reports |
| No-model hosts | official Codex, Claude Code, and OpenCode CLIs validate manifests/config, discovery, install, enable/disable, upgrade, removal, MCP handshake, and dual-product isolation in temporary homes | `no-model-host-acceptance.json` |
| Reproducible distributions | two independent builds produce byte-identical wheel and sdist; the first verified bytes are the bytes sent downstream | `reproducible-build.json` |
| OCI | exact verified wheel, locked runtime requirements, pinned base digest, non-root UID/GID, no exposed port, network-none runtime, read-only root, dropped capabilities, no-new-privileges | `deeplaw-0.7.0-linux-amd64.oci.tar`, `oci-release-report.json` |
| Supply chain | all dependency profiles pass audit or exact OpenVEX; CycloneDX SBOM and installed license policy pass | `audit-*.json`, `deeplaw-0.7.0.cdx.json`, `deeplaw-0.7.0-licenses.json`, `openvex.json` |
| Signing and provenance | release assets receive Sigstore keyless OIDC bundles; wheel, sdist, OCI, manifest, and SBOM receive GitHub attestations | `*.sigstore.json`, GitHub attestation service |
| Exact publication | release job publishes the same files assembled and signed; it does not rebuild after verification | `SHA256SUMS`, release workflow |
| Post-release | assets are downloaded from the public GitHub Release, checksums/signatures/provenance are reverified, and wheel/sdist are clean-installed again | `post-release-verification.json` |

Mandatory test evidence does not treat a skip as a pass. Linux and macOS deselect only the two
Windows-native cases; Windows executes them. The historical v0.6 migration test receives a wheel
built from exact commit `e0f1fe3ff01d3026df12673d57c69014c2c4dca4`, binds its SHA-256, and does
not use an unavailable-fixture skip as release evidence.

## Competitive evidence not completed

The following remain required before any best, SOTA, overall-optimum, or all-baselines-surpassed
claim:

1. real model-task E2E on Codex, Claude Code, and OpenCode;
2. complete real results for all 17 registered named systems/configurations;
3. both preregistered secret held-out suites;
4. genuine signatures from independent evaluator organizations.

These four items appear in `competitive_evidence_missing`. They intentionally produce
`competitive_claim_eligible=false` and intentionally do not change
`commercial_release_eligible=true`.
