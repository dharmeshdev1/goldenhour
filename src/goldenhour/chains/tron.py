"""Tron through TronGrid.

Tron is an EVM chain underneath, but its API speaks protobuf-shaped JSON: a transaction holds one
"contract" of a given type, addresses come as hex with a 0x41 prefix in one endpoint and as
base58check in another, and times are in milliseconds. TRC-20 token transfers have their own
endpoint. This adapter flattens all of it into the same transfers every other chain produces.
"""

from __future__ import annotations

import os

import base58

from goldenhour.http import PoliteClient
from goldenhour.models import Chain, Kind, Page, Token, Transfer, fee_node, native_asset, node_id, token_asset

TRONGRID = "https://api.trongrid.io"
ASSET = native_asset(Chain.TRON)
PAGE = 200
MARGIN_MS = 15 * 60 * 1000  # each list is re-read from 15 minutes below its cursor


def to_base58(address: str) -> str:
    """Hex `41...` to base58check `T...`; base58 passes through."""
    a = address.strip()
    if a.startswith("T"):
        return a
    if a.startswith("0x"):
        a = "41" + a[2:]
    if len(a) == 42 and a.startswith("41"):
        return base58.b58encode_check(bytes.fromhex(a)).decode()
    raise ValueError(f"not a tron address: {address!r}")


def _amount(value: object) -> int:
    if isinstance(value, dict):
        # Internal transactions report call value as {"_": n} for TRX, or per token id for TRC-10.
        return int(value.get("_", 0) or 0)
    try:
        return int(value or 0)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0


def _memo(data: str) -> str:
    try:
        text = bytes.fromhex(data).decode("utf-8", "replace") if data else ""
    except ValueError:
        return ""
    return text[:200] if text.isprintable() else ""


def tx_transfers(tx: dict, subject: str) -> list[Transfer]:
    txid = tx.get("txID", "")
    ts = int(tx.get("block_timestamp", 0)) // 1000
    block = int(tx.get("blockNumber", 0))
    ret = (tx.get("ret") or [{}])[0]
    ok = ret.get("contractRet", "SUCCESS") == "SUCCESS"
    contracts = (tx.get("raw_data") or {}).get("contract") or []
    out: list[Transfer] = []
    if not contracts:
        return out
    c = contracts[0]
    kind = c.get("type", "")
    memo = _memo((tx.get("raw_data") or {}).get("data", ""))
    v = (c.get("parameter") or {}).get("value") or {}
    owner = to_base58(v["owner_address"]) if v.get("owner_address") else ""
    if ok and kind == "TransferContract" and v.get("to_address"):
        amount = _amount(v.get("amount"))
        if amount > 0:
            out.append(
                Transfer(
                    uid=f"tron:{txid}:n",
                    chain=Chain.TRON,
                    tx=txid,
                    block=block,
                    ts=ts,
                    position=0,
                    sender=node_id(Chain.TRON, owner),
                    receiver=node_id(Chain.TRON, to_base58(v["to_address"])),
                    asset=ASSET,
                    amount=amount,
                    kind=Kind.NATIVE,
                    meta={"memo": memo} if memo else {},
                )
            )
    elif ok and kind == "TriggerSmartContract" and v.get("contract_address"):
        call_value = _amount(v.get("call_value"))
        if call_value > 0:
            out.append(
                Transfer(
                    uid=f"tron:{txid}:n",
                    chain=Chain.TRON,
                    tx=txid,
                    block=block,
                    ts=ts,
                    position=0,
                    sender=node_id(Chain.TRON, owner),
                    receiver=node_id(Chain.TRON, to_base58(v["contract_address"])),
                    asset=ASSET,
                    amount=call_value,
                    kind=Kind.NATIVE,
                    meta={"method": str(v.get("data", ""))[:8]},
                )
            )
    if ok:
        seen: dict[tuple[str, str, int], int] = {}
        for k, it in enumerate(tx.get("internal_transactions") or []):
            data = it.get("data") or {}
            if data.get("rejected"):
                continue
            amount = _amount(data.get("call_value"))
            if amount <= 0 or not it.get("from_address") or not it.get("to_address"):
                continue
            src, dst = to_base58(it["from_address"]), to_base58(it["to_address"])
            n = seen.get((src, dst, amount), 0)
            seen[(src, dst, amount)] = n + 1
            out.append(
                Transfer(
                    uid=f"tron:{txid}:i:{src}:{dst}:{amount}:{n}",
                    chain=Chain.TRON,
                    tx=txid,
                    block=block,
                    ts=ts,
                    position=100_000 + k,
                    sender=node_id(Chain.TRON, src),
                    receiver=node_id(Chain.TRON, dst),
                    asset=ASSET,
                    amount=amount,
                    kind=Kind.INTERNAL,
                )
            )
    fee = int(ret.get("fee") or 0) or int(tx.get("net_fee") or 0) + int(tx.get("energy_fee") or 0)
    if fee > 0 and owner == subject:
        out.append(
            Transfer(
                uid=f"tron:{txid}:f",
                chain=Chain.TRON,
                tx=txid,
                block=block,
                ts=ts,
                position=1_000_000,
                sender=node_id(Chain.TRON, owner),
                receiver=fee_node(Chain.TRON),
                asset=ASSET,
                amount=fee,
                kind=Kind.FEE,
            )
        )
    return out


