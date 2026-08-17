# DeepLaw documentation

This file is navigation only. It is not a release ledger, qualification report, architecture
source, or second product status board. The linked document owns the semantics for its category;
runtime behavior remains authoritative in code, contracts, migrations, tests, `pyproject.toml`, and
`uv.lock`.

## Normative

- [Product Requirements](PRODUCT_REQUIREMENTS.md) — product outcomes, frozen roles, boundaries,
  non-goals, and change control.
- [Architecture](ARCHITECTURE.md) — the sole current architecture constitution.
- [PRD Traceability Matrix](PRD_TRACEABILITY_MATRIX.md) — requirement-to-runtime and evidence
  mapping, without replacing runtime or qualification records.
- [Security policy](../SECURITY.md) — security, privacy, trust, and disclosure boundaries.
- [ADR index](adr/) — accepted consequential architecture decisions.

## Current machine state

- [v0.13 qualification protocol](V0_13_QUALIFICATION_PROTOCOL.md) — protocol and evidence rules.
- [Evaluation protocol](EVALUATION_PROTOCOL.md) — repository and task-evaluation contracts.
- [External benchmark protocol](EXTERNAL_BENCHMARK_PROTOCOL.md) — named-comparator and external
  evidence rules.
- [Active qualification records](../benchmarks/v013/) — machine-readable active inputs and state.
- [Release gate classifications](../benchmarks/release/) — versioned machine gate definitions.

The active qualification record and its exact artifact/evidence bindings are the only current
machine-state sources. This navigation page intentionally does not restate their values.

## Subsystem specs

- [Autonomous Knowledge OS](AUTONOMOUS_KNOWLEDGE_OS.md)
- [Living Wiki compiler](LIVING_WIKI_COMPILER.md)
- [Legal Pack and evidence policy](DEEPLAW_2.md)
- [Agent adapters and Host boundary](AGENT_ADAPTERS.md)
- [External qualification details](EXTERNAL_BENCHMARK_PROTOCOL.md)
- [Upstream reuse policy](UPSTREAM_REUSE.md)
- [Upstream research](V0_13_UPSTREAM_RESEARCH.md)

## Research

- [v0.13 upstream research](V0_13_UPSTREAM_RESEARCH.md)
- [Upstream capability matrix](UPSTREAM_CAPABILITY_MATRIX.md)
- [Upstream reuse](UPSTREAM_REUSE.md)

Research informs falsifiable requirements; it does not authorize a feature, dependency, Authority,
or release claim.

## Historical Pass evidence

- [Historical v0.13 Pass records](V0_13_PASS19_DISPOSITION.md) — begin at the relevant immutable
  record and follow its linked predecessors/successors.
- [Later retained disposition](V0_13_PASS20_DISPOSITION.md) — evidence pointer only.

Historical Pass files are immutable evidence snapshots. They do not define current architecture,
current product surfaces, current machine state, or release readiness.
