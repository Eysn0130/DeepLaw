# DeepLaw Living Wiki Compiler

Status: **Current source candidate; package/main 0.12.0 Beta; release_ready=false**, reviewed
2026-08-17. The Living Wiki is one of three product roles on the shared governed kernel, alongside
Task Continuity / Governed Project Knowledge and Source-native Evidence Library. Its projections
are rebuildable and source-linked; Wiki pages are not a complete editable canonical copy of a
professional source. Qualification remains bound to
[`benchmarks/v013/active-qualification-v2.json`](../benchmarks/v013/active-qualification-v2.json):
`machine_evaluation_pending`, profile `machine_evaluated_no_human_attestation`, Gate v8, with no
`0.13.0` tag or release. Historical receipts are development evidence only, not Human Gold,
legal-expert attestation, or release qualification.

The shared Context Compiler is `Discovery → Admission → Selection → Bounded Verifiable Knowledge
Capsule → thin Host drivers`. It is not a fourth product or second retrieval engine. Transcript,
prompt, raw log, and hidden reasoning are not automatically persisted as memory.

## Contract boundary

DeepLaw preserves and parses a Source Revision before a host model is involved. The host Agent
proposes a closed Compilation Plan; deterministic DeepLaw code validates and commits it.

```text
immutable Source Revision
→ persisted Source IR and stable fragments
→ bounded immutable Compilation Packets
→ host-proposed closed Compilation Plans
→ grant, CAS, identity, source, scope, sensitivity and Authority validation
→ one short all-or-nothing canonical commit
→ registered Markdown Knowledge Revisions and relation revisions
→ rebuildable Wiki, graph, indexes and Canvas
→ purpose-aware bounded retrieval
```

The host owns model inference. DeepLaw owns packet boundaries, schemas, identity collision checks,
evidence binding, permissions, idempotency, commit, audit, projection and verification. No
database transaction remains open while a model is running.

## Compilation Run

The durable state machine is:

```text
planned → staging → validating → ready_to_commit
        → committed → projection_pending → succeeded

terminal failure states: failed, aborted
resumable: an independent boolean
```

The idempotent Run identity binds the canonical `source_key`, exact Source Revision and persisted
Source IR compilation/digest, compiler profile and version, prompt template/configuration digests,
plan configuration digest and packet policy. The resulting identity is bound to the creating
grant; a different grant cannot take over its replay. `source_key` is the stable source identity
in the current architecture; legacy per-ingest `source_id` is not substituted for it. A Run
records safe
host/model provenance, Source IR digest, input audit heads, packet and staged counts, coverage,
warnings, identity/contradiction counts, validation/publication/projection digests, honest token
usage (`reported=false` when unavailable), elapsed time, retry count and a bounded failure digest.
It does not persist hidden reasoning, raw chat, credentials, case data or local paths.

Packets and Plans are immutable content-addressed artifacts. Staging rows are not admitted by
recall, graph, Wiki or context. Validation prepares exact Markdown bytes and revision identities.
The final `BEGIN IMMEDIATE` transaction publishes every non-proposal Knowledge and relation
revision, dependencies, outputs, audit event, rebuild work and receipt together.

Statement Evidence keeps a stable digest for every Statement, evidence map and receipt, but new
commits store those small logical artifacts in deterministic CAS bundles capped at 768 members and
8 MiB. The additive bundle-member mapping is part of the Ledger persistence contract. Readers first
resolve that mapping and otherwise fall back to the previous one-digest/one-file layout. Bundle and
member hashes are both verified; paths and ordinals never become semantic identity.

Projection failure cannot undo canonical knowledge. It leaves the Run at `projection_pending`;
`resume --project` retries deterministic materialization and verification. User-facing Source
status therefore reports canonical commit/admission independently from Wiki projection readiness:
`projection_pending` remains canonically compiled and admissible, but
`wiki_projection_pending=true` and `wiki_projection_ready=false`. Only `succeeded` reports the Wiki
projection ready.

## Plan and identity rules

The closed Plan contract is
[`source-compilation-plan.v1.schema.json`](../contracts/source-compilation-plan.v1.schema.json).
It contains object, relation and identity actions, unresolved identities, contradictions, coverage,
skipped fragments and warnings. Unknown fields fail validation.

Every source reference must bind the exact allowed Source Revision, stable fragment, locator and
quote digest. A Plan cannot choose scope, lower sensitivity, request official or legal Authority,
change a grant, choose a filesystem path, edit the Wiki, or treat source text as an instruction.
Committed compiler output remains `agent_derived` and `legal_authority=false`.

