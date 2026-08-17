<p align="center">
  <a href="README.md">简体中文</a> · <strong>English</strong>
</p>

<h1 align="center">DeepLaw</h1>

<p align="center">
  <img src="assets/brand/deeplaw-2-glass.png" width="820" alt="DeepLaw 2.0 product brand" />
</p>

<p align="center">
  <strong>Local-first Agent Knowledge OS</strong><br />
  <sub>Source-native Evidence · Governed Living Wiki · Verifiable Context</sub>
</p>

DeepLaw compiles source material into governed knowledge and a Living Wiki, then returns a bounded,
verifiable Knowledge Capsule for the current task. It is not generic RAG, a complete transcript
archive, an Obsidian replacement, a legal adjudicator, or an Agent runtime.

The architecture is frozen as three product roles on one shared governed kernel:

- Task Continuity / Governed Project Knowledge;
- Source-native Evidence Library;
- Living Wiki.

All three use one Context Compiler:
`Discovery → Admission → Selection → Bounded Verifiable Knowledge Capsule`. The Context Compiler is
not a fourth product or a second retrieval engine. Legal Pack is the first-party legal policy plane
of the Evidence Library. Professional sources retain their original bytes, versions, Fragments,
and Locators; the Wiki is a rebuildable projection, not a complete editable canonical copy.

## Current honest state

- Public package/main: `0.12.0 Beta`; latest tag: `v0.12.0`.
- Active qualification: `machine_evaluation_pending`; profile:
  `machine_evaluated_no_human_attestation`; Gate classification: v8.
- `release_ready=false`; there is no `0.13.0` tag or release.
- Current Provider advertisement: knowledge-support input v7 / output v6 with only `query`,
  `context`, and `explain`; input v1-v6 and output v1-v5 are compatibility/internal only.
- Local regressions, mocks, dry-runs, old reports, and no-model smoke are not real-Host, Human Gold,
  legal-expert, 3-OS, scale, supply-chain, or release evidence. Missing qualification remains
  `not_executed`.

Current machine state is read only from
[`benchmarks/v013/active-qualification-v2.json`](benchmarks/v013/active-qualification-v2.json) and
[`benchmarks/release/v013-gate-classification-v8.json`](benchmarks/release/v013-gate-classification-v8.json).
This README is not a second status ledger.

## Install

The released package requires Python 3.11+ and [`uv`](https://docs.astral.sh/uv/):

```bash
uv tool install \
  https://github.com/Eysn0130/DeepLaw/releases/download/v0.12.0/deeplaw-0.12.0-py3-none-any.whl
deeplaw --version
```

For repository development:

```bash
uv sync --all-extras
```

## Primary real journey

Create and inspect the local Vault first. `doctor` must report canonical/autonomous readiness and
an actionable Gap when a prerequisite is missing.

```bash
deeplaw knowledge init --vault ./vault --name my-project --scope project
deeplaw knowledge doctor --vault ./vault
```

Start the task line and generate a task-neutral, read-only Host configuration for manual merge.
Static `host connect` does not select a task, manage Host authentication/runtime, or enable
`knowledge_sink`.

```bash
deeplaw knowledge task start --vault ./vault \
  --project DeepLaw --task 'Finish the selected task.' --workspace .
deeplaw knowledge task locate --vault ./vault \
  --project DeepLaw --task 'Finish the selected task.' --workspace .
deeplaw knowledge host connect --host codex --vault ./vault
```

First-session binding is an explicit owner mutation requiring an existing Sink grant, an
idempotency key, the current workspace, and a safely computed session SHA-256. A raw official
session ID must never enter argv, the Ledger, logs, receipts, or Provider output.

```bash
deeplaw knowledge task bind-host-session --vault ./vault \
  --host codex --session-sha256 SESSION_SHA256 \
  --task-handle TASK_HANDLE --workspace . --grant-id GRANT_ID \
  --idempotency-key BIND_IDEMPOTENCY_KEY --confirm-no-case-data
deeplaw knowledge task resolve-host-continuity --vault ./vault \
  --host codex --session-sha256 SESSION_SHA256 --workspace .
deeplaw knowledge task resume --vault ./vault \
  --project DeepLaw --task 'Finish the selected task.' --workspace .
```

Ordinary recovery does not require a task handle. Fork, compaction, stale checkpoint, wrong
task/worktree, ambiguous binding, and selective forget must revalidate current state and fail closed
with a structured Gap.

After adding a source, use the same Context Compiler for bounded delivery. Exact citation tasks
drill down to the Source Revision, Fragment, and Locator rather than copying the complete source
into the Wiki or Provider context.

```bash
deeplaw knowledge source add --vault ./vault --source ./guide.md \
  --confirm-no-case-data
deeplaw knowledge context --vault ./vault --task 'Verify the guide.' \
  --purpose verify --confirm-no-case-data
```

## Fixed boundaries

- `knowledge_support` is permanently read-only; `knowledge_sink` is a separate grant-controlled
  mutation process; `law_support` is separate and read-only.
- DeepLaw does not automatically read or retain prompts, transcripts, hidden reasoning, auth,
  Secrets, or raw logs.
- Provider output excludes paths, session hashes, internal selection/receipt identity, unadmitted
  content, and Secrets.
- Ordinary reads do not append the canonical Ledger; durable mutations use the shared Coordinator.
- No remote canonical storage, telemetry, GUI/cloud control plane, or additional knowledge engine.

See [`docs/README.md`](docs/README.md) for normative and subsystem documentation and
[`SECURITY.md`](SECURITY.md) for security boundaries. DeepLaw is licensed under the
[Apache License 2.0](LICENSE).
