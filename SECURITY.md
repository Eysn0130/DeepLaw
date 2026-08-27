# Security Policy

DeepLaw is a local, single-OS-user Agent Knowledge OS with a separate Chinese Legal Pack.
`law_support` and `knowledge_support` are read-only. Agent-derived mutation is available only
through the different, explicitly enabled, scope-bound `knowledge_sink` process; Legal Pack and
source administration remain local CLI work. The repository distributes code
under Apache License 2.0; it does not distribute legal-source packages, case
documents, generated vault/release databases, or OCR corpora.

Current product and qualification boundary (2026-08-21): package/main remain `0.12.0 Beta`,
`release_ready=false`, active status is `machine_evaluation_pending`, profile is
`kernel_release_core`, and Gate classification is v9. The three product roles
(Task Continuity / Governed Project Knowledge, Source-native Evidence Library, Living Wiki) share
one governed kernel and Context Compiler. Transcript, prompt, raw log and hidden reasoning are not
automatically persisted as memory. Current `knowledge_support` advertisement is input v7/output v6
with only `query`, `context`, and `explain`; input v1-v6 and output v1-v5 are internal compatibility
only. Missing qualification evidence remains `not_executed`; no tag or release follows.

## Supported versions

Security fixes are evaluated for the current software release, `v0.12.0`, and the `main` branch.
Historical v0.12 release artifacts retain their manifest-v5 contract. The current `0.13.x`
machine-only path is fail-closed on commercial manifest v9; old v5-v8 manifests,
repository-visible development Gold, or no-model Host smoke cannot satisfy model-task acceptance.
The v9 manifest binds exact candidate artifacts, Kernel evidence bundle, Host tasks, 10k scale,
cross-platform, supply-chain, provenance, and public-redownload evidence. Optional Capability and
Competitive/Research Claim gates remain explicit `not_executed` non-claims and do not substitute
for Kernel evidence.
Real Host qualification retains the closed `host-preflight-receipt/v1` and current
`host-process-receipt/v2` control records. `host-process-receipt/v1` is historical/invalidated and
cannot satisfy current qualification. They bind safe reason/status codes, exact binary and
repository-external broker hashes, owner-only broker mode, and negative isolation facts. Raw
commands, environment values, paths, process identifiers, stdout/stderr, prompts, transcripts,
hidden reasoning, authentication material, and Secrets are forbidden from those records.
Unknown later
versions have no implicit manifest downgrade. Comparative leadership remains separately gated with
`competitive_claim_eligible=false`. Older versions, local knowledge-release artifacts, and
third-party packages are not separately supported unless a release notice says otherwise.
The published v0.12 manifest records `commercial_release_eligible=true` and
`quality_protocol_eligible=true`; those historical flags do not qualify this v0.13 source
candidate or bypass manifest v9.

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

- a write path or command execution reachable through `law_support` or `knowledge_support`, or a
  `knowledge_sink` mutation that bypasses its grant, scope, sensitivity, operation, rate, or
  idempotency contract;
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
owner permissions. This is intentionally a single-user local security boundary,
not multi-tenant authorization or application-level encryption at rest. DeepLaw
does not plan a team-RBAC, remote-database, or hosted-SaaS mode. Sharing one
vault path between OS users is unsupported; use separate vaults and OS identities.
Use the operating system's full-disk encryption when confidentiality at rest is
required.

`deeplaw knowledge doctor --permissions` verifies owner-only permissions and
link safety for the Vault root, manifest, database, stored sources, model files,
and derived index files. POSIX uses ownership and mode checks. Windows uses
native security descriptors: it verifies the owner SID, rejects broad grants to
Users or Everyone, and rejects reparse points and junctions. Initialization and
administrative write paths apply owner-only ACL hardening. The release workflow runs the
Windows-only native ACL, junction, and reparse-point tests on `windows-latest` with zero mandatory
skips and publishes the bound platform report.