Ordinary object and identity actions remain packet/run-bound. A profile-v2 Synthesis may include
cross-source evidence only when each exact Source Revision is present in its digest-bound Synthesis
input set and each fragment independently passes current lifecycle, scope, sensitivity, locator and
quote-hash admission. This is evidence binding, not Authority elevation.

Exact semantic identity is reused deterministically. For Entity and Concept actions, normalized
title/alias collisions fail closed instead of silently creating a duplicate. An Agent may preserve
a genuinely separate same-name object only by recording an `ambiguous` or `possible_duplicate`
candidate that names all exact existing candidates. Such a candidate remains a visible gap; it
does not execute a merge. `same_as`, merge and split suggestions likewise remain proposals until an
independently authorized identity-resolution operation records the immutable decision.

## Dependency and freshness model

The Ledger records:

- Source Revision/fragment → Knowledge or relation revision;
- Knowledge revision → Synthesis or relation revision;
- relation revision → Synthesis revision;
- Compilation Run → output revision set;
- exact full Synthesis input sets and their digest.

Freshness is independent of Authority and epistemic state: `fresh`, `stale`, `invalidated` or
`unknown`. A successor Source IR is structurally diffed into added, changed, moved, unchanged and
missing stable fragments. Exact moved content stays fresh. Changed or removed evidence propagates
through revision dependencies; stale relations leave the current graph and stale knowledge leaves
current admission while immutable history remains queryable. Source withdrawal reuses the existing
source-admission decision.

`deeplaw knowledge compile refresh` records the transition and its reasons. It does not silently
create replacement semantic content; a host must run a new compilation when refreshed revisions
are required.

## Rich Living Wiki projection

Projection is deterministic and model-free. It produces:

- distinct `wiki/index.md` navigation and `wiki/overview.md` canonical-Synthesis rendering;
- active Source status pages, with explicit `uncompiled` state and sharded exact
  Source Revision/fragment/locator/digest drill-down indexes;
- rich typed pages for claims, concepts, entities, events, decisions, procedures, experiences,
  preferences, comparisons, syntheses, memory and Skills;
- human-readable relation titles plus stable IDs;
- deterministic community, contradiction, gap and Ledger-backed recent-change pages;
- sharded indexes above 300 objects and explicit pagination metadata;
- global, kind/community and per-object bounded JSON Canvas files;
- a manifest binding audit heads, effective Compilation Run state, configuration, complete
  generated-file inventory and hashes.

### Named projection profiles

The named projection profile keeps the schema family name `deeplaw.projection-profile/v1`, with a
closed profile `version` of `"1"` or `"2"`. Historical v1 profiles and v1/v2 Living Wiki
manifests remain valid inputs for verification and projection recovery. Current named profile and
build output uses version `"2"`. Source-to-Knowledge identity, governance and compilation
semantics are unchanged, but named projection defaults and physical materialization semantics
changed in v2 as described below.

`standard` v2 is the default **Core Living Wiki**. It enables only root/overview/source/core/recent/
gaps plus kind shards and kind indexes. Communities, global Canvas, kind Canvas, community Canvas,
per-object Canvas and local per-object Canvas are all disabled. `full` v2 is an explicit advanced
opt-in: it enables communities and all four Canvas families (global, kind, community and
per-object), while retaining the richer generated per-object Wiki pages.

The default `standard` and explicit `minimal` physical layouts reuse each current registered
Knowledge Markdown Revision as the human/Agent-readable object page. Page Registry binds its
stable Knowledge identity, exact revision, governance fields, byte size and SHA; paths or
frontmatter never establish identity or Authority. These registered files are indexed but are not
added to the projection ownership manifest, so rebuild and reconcile never claim or rewrite them.
Only generated root, source, bounded evidence, index, recent-change and Gap pages are projection
owned. This is a deterministic physical-layout choice, not a new page family, canonical store,
database or retrieval engine.

For `standard` and `minimal`, the existing Link Index also projects current admitted Ledger
relations and exact Source references between registered page identities. These are explicitly
typed as governed relation links rather than invented textual Wikilinks; they provide bounded
backlink/outlink and Source drill-down navigation without rewriting canonical Markdown or
elevating Authority.
This removes generated per-object page fan-out from `standard` and `minimal`; `full` retains the
explicit advanced generated per-object views. No profile or page family is added by this contract
change.

The manifest binds the complete selected profile and its SHA-256 configuration digest. The same
ownership manifest, rebuild and recovery path remains responsible for profile switches and
interrupted projection transactions; user-owned files remain outside the derived ownership set.
The Page Registry, Link Index and Resolver continue to provide the shared identity, link and lookup
surfaces behind either profile.

