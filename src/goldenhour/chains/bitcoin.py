"""Bitcoin through Esplora, the API behind mempool.space and blockstream.info.

Bitcoin has no accounts. A transaction spends whole outputs of earlier transactions and creates new
ones, so "who paid whom" is many-to-many. Each transaction becomes a node of its own: every input
flows into it and every output flows out of it. The same proportional rule that follows value
through an account then follows it through a transaction, with no special case for coins.
"""

from __future__ import annotations

from collections import Counter

from goldenhour.http import PoliteClient
from goldenhour.models import Chain, Kind, Page, Transfer, btc_tx_node, fee_node, native_asset, node_id

ESPLORA = ("https://mempool.space/api", "https://blockstream.info/api")
ASSET = native_asset(Chain.BITCOIN)

# Addresses with more transactions than this are exchanges, payment processors or pools. Attackers
# use fresh addresses, and walking an exchange's history would follow everyone else's money.
HUB_TX_COUNT = 2_000


def _script_node(script: str) -> str:
    # Outputs without a standard address (bare multisig, OP_RETURN with value) still hold coins.
    return f"bitcoin:@script:{script[:64]}"


def op_return_text(vout: list[dict]) -> str:
    """The text of an OP_RETURN output, if any. Cross-chain swaps (THORChain, for one) put their
    instructions there: `=:ETH.ETH:0x...` means "swap this bitcoin for ETH sent to 0x..."."""
    for o in vout:
        if o.get("scriptpubkey_type") != "op_return":
            continue
        parts = str(o.get("scriptpubkey_asm", "")).split()
        data = parts[-1] if len(parts) >= 2 else ""
        try:
            text = bytes.fromhex(data).decode("utf-8", "replace")
        except ValueError:
            continue
        if text.isprintable():
            return text[:200]
    return ""


def tx_transfers(tx: dict) -> list[Transfer]:
    status = tx.get("status") or {}
    if not status.get("confirmed"):
        return []
    txid = tx["txid"]
    block = int(status.get("block_height") or 0)
    ts = int(status.get("block_time") or 0)
    hub = btc_tx_node(txid)
    vin = tx.get("vin") or []
    vout = tx.get("vout") or []
    values = [int(o.get("value") or 0) for o in vout if int(o.get("value") or 0) > 0]
    equal = Counter(values).most_common(1)[0][1] if values else 0
    shape = {"n_in": str(len(vin)), "n_out": str(len(vout)), "equal_out": str(equal)}
    memo = op_return_text(vout)
    if memo:
        shape["memo"] = memo
    out: list[Transfer] = []
    for i, v in enumerate(vin):
        if v.get("is_coinbase"):
            continue
        prev = v.get("prevout") or {}
        value = int(prev.get("value") or 0)
        if value <= 0:
            continue
        addr = prev.get("scriptpubkey_address")
        out.append(
            Transfer(
                uid=f"bitcoin:{txid}:in:{i}",
                chain=Chain.BITCOIN,
                tx=txid,
                block=block,
                ts=ts,
                position=i,
                sender=node_id(Chain.BITCOIN, addr) if addr else _script_node(prev.get("scriptpubkey", "")),
                receiver=hub,
                asset=ASSET,
                amount=value,
                kind=Kind.UTXO,
                meta=shape,
            )
        )
    for j, o in enumerate(vout):
        value = int(o.get("value") or 0)
        if value <= 0:
            continue
        addr = o.get("scriptpubkey_address")
        out.append(
            Transfer(
                uid=f"bitcoin:{txid}:out:{j}",
                chain=Chain.BITCOIN,
                tx=txid,
                block=block,
                ts=ts,
                position=100_000 + j,
                sender=hub,
                receiver=node_id(Chain.BITCOIN, addr) if addr else _script_node(o.get("scriptpubkey", "")),
                asset=ASSET,
                amount=value,
                kind=Kind.UTXO,
                meta=shape,
            )
        )
    fee = int(tx.get("fee") or 0)
    if fee > 0 and vin and not vin[0].get("is_coinbase"):
        out.append(
            Transfer(
                uid=f"bitcoin:{txid}:fee",
                chain=Chain.BITCOIN,
                tx=txid,
                block=block,
                ts=ts,
                position=1_000_000,
                sender=hub,
                receiver=fee_node(Chain.BITCOIN),
                asset=ASSET,
                amount=fee,
                kind=Kind.FEE,
            )
        )
    return out


class EsploraAdapter:
    chain = Chain.BITCOIN

    def __init__(self, client: PoliteClient, bases: tuple[str, ...] = ESPLORA) -> None:
        self.client = client
        self.bases = bases

    async def _get(self, path: str) -> object:
        last: Exception | None = None
        for base in self.bases:
            try:
                return await self.client.get_json(base + path)
            except Exception as exc:
                last = exc
        assert last is not None
        raise last

    async def stats(self, address: str) -> dict:
        data = await self._get(f"/address/{address}")
        return data if isinstance(data, dict) else {}

    async def balance(self, address: str) -> int:
        chain = (await self.stats(address)).get("chain_stats") or {}
        return int(chain.get("funded_txo_sum", 0)) - int(chain.get("spent_txo_sum", 0))

    async def history(self, address: str, cursor: dict | None = None) -> Page:
        """New confirmed transactions since the cursor, newest first from the API, all normalized.

        Esplora pages 25 confirmed transactions at a time, newest first. Transactions are only ever
        added, so knowing how many there were last time says how far back to read.
        """
        cursor = dict(cursor or {})
        stats = await self.stats(address)
        total = int((stats.get("chain_stats") or {}).get("tx_count", 0))
        known = int(cursor.get("tx_count", 0))
        page = Page(cursor={**cursor, "tx_count": total})
        if total > HUB_TX_COUNT:
            page.complete = False
            page.cursor["hub"] = True
            return page
        wanted = total - known
        if wanted <= 0:
            return page
        fetched = 0
        last_seen: str | None = None
        while fetched < wanted:
            path = f"/address/{address}/txs/chain" + (f"/{last_seen}" if last_seen else "")
            batch = await self._get(path)
            if not isinstance(batch, list) or not batch:
                break
            for tx in batch:
                page.transfers.extend(tx_transfers(tx))
            fetched += len(batch)
            last_seen = batch[-1]["txid"]
            if len(batch) < 25:
                break
        return page

    async def transaction(self, txid: str) -> list[Transfer]:
        tx = await self._get(f"/tx/{txid}")
        return tx_transfers(tx) if isinstance(tx, dict) else []