Source files, conversation exports, tool results, packages, and generated lessons are untrusted
inputs. External/imported material remains proposal or quarantine governed. Agent-derived
Knowledge Revisions may become active without per-item review only after a concrete sink grant,
closed schema, exact scope/sensitivity, provenance, idempotency, size/rate/capacity, stored
prompt-injection, and authority-elevation gates pass. Autonomous activation never means human
verification, official origin, instruction authority, or permission. Instruction-like and
invisible-control content is quarantined; retrieved content remains data.

Explicit Source Snapshot connectors are offline administration, not Agent retrieval. HTTPS
preflight never resolves DNS or opens a connection. Capture requires `--confirm-network`, permits
only canonical public-DNS HTTPS on port 443, rejects credentials/query/fragment/IP literals and any
non-global or mixed DNS answer, pins the chosen IP while validating TLS SNI, and repeats the check
for every redirect. It accepts at most five redirects and 64 MiB, requests identity encoding,
rejects compressed or ambiguous-length responses, and can require an expected SHA-256. It does not
honor proxy variables or fall back to another fetcher; networks that expose only private,
benchmark, or interception DNS addresses fail closed. Remote bytes are always `untrusted` and
review-gated.

The Git connector accepts only an existing non-symlink local directory, a stable non-secret
repository ID, and an exact full commit object ID. It executes only bounded `rev-parse`, `ls-tree`,
and `cat-file` plumbing with shell disabled, replacement objects and lazy fetch disabled, prompts
disabled, and global/system Git config ignored. It performs no clone or checkout. Only regular
supported blobs enter snapshots, and each returned byte sequence is checked against its Git object
ID. The absolute repository path is owner-only operational metadata; canonical identity contains
only the synthetic repository ID, commit, and encoded relative path.

Snapshot directories, manifests, and bytes are owner-only and hash-bound to the Vault, connector,
origin, logical path, and content. Resumable ingest re-verifies the snapshot record and bytes before
compilation. Snapshot jobs are not registered for watch/sync, connector commands are absent from
all MCP servers, and capture success never grants review, applicability, legal authority, or
activation. These checks reduce SSRF and accidental provenance drift; they do not defend against
arbitrary same-owner code execution that can rewrite all local files and audit state.

DOCX is treated as an untrusted ZIP container. OOXML members have byte and
compression-ratio bounds, and XML parsing rejects DTD declarations, entity
expansion, and external references before text extraction.

General Knowledge Assets always declare `legal_authority=false`. A local user
cannot assert `verified_source` through the current CLI or store API. Official
legal authority and user-private legal references remain separate Legal Pack
scopes; neither can be rewritten through the Agent interface.

Both read-only Agent interfaces apply the same final provider-output gate: `law_support` and
`knowledge_support` fail closed, without echoing the match, if a bounded result still contains a
recognized local absolute path, secret-like value, or unsafe invisible/bidirectional Unicode.
The same projection applies to MCP exception messages, so filesystem or credential-bearing
failures become a generic fail-closed error instead of being reflected to the host model.

The optional `knowledge_support` server:

- opens exactly one selected vault read-only;
- advertises the current `knowledge-support.input/v7` and `knowledge-support.output/v6` contracts;
- exposes only `query`, `context`, and `explain` in that current Provider advertisement. Historical
  v1-v6 input and v1-v5 output operation inventories remain internal compatibility contracts;
- excludes inactive and restricted revisions and strips local filesystem paths;
- fails closed without echoing the matched value if any bounded response still contains a local
  absolute path or recognized secret-like material;
- reconciles both event histories with current object/source/relation/workspace state and verifies
  selected source/CAS bytes;
- returns bounded task context and never writes feedback or memory.

Working-checkpoint routing is an admission selector, not a capability. The derived route index is
queried before ordinary content discovery, remains bounded and rebuildable, and is revalidated
against canonical Run/Revision/Ledger state. Routing identity excludes paths, branch names, current
commits, Host sessions, and credentials; checkpoint base/dirty state is a separate snapshot.
Route mismatch fails closed without an existence oracle, while a same-route snapshot divergence
returns only a sanitized Gap and opaque receipt. Provider projection recursively strips binding,
route, snapshot, Host-hint, and local-path fields.

