# ADR 0003: Screening fetches a 1-in-4,096 shard

Status: accepted

## Context

Asking a public service "is this address dirty?" tells that service which address you care about:
a customer's deposit, a counterparty, an investigation. Compliance teams cannot send that to a
third party.

## Decision

The screening index is split into 4,096 shards by the first three hex characters of the SHA-256 of
the address alone, spelled canonically (EVM and bech32 lowercased, base58 as written) with no chain
attached. Each key lists one entry per chain the address appears on. The client hashes locally,
fetches one shard and keeps the entries for the chains it is checking. The host learns a prefix
shared by roughly one address in 4,096, the same k-anonymity design as the Have I Been Pwned range
API.

The chain stays out of the hash on purpose. An EVM address is the same on six chains, and a base58
string can be both a Tron and a Solana address. Hashing `chain:address` would make one lookup fetch
up to six shards, and six prefixes that all belong to one address narrow it down far more than one
prefix does: the host could intersect them.

## Consequences

- Private by construction, and still a static file a CDN can serve.
- One request per lookup, whatever chain the address is on.
- Shards are small today; they grow with the trail and remain cacheable.
- A miss means "not on this trail", never "clean". Every interface says so.
