"""From stored facts to the numbers on the page.

Everything here is computed from the transfers in the store, the freeze events read from the
issuers' contracts and hourly prices. Nothing is copied from a news report. Where the public record
has a number Goldenhour cannot verify on-chain (the total stolen across all eleven chains, for
example), it is shown next to ours, labeled with its source, never blended into it.

Balances come from each address's own ledger: everything it received minus everything it sent,
fees included. For the fresh addresses a launderer uses, whose whole history is in the store, this
is exact and needs no extra requests.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from goldenhour.case import Case
from goldenhour.freeze import FreezeEvent
from goldenhour.models import NATIVE_SYMBOL, Chain, Kind, Transfer, is_virtual, split_node
from goldenhour.trail import TRAIL_ROLES, Graph, Trail

# Stablecoins an issuer can freeze, by contract. Value held in these is recoverable in principle
# for as long as it sits still; the race is to freeze before it is swapped.
FREEZABLE: dict[str, str] = {
    "ethereum:0xdac17f958d2ee523a2206206994597c13d831ec7": "Tether",
    "ethereum:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "Circle",
    "ethereum:0xc5f0f7b66764f6ec8c8dff7ba683102295e16409": "First Digital",
    "ethereum:0x6c3ea9036406852006290770bedfcaba0e23a0e8": "Paxos",
    "tron:TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t": "Tether",
    "tron:TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8": "Circle",
    "solana:EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "Circle",
    "solana:Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "Tether",
    "arbitrum:0xaf88d065e77c8cc2239327c5edb3a432268e5831": "Circle",
    "arbitrum:0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9": "Tether",
    "base:0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": "Circle",
    "optimism:0x0b2c639c533813f4aa9d7837caf62653d097ff85": "Circle",
    "optimism:0x94b008aa00579c1307b0ef2c499ad98a8ce58e58": "Tether",
    "polygon:0x3c499c542cef5e3811e1192ce70d8cc03d5c3359": "Circle",
    "polygon:0xc2132d05d31c914a87c6611c10748aeb04b58e8f": "Tether",
    "avalanche:0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e": "Circle",
    "avalanche:0x9702230a8ea53601f5cd2dc00fdbc13d4df4a8c7": "Tether",
}

SWAP_RETURN = 0.8

EXIT_CATEGORIES = ("exchange", "mixer", "bridge", "lending", "dex", "staking", "other")


def category_bucket(category: str) -> str:
    if category in ("exchange", "exchange-deposit"):
        return "exchange"
    if category in EXIT_CATEGORIES:
        return category
    return "other"


@dataclass(slots=True)
class Analysis:
    case: Case
    graph: Graph
    trail: Trail
    freezes: list[FreezeEvent]
    now: int
    roles: dict[str, dict] = field(init=False)
    trail_nodes: set[str] = field(init=False)
    victims: set[str] = field(init=False)
    frozen_at: dict[tuple[str, str], int] = field(init=False)  # (node, issuer) -> first freeze ts
    # (node, issuer) -> [start, end) spans while frozen; end is 0 while the freeze still holds.
    frozen_spans: dict[tuple[str, str], list[list[int]]] = field(init=False)
    _balances: dict[str, dict[str, int]] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        self.roles = self.trail.roles
        self.trail_nodes = {n for n, r in self.roles.items() if r["role"] in TRAIL_ROLES}
        self.victims = {n for n, r in self.roles.items() if r["role"] == "victim"}
        self.frozen_at = {}
        self.frozen_spans = {}
        # At the same second a freeze sorts before an unfreeze, so the pair reads as a short freeze.
        for e in sorted(self.freezes, key=lambda e: (e.ts, e.kind != "freeze")):
            key = (e.node, e.issuer)
            spans = self.frozen_spans.setdefault(key, [])
            if e.kind == "freeze":
                self.frozen_at.setdefault(key, e.ts)
                if not spans or spans[-1][1]:
                    spans.append([e.ts, 0])
            elif e.kind == "unfreeze" and spans and not spans[-1][1]:
                spans[-1][1] = e.ts

    def frozen_now(self, node: str, issuer: str) -> bool:
        spans = self.frozen_spans.get((node, issuer)) or []
        return bool(spans) and spans[-1][1] == 0

    def frozen_by(self, node: str, issuer: str, ts: int) -> bool:
        """Whether the issuer had this address frozen at `ts`."""
        return any(start <= ts and (not end or ts < end) for start, end in self.frozen_spans.get((node, issuer), []))

    # Ledgers

    def balances(self, node: str) -> dict[str, int]:
        """Asset -> amount the address holds now, from its own ledger. Cross-chain links are not
        value movements (the real outbound transaction is in the ledger already) and are skipped."""
        if node in self._balances:
            return self._balances[node]
        bal: dict[str, int] = defaultdict(int)
        for t in self.graph.into.get(node, []):
            if t.kind is not Kind.BRIDGE:
                bal[t.asset] += t.amount
        for t in self.graph.out.get(node, []):
            if t.kind is not Kind.BRIDGE:
                bal[t.asset] -= t.amount
        clean = {a: v for a, v in bal.items() if v > 0}
        self._balances[node] = clean
        return clean

    def value(self, asset: str, amount: int, ts: int | None = None) -> float:
        if not self.graph.valued(asset):
            return 0.0
        decimals = self.decimals(asset)
        return self.graph.prices.usd(asset, amount, decimals, ts)

    def decimals(self, asset: str) -> int:
        chain, _, tail = asset.partition(":")
        if tail == "native":
            from goldenhour.models import NATIVE_DECIMALS

            return NATIVE_DECIMALS[Chain(chain)]
        token = self.graph.tokens.get(asset)
        return token.decimals if token else 18

    def symbol(self, asset: str) -> str:
        chain, _, tail = asset.partition(":")
        if tail == "native":
            return NATIVE_SYMBOL[Chain(chain)]
        lid_quote = self.graph.prices.quotes.get(_lid(asset))
        token = self.graph.tokens.get(asset)
        return (lid_quote.symbol if lid_quote and lid_quote.symbol else token.symbol if token else "?")[:16]

    # The theft

    def theft(self) -> list[tuple[Transfer, float]]:
        """Transfers that took value out of the victim's wallets and into the trail, with their
        dollar value at the time. Bitcoin is credited per output: only outputs paid to the trail."""
        end = self.case.end_ts + 2 * 3600
        found: list[tuple[Transfer, float]] = []
        for v in self.victims:
            for t in self.graph.out.get(v, []):
                if not (self.case.start_ts <= t.ts <= end) or t.kind is Kind.FEE:
                    continue
                if t.receiver.startswith("bitcoin:@tx:"):
                    for o in self.graph.out.get(t.receiver, []):
                        if o.receiver in self.trail_nodes:
                            found.append((o, self.graph.usd(o)))
                elif t.receiver in self.trail_nodes:
                    found.append((t, self.graph.usd(t)))
        unique = {t.uid: (t, v) for t, v in found}
        return sorted(unique.values(), key=lambda tv: tv[0].ts)

    # Where it is now

    def holdings(self) -> dict[str, object]:
        by_chain: dict[str, list[float]] = defaultdict(lambda: [0.0, 0])
        by_asset: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
        total = 0.0
        holders = 0
        for n in self.trail_nodes:
            held = 0.0
            for asset, amount in self.balances(n).items():
                if self.is_frozen(n, asset):
                    continue
                v = self.value(asset, amount)
                if v <= 0:
                    continue
                held += v
                by_asset[asset][0] += amount / 10 ** self.decimals(asset)
                by_asset[asset][1] += v
            if held >= 1.0:
                holders += 1
                chain = split_node(n)[0]
                by_chain[chain][0] += held
                by_chain[chain][1] += 1
                total += held
        chain_rows: list[dict[str, Any]] = [
            {"chain": c, "usd": v[0], "addresses": int(v[1])} for c, v in by_chain.items()
        ]
        asset_rows: list[dict[str, Any]] = [
            {"symbol": self.symbol(a), "chain": a.partition(":")[0], "amount": v[0], "usd": v[1]}
            for a, v in by_asset.items()
            if v[1] >= 1000
        ]
        return {
            "usd": total,
            "addresses": holders,
            "by_chain": sorted(chain_rows, key=lambda r: -r["usd"]),
            "by_asset": sorted(asset_rows, key=lambda r: -r["usd"])[:40],
        }

    def is_frozen(self, node: str, asset: str) -> bool:
        issuer = FREEZABLE.get(asset)
        return bool(issuer and self.frozen_now(node, issuer))

    def frozen(self) -> dict[str, object]:
        rows: dict[tuple[str, str, str], dict] = {}
        total = 0.0
        for (node, issuer), ts in self.frozen_at.items():
            if node not in self.trail_nodes or not self.frozen_now(node, issuer):
                continue
            for asset, amount in self.balances(node).items():
                if FREEZABLE.get(asset) != issuer:
                    continue
                v = self.value(asset, amount)
                key = (issuer, asset.partition(":")[0], self.symbol(asset))
                row = rows.setdefault(
                    key,
                    {
                        "issuer": issuer,
                        "chain": key[1],
                        "symbol": key[2],
                        "amount": 0.0,
                        "usd": 0.0,
                        "addresses": 0,
                        "first_at": ts,
                    },
                )
                row["amount"] += amount / 10 ** self.decimals(asset)
                row["usd"] += v
                row["addresses"] += 1
                row["first_at"] = min(row["first_at"], ts)
                total += v
        for row in rows.values():
            row["first_at"] = iso(int(row["first_at"]))
        return {"usd": total, "by_issuer": sorted(rows.values(), key=lambda r: -r["usd"])}

    def net_outflows(self) -> list[tuple[Transfer, float, str]]:
        """Value that left the trail for good: (transfer, dollars, bucket).

        A swap sends value to a pool and gets value back in the same transaction; only the part
        that does not come back has left. Transfers between trail addresses and cross-chain links
        the trail continues through are not exits.
        """
        out: list[tuple[Transfer, float, str]] = []
        seen_hubs: set[str] = set()
        for n in self.trail_nodes:
            by_tx: dict[str, list[Transfer]] = defaultdict(list)
            for t in self.graph.out.get(n, []):
                if t.ts < self.case.start_ts or t.kind in (Kind.FEE, Kind.BRIDGE):
                    continue
                if t.receiver.startswith("bitcoin:@tx:"):
                    if t.receiver not in seen_hubs:
                        seen_hubs.add(t.receiver)
                        out.extend(self._bitcoin_exits(t.receiver))
                    continue
                if t.receiver in self.trail_nodes or t.receiver in self.victims:
                    continue
                by_tx[t.tx].append(t)
            for tx, sent in by_tx.items():
                back = sum(
                    self.graph.usd(t)
                    for t in self.graph.into.get(n, [])
                    if t.tx == tx and t.kind is not Kind.BRIDGE and t.sender not in self.trail_nodes
                )
                gone = sum(self.graph.usd(t) for t in sent)
                # A swap returns most of the value in the same transaction; what it does not return
                # is slippage and fees, not money leaving the attacker's hands.
                if back >= SWAP_RETURN * gone:
                    continue
                net = gone - back
                if net <= 1.0:
                    continue
                main = max(sent, key=self.graph.usd)
                out.append((main, net, self.bucket(main.receiver)))
        return out

    def _bitcoin_exits(self, hub: str) -> list[tuple[Transfer, float, str]]:
        """Each output of a bitcoin transaction that left the trail, judged on its own.

        Outputs to trail addresses are moves, and change back to an address that funded the
        transaction is not an exit. Every other output carries the transaction's trail share.
        """
        share = self.trail.fraction(hub)
        if share <= 0:
            return []
        outs = self.graph.out.get(hub, [])
        funders = {i.sender for i in self.graph.into.get(hub, [])}
        mixed = is_coinjoin(outs)
        found: list[tuple[Transfer, float, str]] = []
        for o in outs:
            if o.kind is Kind.FEE or o.receiver in self.trail_nodes or o.receiver in funders:
                continue
            usd = self.graph.usd(o) * share
            if usd <= 1.0:
                continue
            found.append((o, usd, "mixer" if mixed else self.bucket(o.receiver)))
        return found

    def bucket(self, receiver: str) -> str:
        if receiver.startswith("bitcoin:@tx:"):
            outs = self.graph.out.get(receiver, [])
            if is_coinjoin(outs):
                return "mixer"
            dests = [o.receiver for o in outs if o.kind is not Kind.FEE]
            if any(d in self.trail_nodes for d in dests):
                return "trail"
            if dests and not any(d in self.roles for d in dests):
                return "pending"
            return category_bucket(
                max(((self.roles.get(d) or {}).get("category", "") for d in dests), key=len, default="")
            )
        if is_virtual(receiver):
            # The XRP Ledger's own exchange counts as a DEX; any other ledger-held value as other.
            return "dex" if receiver.endswith(":@dex") else "other"
        row = self.roles.get(receiver)
        if row is None:
            # Not judged yet: the next pass decides whether it is the attacker's, a service or an
            # outsider. Until then it is not counted as having left the trail.
            return "pending"
        return category_bucket(row.get("category", ""))

    def exits(self) -> dict[str, object]:
        cats: dict[str, dict] = {}
        total = 0.0
        for t, usd, bucket in self.net_outflows():
            if bucket in ("trail", "pending"):
                continue
            entity = self.entity(t.receiver, bucket)
            c = cats.setdefault(
                bucket, {"category": bucket, "usd": 0.0, "count": 0, "entities": defaultdict(lambda: [0.0, 0])}
            )
            c["usd"] += usd
            c["count"] += 1
            c["entities"][entity][0] += usd
            c["entities"][entity][1] += 1
            total += usd
        rows = []
        for c in cats.values():
            ents = sorted(
                ({"name": k, "usd": v[0], "count": v[1]} for k, v in c["entities"].items()), key=lambda r: -r["usd"]
            )
            rows.append({**c, "entities": ents[:12]})
        return {"usd": total, "by_category": sorted(rows, key=lambda r: -r["usd"])}

    def entity(self, node: str, bucket: str) -> str:
        if node.startswith("bitcoin:@tx:"):
            return "Bitcoin coinjoin" if bucket == "mixer" else "Bitcoin transaction"
        row = self.roles.get(node) or {}
        info = row.get("info") or {}
        name = (info.get("entity") or row.get("label") or "").strip()
        if name:
            return name[:60]
        return "Unlabeled " + ("service" if bucket != "other" else "address")

    def converted_to_btc(self) -> dict[str, float]:
        btc = usd = 0.0
        swaps = 0
        for n in self.trail_nodes:
            for t in self.graph.into.get(n, []):
                if t.kind is Kind.BRIDGE and t.chain is Chain.BITCOIN:
                    btc += t.amount / 1e8
                    usd += self.graph.usd(t)
                    swaps += 1
        return {"btc": btc, "usd": usd, "swaps": swaps}

    # The first hour

    def golden_hour(self, minutes_after_end: int = 30) -> dict[str, object]:
        theft = self.theft()
        if not theft:
            return {}
        t0 = theft[0][0].ts
        horizon = max(240, math.ceil((self.case.end_ts - t0) / 60) + minutes_after_end)
        drained = [0.0] * (horizon + 1)
        for t, usd in theft:
            m = max(0, (t.ts - t0) // 60)
            if m <= horizon:
                drained[m] += usd
        for i in range(1, horizon + 1):
            drained[i] += drained[i - 1]
        # Freezable stablecoins: a sweep over every stablecoin movement touching the trail.
        moves: list[tuple[int, str, str, int]] = []  # ts, node, asset, signed amount
        for node in self.trail_nodes:
            for t in self.graph.into.get(node, []):
                if t.asset in FREEZABLE and t.kind is not Kind.BRIDGE:
                    moves.append((t.ts, node, t.asset, t.amount))
            for t in self.graph.out.get(node, []):
                if t.asset in FREEZABLE and t.kind is not Kind.BRIDGE:
                    moves.append((t.ts, node, t.asset, -t.amount))
        moves.sort()
        holdings: dict[tuple[str, str], int] = defaultdict(int)
        escaped = 0.0
        freezable_curve, escaped_curve, frozen_curve = [], [], []
        idx = 0
        for m in range(horizon + 1):
            limit = t0 + 60 * m + 59
            while idx < len(moves) and moves[idx][0] <= limit:
                ts, node, asset, delta = moves[idx]
                before = holdings[(node, asset)]
                holdings[(node, asset)] = before + delta
                if delta < 0 and ts >= t0:
                    # Out of the trail entirely (swapped, lent, bridged) unless another trail address got it.
                    escaped += self._escaped_usd(node, asset, -delta, ts)
                idx += 1
            free = frozen = 0.0
            for (node, asset), amount in holdings.items():
                if amount <= 0:
                    continue
                v = self.value(asset, amount)
                issuer = FREEZABLE[asset]
                if self.frozen_by(node, issuer, limit):
                    frozen += v
                else:
                    free += v
            freezable_curve.append(round(free, 2))
            frozen_curve.append(round(frozen, 2))
            escaped_curve.append(round(escaped, 2))
        events = self._events(theft, t0, horizon)
        total_drained = drained[-1]
        scenarios = []
        for n in (1, 2, 3, 5, 10, 15, 20, 30, 37, 45, 60, 90, 120, 180, 240):
            if n > horizon:
                continue
            scenarios.append(
                {
                    "response_minutes": n,
                    # A response that starts at minute n freezes each trail address as it receives
                    # stablecoins from then on, so everything that escaped after n would have stayed.
                    "freezable_saved_usd": round(max(0.0, escaped_curve[-1] - escaped_curve[n]), 2),
                    "drain_prevented_usd": round(max(0.0, total_drained - drained[n]), 2),
                }
            )
        return {
            "t0": iso(t0),
            "minutes": list(range(horizon + 1)),
            "drained_usd": [round(x, 2) for x in drained],
            "freezable_usd": freezable_curve,
            "frozen_usd": frozen_curve,
            "escaped_usd": escaped_curve,
            "events": events,
            "scenarios": scenarios,
        }

    def _escaped_usd(self, node: str, asset: str, amount: int, ts: int) -> float:
        # Only count it if no trail address received it in the same transaction.
        for t in self.graph.out.get(node, []):
            if t.ts == ts and t.asset == asset and t.amount == amount:
                if t.receiver in self.trail_nodes:
                    return 0.0
                return self.graph.usd(t)
        return self.value(asset, amount, ts)

    def _events(self, theft: list[tuple[Transfer, float]], t0: int, horizon: int) -> list[dict]:
        events: list[dict] = []
        firsts: dict[str, Transfer] = {}
        for t, _ in theft:
            firsts.setdefault(str(t.chain), t)
        for chain, t in firsts.items():
            events.append(
                {
                    "minute": (t.ts - t0) // 60,
                    "at": iso(t.ts),
                    "label": f"First stolen transfer on {chain_name(chain)}",
                    "kind": "theft",
                    "source": "chain",
                }
            )
        seen: set[tuple[str, str]] = set()
        for e in sorted(self.freezes, key=lambda e: e.ts):
            if e.kind != "freeze" or e.node not in self.trail_nodes:
                continue
            key = (e.issuer, str(e.chain))
            if key in seen:
                continue
            seen.add(key)
            m = (e.ts - t0) // 60
            if 0 <= m <= horizon:
                events.append(
                    {
                        "minute": m,
                        "at": iso(e.ts),
                        "label": f"{e.issuer} freezes the first trail address on {chain_name(str(e.chain))}",
                        "kind": "freeze",
                        "source": "chain",
                    }
                )
        last = theft[-1][0]
        events.append(
            {
                "minute": (last.ts - t0) // 60,
                "at": iso(last.ts),
                "label": "Last stolen transfer seen on-chain",
                "kind": "theft",
                "source": "chain",
            }
        )
        revoke = self.case.end_ts
        events.append(
            {
                "minute": (revoke - t0) // 60,
                "at": iso(revoke),
                "label": "Binance revokes the EIP-7702 delegations",
                "kind": "revoke",
                "source": "thoropass",
            }
        )
        return sorted(events, key=lambda e: e["minute"])


CHAIN_NAMES = {
    "ethereum": "Ethereum",
    "bitcoin": "Bitcoin",
    "tron": "Tron",
    "xrpl": "the XRP Ledger",
    "solana": "Solana",
    "optimism": "Optimism",
    "arbitrum": "Arbitrum",
    "base": "Base",
    "polygon": "Polygon",
    "avalanche": "Avalanche",
    "near": "NEAR",
}


def chain_name(chain: str) -> str:
    return CHAIN_NAMES.get(chain, chain)


def is_coinjoin(outs: list[Transfer]) -> bool:
    """A bitcoin transaction that looks like a coinjoin: many inputs and outputs and many outputs
    of exactly the same value. WabiSabi rounds hold dozens of equal standard denominations."""
    if not outs:
        return False
    meta = outs[0].meta
    try:
        n_in, n_out, equal = int(meta.get("n_in", 0)), int(meta.get("n_out", 0)), int(meta.get("equal_out", 0))
    except ValueError:
        return False
    return n_in >= 20 and n_out >= 20 and equal >= 5


def iso(ts: int) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _lid(asset: str) -> str:
    from goldenhour.prices import llama_id

    return llama_id(asset) or ""
