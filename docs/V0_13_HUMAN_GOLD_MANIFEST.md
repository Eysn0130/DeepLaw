# DeepLaw v0.13 Human Gold manifest disposition

Status: **review_pending / no reviewer confirmation**. This document records absence; it is not a
Human Gold artifact and does not create a reviewer identity.

## Current evidence

| Item | Exact value |
| --- | --- |
| Semantic candidate | `benchmarks/semantic/semantic-gold-candidate-v1.json` |
| Candidate SHA-256 | `d3e85a1233ef2acccabf279dd7955733eddb3cfe53c5990c5ed42c50236386c3` |
| Existing freeze | `benchmarks/semantic/semantic-gold-freeze-v1.json` |
| Freeze SHA-256 | `3682f30716c8a0ead139aa09e41dcd715783648c17948b0aae63daffeb2edb67` |
| Human review packet contract | `deeplaw.semantic-human-review-packet/v1` |
| Contract SHA-256 | `51f808c154688bdb692d8bebdbf4458b678819088bac57c8147a9eb5ac531eaa` |
| v0.13 required status | `human_confirmed` |
| Observed reviewer identity | none |
| Observed maintainer confirmation | `false` |
| v0.13 gate status | `review_pending` |

The retained v0.12-era freeze says Human Gold was not required for its deterministic
machine-consensus scope. That earlier policy does not satisfy or override the stricter v0.13 GA
acceptance matrix. The v0.13 gate requires actual human confirmation of the blind Gold and cannot
be fulfilled by a model, Worker, maintainer inference, source hash, or this disposition document.

## Required future workflow

```bash
uv run --frozen python -m benchmarks.semantic.export_human_review_packet \
  --gold <external-v0.13-gold-candidate.json> \
  --compiler-report <external-blind-compiler-report.json> \
  --query-report <external-blind-query-report.json> \
  --output-directory <review-packet-directory>

uv run --frozen python -m benchmarks.semantic.review_gold \
  <reviewed-gold.json> \
  --confirm \
  --reviewer-id <real-reviewer-id> \
  --reason <review-record> \
  --output <confirmed-gold.json>
```

The reviewer must inspect the actual v0.13 statement/evidence, identity/relation, duty,
contradiction, gap and security labels. Review must occur outside the compiling Agent's write
boundary, and the resulting manifest must bind exact candidate and packet bytes. No placeholder
may be committed as a confirmation.

## Decision

`human_gold_confirmed=false`, `expert_review_confirmed=false`, `reviewer_id=null`,
`claim_eligible=false`, and `competitive_claim_eligible=false`. The Human Gold portion of G01 and
the Human/Expert quality gates remain unmet; v0.13.0 GA is forbidden.