The optional `knowledge_sink` server:

- is a separate stdio process with one `knowledge_sink` leaf and write/destructive annotations;
- starts only with an exact owner-created grant ID whose token file is owner-only;
- authenticates writer, operation allowlist, exact scope, maximum sensitivity, input bytes,
  mutation rate, capacity, and idempotency on every request;
- creates immutable Markdown/CAS revisions plus Ledger/audit records through the same domain store
  used by the CLI;
- cannot mutate official or private Legal Pack data, source evidence, Authority, audit history,
  filesystem paths, exports, signing keys, or host permissions;
- requires explicit confirmation that no client or case material is present.

The frozen `knowledge-sink.input/v2` contract still accepts legacy unbound `record_run`. New bound
working-state writes use additive input v6. Legacy reconciliation is an owner operation that writes
a new bound Run and successor Revision; it never mutates historical rows in place.

Read-only is the `law_support`/`knowledge_support` boundary and grants are the
`knowledge_sink` boundary; neither is an operating-system sandbox. A host that separately grants
an Agent arbitrary shell or filesystem
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

Local Review Receipts, Task Run Receipts, and structured Feedback Ledger records are closed,
hash-bound contracts reconciled against their audit events and current stored records. Ordinary
record/hash tampering fails integrity verification. Review Receipt v1 deliberately contains a
`null` signature, so it is not proof of an independent signer or team authorization.

A Task Run Receipt can be created only from a Capsule that passes current Vault verification.
The Store derives and cross-checks the Capsule identity, digest, historical audit anchor, selected
Asset IDs, and embedded source IDs instead of trusting caller-supplied inventories. Every
source-bound Capsule item must carry at least one matching compact source reference; a resealed
Capsule with all embedded provenance removed is invalid.

Control-plane migration creates a verified owner-only backup before applying database changes,
verifies lifecycle coverage and the complete audit replay afterward, and supports explicit atomic
rollback. Rollback retains the replaced Vault in a sibling recovery directory instead of deleting
it. Backup markers commit the manifest, consistent SQLite backup, stored-source inventory,
revision, and audit head. Operators must still place backups on storage with an appropriate
confidentiality and durability policy.

Autonomous Vault snapshots also include capability state and owner-only token material needed for
an exact restore. Treat such snapshots as credentials: preserve owner-only permissions, never
commit or publish them, and rotate/revoke restored grants when snapshot custody is uncertain.

The retained v0.7 optional Discovery Index is a derived, removable compatibility candidate index and never
a source of truth. Provisioning downloads only one fixed model profile and
accepts it only after the exact repository revision, five-file inventory, byte
sizes, and SHA-256 values pass. Query execution is local-only. The index binds
the model identity, vault ID/revision/audit head, Asset content and projection,
record bytes, and vector bytes. It contains only active, human-reviewed,
non-expired, non-restricted Assets; source-bound results revalidate their
stored source bytes. Extra files, symlinks, unsafe permissions, model drift,
dimension/row-width drift, non-finite vectors, source changes, vault changes,
or post-verification file replacement fail closed.

That compatibility model-backed Discovery path remains outside the default MCP/Context Compiler.
The v0.9 autonomous core's deterministic offline hash-dense, FTS, reranker, graph, and Wiki indexes
are a separate rebuildable path bound to both audit heads, exact derived bytes, and model/profile
identity. Stale or damaged derived state fails closed and current recall uses a bounded canonical
fallback. The older model-backed discovery path remains operator/research CLI functionality until held-out
task-success, noise, provenance, lifecycle, poisoning, resource, and cost gates
pass. Its case-data confirmation is an explicit operator boundary, not a
personal-data classifier. Client and case material remains forbidden.

