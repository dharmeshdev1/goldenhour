"""The trail: which addresses hold stolen money, found by following it forward from the theft.

The rule is short enough to state on one line and defend in court: an address joins the trail when
most of the value it ever received came from the trail. It starts from the addresses the FBI named
and the first hops out of the victim's wallets, and repeats until nothing new qualifies.

Three refinements keep the rule honest:

- **Services stop the walk.** Exchanges, bridges, pools and other contracts hold everyone's money.
  Value that reaches one has left the attacker's direct control, and is recorded as an exit to that
  service instead of being followed into its general ledger.
- **Bitcoin transactions are nodes.** A transaction's outputs carry the share of its inputs that
  came from the trail, so a peel chain is followed and an unrelated payment mixed into a
  transaction is not credited to the attacker.
- **Cross-chain swaps are links.** When THORChain records that a trail address swapped ETH for
  bitcoin delivered to a new address, that address is on the trail: the attacker chose it.

Everything here is recomputed from stored transfers on every run. The only memory between runs is
which addresses were judged, and why.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from goldenhour.assets import valued
from goldenhour.models import NATIVE_DECIMALS, Chain, Kind, Token, Transfer, is_virtual, split_node
from goldenhour.prices import PriceBook

TRAIL_ROLES = ("seed", "trail")


@dataclass(frozen=True, slots=True)
class Policy:
    start_ts: int  # value moving before this is history, not theft
    min_usd: float = 1_000.0  # outflows smaller than this are not followed (dust, poisoning, fees)
    join_share: float = 0.5  # share of an address's lifetime inflow that must come from the trail
    sweep_share: float = 0.8  # outflow share to one exchange that marks a customer deposit address


@dataclass(slots=True)
class Candidate:
    node: str
    usd: float = 0.0  # value the trail sent it
    parent: str = ""
    parent_usd: float = 0.0
    first_ts: int = 0


@dataclass(slots=True)
class Graph:
    """Indexes over the stored transfers, with dollar values attached."""

    prices: PriceBook
    tokens: dict[str, Token]
    out: dict[str, list[Transfer]] = field(default_factory=lambda: defaultdict(list))
    into: dict[str, list[Transfer]] = field(default_factory=lambda: defaultdict(list))
    by_tx: dict[str, list[Transfer]] = field(default_factory=lambda: defaultdict(list))
    seen: set[str] = field(default_factory=set)
    stolen: set[str] = field(default_factory=set)  # tokens the victim's wallets sent: real by construction
    _usd: dict[str, float] = field(default_factory=dict)

    def add(self, transfers: list[Transfer]) -> None:
        for t in transfers:
            if t.uid in self.seen:
                continue
            self.seen.add(t.uid)
            self.out[t.sender].append(t)
            self.into[t.receiver].append(t)
            self.by_tx[t.tx].append(t)

    def decimals(self, t: Transfer) -> int:
        tail = t.asset.partition(":")[2]
        if tail == "native":
            return NATIVE_DECIMALS[Chain(t.asset.partition(":")[0])]
        if "out_decimals" in t.meta:
            return int(t.meta["out_decimals"])
        token = self.tokens.get(t.asset)
        return token.decimals if token else 18

    def valued(self, asset: str) -> bool:
        return valued(asset, self.stolen)

    def usd_amount(self, asset: str, amount: int) -> float:
        """Dollar value of an amount of an asset at today's price."""
        if not self.valued(asset):
            return 0.0
        chain, _, tail = asset.partition(":")
        if tail == "native":
            decimals = NATIVE_DECIMALS[Chain(chain)]
        else:
            token = self.tokens.get(asset)
            decimals = token.decimals if token else 18
        return self.prices.usd(asset, amount, decimals)

    def usd(self, t: Transfer) -> float:
        """Dollar value of a transfer at the hour it happened; zero for spam and unpriced tokens."""
        cached = self._usd.get(t.uid)
        if cached is not None:
            return cached
        if not self.valued(t.asset) and t.kind is not Kind.BRIDGE:
            self._usd[t.uid] = 0.0
            return 0.0
        if t.kind is Kind.BRIDGE and t.meta.get("usd"):
            # Bridges price both legs of a swap themselves; for outbound tokens nothing else would.
            try:
                value = float(t.meta["usd"])
            except ValueError:
                value = 0.0
            if value <= 0:
                value = self.prices.usd(t.asset, t.amount, self.decimals(t), t.ts)
        else:
            value = self.prices.usd(t.asset, t.amount, self.decimals(t), t.ts)
        self._usd[t.uid] = value
        return value


