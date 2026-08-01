# ADR 0005: Internal Authoritative Pack Core

- Status: Accepted for internal vNext use
- Date: 2026-08-01

## Context

Pack identity, issuer, trust roots, signed catalog sequence, revocation, immutable releases, exact
sources, segments, locators, extraction provenance, capabilities, receipts, historical pinning and
atomic activation are not legal-domain concepts. Evidence Duties, legal temporal meaning and legal
challenges are. Renaming `law_support` or generalising its legal policies would mix these layers and
could erase Authority partitions.

## Decision

DeepLaw introduces the internal `deeplaw.authoritative-pack-core/v1` descriptor for the shared
identity and trust envelope. It is a closed, digest-bound view over an existing governed store; it
is not a new database, ranking engine or mutation path. Each Pack retains its own trust root,
physical partition and domain policy. The Legal Pack continues to own its Evidence Duties,
legal-topic and temporal semantics, Challenge Trace and the read-only `law_support` process.

A pure synthetic non-legal policy fixture validates the abstraction without claiming public
Authority. No `authoritative_support` MCP leaf is added. Such a public interface can be considered
only after a non-legal, open-licensed Authoritative Pack has been operated end-to-end with a real
issuer/trust workflow.

## Consequences

- Shared Pack identity and activation semantics are explicit and hash-verifiable.
- Trust roots, rankings, receipts and permissions cannot be merged across Packs.
- Agent interpretation never becomes Pack content or Pack Authority.
- The abstraction creates no public capability claim and no second storage engine.
