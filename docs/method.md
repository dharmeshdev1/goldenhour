# Method

How Goldenhour decides where stolen money is, and what each number on the site means. Every rule
here is implemented in code that is linked from its section, and every number the site shows can
be recomputed from public chain data with the commands in [Reproduce it](#reproduce-it).

## The case

A case is one theft: its time window, its seeds (addresses known to hold stolen funds) and the
victim's wallets. The Binance case lives in [`cases/binance-2026-10`](../cases/binance-2026-10):

- **Seeds.** The 628 addresses the FBI published on 31 October 2026 (608 Ethereum, 19 Bitcoin,
  1 Solana), in source order, plus first hops on chains the FBI list does not cover. Each of those
  was found and checked from chain data, and `case.yaml` records the transactions that prove it.
- **Window.** 19:00 UTC on 29 October (an hour before the first stolen transfer) to 00:10 UTC on
  30 October, when Binance revoked the EIP-7702 delegations.
- **Victim wallets.** Binance wallets that paid a first hop or a seed inside the window. On the XRP
  Ledger their labels come from XRPScan. Where no public label is available without an account, the
  wallet is identified by the theft transfers themselves.

## Following the money

The trail is the set of addresses that hold, or held, stolen money. It is built by
[`tracer.py`](../src/goldenhour/tracer.py) and [`trail.py`](../src/goldenhour/trail.py).

**The rule.** An address joins the trail when most of the value it ever received came from the
trail. Value is counted in US dollars at the hour each transfer happened.

That rule is deliberately simple. Industry tools offer several "taint" models (poison, haircut,
first-in-first-out), and they disagree most exactly where it matters: on addresses that mix stolen
and clean money. The majority rule makes one claim that is easy to check and hard to dispute: this
address's money mostly came from the theft. Addresses that received some stolen money but mostly
other money are recorded as counterparties, with the amount, and not followed.

Four refinements keep it honest:

1. **Services stop the walk.** Exchanges, bridges, pools, lending markets and other contracts hold
   everyone's money. When stolen money reaches one, it has left the attacker's direct control. It is
   recorded as an exit to that service and not followed into the service's general ledger.
   A service is recognized by being a contract (EVM, Tron, Solana program accounts), by a public
   explorer tag, or by behavior: an address with thousands of transactions is never a fresh
   laundering address.
2. **Exchange deposit addresses are recognized by where they sweep.** An exchange gives each
   customer an address and sweeps it into a hot wallet. Such an address receives only the
   customer's money, so by inflow alone it looks like the attacker's own. When 80% or more of what
   it sends goes to one exchange, it is a deposit address: the money has reached that exchange.
3. **Bitcoin transactions are nodes.** A transaction's outputs carry the share of its inputs that
   came from the trail, so a peel chain is followed and an unrelated payment mixed into the same
   transaction is not credited to the attacker. A transaction with twenty or more inputs and
   outputs and five or more outputs of exactly the same value is treated as a coinjoin: money that
   enters one is counted as mixed.
4. **Cross-chain links are facts, not guesses.** THORChain and Relay both publish both legs of
   every swap. When a trail address swaps ETH for bitcoin delivered to a new address, that address
   is on the trail: the attacker chose it. Bridges that do not publish both legs are recorded as
   exits ("bridged, not followed"). Goldenhour does not match deposits to withdrawals by amount and
   timing, because those guesses are wrong often enough to put innocent people on a list.

**Walking backwards.** The FBI list names addresses that "hold or have held" stolen funds, which is
mostly the second hop and beyond. The first hops, which took the money straight out of the victim's
wallets, are found by walking backwards from the seeds inside the theft window only. Earlier
history is not part of the theft and is never walked.

**Reconsidering.** An address judged early may have looked like an outsider only because the
addresses that paid it were not on the trail yet. After every pass, every counterparty is judged
again against the larger trail, without new requests.

**Dust and spam.** An address the trail paid less than $1,000 in total, summed over every
transfer it received from trail addresses, is not followed. Attackers' addresses are
showered with worthless tokens, some named "ETH" or "USDT", and with tokens a price feed will
happily quote from a thin pool. A token counts only if it is a well-known asset listed by contract
address in [`assets.py`](../src/goldenhour/assets.py), or if the victim's own wallets sent it during
the theft. Everything else is worth exactly zero in Goldenhour's accounting.

## Where the money is now

Computed by [`analysis.py`](../src/goldenhour/analysis.py) on every run, from scratch.

- **Stolen (verified on-chain).** Every transfer from a victim wallet to a trail address inside the
  window, at the hour it happened. This is a lower bound: chains Goldenhour does not read yet are
  missing from it, and the site shows the publicly reported total next to it, with its source.
- **Held by the attacker.** Each trail address's balance from its own ledger (everything it
  received minus everything it sent, fees included), valued at current prices. For the fresh
  addresses a launderer uses, the whole history is on record. On the XRP Ledger and Solana every
  transaction is also reconciled with the balances its metadata states before and after, so
  trades on the ledger's own exchange, account deletions, wrapped SOL and payments made directly
  by programs are in the ledger too. On EVM chains and Tron the ledger is built from the
  explorers' transaction, internal transaction and token transfer lists. It was checked against
  live balances for the Tron first hops, to the last unit.