def stolen_assets(graph: Graph, roles: dict[str, dict], start: int, end: int) -> set[str]:
    """Tokens the victim's wallets sent inside the theft window: valued even if not listed."""
    victims = [n for n, r in roles.items() if r["role"] == "victim"]
    return {t.asset for v in victims for t in graph.out.get(v, []) if start <= t.ts <= end}


class Trail:
    """The trail's state for one case: who is on it, who was judged and how."""

    def __init__(self, graph: Graph, roles: dict[str, dict], policy: Policy) -> None:
        self.g = graph
        self.roles = roles  # node -> stored row (role, hop, parent, reason, ...)
        self.policy = policy
        self._fraction: dict[str, float] = {}

    def on_trail(self, node: str) -> bool:
        row = self.roles.get(node)
        return bool(row and row["role"] in TRAIL_ROLES)

    def fraction(self, node: str, depth: int = 0) -> float:
        """How much of what `node` passes on is trail money: 1 for trail addresses, the input share
        for a bitcoin transaction node, 0 for everyone else."""
        if self.on_trail(node):
            return 1.0
        if not node.startswith("bitcoin:@tx:") or depth > 2:
            return 0.0
        if node in self._fraction:
            return self._fraction[node]
        total = trail = 0.0
        for t in self.g.into.get(node, []):
            v = self.g.usd(t)
            total += v
            trail += v * self.fraction(t.sender, depth + 1)
        share = trail / total if total > 0 else 0.0
        self._fraction[node] = share
        return share

    def invalidate(self) -> None:
        self._fraction.clear()

    def candidates(self) -> list[Candidate]:
        """Addresses the trail sent at least `min_usd` to that nobody has judged yet, largest first."""
        found: dict[str, Candidate] = {}

        def credit(receiver: str, usd: float, parent: str, ts: int) -> None:
            if usd <= 0 or is_virtual(receiver) or receiver in self.roles:
                return
            c = found.setdefault(receiver, Candidate(receiver, first_ts=ts))
            c.usd += usd
            c.first_ts = min(c.first_ts, ts)
            if usd > c.parent_usd:
                c.parent, c.parent_usd = parent, usd

        for receiver, usd, parent, ts in self.payments():
            credit(receiver, usd, parent, ts)
        return sorted((c for c in found.values() if c.usd >= self.policy.min_usd), key=lambda c: -c.usd)

    def payments(self) -> list[tuple[str, float, str, int]]:
        """Every payment the trail made since the theft began: (receiver, dollars, payer, time).

        A bitcoin transaction is counted once, however many trail inputs it spends: each output
        carries the transaction's trail share, credited to its largest trail input.
        """
        out: list[tuple[str, float, str, int]] = []
        hubs: dict[str, tuple[str, float]] = {}
        for node, row in self.roles.items():
            if row["role"] not in TRAIL_ROLES:
                continue
            for t in self.g.out.get(node, []):
                if t.ts < self.policy.start_ts or t.kind is Kind.FEE:
                    continue
                if t.receiver.startswith("bitcoin:@tx:"):
                    v = self.g.usd(t)
                    if t.receiver not in hubs or v > hubs[t.receiver][1]:
                        hubs[t.receiver] = (node, v)
                else:
                    out.append((t.receiver, self.g.usd(t), node, t.ts))
        for hub, (payer, _) in hubs.items():
            share = self.fraction(hub)
            for o in self.g.out.get(hub, []):
                if o.kind is not Kind.FEE:
                    out.append((o.receiver, self.g.usd(o) * share, payer, o.ts))
        return out

    def upstream(self, window_end: int, depth_roles: tuple[str, ...] = TRAIL_ROLES) -> list[Candidate]:
        """Unjudged addresses that paid the trail during the theft window, largest first.

        The FBI named addresses that "hold or have held" stolen funds, which is mostly the second
        hop and beyond. The first hops (the addresses that took the money straight out of the
        victim's wallets) and the victim's wallets themselves are found by walking backwards from
        the seeds, but only inside the theft window: earlier history is not part of the theft.
        """
        found: dict[str, Candidate] = {}

        def credit(sender: str, usd: float, child: str, ts: int) -> None:
            if usd <= 0 or is_virtual(sender) or sender in self.roles:
                return
            c = found.setdefault(sender, Candidate(sender, first_ts=ts))
            c.usd += usd
            c.first_ts = min(c.first_ts, ts)
            if usd > c.parent_usd:
                c.parent, c.parent_usd = child, usd

        for node, row in self.roles.items():
            if row["role"] not in depth_roles:
                continue
            for t in self.g.into.get(node, []):
                if not (self.policy.start_ts <= t.ts <= window_end) or t.kind is Kind.FEE:
                    continue
                if t.sender.startswith("bitcoin:@tx:"):
                    for i in self.g.into.get(t.sender, []):
                        credit(i.sender, self.g.usd(i), node, i.ts)
                else:
                    credit(t.sender, self.g.usd(t), node, t.ts)
        return sorted((c for c in found.values() if c.usd >= self.policy.min_usd), key=lambda c: -c.usd)

    def window_inflow_from(self, node: str, sources: set[str], start: int, end: int) -> tuple[float, float]:
        """(dollars from `sources`, dollars in total) received by `node` between start and end."""
        total = matched = 0.0
        for t in self.g.into.get(node, []):
            if not (start <= t.ts <= end) or t.kind is Kind.FEE:
                continue
            v = self.g.usd(t)
            total += v
            senders = {t.sender}
            if t.sender.startswith("bitcoin:@tx:"):
                senders = {i.sender for i in self.g.into.get(t.sender, [])}
            if senders & sources:
                matched += v
        return matched, total

    def inflow(self, node: str) -> tuple[float, float]:
        """(dollars received from the trail, dollars received in total) over the address's life.

        Two kinds of incoming value are the address's own money coming back, not new money, and are
        left out of both sides:

        - the output of a swap: value received in a transaction in which the address itself paid
          out (it sent USDT to a pool and got ETH back);
        - a return from a counterparty, up to what the address had sent that same counterparty
          (a withdrawal of its own deposit, a refund).

        Anything beyond that counts in full, whoever sent it. Without the first exclusion a
        launderer who swaps stolen USDT for ETH would look as if most of his money came from the
        DEX; limiting the second to what was sent keeps anyone from hiding outside money as a
        "return".
        """
        incoming = sorted((t for t in self.g.into.get(node, []) if t.kind is not Kind.FEE), key=lambda t: t.ts)
        outgoing = [t for t in self.g.out.get(node, []) if t.kind is not Kind.FEE]
        paid_txs = {t.tx for t in outgoing}
        sent_to: dict[str, float] = defaultdict(float)
        for t in outgoing:
            sent_to[t.receiver] += self.g.usd(t)
        returned: dict[str, float] = defaultdict(float)
        total = trail = 0.0
        for t in incoming:
            share = self.fraction(t.sender)
            v = self.g.usd(t)
            if share == 0.0:
                if t.tx in paid_txs:
                    continue
                room = sent_to.get(t.sender, 0.0) - returned[t.sender]
                if room > 0:
                    used = min(room, v)
                    returned[t.sender] += used
                    v -= used
            if v <= 0:
                continue
            total += v
            trail += v * share
        return trail, total

    def sweeps_to(self, node: str, services: dict[str, dict]) -> str:
        """The exchange an address forwards nearly everything to, if any.

        Exchanges give each customer a deposit address and sweep it into a hot wallet within
        minutes. Such an address receives only the customer's money, so by inflow alone it would
        look like the attacker's own. What gives it away is where the money goes next.
        """
        by_dest: dict[str, float] = defaultdict(float)
        total = 0.0
        for t in self.g.out.get(node, []):
            if t.kind is Kind.FEE:
                continue
            v = self.g.usd(t)
            total += v
            row = services.get(t.receiver)
            if row and row.get("category") == "exchange":
                by_dest[row.get("label") or row.get("category", "")] += v
        if total <= 0 or not by_dest:
            return ""
        entity, value = max(by_dest.items(), key=lambda kv: kv[1])
        return entity if value / total >= self.policy.sweep_share else ""


def node_chain(node: str) -> Chain:
    return split_node(node)[0]