def trc20_transfer(row: dict, occurrence: int) -> tuple[Transfer | None, Token | None]:
    if row.get("type") != "Transfer":
        return None, None
    info = row.get("token_info") or {}
    contract = info.get("address", "")
    if not contract:
        return None, None
    asset = token_asset(Chain.TRON, contract)
    token = Token(
        asset=asset,
        symbol=str(info.get("symbol", ""))[:32],
        decimals=int(info.get("decimals") or 0),
        name=str(info.get("name", ""))[:64],
    )
    value = _amount(row.get("value"))
    if value <= 0 or not row.get("from") or not row.get("to"):
        return None, token
    txid = row.get("transaction_id", "")
    src, dst = row["from"], row["to"]
    return (
        Transfer(
            uid=f"tron:{txid}:t:{contract}:{src}:{dst}:{value}:{occurrence}",
            chain=Chain.TRON,
            tx=txid,
            block=0,
            ts=int(row.get("block_timestamp", 0)) // 1000,
            position=10_000 + occurrence,
            sender=node_id(Chain.TRON, src),
            receiver=node_id(Chain.TRON, dst),
            asset=asset,
            amount=value,
            kind=Kind.TOKEN,
        ),
        token,
    )


class TronAdapter:
    chain = Chain.TRON

    def __init__(self, client: PoliteClient, api_key: str | None = None) -> None:
        self.client = client
        self.api_key = api_key or os.environ.get("TRONGRID_API_KEY") or None

    async def _rows(self, path: str, params: dict[str, object]) -> list[dict]:
        rows: list[dict] = []
        query = dict(params)
        headers = {"TRON-PRO-API-KEY": self.api_key} if self.api_key else None
        while True:
            resp = await self.client.request("GET", TRONGRID + path, params=query, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            batch = data.get("data") or []
            rows.extend(batch)
            fingerprint = (data.get("meta") or {}).get("fingerprint")
            if not fingerprint or len(batch) < PAGE:
                return rows
            query["fingerprint"] = fingerprint

    async def history(self, address: str, cursor: dict | None = None) -> Page:
        """Transactions and TRC-20 transfers since the cursor. The two lists keep their own cursors
        and each read starts MARGIN_MS below its own, so a row indexed late is still read."""
        cursor = dict(cursor or {})
        since = int(cursor.get("ts_ms", 0))
        lists = {name: int((cursor.get("lists") or {}).get(name, since)) for name in ("tx", "trc20")}

        def params(name: str) -> dict[str, object]:
            start = max(lists[name] - MARGIN_MS, 0) if lists[name] else 0
            return {"only_confirmed": "true", "limit": PAGE, "order_by": "block_timestamp,asc", "min_timestamp": start}

        txs = await self._rows(f"/v1/accounts/{address}/transactions", params("tx"))
        trc = await self._rows(f"/v1/accounts/{address}/transactions/trc20", params("trc20"))
        page = Page()
        latest = since
        for tx in txs:
            if "txID" not in tx:
                continue
            latest = max(latest, int(tx.get("block_timestamp", 0)))
            page.transfers.extend(tx_transfers(tx, address))
        seen: dict[tuple[str, ...], int] = {}
        for row in trc:
            key = (
                row.get("transaction_id", ""),
                (row.get("token_info") or {}).get("address", ""),
                row.get("from", ""),
                row.get("to", ""),
                str(row.get("value", "")),
            )
            n = seen.get(key, 0)
            seen[key] = n + 1
            latest = max(latest, int(row.get("block_timestamp", 0)))
            t, token = trc20_transfer(row, n)
            if token is not None:
                page.tokens[token.asset] = token
            if t is not None:
                page.transfers.append(t)
        # Rows read twice (the margin, the same block) have the same ids and are dropped as duplicates,
        # which is cheaper than missing one.
        tops = {
            "tx": max([lists["tx"], *(int(t.get("block_timestamp", 0)) for t in txs if "txID" in t)]),
            "trc20": max([lists["trc20"], *(int(r.get("block_timestamp", 0)) for r in trc)]),
        }
        page.cursor = {**cursor, "ts_ms": max(since, latest), "lists": tops}
        return page

    async def account(self, address: str) -> dict:
        resp = await self.client.request("GET", f"{TRONGRID}/v1/accounts/{address}")
        resp.raise_for_status()
        data = resp.json().get("data") or []
        return data[0] if data else {}