- **Frozen.** Tether and Circle freeze addresses on-chain and say so in events
  (`AddedBlackList`, `Blacklisted`), and lift freezes the same way (`RemovedBlackList`,
  `UnBlacklisted`). A trail address's balance of a stablecoin whose issuer froze that address, and
  has not lifted the freeze, is frozen, not held. An event read fails loudly rather than returning
  a partial list, and a source that is down keeps the events already read.
- **Moved out.** Value that left the trail for a service, net of what came back in the same
  transaction. A swap that returns 80% or more of the value is a swap, not an exit: the slippage is
  not money leaving the attacker's hands.
- **Converted to bitcoin.** Value that crossed into bitcoin through a THORChain or Relay link.

## The golden hour

[`analysis.py`](../src/goldenhour/analysis.py), `golden_hour()`. Minute 0 is the first stolen
transfer seen on-chain.

- **Drained** is the cumulative value taken from the victim's wallets by each minute.
- **Freezable** is the value of stolen stablecoins sitting at trail addresses whose issuer had not
  frozen them yet, minute by minute, from a sweep over every stablecoin movement touching the
  trail.
- **Escaped** is stablecoin value that left the trail (swapped, lent or bridged) before a freeze.
- **Scenarios** ask two questions about a response that started N minutes after minute 0. If, from
  minute N on, every issuer had frozen each trail address as soon as it received their stablecoins,
  how much of what in reality was swapped or moved out after minute N would have stayed frozen? And
  if the signing path had been cut at minute N, how much would never have left the victim's
  wallets? Both assume each address is known within moments of receiving money, which is what
  real-time detection and tracing are for. With EIP-7702 delegations in place, cutting the signing
  path means revoking them, not only pausing withdrawals.

## Detection

[`watch.py`](../src/goldenhour/watch.py) and [`detect_eval.py`](../src/goldenhour/detect_eval.py).
Four rules read a custody wallet's outgoing transfers in time order. The strongest needs no
threshold at all: an ordinary account cannot move value from inside contract code unless it is
running delegated code, so value leaving an EOA through an internal transaction means an EIP-7702
delegation is live. The other three compare against the wallet's own history: a ten-minute outflow
beyond any seen, a single transfer beyond any seen to a new address, and tokens moved in a
transaction the wallet did not send.

The replay calibrates each wallet's thresholds on the first half of the days before the theft,
counts false alarms on the second half (days the calibration never saw), and then replays the
theft. The published replay says how many days it used. An isolation forest over five features of
each transfer is evaluated beside the rules, trained on the calibration days only.

The victim's busiest wallet moves thousands of tokens in some single blocks, and the free explorer
fails on a few of those blocks whatever the query. The replay skips a block only after every
narrower range and page size has failed, and reports the number it skipped as `unread_blocks`.
The tracer never skips: a read with a gap fails and is tried again on the next run.

## What Goldenhour cannot see

- **Chains not read yet.** The theft also reached Optimism, Arbitrum, Base, Avalanche, Polygon and
  NEAR, a few percent of the total by public accounts. Those first hops are not on the trail yet,
  and Arbitrum, Base and Polygon's free explorers refuse automated clients (an Etherscan key lifts
  that). Money that crossed into any chain through a bridge that does not publish both legs is
  counted as "bridged, not followed".
- **Inside services.** Once money reaches an exchange, a mixer or an instant swap service, its
  next movement is that service's private bookkeeping.
- **Off-chain deals.** Over-the-counter brokers settle on their own books.
- **Labels.** Public labels are incomplete. An unlabeled exchange hot wallet is recognized by its
  scale, not by its name, and is shown as an unlabeled service.
- **Attribution.** Goldenhour follows money. It does not identify people. "On the trail" means
  most of the address's money came from the theft, nothing more.

## Reproduce it

```sh
uv sync --extra ml
export GOLDENHOUR_STATE=state
uv run goldenhour trace --budget 3600     # fetch, judge, follow (hours from scratch, minutes after)
uv run goldenhour publish --out site/api/v1
uv run goldenhour detect                  # the detector replay
```

Or start from the signed daily snapshot, and check where it was built first:

```sh
gh release download state --repo dharmeshdev1/goldenhour --pattern state.tar.gz
gh attestation verify state.tar.gz --repo dharmeshdev1/goldenhour \
  --signer-workflow dharmeshdev1/goldenhour/.github/workflows/daily.yml --source-ref refs/heads/main
```