The Wiki, graph, FTS, dense index, communities, Canvas and caches are disposable. Rebuild never
calls a model and never creates semantic prose that is absent from a canonical Knowledge Revision.
The same governed inputs and effective Run state produce the same Living Wiki manifest digest.

The aggregate derived manifest may additionally carry
`deeplaw.derived-read-snapshot/v1`. It is a rebuildable verification receipt, not another Ledger
or database: an explicit rebuild first completes canonical verification, checkpoints SQLite, and
binds the exact database bytes, both audit heads, and the autonomous sequence. One-shot CLI
`query` and `wiki` reads may use that binding to avoid replaying the complete Ledger. Any byte,
head, sequence, WAL, manifest, or projection identity change invalidates the binding and falls back
to full fail-closed verification. Reads never create or refresh this receipt; exact Source and Wiki
page bytes remain verified on demand. Historical generator version `1` manifests remain readable
through full verification and deterministic rebuild. One-shot `query` does not load the Wiki
bundle. One-shot `wiki` reads reuse the aggregate manifest's exact v2 hash binding while retaining
v3 top/component/shard verification, so neither path repeats a per-page schema walk at startup.

## CLI workflow

First initialize and inspect the Vault, add the owner-selected Source, complete its explicit source
review, and request the read-only Host handoff. `handoff` does not invoke a model, include a grant,
or write canonical state. The returned `source_revision_id` is then bound to the existing
Compilation Coordinator flow.

```bash
deeplaw knowledge init --vault ./vault --name my-project --scope project
deeplaw knowledge doctor --vault ./vault
deeplaw knowledge source add --vault ./vault --source ./guide.md \
  --confirm-no-case-data
deeplaw knowledge review manifest --vault ./vault --source-id source_REPLACE
deeplaw knowledge review approve-source --vault ./vault --source-id source_REPLACE \
  --review-manifest-sha256 REVIEW_MANIFEST_SHA256 \
  --reviewer-id owner --reason "Reviewed the selected public source." \
  --confirm-reviewed
deeplaw knowledge compile handoff --vault ./vault \
  --source-revision-id sourcerev_REPLACE

deeplaw knowledge sink enable \
  --vault ./vault \
  --writer-id compiler-agent \
  --scope project \
  --profile semantic-compiler

deeplaw knowledge compile profile --vault ./vault

deeplaw knowledge compile begin \
  --vault ./vault \
  --grant-id grant_REPLACE \
  --source-revision-id sourcerev_REPLACE \
  --host-identity codex \
  --model-identity MODEL_REPLACE \
  --confirm-no-case-data

deeplaw knowledge compile packet \
  --vault ./vault --grant-id grant_REPLACE --run-id compilationrun_REPLACE

deeplaw knowledge compile stage \
  --vault ./vault --grant-id grant_REPLACE --run-id compilationrun_REPLACE \
  --plan ./plan.json --confirm-no-case-data

deeplaw knowledge compile validate \
  --vault ./vault --grant-id grant_REPLACE --run-id compilationrun_REPLACE \
  --confirm-no-case-data

deeplaw knowledge compile commit \
  --vault ./vault --grant-id grant_REPLACE --run-id compilationrun_REPLACE \
  --confirm-no-case-data

deeplaw knowledge compile resume \
  --vault ./vault --grant-id grant_REPLACE --run-id compilationrun_REPLACE \
  --project --confirm-no-case-data

deeplaw knowledge autonomy verify --vault ./vault
deeplaw knowledge compile explain --vault ./vault --run-id compilationrun_REPLACE
deeplaw knowledge query --vault ./vault \
  --query "What governs admission?" --purpose answer
deeplaw knowledge context --vault ./vault \
  --task "Verify what governs admission." --purpose verify --confirm-no-case-data
deeplaw knowledge wiki page --vault ./vault \
  --wiki-path wiki/sources/sourcerev_REPLACE.md
```

The `semantic-compiler` profile expands only to the existing compilation and synthesis-refresh
operations. It does not allow ordinary `remember`, backfill, grant administration, Legal Pack
mutation or any Authority upgrade. The grant token stays outside source control.

`packet` returns one packet at a time and then a closed packet-end receipt. Every packet must be
staged before full-Run validation. A staged packet may be atomically replaced before successful
validation. Use `abort --reason ...` before canonical commit; after commit, recovery must finish
materialization/projection rather than pretending that the canonical transaction did not happen.

Purpose-aware querying uses:

```bash
deeplaw knowledge query \
  --vault ./vault \
  --query "What governs admission?" \
  --purpose answer

deeplaw knowledge query \
  --vault ./vault \
  --query "Quote the exact source rule." \
  --purpose quote
```

