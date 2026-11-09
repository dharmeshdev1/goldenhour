"""Cross-chain swaps through THORChain, read from Midgard.

THORChain is a set of pools that swaps native coins between chains: send ETH to its vault with a
memo naming a bitcoin address, and bitcoin arrives there. Unlike most bridges, Midgard records both
ends of every swap: the inbound transaction on the source chain and the outbound transaction on the
destination chain. That makes these links facts, not guesses, which is why the Binance attacker's
heavy use of THORChain is traceable at all.
"""

from __future__ import annotations

from goldenhour.http import PoliteClient
from goldenhour.models import Chain, InvalidAddress, Kind, Transfer, native_asset, node_id, token_asset

MIDGARD = "https://gateway.liquify.com/chain/thorchain_midgard/v2"
THORNODE = "https://gateway.liquify.com/chain/thorchain_api"

# Swap memos: `=:ASSET:DEST`, `SWAP:...`, `s:...` (and the arrow forms some interfaces write).
SWAP_PREFIXES = ("=:", "swap:", "s:", "=<:", "=>:")


def is_swap_memo(memo: str) -> bool:
    return memo.strip().lower().startswith(SWAP_PREFIXES)


def midgard_txid(tx: str) -> str:
    """Midgard keys transactions by upper-case hex without a 0x prefix, on every chain."""
    return tx.removeprefix("0x").upper()


