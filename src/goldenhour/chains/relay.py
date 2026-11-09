"""Cross-chain transfers through Relay, read from Relay's public request index.

Relay fills a user's request on the destination chain from a solver's own funds, so on-chain the
two legs never touch: the deposit goes to Relay on one chain and an unrelated-looking payment
arrives on another. Relay's API records both legs of every request, with the user, the recipient,
the assets and their dollar values. As with THORChain, that turns a guess into a fact.
"""

from __future__ import annotations

from datetime import datetime

from goldenhour.http import PoliteClient
from goldenhour.models import Chain, InvalidAddress, Kind, Transfer, native_asset, node_id, token_asset

RELAY = "https://api.relay.link/requests/v2"

CHAIN_IDS: dict[int, Chain] = {
    1: Chain.ETHEREUM,
    10: Chain.OPTIMISM,
    42161: Chain.ARBITRUM,
    8453: Chain.BASE,
    137: Chain.POLYGON,
    43114: Chain.AVALANCHE,
    792703809: Chain.SOLANA,
    728126428: Chain.TRON,
    8253038: Chain.BITCOIN,
}
REVERSE = {v: k for k, v in CHAIN_IDS.items()}
NATIVE_ADDRESSES = {
    "0x0000000000000000000000000000000000000000",
    "11111111111111111111111111111111",
    "bc1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
}


def _asset(chain: Chain, address: str) -> str:
    if not address or address.lower() in NATIVE_ADDRESSES or address in NATIVE_ADDRESSES:
        return native_asset(chain)
    return token_asset(chain, address)


def _ts(value: object) -> int:
    if isinstance(value, int | float):
        return int(value)
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


def links(request: dict) -> list[Transfer]:
    """The cross-chain transfer one completed Relay request represents."""
    if request.get("status") != "success":
        return []
    data = request.get("data") or {}
    meta = data.get("metadata") or {}
    cin, cout = meta.get("currencyIn") or {}, meta.get("currencyOut") or {}
    in_chain = CHAIN_IDS.get(int((cin.get("currency") or {}).get("chainId") or 0))
    out_chain = CHAIN_IDS.get(int((cout.get("currency") or {}).get("chainId") or 0))
    ins, outs = data.get("inTxs") or [], data.get("outTxs") or []
    user = request.get("user") or meta.get("sender") or ""
    recipient = request.get("recipient") or meta.get("recipient") or ""
    if in_chain is None or out_chain is None or not ins or not outs or not user or not recipient:
        return []
    try:
        sender = node_id(in_chain, user)
        receiver = node_id(out_chain, recipient)
    except InvalidAddress:
        return []
    out_tx = str(outs[0].get("hash") or "")
    amount = int(str(cout.get("amount") or "0") or 0)
    if not out_tx or amount <= 0:
        return []
    usd = cin.get("amountUsd") or cout.get("amountUsd") or ""
    return [
        Transfer(
            uid=f"relay:{request.get('id', out_tx)}",
            chain=out_chain,
            tx=out_tx.lower() if out_chain.is_evm else out_tx,
            block=int(outs[0].get("block") or 0),
            ts=_ts(outs[0].get("timestamp") or request.get("updatedAt") or request.get("createdAt")),
            position=0,
            sender=sender,
            receiver=receiver,
            asset=_asset(out_chain, str((cout.get("currency") or {}).get("address") or "")),
            amount=amount,
            kind=Kind.BRIDGE,
            meta={
                "protocol": "relay",
                "in_tx": str(ins[0].get("hash") or ""),
                "in_chain": str(in_chain),
                "out_symbol": str((cout.get("currency") or {}).get("symbol") or "")[:16],
                "out_decimals": str(int((cout.get("currency") or {}).get("decimals") or 0)),
                "usd": str(usd),
            },
        )
    ]


class RelayAdapter:
    def __init__(self, client: PoliteClient) -> None:
        self.client = client

    async def requests(self, user: str, limit: int = 2_000) -> list[dict]:
        found: list[dict] = []
        params: dict[str, object] = {"user": user, "limit": 50}
        while len(found) < limit:
            data = await self.client.get_json(RELAY, params)
            batch = (data or {}).get("requests") or [] if isinstance(data, dict) else []
            found.extend(batch)
            token = (data or {}).get("continuation") if isinstance(data, dict) else None
            if not batch or not token or len(batch) < 50:
                break
            params["continuation"] = token
        return found

    async def links_for(self, user: str) -> list[Transfer]:
        out: list[Transfer] = []
        for request in await self.requests(user):
            out.extend(links(request))
        return out
