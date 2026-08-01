# Evidence admission workflow

Evidence admission means accepting a source only after identity, lifecycle, scope, sensitivity,
and provenance checks succeed. Evidence ranking never establishes authority.

## Procedure

1. Verify the exact Source Revision bytes.
2. Check scope and sensitivity.
3. Validate every fragment locator and quote hash.
4. Admit the source or return an explicit gap.

## Timeline

- 2025-01-10: the admission policy was drafted.
- 2025-03-15: locator validation became mandatory.
- 2025-05-20: silent fallback was prohibited.
