"""Write the public API: static JSON (and Parquet) that the site and anyone else can read.

Every file is rewritten from scratch on each run, so the published data can never drift from the
store. Writes go to a temporary directory that replaces the old one at the end, so a reader never
sees half an update.
"""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from goldenhour.analysis import FREEZABLE, Analysis, category_bucket, is_coinjoin, iso
from goldenhour.case import Case
from goldenhour.freeze import FreezeEvent
from goldenhour.models import EVM_CHAINS, Chain, Kind, Transfer, is_virtual, split_node
from goldenhour.prices import PriceBook
from goldenhour.screen import node_key as screen_key
from goldenhour.store import Store
from goldenhour.trail import TRAIL_ROLES, Graph, Policy, Trail, stolen_assets

EXPLORER_TX = {
    "ethereum": "https://etherscan.io/tx/{}",
    "optimism": "https://optimistic.etherscan.io/tx/{}",
    "arbitrum": "https://arbiscan.io/tx/{}",
    "base": "https://basescan.org/tx/{}",
    "polygon": "https://polygonscan.com/tx/{}",
    "avalanche": "https://snowtrace.io/tx/{}",
    "bitcoin": "https://mempool.space/tx/{}",
    "tron": "https://tronscan.org/#/transaction/{}",
    "xrpl": "https://xrpscan.com/tx/{}",
    "solana": "https://solscan.io/tx/{}",
    "near": "https://nearblocks.io/txns/{}",
}
GRAPH_LIMIT = 2_000
FEED_LIMIT = 200

GOLDEN_HOUR_NOTES = [
    "Minute 0 is the first stolen transfer Goldenhour can see on-chain.",
    "Freezable means stolen stablecoins (USDT, USDC and others whose issuer can freeze an address) sitting at an "
    "address on the trail that its issuer had not frozen yet.",
    "Could still have been frozen: if, from that minute on, every issuer had frozen each trail address as soon as it "
    "received their stablecoins, the stablecoins that in reality were swapped or moved out after that minute would "
    "have stayed frozen. That needs each address to be known within moments, which is what real-time detection and "
    "tracing provide.",
    "Drain prevented assumes the signing path was cut at that minute, so nothing left the victim's wallets afterwards. "
    "With EIP-7702 delegations in place, that means revoking them, not only pausing withdrawals.",
    "Values are in US dollars at the hour each transfer happened.",
]


def tx_url(chain: str, tx: str) -> str:
    template = EXPLORER_TX.get(chain)
    return template.format(tx) if template and tx else ""


