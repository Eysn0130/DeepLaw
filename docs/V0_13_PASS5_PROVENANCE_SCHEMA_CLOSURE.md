# DeepLaw v0.13 Pass 5 provenance schema closure

Status: **candidate-only contract and local envelope validator; not a Gate Result consumer or
release decision** (2026-08-10).

## Compatibility judgment

`deeplaw.provenance-bound-gate-result/v1` is retained. The repository audit found no enabled
consumer, no real Gate Result, and no assembly path that can consume this contract. The v1 name
therefore remains a candidate-only compatibility boundary; this change does not declare a Core
Gate executed, passed, release-ready, or claim-eligible.

The v1 envelope is now closed around the missing provenance invariants:

- every raw input has a unique `input_id`; all execution, metric, failure, hard-failure, and
  redaction references must resolve to an input and every input must be consumed;
- protocol, threshold, Gold, and corpus bindings are frozen with `const: true`;
- execution rows carry explicit dimensions; `run_ids` and `unique_dimensions` are deterministic
  derivations, not caller counters;
- validator source and executable bindings include relative path, byte size, and SHA-256;
- the local validator reopens every bound file and recomputes file, record, and envelope digests;
- raw input and validator bindings have a 64 MiB hard file bound that is checked before reading;
- metric numbers must be finite, including Python mappings that could otherwise contain `NaN` or
  infinity.

## Validator seam

`benchmarks/release/provenance_gate_result.py` exposes `validate_gate_result` and
`validate_gate_result_file`. They validate one envelope under an explicit filesystem root,
reject symlinks and root escapes, enforce the published JSON Schema, and fail closed on any
reference, frozen-state, derived-run/dimension, finite-number, byte/hash, or canonical-digest
drift. Optional expected validator identity/path arguments let a future owner bind a dedicated
validator without making this generic seam an assembler.

## Deliberate limits

- The validator does not run a command, prove Host/model identity, score a gate, aggregate Core
  results, or make a release decision.
- It does not infer missing raw evidence from hashes or summaries.
- The 12 required Core validators remain incomplete and `assembly_enabled=false` remains the
  only permitted classification state (`blocked_missing_validator` or
  `blocked_missing_raw_contract`).
- Real Host runs, frozen external corpora, independent evaluators, and release assembly remain
  not executed. A locally valid synthetic envelope is contract regression evidence only.