The SQLite event chain plus current-state replay detects accidental or partial
tampering, including a status-only edit or forged FTS projection. Stable
database and source-file fingerprints cache only unchanged verified state; a
pinned reader rejects a replaced database. Because the
head is stored in the same local vault, an attacker with arbitrary write access
to the vault can rebuild both events and head. It is not a remote transparency
log or external signature. Preserve trusted backups and use signed releases
when publisher identity is required.

## Dependency and document-engine boundary

The default DeepLaw runtime, both read-only query servers, and the Knowledge Sink do not import the
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
`transformers<5`. The current audit reports the exact PYSEC/GHSA identifiers
recorded in the checked-in OpenVEX document against code paths for X-CLIP
conversion, Trainer checkpoint restore, user-selected causal-model
repositories, and LightGlue remote models. Those paths are outside DeepLaw's
closed pipeline execution surface. The
[OpenVEX statement](security/openvex.json) records that product-level
assessment; it is not a claim that the upstream `transformers` distribution
has no advisories.

Any change to the DeepLaw version, document-engine dependency, `transformers`
version, backend, accepted arguments, model-loading path, or MCP exposure
invalidates that assessment and requires a new audit, VEX version, and actual
document-engine test. The current dirty-worktree construction diagnostic is
recorded in
[`benchmarks/release/document-engine-actual-pdf-2026-07-28.json`](benchmarks/release/document-engine-actual-pdf-2026-07-28.json):
the pinned 15-file model bundle was fully rehashed, a generated one-page PDF was
processed through the real `pipeline/txt/en` entrypoint without ingest-time
network access, and the expected text hash matched. It is narrow local evidence,
not a frozen release, OCR/layout corpus, or cross-platform security claim. The
reproducible gates are:

```bash
uv export --frozen --no-dev --no-emit-project --no-header \
  --format requirements-txt \
  | uvx pip-audit --no-deps --disable-pip -r /dev/stdin

uv export --frozen --no-dev --extra document-engine --no-emit-project \
  --no-header --format requirements-txt \
  | uvx pip-audit --no-deps --disable-pip -r /dev/stdin \
    --ignore-vuln GHSA-29pf-2h5f-8g72 \
    --ignore-vuln GHSA-69w3-r845-3855 \
    --ignore-vuln GHSA-fgcw-684q-jj6r \
    --ignore-vuln PYSEC-2025-217 \
    --ignore-vuln PYSEC-2026-2288 \
    --ignore-vuln PYSEC-2026-2289 \
    --ignore-vuln PYSEC-2026-2290

uv export --frozen --no-dev --extra discovery --no-emit-project \
  --no-header --format requirements-txt \
  | uvx pip-audit --no-deps --disable-pip -r /dev/stdin
```

The second command is valid only together with the checked-in VEX and its
execution-surface tests. A new or unmatched advisory fails the gate.

## Approved evaluation harness

An Owner-approved evaluation harness may read a repository-ignored `.env` only
through a dotenv parser and only to extract one explicitly configured DeepSeek
key. The launcher must not `cat`, `source`, `printenv`, `env`, `ps e`, or use an
equivalent whole-environment dump; it must not inherit the full environment, and
it must never print or record a Secret.

The harness may start only the explicitly configured read-only DeepLaw MCP child;
that child receives a closed environment whitelist and cannot access the
repository `.env` or any temporary Secret. No other inherited or auto-discovered
MCP server, tool, grant, vault, or credential is allowed. A DeepSeek Secret must
not enter DeepLaw MCP, prompts, argv, stdout, stderr, reports, or artifacts. Use a
synthetic canary and scan launcher inputs, command output, logs, reports, and
artifacts for both the canary and the actual Secret before and after each run.
Any temporary Secret file is mode `0600` and is deleted on normal exit and failure
cleanup.

If a key has appeared in chat, Git, logs, or an artifact, stop the evaluation and
rotate it before any further run. This evaluation-only rule does not change
DeepLaw's MCP, Authority, Ledger, telemetry, or Secret boundaries.

## Authorization boundary

This policy does not authorize access to systems, accounts, data, or legal
sources that you do not own or have permission to test. Test only with
synthetic data or material you are authorized to use.
