# Security Policy

DeepLaw 2.0 is a local-first Agent Knowledge OS with a separate Chinese Legal
Pack. Both Agent/MCP surfaces are read-only; persistent Knowledge Asset and
Legal Pack administration is local CLI work. The repository distributes code
under Apache License 2.0; it does not distribute legal-source packages, case
documents, generated vault/release databases, or OCR corpora.

## Supported versions

Security fixes are evaluated for the current software release, `v0.4.0`, and
the `main` branch. Older versions, local knowledge-release artifacts, and
third-party packages are not separately supported unless a release notice says
otherwise.

## Report a vulnerability privately

Do not open a public issue, discussion, pull request, or social-media post with
vulnerability details.

Use the repository Security page and select **Report a vulnerability**:

<https://github.com/Eysn0130/DeepLaw/security/advisories/new>

If that control is not available, do not disclose the details publicly. Open
at most a detail-free issue asking the maintainer to enable a private reporting
channel, then wait for a private channel before sharing technical information.
Do not place an exploit, affected path, credential, private identifier, or
sensitive log in that public request.

Include privately, when safe:

- the affected commit or version;
- impact and the boundary crossed;
- a minimal reproduction using synthetic or source-free data;
- whether credentials, case-private data, legal-source text, or release
  artifacts may have been exposed;
- a suggested mitigation, if known.

Never send live credentials, real case materials, private user identifiers,
source DOCX/PDF files, generated SQLite releases, full OCR text, or unredacted
host logs. Describe sensitive material and provide the smallest synthetic
reproduction instead.

The project does not currently promise a response or remediation SLA. The
maintainer will coordinate disclosure and remediation on a best-effort basis.
Please keep the report private until a fix or an agreed disclosure date exists.

## Security scope

Examples of security-relevant reports include:

- a write path or command execution reachable through the read-only runtime;
- path traversal, symlink escape, or release-boundary bypass;
- receipt, hash, release pinning, or immutable-database verification bypass;
- official-catalog signature bypass, signing-key exposure, or trust-store confusion;
- leakage of credentials, case-private data, host paths, or provider-visible
  data beyond the documented budget;
- cross-vault reads, restricted-asset disclosure, trust laundering, memory
  poisoning, stored prompt injection, or unauthorized activation;
- Knowledge Capsule/package hash bypass, unsafe package expansion, or a
  portable import becoming active without explicit local review;
- unsafe archive, parser, MCP, or dependency behavior with a concrete impact.

Legal interpretation disagreements, source-currentness corrections, retrieval
quality suggestions, and documentation errors are normally not security
vulnerabilities. They may be reported through a public issue only when the
report contains no private, licensed, or otherwise restricted material.

## Signing-key custody

The single-maintainer catalog-signing key is stored outside the repository at
`~/.config/deeplaw/signing/official-catalog-ed25519.pem` by default. Its parent
directory is mode `0700` and the key is mode `0600`; only public keys and detached
signatures belong in Git. `DEEPLAW_SIGNING_KEY_FILE` may point to another dedicated
owner-only location. Never attach the private key to an issue, pull request, CI
secret dump, log, backup artifact, or release package.

If key exposure is suspected, stop signing, report it through the private channel,
publish a package update whose trust store revokes the affected public key and adds
a replacement, then sign the next monotonic catalog with the replacement. Existing
clients still require the trusted package update; the catalog signature mechanism is
not an online revocation or freeze-detection service.

## Knowledge Asset threat boundary

Knowledge vaults are isolated by physical directories and operating-system
owner permissions. This is a single-user local security boundary, not
multi-tenant authorization or encryption at rest. Deployments that serve
multiple users require an external authenticated service boundary; sharing one
vault path is unsupported.

Source files, conversation exports, tool results, packages, and generated
lessons are untrusted inputs. They compile to proposed or quarantined assets.
They do not become Agent-visible until a human explicitly approves each asset.
Instruction-like and invisible-control content quarantines both compiled and
manual proposals. Quarantine activation requires an additional explicit risk
confirmation. Even approved source content is data unless the asset is an approved
constraint/rule/procedure.

DOCX is treated as an untrusted ZIP container. OOXML members have byte and
compression-ratio bounds, and XML parsing rejects DTD declarations, entity
expansion, and external references before text extraction.

General Knowledge Assets always declare `legal_authority=false`. A local user
cannot assert `verified_source` through the current CLI or store API. Official
legal authority and user-private legal references remain separate Legal Pack
scopes; neither can be rewritten through the Agent interface.

The optional `knowledge_support` server:

- opens exactly one selected vault read-only;
- exposes only `search`, `get`, `context`, `verify`, and `inspect`;
- excludes inactive and restricted assets and strips local filesystem paths;
- reconciles event history with current Asset/source/relation/FTS state and
  verifies source bytes for selected source-bound Assets;
- returns bounded task context and never writes feedback or memory.

Read-only is a DeepLaw MCP and plugin boundary, not an operating-system
sandbox. A host that separately grants an Agent arbitrary shell or filesystem
access under the vault owner's account also grants access to offline
administration and local files; DeepLaw cannot distinguish that process from
the human owner. Hosts must deny Agent writes to `~/.deeplaw`, keep
administrative CLI execution outside the Agent sandbox, or run the read-only
MCP under a separate OS identity. Integrity checks fail closed on ordinary
release or vault tampering, but they do not turn same-owner arbitrary code
execution into human authorization.

Portable `.dlk` v1 packages hash every payload and all identity-bearing
manifest fields, but they do not yet authenticate a publisher. Every imported
asset is therefore marked `untrusted` and `quarantined`. Do not treat a valid
package hash as proof of authorship.

The SQLite event chain plus current-state replay detects accidental or partial
tampering, including a status-only edit or forged FTS projection. Stable
database and source-file fingerprints cache only unchanged verified state; a
pinned reader rejects a replaced database. Because the
head is stored in the same local vault, an attacker with arbitrary write access
to the vault can rebuild both events and head. It is not a remote transparency
log or external signature. Preserve trusted backups and use signed releases
when publisher identity is required.

## Dependency and document-engine boundary

The default DeepLaw runtime and both read-only MCP servers do not import the
optional document engine. The engine is an offline operator/build dependency,
not an Agent tool. Model provisioning is a separate explicit administrative
operation, `deeplaw document-engine setup`; it downloads one pinned repository
revision and writes an owner-only configuration only after the exact 15-file
set, byte sizes, and SHA-256 values pass. `status` fully rehashes that bundle.
No ingestion, MCP, search, or context operation provisions models.

The first-party engine entrypoint accepts only:

- `--version`, which reports the pinned engine and model-manifest identity
  without importing the optional engine; or
- the exact bounded `-p/-o/-m/-b/-l/-s/-e` command emitted by
  `src/deeplaw/document_engine.py`, with `-b pipeline`, method
  `auto|txt|ocr`, absolute local input/output paths, and at most 5,000 pages.

Unknown, duplicate, reordered, remote-model, checkpoint, VLM, hybrid, training,
and repository options are rejected before the optional upstream package is
imported. Before that import, DeepLaw verifies every pinned model byte, removes
all inherited `MINERU_*` overrides, creates a minimal temporary local-only
configuration, and sets the model clients to offline mode. A missing, extra,
modified, unsafe, or group/world-writable bundle fails closed; parsing never
falls back to a download. The parent adapter also applies wall-clock, output,
file-count, JSON, text, process-memory, CPU, file-size, and file-descriptor
bounds. Engine output is untrusted candidate data and cannot directly establish
legal authority.

The frozen default dependency graph has no findings in the documented
`pip-audit` gate. The optional document-engine graph currently includes
`transformers==4.57.6` because the pinned pipeline dependency requires
`transformers<5`. `pip-audit` reports four unique PYSEC identifiers (five
records) against code paths for X-CLIP conversion, Trainer checkpoint restore,
user-selected causal-model repositories, and LightGlue remote models. Those
paths are outside DeepLaw's closed pipeline execution surface. The
[OpenVEX statement](security/openvex.json) records that product-level
assessment; it is not a claim that the upstream `transformers` distribution
has no advisories.

Any change to the DeepLaw version, document-engine dependency, `transformers`
version, backend, accepted arguments, model-loading path, or MCP exposure
invalidates that assessment and requires a new audit, VEX version, and actual
document-engine test. The reproducible gates are:

```bash
uv export --frozen --no-dev --no-emit-project --no-header \
  --format requirements-txt \
  | uvx pip-audit --no-deps --disable-pip -r /dev/stdin

uv export --frozen --no-dev --extra document-engine --no-emit-project \
  --no-header --format requirements-txt \
  | uvx pip-audit --no-deps --disable-pip -r /dev/stdin \
    --ignore-vuln PYSEC-2025-217 \
    --ignore-vuln PYSEC-2026-2288 \
    --ignore-vuln PYSEC-2026-2289 \
    --ignore-vuln PYSEC-2026-2290
```

The second command is valid only together with the checked-in VEX and its
execution-surface tests. A new or unmatched advisory fails the gate.

## Authorization boundary

This policy does not authorize access to systems, accounts, data, or legal
sources that you do not own or have permission to test. Test only with
synthetic data or material you are authorized to use.
