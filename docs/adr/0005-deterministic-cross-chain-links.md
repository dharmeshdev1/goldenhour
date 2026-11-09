# ADR 0005: Cross-chain links come only from protocols that publish both legs

Status: accepted

## Context

Laundering crosses chains. Some protocols (THORChain, Relay) publish both the deposit and the
payout of every transfer. Others leave two unrelated-looking transactions that can only be matched
by amount and timing, which mismatches often, especially for round amounts in busy periods.

## Decision

Goldenhour follows a cross-chain transfer only when the protocol itself records both legs. Value
sent into any other bridge is reported as "bridged, not followed".

## Consequences

- Every cross-chain hop on the trail is a published fact, with both transaction ids.
- Some value is not followed past a bridge. It is counted, labeled, and visible.
