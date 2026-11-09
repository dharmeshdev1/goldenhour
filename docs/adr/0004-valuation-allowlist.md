# ADR 0004: Only listed assets and the victim's own tokens have a dollar value

Status: accepted

## Context

Attackers' addresses receive airdropped tokens: lookalikes named "ETH" or "USDT", and tokens with a
thin pool that price feeds quote with high confidence. One such token, valued at face, would add
any amount a spammer likes to what "the attacker holds".

## Decision

A token is valued only if it is listed by contract address in `assets.py` (major stablecoins,
wrapped coins, liquid staking and lending receipts, large tokens), or if the victim's own wallets
sent it during the theft. Every chain's native coin is valued. Everything else counts as zero.

## Consequences

- Holdings and flows cannot be inflated by spam.
- A real but unlisted token the attacker swaps into is undervalued until it is listed. The cost is
  an understatement, never an overstatement.