class Publisher:
    def __init__(self, store: Store, case: Case, prices: PriceBook, freezes: list[FreezeEvent], now: int) -> None:
        self.store = store
        self.case = case
        self.now = now
        graph = Graph(prices, store.token_table())
        graph.add(store.all_transfers())
        roles = {r["node"]: r for r in store.nodes_by_role()}
        graph.stolen = stolen_assets(graph, roles, case.start_ts, case.end_ts + 7200)
        self.trail = Trail(graph, roles, Policy(start_ts=case.start_ts))
        self.graph = graph
        self.a = Analysis(case, graph, self.trail, freezes, now)
        self.freezes = freezes

    # Documents

    def case_doc(self) -> dict:
        c = self.case
        return {
            "id": c.id,
            "title": c.title,
            "victim": c.victim,
            "attribution": c.attribution,
            "window": {"start": iso(c.start_ts), "end": iso(c.end_ts)},
            "reported": c.reported,
            "timeline": c.timeline,
            "sources": c.sources,
        }

    def summary(self) -> dict:
        a = self.a
        theft = a.theft()
        by_chain: dict[str, float] = defaultdict(float)
        by_asset: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
        for t, usd in theft:
            by_chain[str(t.chain)] += usd
            by_asset[t.asset][0] += t.amount / 10 ** a.decimals(t.asset)
            by_asset[t.asset][1] += usd
        stolen_total = sum(by_chain.values())
        trail_nodes = a.trail_nodes
        trail_transfers = sum(len(self.graph.out.get(n, [])) for n in trail_nodes)
        services = {
            r
            for n in trail_nodes
            for t in self.graph.out.get(n, [])
            if (r := t.receiver) in self.trail.roles and self.trail.roles[r]["role"] == "service"
        }
        last_move = max(
            (t.ts for n in trail_nodes for t in self.graph.out.get(n, []) if t.kind is not Kind.FEE), default=0
        )
        data_through = max((t.ts for ts in self.graph.out.values() for t in ts), default=0)
        asset_rows: list[dict[str, Any]] = [
            {"symbol": a.symbol(k), "chain": k.partition(":")[0], "amount": v[0], "usd": round(v[1], 2)}
            for k, v in by_asset.items()
            if v[1] >= 1000
        ]
        return {
            "generated_at": iso(self.now),
            "data_through": iso(data_through) if data_through else iso(self.now),
            "last_movement_at": iso(last_move) if last_move else None,
            "stolen": {
                "usd_at_theft": round(stolen_total, 2),
                "reported_usd": self.case.reported.get("stolen_usd"),
                "reported_source": self.case.reported.get("stolen_usd_source"),
                "by_chain": sorted(
                    ({"chain": c, "usd": round(v, 2)} for c, v in by_chain.items()), key=lambda r: -r["usd"]
                ),
                "by_asset": sorted(asset_rows, key=lambda r: -r["usd"])[:30],
                "transfers": len(theft),
                "victim_wallets": len(
                    {t.sender for t, _ in theft if not is_virtual(t.sender)}
                    | {
                        i.sender
                        for t, _ in theft
                        if t.sender.startswith("bitcoin:@tx:")
                        for i in self.graph.into.get(t.sender, [])
                        if i.sender in a.victims
                    }
                ),
                "first_at": iso(theft[0][0].ts) if theft else None,
                "last_at": iso(theft[-1][0].ts) if theft else None,
            },
            "now": {"held": a.holdings(), "frozen": a.frozen(), "exited": a.exits()},
            "converted": {"to_btc": a.converted_to_btc()},
            "trail": {
                "addresses": len(trail_nodes),
                "chains": sorted({split_node(n)[0] for n in trail_nodes}),
                "max_hop": max((int(self.trail.roles[n].get("hop", 0)) for n in trail_nodes), default=0),
                "transfers": trail_transfers,
                "services_touched": len(services),
            },
        }

    def flows(self, summary: dict) -> dict:
        """Victim to the chain each value left on, to where that value is now."""
        a = self.a
        origin = self._origins()
        states: dict[tuple[str, str], float] = defaultdict(float)
        for n in a.trail_nodes:
            chain = origin.get(n, str(split_node(n)[0]))
            for asset, amount in a.balances(n).items():
                v = a.value(asset, amount)
                if v <= 0:
                    continue
                state = "Frozen" if a.is_frozen(n, asset) else "Held at trail addresses"
                states[(chain, state)] += v
        for t, usd, bucket in a.net_outflows():
            if bucket == "trail":
                continue
            chain = origin.get(t.sender, str(split_node(t.sender)[0]))
            label = {
                "exchange": "Sent to exchanges",
                "mixer": "Mixed",
                "bridge": "Bridged, not followed",
                "lending": "In lending protocols",
                "dex": "Left through swaps",
                "staking": "Staked",
                "other": "Other addresses",
                "pending": "Not yet followed",
            }[bucket]
            states[(chain, label)] += usd
        nodes = [{"id": "victim", "label": self.case.victim, "kind": "victim"}]
        links = []
        chains = sorted({c for c, _ in states})
        for c in chains:
            total = sum(v for (cc, _), v in states.items() if cc == c)
            if total < 1:
                continue
            nodes.append({"id": f"chain:{c}", "label": c.capitalize(), "kind": "chain"})
            links.append({"source": "victim", "target": f"chain:{c}", "usd": round(total, 2)})
        for state in sorted({s for _, s in states}):
            nodes.append({"id": f"state:{state}", "label": state, "kind": "state"})
        for (c, s), v in states.items():
            if v >= 1:
                links.append({"source": f"chain:{c}", "target": f"state:{s}", "usd": round(v, 2)})
        return {"nodes": nodes, "links": links}

    def _origins(self) -> dict[str, str]:
        """The chain each trail address's money was stolen on, by following parents to a first hop."""
        out: dict[str, str] = {}
        for n in self.a.trail_nodes:
            seen = set()
            cur = n
            while cur and cur not in seen:
                seen.add(cur)
                row = self.trail.roles.get(cur) or {}
                parent = row.get("parent") or ""
                if not parent or parent not in self.a.trail_nodes:
                    break
                cur = parent
            out[n] = str(split_node(cur)[0])
        return out

    def feed(self) -> dict:
        a = self.a
        items: list[dict] = []
        for n in a.trail_nodes:
            by_tx: dict[str, list[Transfer]] = defaultdict(list)
            for t in self.graph.out.get(n, []):
                if t.ts >= self.case.start_ts and t.kind is not Kind.FEE:
                    by_tx[t.tx].append(t)
            for tx, sent in by_tx.items():
                main = max(sent, key=self.graph.usd)
                usd = self.graph.usd(main)
                if usd < 1_000:
                    continue
                kind, note = self._feed_kind(n, main, tx)
                items.append(self._item(main, kind, note, usd))
        for e in self.freezes:
            if e.node not in a.trail_nodes or e.kind not in ("freeze", "unfreeze"):
                continue
            # What this issuer's own stablecoins at the address are worth now, frozen or not.
            usd = sum(
                a.value(asset, amt) for asset, amt in a.balances(e.node).items() if FREEZABLE.get(asset) == e.issuer
            )
            items.append(
                {
                    "ts": iso(e.ts),
                    "chain": str(e.chain),
                    "tx": e.tx,
                    "explorer_url": tx_url(str(e.chain), e.tx),
                    "kind": e.kind,
                    "from": split_node(e.node)[1],
                    "from_label": self._label(e.node),
                    "to": "",
                    "to_label": e.issuer,
                    "symbol": e.symbol,
                    "amount": 0.0,
                    "usd": round(usd, 2),
                    "note": f"{e.issuer} froze this address"
                    if e.kind == "freeze"
                    else f"{e.issuer} lifted its freeze on this address",
                }
            )
        items.sort(key=lambda i: i["ts"], reverse=True)
        return {"generated_at": iso(self.now), "items": items[:FEED_LIMIT]}

    def _feed_kind(self, sender: str, t: Transfer, tx: str) -> tuple[str, str]:
        a = self.a
        if t.kind is Kind.BRIDGE:
            protocol = "Relay" if t.meta.get("protocol") == "relay" else "THORChain"
            return "bridge", f"{protocol} transfer to {t.chain}"
        back = [x for x in self.graph.into.get(sender, []) if x.tx == tx and x.sender not in a.trail_nodes]
        if back and sum(self.graph.usd(x) for x in back) >= 0.5 * self.graph.usd(t):
            return "swap", f"swapped for {a.symbol(max(back, key=self.graph.usd).asset)}"
        if t.receiver in a.trail_nodes:
            return "move", "between trail addresses"
        if t.receiver.startswith("bitcoin:@tx:"):
            outs = self.graph.out.get(t.receiver, [])
            if is_coinjoin(outs):
                return "mix", "entered a coinjoin"
            if any(o.receiver in a.trail_nodes for o in outs):
                return "move", "between trail addresses"
        bucket = a.bucket(t.receiver)
        if bucket == "pending":
            return "move", "to an address not judged yet"
        if bucket == "mixer":
            return "mix", "entered a mixer"
        if bucket == "bridge":
            return "bridge", "sent to a bridge"
        return "exit", f"left the trail ({bucket})"

    def _label(self, node: str) -> str:
        row = self.trail.roles.get(node) or {}
        if row.get("role") in TRAIL_ROLES:
            return "FBI-listed address" if row.get("role") == "seed" else f"Trail, hop {row.get('hop', 0)}"
        label = row.get("label") or ""
        category = row.get("category") or ""
        return (label or category.replace("-", " ") or "")[:60]

    def _item(self, t: Transfer, kind: str, note: str, usd: float) -> dict:
        receiver = t.receiver
        if receiver.startswith("bitcoin:@tx:"):
            outs = [o for o in self.graph.out.get(receiver, []) if o.kind is not Kind.FEE]
            receiver = max(outs, key=self.graph.usd).receiver if outs else receiver
        return {
            "ts": iso(t.ts),
            "chain": str(t.chain),
            "tx": t.tx,
            "explorer_url": tx_url(str(t.chain), t.tx),
            "kind": kind,
            "from": split_node(t.sender)[1],
            "from_label": self._label(t.sender),
            "to": split_node(receiver)[1] if not is_virtual(receiver) else "",
            "to_label": self._label(receiver),
            "symbol": self.a.symbol(t.asset),
            "amount": t.amount / 10 ** self.a.decimals(t.asset),
            "usd": round(usd, 2),
            "note": note,
        }

    def graph_doc(self) -> dict:
        a = self.a
        edges: dict[tuple[str, str], dict] = {}

        def add(src: str, dst: str, usd: float, ts: int, kind: str) -> None:
            if usd <= 0 or src == dst:
                return
            e = edges.setdefault(
                (src, dst),
                {"source": src, "target": dst, "usd": 0.0, "count": 0, "first_ts": ts, "last_ts": ts, "kind": kind},
            )
            e["usd"] += usd
            e["count"] += 1
            e["first_ts"] = min(e["first_ts"], ts)
            e["last_ts"] = max(e["last_ts"], ts)
            if kind == "bridge":
                e["kind"] = "bridge"

        for n in a.trail_nodes | a.victims:
            for t in self.graph.out.get(n, []):
                if t.ts < self.case.start_ts or t.kind is Kind.FEE:
                    continue
                if n in a.victims and t.receiver not in a.trail_nodes and not t.receiver.startswith("bitcoin:@tx:"):
                    continue
                if t.receiver.startswith("bitcoin:@tx:"):
                    ins = self.graph.into.get(t.receiver, [])
                    total_in = sum(self.graph.usd(i) for i in ins) or 1.0
                    share = self.graph.usd(t) / total_in
                    for o in self.graph.out.get(t.receiver, []):
                        if o.kind is Kind.FEE or (n in a.victims and o.receiver not in a.trail_nodes):
                            continue
                        add(n, o.receiver, self.graph.usd(o) * share, o.ts, "transfer")
                else:
                    add(n, t.receiver, self.graph.usd(t), t.ts, "bridge" if t.kind is Kind.BRIDGE else "transfer")
        received: dict[str, float] = defaultdict(float)
        for e in edges.values():
            received[e["target"]] += e["usd"]
        held = {n: sum(a.value(asset, amt) for asset, amt in a.balances(n).items()) for n in a.trail_nodes}
        involved = {e["source"] for e in edges.values()} | {e["target"] for e in edges.values()}
        involved = {n for n in involved if not is_virtual(n)}
        score = {n: received.get(n, 0.0) + held.get(n, 0.0) + (1e12 if n in a.victims else 0) for n in involved}
        keep = set(sorted(involved, key=lambda n: -score[n])[:GRAPH_LIMIT])
        nodes = []
        for n in keep:
            row = self.trail.roles.get(n) or {}
            chain, address = split_node(n)
            role = row.get("role", "counterparty")
            if role not in ("victim", "seed", "trail", "service", "hub", "counterparty"):
                role = "counterparty"
            nodes.append(
                {
                    "id": n,
                    "chain": str(chain),
                    "address": address,
                    "role": role,
                    "category": row.get("category", ""),
                    "label": self._label(n) if role not in TRAIL_ROLES else "",
                    "hop": int(row.get("hop", 0)),
                    "received_usd": round(received.get(n, 0.0), 2),
                    "held_usd": round(held.get(n, 0.0), 2),
                    "first_ts": int(row.get("joined_ts", 0) or 0),
                }
            )
        kept_edges = [
            {**e, "usd": round(e["usd"], 2)} for e in edges.values() if e["source"] in keep and e["target"] in keep
        ]
        return {
            "nodes": nodes,
            "edges": kept_edges,
            "truncated": len(involved) > len(keep),
            "total_nodes": len(involved),
            "total_edges": len(edges),
        }

    def screen_entries(self) -> dict[str, dict]:
        a = self.a
        entries: dict[str, dict] = {}
        received: dict[str, float] = defaultdict(float)
        for receiver, usd, _, _ in self.trail.payments():
            if not is_virtual(receiver):
                received[receiver] += usd
        for node, row in self.trail.roles.items():
            role = row["role"]
            if role == "victim" or is_virtual(node):
                continue
            if role in TRAIL_ROLES:
                exposure = "trail"
            elif received.get(node, 0.0) >= 1.0:
                exposure = "direct"
            else:
                continue
            chain, address = split_node(node)
            held = sum(a.value(asset, amt) for asset, amt in a.balances(node).items()) if role in TRAIL_ROLES else 0.0
            entries[node] = {
                "node": node,
                "chain": str(chain),
                "address": address,
                "role": role,
                "category": row.get("category", ""),
                "label": (row.get("label") or "")[:80],
                "hop": int(row.get("hop", 0)),
                "received_usd": round(received.get(node, 0.0), 2),
                "held_usd": round(held, 2),
                "first_ts": int(row.get("joined_ts", 0) or 0),
                "reason": (row.get("reason") or "")[:240],
                "exposure": exposure,
            }
        return entries

    # Writing

    def write(self, out: Path, extra: dict[str, object] | None = None) -> dict[str, int]:
        tmp = out.with_name(out.name + ".tmp")
        if tmp.exists():
            shutil.rmtree(tmp)
        (tmp / "screen").mkdir(parents=True)
        summary = self.summary()
        docs: dict[str, object] = {
            "case.json": self.case_doc(),
            "summary.json": summary,
            "flows.json": self.flows(summary),
            "feed.json": self.feed(),
            "graph.json": self.graph_doc(),
            "golden-hour.json": {**self.a.golden_hour(), "notes": GOLDEN_HOUR_NOTES},
            **(extra or {}),
        }
        for name, doc in docs.items():
            _dump(tmp / name, doc)
        entries = self.screen_entries()
        # One key per address, whatever the chain: a list holds the entry for each chain.
        shards: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
        for entry in entries.values():
            key = screen_key(entry["node"])
            shards[key[:3]][key].append(entry)
        for i in range(4096):
            prefix = f"{i:03x}"
            _dump(tmp / "screen" / f"{prefix}.json", {"v": 2, "entries": dict(shards.get(prefix, {}))})
        self._parquet(tmp / "data")
        # Swap in place: rename the old tree aside, the new one in, then delete the old. A reader
        # sees either the complete old API or the complete new one, never a missing directory
        # for longer than one rename.
        old = out.with_name(out.name + ".old")
        if old.exists():
            shutil.rmtree(old)
        if out.exists():
            out.rename(old)
        tmp.rename(out)
        if old.exists():
            shutil.rmtree(old)
        return {"screen_entries": len(entries), "files": len(docs) + 4096}

    def _parquet(self, directory: Path) -> None:
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:  # the base install publishes JSON only
            return
        directory.mkdir(parents=True, exist_ok=True)
        a = self.a
        rows = []
        for n in a.trail_nodes | a.victims:
            for t in self.graph.out.get(n, []):
                if t.ts < self.case.start_ts or t.kind is Kind.FEE:
                    continue
                rows.append(
                    {
                        "uid": t.uid,
                        "chain": str(t.chain),
                        "tx": t.tx,
                        "ts": t.ts,
                        "sender": t.sender,
                        "receiver": t.receiver,
                        "symbol": a.symbol(t.asset),
                        "amount": t.amount / 10 ** a.decimals(t.asset),
                        "usd": round(self.graph.usd(t), 2),
                        "kind": str(t.kind),
                    }
                )
        pq.write_table(pa.Table.from_pylist(rows), directory / "transfers.parquet", compression="zstd")
        nodes = []
        for node, row in self.trail.roles.items():
            chain, address = split_node(node)
            held = (
                sum(a.value(asset, amt) for asset, amt in a.balances(node).items())
                if row["role"] in TRAIL_ROLES
                else 0.0
            )
            nodes.append(
                {
                    "node": node,
                    "chain": str(chain),
                    "address": address,
                    "role": row["role"],
                    "category": row.get("category", ""),
                    "label": (row.get("label") or "")[:80],
                    "hop": int(row.get("hop", 0)),
                    "received_usd": 0.0,
                    "held_usd": round(held, 2),
                }
            )
        pq.write_table(pa.Table.from_pylist(nodes), directory / "nodes.parquet", compression="zstd")


def _dump(path: Path, doc: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, separators=(",", ":"), ensure_ascii=False, default=str))


def evm_variants(address: str) -> list[str]:
    """Node ids an EVM address may have: the same key can hold funds on every EVM chain."""
    return [f"{c}:{address.lower()}" for c in sorted(str(c) for c in EVM_CHAINS)]


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = ["Chain", "Publisher", "category_bucket", "evm_variants", "now_iso", "screen_key", "tx_url"]
