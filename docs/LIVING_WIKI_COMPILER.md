# DeepLaw Living Wiki Compiler

Status: **Current source candidate; package 0.12.0; release_ready=false**, 2026-08-11. Release
eligibility and exact evidence are tracked in [`V0_13_ACCEPTANCE_MATRIX.md`](V0_13_ACCEPTANCE_MATRIX.md),
the product surface manifest, and the applicable frozen qualification artifacts. This status does
not make Obsidian or Tolaria production-qualified.

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
`resume --project` retries deterministic materialization and verification.

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

The Wiki, graph, FTS, dense index, communities, Canvas and caches are disposable. Rebuild never
calls a model and never creates semantic prose that is absent from a canonical Knowledge Revision.
The same governed inputs and effective Run state produce the same Living Wiki manifest digest.

## CLI workflow

First create an owner-controlled least-privilege compiler grant. The built-in profile expands to
only the seven Compilation Run operations; it does not allow ordinary `remember`, backfill, grant
administration or Legal Pack mutation. The grant token stays outside source control.

```bash
deeplaw knowledge sink enable \
  --vault ./vault \
  --writer-id compiler-agent \
  --scope project \
  --profile compiler

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
```

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

`knowledge_support` input/output v6 is read-only and exposes compilation
inventory/profile/status/explain, purpose-aware Query Plan v6 query/context and existing
verification operations. `knowledge_sink` input v5 / output v4 is a separate
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