`answer` defaults to `compiled-first-v1`. `verify`, `quote`, `historical` and `legal` default to
evidence-first behavior. Any source fallback, truncation, gap or rejected candidate is visible in
the Query Plan and bounded receipt. Provider Source references/evidence are typed to exact Source
Revision, fragment, locator and quote hash. If the complete source passage cannot fit its hard
budget, it is withheld and the duty remains a Gap rather than returning a truncated passage as
exact evidence. Query is read-only.

## Stable Python API

```python
from deeplaw.api import KnowledgeOS

knowledge_os = KnowledgeOS.open("./vault")
profile = knowledge_os.compilations.profile()
run = knowledge_os.compilations.begin(
    grant_id="grant_REPLACE",
    source_revision_id="sourcerev_REPLACE",
    compiler_profile=profile["compiler_profile"],
    compiler_profile_version=profile["compiler_profile_version"],
    host_identity="codex",
    model_identity="MODEL_REPLACE",
    prompt_template_id=profile["prompt_template_id"],
    prompt_config_sha256=profile["prompt_config_sha256"],
    plan_configuration_sha256=profile["plan_configuration_sha256"],
    confirm_no_case_data=True,
)

while packet := run.next_packet():
    plan = host_agent_proposes_closed_plan(packet)  # host-owned inference
    run.stage(plan, confirm_no_case_data=True)

run.validate(confirm_no_case_data=True)
run.commit(confirm_no_case_data=True)
run.resume(project=True, confirm_no_case_data=True)
assert knowledge_os.verify()["valid"]
answer_context = knowledge_os.retrieval.query("admission rule", purpose="answer")
```

The public facade maps internal validation, not-found, permission and state conflicts to stable
`KnowledgeOSError` subclasses without exposing persistence internals.

## MCP and host workflow

The Provider advertisement is read-only `knowledge-support` input v7/output v6 and exposes only
`query`, `context`, and `explain`. The local compiler inventory/profile/status and historical
verification operations remain internal compatibility calls; input v1-v6 and output v1-v5 are
not advertised as current operations. `knowledge_sink` input v6 / output v4 is a separate
process and exposes only the operations in its owner-created grant. Its caller-supplied idempotency
key is durably bound to the exact closed request and a content-addressed result; reusing the key for
different input fails closed. A compiler host requires both processes; a Skill or retrieved
document cannot create or widen the grant.

The shared Skill is
[`compile-living-wiki`](../plugins/deeplaw-knowledge-os/skills/compile-living-wiki/SKILL.md).
The OpenCode least-privilege example is
[`knowledge-compiler.example.jsonc`](../adapters/opencode/knowledge-compiler.example.jsonc).
Codex, Claude Code and OpenCode use the same domain coordinator and closed Plan.

## Controlled query backfill

Query results are not written automatically. A durable, reusable and novel synthesis can be
proposed into `drafts/`, deterministically validated, and only then explicitly promoted by a grant
that allows backfill operations. Promotion remains `agent_derived`, source-bound when evidence is
available, honestly source-free otherwise, and always `legal_authority=false`.

## Recovery table

| Failure point | Canonical visibility | Recovery |
| --- | --- | --- |
| packet generation/staging | none | repeat idempotently or abort |
| Plan validation | none | inspect bounded error, replace staged Plan, revalidate |
| before canonical transaction commit | none | resume or safe abort |
| after canonical commit, before Markdown materialization | committed Ledger state | startup/resume replays pending materialization |
| projection build | canonical revisions remain admitted | Run stays `projection_pending`; `resume --project` retries |
| source successor/withdrawal | history retained; affected current admission changes | inspect freshness report and compile the successor |

## Executable examples

The no-model end-to-end example is
[`examples/living_wiki/run_demo.py`](../examples/living_wiki/run_demo.py). It performs real ingest,
packet staging, atomic commit, projection, verification and compiled-first retrieval with synthetic
content and no network:

```bash
uv run python -m examples.living_wiki.run_demo \
  --workspace /tmp/deeplaw-living-wiki-demo
```

The real-host harness is opt-in:

```bash
uv run python -m benchmarks.hosts.run_living_wiki_host_harness \
  --host codex \
  --host-version VERSION_REPLACE \
  --model-identity MODEL_REPLACE \
  --source-revision-id sourcerev_REPLACE
```

Without `--execute`, it emits `status=not_executed`. An explicit execution additionally requires a
Vault and a closed command manifest. It records only bounded hashes/counts and accepts success only
when a new host-bound Run is `succeeded` and full Vault verification passes.
