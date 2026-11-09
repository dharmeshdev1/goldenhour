"""The tracer: fetch, judge, follow, repeat.

One pass reads the history of every address that needs it, identifies every address the trail
sent real money to, and either adds it to the trail, records it as a service the money exited
into, or records it as an ordinary counterparty. Passes repeat until the trail stops growing or
the time budget runs out, which is what lets the same code run a multi-hour backfill on a laptop
and a five-minute incremental update on a CI runner.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from typing import cast

from goldenhour.case import Case
from goldenhour.chains.bitcoin import EsploraAdapter
from goldenhour.chains.blockscout import BASES as BLOCKSCOUT_V2
from goldenhour.chains.blockscout import BlockscoutAdapter
from goldenhour.chains.etherscan import EtherscanAdapter, explorers_for
from goldenhour.chains.relay import CHAIN_IDS as RELAY_CHAIN_IDS
from goldenhour.chains.relay import RelayAdapter
from goldenhour.chains.solana import SolanaAdapter
from goldenhour.chains.thorchain import PREFIX as THOR_CHAINS
from goldenhour.chains.thorchain import MidgardAdapter, is_swap_memo
from goldenhour.chains.thorchain import links as thor_links
from goldenhour.chains.tron import TronAdapter
from goldenhour.chains.xrpl import XrplAdapter
from goldenhour.http import PoliteClient
from goldenhour.identify import PUBLIC_RPC, Identifier, Identity
from goldenhour.models import Chain, InvalidAddress, Kind, Page, is_virtual, node_id, split_node
from goldenhour.prices import PriceBook
from goldenhour.store import Store, now_ts
from goldenhour.trail import TRAIL_ROLES, Candidate, Graph, Policy, Trail, stolen_assets

log = logging.getLogger("goldenhour.tracer")

# Addresses whose own history is larger than this are services or busy wallets, never fresh
# laundering hops. Their history is not walked.
HUB_ROWS = 5_000
# Transactions sent, beyond which a wallet is treated as a service. Exchange hot wallets send
# millions; Binance 14 had sent about 18.9 million by October 2026.
EXCHANGE_SCALE = 20_000
THOR_SUPPORTED = set(THOR_CHAINS.values())
RELAY_CHAINS = set(RELAY_CHAIN_IDS.values())
# Bump when a rule for judging addresses changes: stored verdicts are then judged again, from
# stored data, before anything else runs.
RULES_VERSION = 2
# Bumped when a chain's parser changes what a transaction becomes: (version, whether the transfers
# already stored changed meaning). Solana 2 counts SOL at the owner (wrapped SOL is SOL, token
# accounts are their owners) and both chains reconcile every transaction with its metadata.
PARSERS: dict[Chain, tuple[int, bool]] = {Chain.SOLANA: (2, True), Chain.XRPL: (2, False)}
THOR_FINAL = ("success", "refund")
THOR_MAX_TRIES = 12
REREAD_S = 6 * 3600  # holders are re-read at least this often, whatever their nonce says


def reparse(store: Store) -> int:
    """Bring stored data up to the current parsers. Returns how many addresses were reset.

    Transfers are facts as a parser read them, so a parser fix has to replace them. Where the
    stored transfers changed meaning, they are dropped with every address judged from them, and
    the seeds are read again from the start. Where the new parser only adds transfers, the chain's
    addresses are read again from the start and the store keeps what it has. Addresses too busy to
    read in full (hubs) keep their place.
    """
    reset = 0
    for chain, (version, drop) in PARSERS.items():
        key = f"parser:{chain}"
        if store.get(key) == version:
            continue
        if drop:
            store.delete_transfers(str(chain))
        for row in store.nodes_by_role():
            if row["chain"] != str(chain):
                continue
            if drop and row["role"] not in ("seed", "victim"):
                store.delete_node(row["node"])
                reset += 1
                continue
            if (row.get("cursor") or {}).get("hub"):
                continue
            store.upsert_node(row["node"], role=row["role"], cursor={}, fetched_at=0)
            reset += 1
        store.put(key, version)
        log.info("%s: parser %s, %d addresses to read again", chain, version, reset)
    return reset


class Tracer:
    def __init__(self, store: Store, client: PoliteClient, case: Case, prices: PriceBook, policy: Policy) -> None:
        self.store = store
        self.client = client
        self.case = case
        self.prices = prices
        self.policy = policy
        self.identifier = Identifier(client)
        key = os.environ.get("ETHERSCAN_API_KEY") or None
        # Every EVM source that works for a chain, used side by side: their transfer ids agree, so
        # any of them can refresh an address another one fetched.
        self.evm: dict[Chain, list] = {}
        for chain in Chain:
            if not chain.is_evm:
                continue
            sources: list = [
                EtherscanAdapter(client, e) for e in explorers_for(chain, key) if "blockscout.com" not in e.base_url
            ]
            if chain in BLOCKSCOUT_V2:
                sources.append(BlockscoutAdapter(client, chain))
            self.evm[chain] = sources
        self.btc = EsploraAdapter(client)
        self.tron = TronAdapter(client)
        self.xrpl = XrplAdapter(client)
        self.solana = SolanaAdapter(client)
        self.midgard = MidgardAdapter(client)
        self.relay = RelayAdapter(client)
        reparse(store)
        self.graph = Graph(prices, store.token_table())
        self.graph.add(store.all_transfers())
        self.trail = Trail(self.graph, {r["node"]: r for r in store.nodes_by_role()}, policy)
        self.bridging = os.environ.get("GOLDENHOUR_BRIDGES", "1") != "0"
        self.thor_known: dict[Chain, set[str]] = {}
        self.thor_resolved: set[str] = set(cast(list[str], store.get(f"thor:{case.id}:resolved", []) or []))
        self.thor_tries: dict[str, int] = dict(cast(dict[str, int], store.get(f"thor:{case.id}:tries", {}) or {}))
        self.deadline = float("inf")
        self.stats: dict[str, int] = {
            "fetched": 0,
            "joined": 0,
            "services": 0,
            "counterparties": 0,
            "hubs": 0,
            "bridged": 0,
            "errors": 0,
        }

    # Fetching

    def _evm_adapter(self, chain: Chain, address: str, cursor: dict) -> object | None:
        adapters = self.evm.get(chain) or []
        if not adapters:
            return None
        # Spread addresses across sources by their last hex digit; the choice is not sticky.
        return adapters[int(address[-1], 16) % len(adapters)]

    async def fetch(self, node: str) -> Page:
        chain, address = split_node(node)
        row = self.trail.roles.get(node) or self.store.node(node) or {}
        cursor = dict(row.get("cursor") or {})
        if chain.is_evm:
            adapter = self._evm_adapter(chain, address, cursor)
            if adapter is None:
                return Page(complete=False, cursor=cursor)
            page = await adapter.history(  # type: ignore[attr-defined]
                address, start_block=int(cursor.get("block", 0)), lists=cursor.get("lists")
            )
            page.cursor = {**cursor, **page.cursor}
        elif chain is Chain.BITCOIN:
            page = await self.btc.history(address, cursor)
        elif chain is Chain.TRON:
            page = await self.tron.history(address, cursor)
        elif chain is Chain.XRPL:
            page = await self.xrpl.history(address, cursor)
        elif chain is Chain.SOLANA:
            page = await self.solana.history(address, cursor)
        else:
            return Page(complete=False, cursor=cursor)
        if len(page.transfers) > HUB_ROWS:
            page.complete = False
            page.cursor["hub"] = True
        self.store.add_tokens(page.tokens.values())
        self.graph.tokens.update(page.tokens)
        self.store.add_transfers(page.transfers)
        self.graph.add(page.transfers)
        self.stats["fetched"] += 1
        return page

    def _save(self, node: str, **fields: object) -> None:
        existing = self.trail.roles.get(node) or {}
        row = {**existing, **fields, "node": node, "chain": node.partition(":")[0]}
        row.pop("node", None)
        self.store.upsert_node(node, **{k: v for k, v in row.items() if k != "chain"})
        self.trail.roles[node] = {**row, "node": node}

    # Prices

    async def refresh_prices(self) -> None:
        self.graph.stolen = stolen_assets(self.graph, self.trail.roles, self.case.start_ts, self.case.end_ts + 7200)
        assets = {t.asset for ts in self.graph.out.values() for t in ts if self.graph.valued(t.asset)}
        await self.prices.load_history(self.client, sorted(assets), self.case.start_ts - 86_400, now_ts())
        self.graph._usd.clear()
        self.prices.save()

    # Seeds and judging

    async def seed(self) -> None:
        # The case file is the investigator's record: its seeds and victim wallets override whatever
        # an earlier pass concluded about those addresses (their fetched history is kept).
        for node in self.case.seeds:
            if (self.trail.roles.get(node) or {}).get("role") != "seed":
                reason = self.case.seed_reasons.get(node, "named by the FBI alert of 31 October 2026")
                self._save(node, role="seed", hop=0, parent="", reason=reason, joined_ts=self.case.start_ts)
        for node, label in self.case.victims.items():
            if (self.trail.roles.get(node) or {}).get("role") != "victim":
                self._save(
                    node,
                    role="victim",
                    category="exchange",
                    label=label,
                    hop=0,
                    reason="wallet of the victim, confirmed by the theft transfers",
                )
        pending = [n for n in self.case.seeds if not (self.trail.roles[n].get("fetched_at"))]
        await self._gather(pending, self._fetch_seed, concurrency=8)

    async def _fetch_seed(self, node: str, nonce: int | None = None) -> None:
        page = await self.fetch(node)
        row = self.trail.roles.get(node) or {}
        keep = {k: v for k, v in (row.get("cursor") or {}).items() if k == "nonce"}
        cursor = {**keep, **page.cursor}
        if nonce is not None:
            cursor["nonce"] = nonce  # only now that the history up to it is stored
        if row.get("role") == "trail" and (page.cursor.get("hub") or not page.complete):
            # A trail address that turns out to have an exchange's volume of history is a service.
            self._save(
                node,
                role="hub",
                category="hub",
                cursor=cursor,
                fetched_at=now_ts(),
                reason="joined the trail, then showed far too much history to be a laundering address",
            )
            return
        self._save(node, cursor=cursor, fetched_at=now_ts())

    async def judge(self, c: Candidate) -> None:
        node = c.node
        parent = self.trail.roles.get(c.parent) or {}
        hop = int(parent.get("hop", 0)) + 1
        ident = await self.identifier.identify(node)
        if not ident.available:
            return  # judged on a later pass, when a source answers
        base = {"parent": c.parent, "hop": hop, "joined_ts": c.first_ts, "info": ident.as_dict()}
        if node in self.case.victims:
            self._save(
                node,
                role="victim",
                category="exchange",
                label=self.case.victims[node],
                **base,
                reason="wallet of the victim",
            )
            return
        if ident.is_service:
            self._service(node, ident, base)
            return
        # Ask how big its history is before reading it: an exchange wallet's would take hours.
        scale = await self.identifier.scale(node)
        if scale is not None and scale >= EXCHANGE_SCALE:
            self.stats["services"] += 1
            self._save(
                node,
                role="service",
                category="exchange",
                label=ident.name or "Unlabeled exchange-scale wallet",
                **{**base, "info": {**ident.as_dict(), "scale": scale}},
                reason=f"has sent {scale:,} transactions: an exchange-scale wallet, not a person",
            )
            return
        page = await self.fetch(node)
        base["cursor"] = page.cursor
        base["fetched_at"] = now_ts()
        if not page.complete or page.cursor.get("hub"):
            self.stats["hubs"] += 1
            self._save(
                node,
                role="hub",
                category="hub",
                label=ident.name,
                **base,
                reason="too much history to be a fresh laundering address; likely a service",
            )
            return
        self.trail.invalidate()
        await self.identify_destinations(node)
        exchange = self.trail.sweeps_to(node, self.trail.roles)
        if exchange:
            self.stats["services"] += 1
            self._save(
                node,
                role="service",
                category="exchange-deposit",
                label=exchange,
                **base,
                reason=f"forwards what it receives to {exchange}: a customer deposit address",
            )
            return
        from_trail, total = self.trail.inflow(node)
        share = from_trail / total if total > 0 else 0.0
        if share >= self.policy.join_share:
            self.stats["joined"] += 1
            self._save(
                node,
                role="trail",
                **base,
                label=ident.name,
                reason=f"{share:.0%} of the ${total:,.0f} it received from others came from the trail",
            )
        else:
            self.stats["counterparties"] += 1
            self._save(
                node,
                role="counterparty",
                **base,
                label=ident.name,
                reason=f"received ${from_trail:,.0f} from the trail, {share:.1%} of all it received",
            )

    async def identify_destinations(self, node: str, top: int = 3) -> None:
        """Identify where an address sent most of its money, before judging it.

        A customer deposit address is recognized by what it sweeps into. Exchanges' hot wallets
        often carry no public label, so scale stands in for the label: a wallet that has sent tens
        of thousands of transactions is a service, not a person.
        """
        by_dest: dict[str, float] = {}
        for t in self.graph.out.get(node, []):
            if t.kind is Kind.FEE or is_virtual(t.receiver) or t.receiver in self.trail.roles:
                continue
            by_dest[t.receiver] = by_dest.get(t.receiver, 0.0) + self.graph.usd(t)
        for dest in sorted(by_dest, key=lambda d: -by_dest[d])[:top]:
            ident = await self.identifier.identify(dest)
            if not ident.available:
                continue
            if ident.is_service:
                self._service(dest, ident, {"hop": 0, "info": ident.as_dict()})
                continue
            scale = await self.identifier.scale(dest)
            if scale is not None and scale >= EXCHANGE_SCALE:
                self.stats["services"] += 1
                self._save(
                    dest,
                    role="service",
                    category="exchange",
                    label=ident.name or "Unlabeled exchange-scale wallet",
                    hop=0,
                    info={**ident.as_dict(), "scale": scale},
                    reason=f"has sent {scale:,} transactions: an exchange-scale wallet, not a person",
                )

    def victim_nodes(self) -> set[str]:
        return {n for n, r in self.trail.roles.items() if r["role"] == "victim"}

    def _is_victim_label(self, ident: Identity) -> bool:
        text = " ".join([ident.name, *ident.tags, ident.entity]).lower()
        return self.case.victim.lower() in text

    async def judge_upstream(self, c: Candidate) -> None:
        """Judge an address that paid the trail during the theft window: the victim's own wallet, a
        first hop that took the money straight out of it, a service, or none of these."""
        node = c.node
        ident = await self.identifier.identify(node)
        if not ident.available:
            return
        base = {"parent": c.parent, "hop": 0, "joined_ts": c.first_ts, "info": {**ident.as_dict(), "upstream": True}}
        if node in self.case.victims or self._is_victim_label(ident):
            label = self.case.victims.get(node) or ident.name or (ident.tags[0] if ident.tags else self.case.victim)
            self._save(
                node,
                role="victim",
                category="exchange",
                label=label,
                **base,
                reason=f"{self.case.victim} wallet that paid the trail during the theft window",
            )
            return
        if ident.is_service:
            self._service(node, ident, base)
            return
        page = await self.fetch(node)
        base["cursor"] = page.cursor
        base["fetched_at"] = now_ts()
        if not page.complete or page.cursor.get("hub"):
            self._save(
                node,
                role="hub",
                category="hub",
                label=ident.name,
                **base,
                reason="paid the trail during the theft window but has too much history to judge",
            )
            return
        window_end = self.case.end_ts + 2 * 3600
        # Its own payers inside the window decide what it is. Judge them first (one level).
        payers = sorted(
            {
                t.sender
                for t in self.graph.into.get(node, [])
                if self.case.start_ts <= t.ts <= window_end
                and t.kind is not Kind.FEE
                and not is_virtual(t.sender)
                and t.sender not in self.trail.roles
            },
            key=lambda s: -sum(self.graph.usd(t) for t in self.graph.into.get(node, []) if t.sender == s),
        )
        for payer in payers[:3]:
            p_ident = await self.identifier.identify(payer)
            if payer in self.case.victims or self._is_victim_label(p_ident):
                label = self.case.victims.get(payer) or p_ident.name or (p_ident.tags[0] if p_ident.tags else "")
                self._save(
                    payer,
                    role="victim",
                    category="exchange",
                    label=label,
                    parent=node,
                    hop=0,
                    joined_ts=c.first_ts,
                    info=p_ident.as_dict(),
                    reason=f"{self.case.victim} wallet that paid a first hop during the theft window",
                )
        from_victim, total = self.trail.window_inflow_from(node, self.victim_nodes(), self.case.start_ts, window_end)
        if total > 0 and from_victim / total >= self.policy.join_share:
            self.stats["joined"] += 1
            self._save(
                node,
                role="trail",
                **base,
                label=ident.name,
                reason=f"took ${from_victim:,.0f} straight out of {self.case.victim} wallets during the theft",
            )
            return
        from_trail, total_all = self.trail.inflow(node)
        if total_all > 0 and from_trail / total_all >= self.policy.join_share:
            self.stats["joined"] += 1
            self._save(
                node,
                role="trail",
                **base,
                label=ident.name,
                reason=f"{from_trail / total_all:.0%} of what it received from others came from the trail",
            )
            return
        self._save(
            node,
            role="counterparty",
            **base,
            label=ident.name,
            reason=f"paid ${c.usd:,.0f} to the trail during the theft window; origin unclear",
        )

    def rejudge(self) -> int:
        """When the judging rules change, judge every stored verdict again, from stored data.

        Verdicts are facts derived from rules. A rule fix that left old verdicts in place would be
        a fix in name only, so a version stamp in the store triggers a full re-judgement.
        """
        key = f"rules_version:{self.case.id}"
        if self.store.get(key) == RULES_VERSION:
            return 0
        changed = 0
        for node, row in list(self.trail.roles.items()):
            # Verdicts that rested on the XRP Ledger's sequence number read as a transaction count.
            reason, label = str(row.get("reason", "")), str(row.get("label", ""))
            if (
                node.startswith("xrpl:")
                and row["role"] == "service"
                and ("exchange-scale" in reason or "Unlabeled exchange-scale" in label)
            ):
                self.store.delete_node(node)
                del self.trail.roles[node]
                changed += 1
        victims = self.victim_nodes()
        window_end = self.case.end_ts + 2 * 3600
        for _ in range(6):
            self.trail.invalidate()
            moved = 0
            for node, row in list(self.trail.roles.items()):
                if row["role"] not in ("trail", "counterparty") or not row.get("fetched_at"):
                    continue
                reason = str(row.get("reason", ""))
                if reason.startswith("destination of"):
                    continue  # cross-chain destinations: chosen by the attacker, not judged by inflow
                if (row.get("info") or {}).get("upstream"):
                    got, total = self.trail.window_inflow_from(node, victims, self.case.start_ts, window_end)
                    if total > 0 and got / total >= self.policy.join_share:
                        verdict = (
                            "trail",
                            f"took ${got:,.0f} straight out of {self.case.victim} wallets during the theft",
                        )
                    else:
                        got, total = self.trail.inflow(node)
                        share = got / total if total > 0 else 0.0
                        verdict = (
                            ("trail", f"{share:.0%} of what it received from others came from the trail")
                            if share >= self.policy.join_share
                            else (
                                "counterparty",
                                f"received ${got:,.0f} from the trail, {share:.1%} of all it received",
                            )
                        )
                else:
                    got, total = self.trail.inflow(node)
                    share = got / total if total > 0 else 0.0
                    verdict = (
                        ("trail", f"{share:.0%} of the ${total:,.0f} it received from others came from the trail")
                        if share >= self.policy.join_share
                        else ("counterparty", f"received ${got:,.0f} from the trail, {share:.1%} of all it received")
                    )
                if verdict[0] != row["role"]:
                    self._save(node, role=verdict[0], reason=verdict[1])
                    moved += 1
            changed += moved
            if not moved:
                break
        self.store.put(key, RULES_VERSION)
        self.stats["rejudged"] = changed
        return changed

    def reconsider(self) -> int:
        """Re-judge counterparties now that the trail is larger. No requests: their history is stored.

        An address judged early may have looked like an outsider only because the addresses that
        paid it were not on the trail yet: the bitcoin change addresses of a peel chain, for one,
        are judged before the peel chain's first address is known. Promotion uses the same rule as
        everything else: most of what it ever received came from the trail.
        """
        promoted = 0
        self.trail.invalidate()
        victims = self.victim_nodes()
        window_end = self.case.end_ts + 2 * 3600
        for node, row in list(self.trail.roles.items()):
            if row["role"] != "counterparty" or not row.get("fetched_at"):
                continue
            if (row.get("info") or {}).get("upstream") and victims:
                from_victim, total_w = self.trail.window_inflow_from(node, victims, self.case.start_ts, window_end)
                if total_w > 0 and from_victim / total_w >= self.policy.join_share:
                    promoted += 1
                    self._save(
                        node,
                        role="trail",
                        hop=0,
                        reason=f"took ${from_victim:,.0f} straight out of {self.case.victim} wallets during the theft",
                    )
                    continue
            from_trail, total = self.trail.inflow(node)
            if total > 0 and from_trail / total >= self.policy.join_share:
                promoted += 1
                self._save(
                    node,
                    role="trail",
                    reason=f"{from_trail / total:.0%} of the ${total:,.0f} it received from others came from the trail",
                )
        if promoted:
            self.trail.invalidate()
            self.stats["promoted"] = self.stats.get("promoted", 0) + promoted
        return promoted

    def _service(self, node: str, ident: Identity, base: dict) -> None:
        self.stats["services"] += 1
        category = ident.category or ("program" if ident.is_contract else "")
        label = ident.name or (ident.tags[0] if ident.tags else "")
        self._save(
            node,
            role="service",
            category=category or "contract",
            label=label,
            **base,
            reason=f"{category or 'contract'}{': ' + label if label else ''}",
        )

    # Cross-chain

    def thor_deposits(self, node: str) -> list[str]:
        """Transactions in which `node` sent value into THORChain and that are not yet resolved.

        A deposit is recognized by its swap memo (bitcoin OP_RETURN, Tron and XRP Ledger memos) or
        by its destination: a THORChain router contract or vault. Midgard answers lookups by
        transaction id, which is all this needs.
        """
        chain = split_node(node)[0]
        known = self.thor_known.get(chain, set())
        found: list[str] = []
        for t in self.graph.out.get(node, []):
            if t.ts < self.case.start_ts or t.kind is Kind.FEE or t.tx in self.thor_resolved:
                continue
            memo = t.meta.get("memo", "")
            hub_memo = ""
            if t.receiver.startswith("bitcoin:@tx:"):
                outs = self.graph.out.get(t.receiver, [])
                hub_memo = outs[0].meta.get("memo", "") if outs else ""
            receiver_row = self.trail.roles.get(t.receiver) or {}
            labelled = "thorchain" in str(receiver_row.get("label", "")).lower()
            if is_swap_memo(memo) or is_swap_memo(hub_memo) or t.receiver in known or labelled:
                found.append(t.tx)
        return sorted(set(found))

    async def bridge(self, node: str) -> None:
        chain, address = split_node(node)
        row = self.trail.roles.get(node) or {}
        info = dict(row.get("info") or {})
        if chain in RELAY_CHAINS and int(info.get("relay_checked_at", 0)) <= int(row.get("fetched_at", 0)):
            # Relay's index is asked only about addresses that paid a Relay contract: its API is
            # rate limited, and an address that never touched Relay cannot have a Relay transfer.
            spent = any(
                t.ts >= self.case.start_ts
                and "relay" in str((self.trail.roles.get(t.receiver) or {}).get("label", "")).lower()
                for t in self.graph.out.get(node, [])
            )
            if spent:
                relay_links = [
                    t for t in await self.relay.links_for(address) if t.ts >= self.case.start_ts and t.sender == node
                ]
                await self._adopt_links(node, row, relay_links, "destination of a Relay transfer made by the trail")
            info["relay_checked_at"] = now_ts()
            self._save(node, info=info)
        if chain not in THOR_SUPPORTED:
            return
        for tx in self.thor_deposits(node):
            if self.thor_tries.get(tx, 0) >= THOR_MAX_TRIES:
                continue
            actions = await self.midgard.by_tx(tx)
            links = [t for a in actions for t in thor_links(a) if t.sender == node]
            # Resolved only once Midgard reports a final status. An empty answer usually means the
            # deposit is not indexed yet (bitcoin needs confirmations), so it is asked again later.
            if actions and all(a.get("status") in THOR_FINAL for a in actions):
                self.thor_resolved.add(tx)
            else:
                self.thor_tries[tx] = self.thor_tries.get(tx, 0) + 1
            await self._adopt_links(node, row, links, "destination of a THORChain swap made by the trail")
        self.store.put(f"thor:{self.case.id}:resolved", sorted(self.thor_resolved))
        self.store.put(f"thor:{self.case.id}:tries", self.thor_tries)

    async def _adopt_links(self, node: str, row: dict, links: list, reason: str) -> None:
        """Store cross-chain links and judge their destinations.

        The attacker chose each destination, so it normally joins the trail. But a destination can
        be a service (a swap delivered straight into an exchange deposit, a contract), and putting
        that on the trail would credit everyone it pays later as receiving stolen money. So each one
        is identified first, and its scale checked, like any other address.
        """
        if not links:
            return
        self.store.add_transfers(links)
        self.graph.add(links)
        for t in links:
            dest = t.receiver
            if dest in self.trail.roles:
                continue
            ident = await self.identifier.identify(dest)
            if not ident.available:
                continue
            base = {"hop": int(row.get("hop", 0)) + 1, "parent": node, "joined_ts": t.ts, "info": ident.as_dict()}
            if ident.is_service:
                self._service(dest, ident, base)
                continue
            scale = await self.identifier.scale(dest)
            if scale is not None and scale >= EXCHANGE_SCALE:
                self._save(
                    dest,
                    role="service",
                    category="exchange",
                    label=ident.name or "Unlabeled exchange-scale wallet",
                    **base,
                    reason=f"received a cross-chain transfer, but has sent {scale:,} transactions",
                )
                continue
            self.stats["bridged"] += 1
            self._save(dest, role="trail", **base, reason=reason)

    async def load_thor_inbound(self) -> None:
        try:
            inbound = await self.midgard.inbound()
        except Exception as exc:
            log.warning("THORChain inbound addresses unavailable: %s", exc)
            return
        for chain, row in inbound.items():
            for address in (row.get("vault"), row.get("router")):
                if not address:
                    continue
                try:
                    self.thor_known.setdefault(chain, set()).add(node_id(chain, address))
                except InvalidAddress:
                    continue

    # Live refresh

    async def _evm_nonces(self, chain: Chain, addresses: list[str]) -> dict[str, int]:
        """Transaction counts for many addresses, 100 per batched JSON-RPC request."""
        url = PUBLIC_RPC.get(chain)
        out: dict[str, int] = {}
        if not url:
            return out
        for i in range(0, len(addresses), 100):
            chunk = addresses[i : i + 100]
            batch = [
                {"jsonrpc": "2.0", "id": k, "method": "eth_getTransactionCount", "params": [a, "latest"]}
                for k, a in enumerate(chunk)
            ]
            try:
                data = await self.client.post_json(url, batch)
            except Exception as exc:
                log.warning("nonce batch failed on %s: %s", chain, exc)
                continue
            for item in data if isinstance(data, list) else []:
                k = item.get("id")
                if isinstance(k, int) and 0 <= k < len(chunk) and isinstance(item.get("result"), str):
                    out[chunk[k]] = int(item["result"], 16)
        return out

    async def changed(self, nodes: list[str]) -> dict[str, int | None]:
        """Which of these trail addresses have done something since they were last read.

        Reading a full history costs one to three requests per address; asking "has it sent
        anything?" costs one request per hundred addresses on EVM chains. A launderer's address
        cannot move value without sending a transaction, so its nonce is the change signal.
        """
        moved: dict[str, int | None] = {}
        by_chain: dict[Chain, list[str]] = {}
        for n in nodes:
            by_chain.setdefault(split_node(n)[0], []).append(n)
        for chain, group in by_chain.items():
            if chain.is_evm:
                nonces = await self._evm_nonces(chain, [split_node(n)[1] for n in group])
                for n in group:
                    row = self.trail.roles.get(n) or {}
                    cursor = dict(row.get("cursor") or {})
                    nonce = nonces.get(split_node(n)[1])
                    if nonce is None:
                        continue
                    if nonce != cursor.get("nonce"):
                        # The new nonce is saved only after its history is read, so a failed or
                        # postponed read is retried on the next tick.
                        moved[n] = nonce
            elif chain is Chain.BITCOIN:
                for n in group:
                    try:
                        stats = await self.btc.stats(split_node(n)[1])
                    except Exception:
                        continue
                    count = int((stats.get("chain_stats") or {}).get("tx_count", 0))
                    if count != int(((self.trail.roles.get(n) or {}).get("cursor") or {}).get("tx_count", -1)):
                        moved[n] = None
            else:
                # Tron, the XRP Ledger and Solana: their history reads are incremental and cheap
                # for quiet addresses, so the read is the check.
                moved.update(dict.fromkeys(group))
        return moved

    async def refresh(self, max_nodes: int = 400) -> int:
        """Re-read trail addresses that still hold value or moved recently, changed ones only."""
        holders: list[tuple[float, str]] = []
        for n, row in self.trail.roles.items():
            if row["role"] not in TRAIL_ROLES or is_virtual(n) or not row.get("fetched_at"):
                continue
            held = self._held_usd(n)
            recent = max((t.ts for t in self.graph.out.get(n, [])), default=0)
            if held >= 1_000 or recent >= now_ts() - 3 * 86_400:
                holders.append((held, n))
        holders.sort(reverse=True)
        candidates = [n for _, n in holders]
        evm = [n for n in candidates if split_node(n)[0].is_evm]
        other = [n for n in candidates if not split_node(n)[0].is_evm]
        # Oldest reads first among non-EVM chains, so every holder is revisited in turn.
        other.sort(key=lambda n: int((self.trail.roles.get(n) or {}).get("fetched_at", 0)))
        moved = {**await self.changed(evm), **await self.changed(other[: max(0, max_nodes // 2)])}
        # A nonce does not move when value leaves through an approval, a permit or delegated code
        # called by someone else, so every holder is also re-read on a slower clock.
        cutoff = now_ts() - REREAD_S
        for n in candidates:
            if n not in moved and int((self.trail.roles.get(n) or {}).get("fetched_at", 0)) < cutoff:
                moved[n] = None
        chosen = list(moved.items())[:max_nodes]
        await self._gather(chosen, lambda item: self._fetch_seed(item[0], item[1]), concurrency=8)
        self.stats["refreshed"] = self.stats.get("refreshed", 0) + len(chosen)
        return len(chosen)

    def _held_usd(self, node: str) -> float:
        bal: dict[str, int] = {}
        for t in self.graph.into.get(node, []):
            if t.kind is not Kind.BRIDGE:
                bal[t.asset] = bal.get(t.asset, 0) + t.amount
        for t in self.graph.out.get(node, []):
            if t.kind is not Kind.BRIDGE:
                bal[t.asset] = bal.get(t.asset, 0) - t.amount
        return sum(self.graph.usd_amount(a, v) for a, v in bal.items() if v > 0)

    # The loop

    async def _gather(self, items: list, fn: Callable, concurrency: int) -> None:
        sem = asyncio.Semaphore(concurrency)

        async def one(item: object) -> None:
            async with sem:
                if time.monotonic() > self.deadline:
                    return  # out of time: left for the next run, nothing is lost
                try:
                    await fn(item)
                except Exception as exc:
                    self.stats["errors"] += 1
                    log.warning("failed on %s: %s: %s", getattr(item, "node", item), type(exc).__name__, str(exc)[:160])

        await asyncio.gather(*(one(i) for i in items))

    async def run(
        self, *, budget_s: float = 3_000, max_judge: int = 400, progress: Callable | None = None, live: bool = False
    ) -> dict:
        deadline = time.monotonic() + budget_s
        self.deadline = deadline
        if self.bridging:
            await self.load_thor_inbound()
        await self.seed()
        self.rejudge()
        if live:
            try:
                await self.refresh_prices()
            except Exception as exc:
                log.warning("price refresh failed: %s: %s", type(exc).__name__, str(exc)[:160])
            await self.refresh()
        while time.monotonic() < deadline:
            try:
                await self.refresh_prices()
            except Exception as exc:
                log.warning("price refresh failed: %s: %s", type(exc).__name__, str(exc)[:160])
            trail_nodes = [n for n, r in self.trail.roles.items() if r["role"] in TRAIL_ROLES]
            if self.bridging:
                await self._gather(trail_nodes, self.bridge, concurrency=4)
            # New bridge destinations need their own history before they can lead anywhere, and
            # addresses read in chunks (Solana) need their next chunk.
            unfetched = [
                n
                for n in trail_nodes
                if not self.trail.roles[n].get("fetched_at") or (self.trail.roles[n].get("cursor") or {}).get("more")
            ]
            unfetched += [n for n, r in self.trail.roles.items() if r["role"] == "trail" and not r.get("fetched_at")]
            await self._gather(sorted(set(unfetched)), self._fetch_seed, concurrency=8)
            self.trail.invalidate()
            upstream = self.trail.upstream(self.case.end_ts + 2 * 3600)[:max_judge]
            if upstream:
                await self._gather(upstream, self.judge_upstream, concurrency=6)
                self.trail.invalidate()
            while self.reconsider():
                pass
            candidates = self.trail.candidates()[:max_judge]
            if progress:
                progress(self.stats, len(candidates) + len(upstream))
            if not candidates and not unfetched and not upstream:
                break
            await self._gather(candidates, self.judge, concurrency=10)
        return self.stats