# THORChain's chain prefixes, for the chains Goldenhour follows.
PREFIX: dict[str, Chain] = {
    "ETH": Chain.ETHEREUM,
    "BTC": Chain.BITCOIN,
    "TRON": Chain.TRON,
    "XRP": Chain.XRPL,
    "SOL": Chain.SOLANA,
    "AVAX": Chain.AVALANCHE,
    "BASE": Chain.BASE,
    "ARB": Chain.ARBITRUM,
}
REVERSE = {v: k for k, v in PREFIX.items()}
TRON_TOKENS = {"TR7NHQJEKQXGTCI8Q8ZY4PL8OTSZGJLJ6T": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"}  # USDT
THOR_DECIMALS = 8  # every amount in Midgard is fixed point with eight decimals
NATIVE_DECIMALS = {
    Chain.ETHEREUM: 18,
    Chain.BITCOIN: 8,
    Chain.TRON: 6,
    Chain.XRPL: 6,
    Chain.SOLANA: 9,
    Chain.AVALANCHE: 18,
    Chain.BASE: 18,
    Chain.ARBITRUM: 18,
}


def parse_asset(asset: str) -> tuple[Chain | None, str, str]:
    """`ETH.USDT-0XDAC...` to (ethereum, "ethereum:0xdac...", "USDT"). Synthetic and trade assets
    (`ETH/ETH`, `ETH~ETH`) live inside THORChain and return chain None."""
    head, sep, rest = asset.partition(".")
    if not sep:
        return None, "", asset
    chain = PREFIX.get(head.upper())
    if chain is None:
        return None, "", asset
    symbol, _, contract = rest.partition("-")
    if not contract:
        return chain, native_asset(chain), symbol
    if chain is Chain.TRON:
        # Midgard upper-cases asset ids, which destroys base58. Known tokens are restored; anything
        # else keeps Midgard's spelling and simply never matches a price.
        return chain, token_asset(chain, TRON_TOKENS.get(contract.upper(), contract)), symbol
    return chain, token_asset(chain, contract.lower()), symbol


def _usd(amount8: object, price: object) -> str:
    try:
        return f"{int(str(amount8)) / 1e8 * float(str(price)):.2f}"
    except (TypeError, ValueError):
        return ""


def links(action: dict) -> list[Transfer]:
    """The cross-chain transfers one successful swap creates: inbound sender to outbound receiver.

    Amounts on the destination chain are converted from Midgard's eight decimals to the chain's own
    units for native coins. Token outbounds keep eight-decimal amounts and record the USD value
    Midgard reports, which is all the tracer needs from them.
    """
    if action.get("status") != "success" or action.get("type") not in ("swap", "refund"):
        return []
    ins = action.get("in") or []
    if not ins:
        return []
    first = ins[0]
    in_coins = first.get("coins") or [{}]
    in_chain, in_asset, _ = parse_asset(in_coins[0].get("asset", ""))
    if in_chain is None or not first.get("address"):
        return []
    try:
        sender = node_id(in_chain, first["address"])
    except InvalidAddress:
        return []
    swap = (action.get("metadata") or {}).get("swap") or {}
    ts = int(action.get("date", "0")) // 1_000_000_000
    out: list[Transfer] = []
    for k, o in enumerate(action.get("out") or []):
        txid = o.get("txID") or ""
        coins = o.get("coins") or []
        if not txid or not coins or not o.get("address"):
            continue
        chain, asset, symbol = parse_asset(coins[0].get("asset", ""))
        if chain is None:
            continue
        try:
            receiver = node_id(chain, o["address"])
        except InvalidAddress:
            continue
        amount8 = int(coins[0].get("amount") or 0)
        if amount8 <= 0:
            continue
        if asset == native_asset(chain):
            amount = (
                amount8 * 10 ** (NATIVE_DECIMALS[chain] - THOR_DECIMALS)
                if NATIVE_DECIMALS[chain] >= THOR_DECIMALS
                else amount8 // 10 ** (THOR_DECIMALS - NATIVE_DECIMALS[chain])
            )
        else:
            amount = amount8
        out.append(
            Transfer(
                uid=f"thorchain:{first.get('txID', '').lower()}:{txid.lower()}:{k}",
                chain=chain,
                tx=txid.lower() if chain.is_evm else txid,
                block=0,
                ts=ts,
                position=k,
                sender=sender,
                receiver=receiver,
                asset=asset,
                amount=amount,
                kind=Kind.BRIDGE,
                meta={
                    "protocol": "thorchain",
                    "in_tx": first.get("txID", ""),
                    "in_asset": in_asset,
                    "in_amount8": str(in_coins[0].get("amount", "")),
                    "out_symbol": symbol,
                    "out_decimals": str(THOR_DECIMALS if asset != native_asset(chain) else NATIVE_DECIMALS[chain]),
                    "in_usd": swap.get("inPriceUSD", ""),
                    "usd": _usd(in_coins[0].get("amount"), swap.get("inPriceUSD")),
                    "out_usd": swap.get("outPriceUSD", ""),
                    "memo": str(swap.get("memo", ""))[:200],
                },
            )
        )
    return out


class MidgardAdapter:
    def __init__(self, client: PoliteClient, base: str = MIDGARD) -> None:
        self.client = client
        self.base = base

    async def actions(self, address: str, limit: int = 2_000) -> list[dict]:
        """Every THORChain action that names `address`, newest first."""
        found: list[dict] = []
        params: dict[str, object] = {"address": address, "limit": 50}
        while len(found) < limit:
            data = await self.client.get_json(f"{self.base}/actions", params)
            batch = (data or {}).get("actions") or [] if isinstance(data, dict) else []
            found.extend(batch)
            token = ((data or {}).get("meta") or {}).get("nextPageToken") if isinstance(data, dict) else None
            if not batch or not token or len(batch) < 50:
                break
            params["nextPageToken"] = token
        return found

    async def by_tx(self, txid: str) -> list[dict]:
        data = await self.client.get_json(f"{self.base}/actions", {"txid": midgard_txid(txid), "limit": 10})
        return (data or {}).get("actions") or [] if isinstance(data, dict) else []

    async def inbound(self) -> dict[Chain, dict[str, str]]:
        """Current vault address and router contract per chain."""
        data = await self.client.get_json(f"{THORNODE}/thorchain/inbound_addresses")
        out: dict[Chain, dict[str, str]] = {}
        for row in data if isinstance(data, list) else []:
            chain = PREFIX.get(str(row.get("chain", "")).upper())
            if chain is None:
                continue
            out[chain] = {"vault": str(row.get("address", "")), "router": str(row.get("router") or "")}
        return out

    async def links_for(self, address: str) -> list[Transfer]:
        out: list[Transfer] = []
        for action in await self.actions(address):
            out.extend(links(action))
        return out
