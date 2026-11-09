# ADR 0001: The trail is a set of addresses chosen by majority inflow

Status: accepted

## Context

Tracing tools usually assign a "taint" share to every unit of value: poison (any contact taints
everything), haircut (proportional) or first-in-first-out. They answer different questions and
disagree most on addresses that mix stolen and clean money, which are exactly the addresses where
a wrong answer harms someone. A public list must be defensible address by address.

## Decision

An address joins the trail when most of the value it ever received, in dollars at the time of each
transfer, came from the trail. Addresses below that line are counterparties, recorded with the
amount they received, and are not followed. Services (contracts, tagged entities, addresses with
exchange-scale history) stop the walk.

## Consequences

- Each verdict is one sentence anyone can check: "86% of the $4.2 million it ever received came
  from the trail".
- Money that passes through a mixed address is not followed past it. That is a deliberate
  trade-off: a missed hop is recoverable later (reconsideration re-judges counterparties as the
  trail grows), a false accusation is not.
- Bitcoin is handled by treating each transaction as a node that passes on the share of its inputs
  that came from the trail, so the same rule applies to coins and accounts.
