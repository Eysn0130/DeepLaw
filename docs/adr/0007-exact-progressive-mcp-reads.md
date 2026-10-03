# ADR 0007: Exact progressive reads through the existing knowledge leaf

- Status: Accepted owner design; source-candidate implementation, qualification pending
- Date: 2026-09-09

## Context and reproduced task

An Agent with only public MCP access must inspect the knowledge revision and source fragment
identified in its query/context result. On candidate `ddb790360abcaf7e61c0855cace11f29a13530dd`,
initialize instructions recommended `wiki/source/verify`, but tools-list advertised input v7 with
only query/context/explain. The exact read request was not admitted by the public input schema.
Having SourceReadService and WikiReadService available through owner CLI/Python did not let that
client finish its evidence duty. The public stdio reproduction is a no-model product failure,
not native-Host or comparative qualification.

This implements existing `PRD-PRINCIPLE-005`, `PRD-WIKI-014..015`, `PRD-CTX-010..012` and
`PRD-EVID-005..009` outcomes on the existing MCP surface. It introduces no new product role,
Knowledge kind, Relation predicate, persistent store, graph engine, model or Agent runtime.

## Decision and smaller alternatives

Keep the single read-only `knowledge_support` leaf. Advertise explicit input v8 and output v7
schemas; preserve input v7 and existing query/context/explain response versions. Add only a typed
`read` route over current admitted non-memory knowledge revisions, corresponding registered Wiki
knowledge pages and exact Source fragments. Keep the Sink and Legal Pack processes separate.

Reuse the existing read runtime, Source/Wiki services, admission, integrity and safe output
projection. Exact source admission must use current governed sensitivity and activation/revocation
state; original import metadata cannot override a later owner change. Every continuation checks
current admission and exact content identity. Reading cannot create or rebuild a projection.

- Recommending existing query/context alone was insufficient: their public selectors do not expose
  the exact Source/Wiki read path needed by the reproduced no-shell client.
- Exposing all historical internal operations would unnecessarily widen the public surface.
- Adding a second `knowledge_read` leaf would duplicate discovery and version-management work
  without improving this task. Internal module separation remains appropriate.
- Adding arbitrary file paths, generic Resources, a second index or persistent exploration store
  is unnecessary. Existing identities and services provide the required evidence path.

This decision deliberately does not add historical exact reads, direct task-memory reads, all Wiki
navigation pages or cross-session exploration accounting. Those require their own reproduced
journeys and admission decisions, rather than an implicit expansion of this route.

## Contract, budgets and costs

The precise fields live in the versioned schemas and the
[adapter contract](../AGENT_ADAPTERS.md#exact-progressive-read-contract). Continuations use an
explicit offset and full-content hash; wrong, changed, denied or unsupported targets fail closed.
New nested result objects are allowlisted. Knowledge provenance, Authority, lifecycle and scope
remain separate; source text does not become legal Authority merely because it is returned.
Existing typed source, knowledge-revision and artifact lineage is retained. Dependency checks are
bounded to 32 reference edges per call, including duplicates, with revision deduplication and cycle
rejection; only direct references are returned. Knowledge dependencies remain current, exact and
non-memory. Artifact lineage does not add an artifact read or execution capability.

The expanded single-tool canonical definition has an explicit **12 KiB** ceiling, replacing the
previous advertisement's **8 KiB** ceiling for this new version. The inspected candidate definition
was 9,375 bytes before final integration; regression tests enforce the ceiling, and the final
candidate must measure it again. This costs additional Host context and is not a performance claim.
The content ceiling is not raised: successful read canonical JSON is at most **64 KiB**.

One MCP lifespan counts at most **32** successful read responses and **256 KiB** of their canonical
JSON bodies, including each body's metadata once. Counts are serialized in process memory. MCP
wrapping and duplicate TextContent/structuredContent, errors, other operations and Provider token
usage are separate. Serialized CallToolResult is hard-bounded to 197,632 bytes, excluding JSON-RPC
id/framing; this is not a Provider-token measurement. Reconnect resets the
counter. These limits do not implement an unbounded-time, whole-task or cross-Host cap, and they
are not grants. The Host still budgets the complete task.

New formal observations must bind `projection_budget.tools_list_max_bytes=12288` to this new
advertisement, with actual measured tool bytes and the unchanged applicable content limits.
Historical 8 KiB fixtures, frozen qualification inputs, reports and results remain unchanged.

## Migration, recovery and validation

No canonical data or Ledger migration is needed: this is a versioned read surface plus correction
of shared current-governance admission. Existing data and input v7 remain readable under their
previous contracts. Rolling back to the old binary removes the new read operation; it cannot
carry forward the new tool advertisement or qualification. Clients must inspect capabilities and
use supported operations. Server restart drops only transient read accounting, not knowledge.

Validation is at the public seam: initialize/tools-list agreement; query to exact knowledge/Wiki/
Source reads; old argument/response compatibility; content-hash pagination; changed sensitivity,
revocation, stale/forgotten/wrong targets; restricted content and split-path leakage; closed nested
output; call/body/transport bounds; and unchanged canonical audit state. Relevant coverage lives
in `tests/test_mcp_progressive_read.py` and existing provider, Source and Host-plan regressions.
No-model tests do not establish native Host recovery or legal completeness.

The candidate changes code, documentation and public contracts. S114 and every other prior
Candidate Full or qualification artifact retain their old commit ceiling. A final stable candidate
requires fresh exact build, platform and formal evidence under the existing invalidation rules;
this ADR does not mark a missing gate passed or reclassify Core failures as optional.
