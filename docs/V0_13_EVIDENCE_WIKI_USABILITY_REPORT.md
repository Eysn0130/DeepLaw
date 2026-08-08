# DeepLaw v0.13 Evidence Wiki human/Agent usability report

Status: **development task passed with explicit limitations**. The result is based on one
repository-external owner-task fixture whose second human review is not executed. It is not a
general Wiki usability or quality claim.

Implementation freeze: `450e79e66a30399385ab4afd2d137414e78b7119`.

## Frozen task and isolation

The source asks a human to open the current Knowledge page, inspect its receipt and link context,
and drill down to the registered Source fragment and exact Source bytes. The Agent task uses only
read interfaces to recover the current Statement, exact Source Revision/fragment/locator/quote
hash, and gaps.

| Input | SHA-256 |
| --- | --- |
| Development source | `b3813163a165779854902076ee11e0de370e62832e1d1451368aa0d191e8ee3a` |
| Owner-task Gold | `e5dd6aa3e391bcecb991163dd1b791ed7fdb90137367aeee22dc5762a7cce6d9` |

The source-only candidate was denied network access, Gold, and the scorer. The deterministic
scorer was denied the source and candidate implementation.

## Exercised chain

```text
Source bytes
-> Source Revision / Fragment / Locator
-> Knowledge Revision / Statement / derived_from Relation Revision
-> Ledger current pointers
-> Page Registry / Link Index / Resolver
-> Wiki Source and Claim pages / outlinks
-> exact Source fragment and stored-byte verification
-> Query Plan v6 / Context Capsule v3
```

Observed facts:

- stored Source bytes and the input bytes had the same exact content and SHA-256;
- the factual Statement was current-supported, had a present receipt, and bound the exact Source
  Revision, fragment, locator, and quote hash;
- the Agent interpretation was a separate Knowledge Revision with `origin=agent_derived`,
  `authority=agent_derived`, and `legal_authority=false`;
- its `derived_from` Relation had evidence references and did not gain legal Authority;
- Registry and Link Index verified, Source and Claim pages were registered, Source-fragment and
  Claim resolution were admitted, and the Claim page exposed four indexed outlinks;
- all reads reported `write_performed=false`, and the audit head was unchanged;
- Query and Context used v6, selected one Statement, and remained within 2,024 Provider bytes.

The score had no hard failures. Final-run hashes are:

| Artifact | SHA-256 |
| --- | --- |
| Wiki candidate | `5a1b58dc7461a296cfb3e40f40b9e5e6d6caf839ac20025e80d59d60262f860f` |
| Wiki score | `6b0f023d87d19af01f0de58cef741b54bf6165c37c84da473c46fdd054490b32` |

## Human/Agent usability limitation

The Statement anchor exists in the Registry, but direct Statement resolution remains
`index_unavailable` with reason `statement_map_deferred` and gap
`statement_semantic_target_not_indexed`. This was expected by the frozen owner-task Gold and is
reported as a limitation, not as a Relation Path. Exact stored-byte equality was verified by the
qualification runner; the ordinary Agent Source interface exposes the admitted fragment and
content hash, not an unrestricted raw-file download.

Guides, Codemap, Relation Path, complete history pagination, object diff, full `as_of` Wiki,
single-Revision revert, 10k/100k Wiki scale, real-host navigation, and independent human task
completion were not executed.

`development_thresholds_passed=true`

`human_gold_confirmed=false`

`release_gate_passed=false`

`claim_eligible=false`

`competitive_claim_eligible=false`
