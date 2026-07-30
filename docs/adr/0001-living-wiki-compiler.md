# ADR 0001: Host-neutral Living Wiki compilation saga

Status: Accepted for the current working-tree implementation
Date: 2026-07-30

## Context

DeepLaw already preserved immutable sources, governed Markdown Knowledge Revisions, a trusted
Ledger, grants, audit and rebuildable indexes. It did not have a host-neutral transaction that let
an external Agent compile one persisted Source IR into many typed revisions without either holding
a database transaction across model inference or making partial semantic output visible.

A compiler must work with Codex, Claude Code, OpenCode and future hosts while keeping source text
untrusted and all governance decisions deterministic.

## Decision

Use one durable, resumable Compilation Run saga:

1. bind an exact Source Revision, persisted Source IR, registered compiler profile and input audit
   heads;
2. create bounded immutable packets outside any model transaction;
3. accept only closed JSON Plans through a compilation-specific grant;
4. write Plans and prepared objects to CAS/staging tables, invisible to normal retrieval;
5. validate the complete Run, including exact evidence, identity, CAS and publication set;
6. publish all canonical Knowledge and relation revisions in one short SQLite transaction;
7. materialize Markdown and rebuild derived projections after canonical commit;
8. retain `projection_pending` and retry when derived work fails.

All CLI, MCP and Python paths call `CompilationCoordinator`. The host performs inference; DeepLaw
does not embed a model backend in the coordinator.

## Consequences

- A large source can use many packets while publication remains all-or-nothing.
- Repeated inputs are idempotent and do not create duplicate revisions.
- A pre-commit failure can be aborted safely; a post-commit failure must complete recovery.
- Projection can lag canonical knowledge without becoming a second Authority.
- The Ledger and artifact store gain additive tables and contracts.
- Real-host quality remains an external evidence track; deterministic fake-Agent CI proves the
  protocol without credentials.

## Rejected alternatives

- **One long transaction around a model call:** blocks writers and makes crash recovery unsafe.
- **Commit each packet independently:** exposes partial coverage as if the source were complete.
- **Let the host write Markdown/SQLite directly:** bypasses grants, CAS, source binding and audit.
- **Make Wiki files canonical:** filenames and editor metadata cannot establish identity or
  Authority.
- **Build separate compiler engines for each host or policy plane:** duplicates governance logic
  and permits drift.
