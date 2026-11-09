"""Issuer freezes, read from the issuers' own contracts.

Tether and Circle can freeze any address that holds their stablecoins, and they record it on-chain:
Tether's USDT emits `AddedBlackList(address)` (and `DestroyedBlackFunds` when it later burns the
frozen balance), Circle's USDC emits `Blacklisted(address)`. Both can also lift a freeze, which
emits `RemovedBlackList(address)` and `UnBlacklisted(address)`. Reading these events is how
Goldenhour knows, without trusting a press release, which addresses were frozen, when, and how much
they held. The timing is the point: the gap between a theft and its freeze is the golden hour.

A read either returns every event in the range or raises FreezeReadError. It never returns a
partial list that looks complete, because a missed unfreeze would overstate what is frozen.
"""

from __future__ import annotations

from dataclasses import dataclass

from goldenhour.http import PoliteClient
from goldenhour.models import Chain, node_id

RPC = "https://ethereum-rpc.publicnode.com"
LOGS_API = "https://api.routescan.io/v2/network/mainnet/evm/1/etherscan/api"
TRONGRID = "https://api.trongrid.io"


class FreezeReadError(RuntimeError):
    """An issuer's events could not be read completely. The caller keeps its cursor."""


@dataclass(frozen=True, slots=True)
class Issuer:
    name: str
    chain: Chain
    contract: str
    symbol: str
    decimals: int
    freeze_topic: str
    unfreeze_topic: str
    burn_topic: str | None
    indexed: bool  # whether the frozen address is an indexed topic (USDC) or in the data (USDT)
    check_selector: str  # view function: is this address frozen?


ISSUERS = [
    Issuer(
        name="Tether",
        chain=Chain.ETHEREUM,
        contract="0xdac17f958d2ee523a2206206994597c13d831ec7",
        symbol="USDT",
        decimals=6,
        freeze_topic="0x42e160154868087d6bfdc0ca23d96a1c1cfa32f1b72ba9ba27b69b98a0d819dc",  # AddedBlackList(address)
        # RemovedBlackList(address)
        unfreeze_topic="0xd7e9ec6e6ecd65492dce6bf513cd6867560d49544421d0783ddf06e76c24470c",
        burn_topic="0x61e6e66b0d6339b2980aecc6ccc0039736791f0ccde9ed512e789a7fbdd698c6",  # DestroyedBlackFunds
        indexed=False,
        check_selector="0xe47d6060",  # isBlackListed(address)
    ),
    Issuer(
        name="Circle",
        chain=Chain.ETHEREUM,
        contract="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        symbol="USDC",
        decimals=6,
        freeze_topic="0xffa4e6181777692565cf28528fc88fd1516ea86b56da075235fa575af6a4b855",  # Blacklisted(address)
        unfreeze_topic="0x117e3210bb9aa7d9baff172026820255c6f6c30ba8999d1c2fd88e2848137c4e",  # UnBlacklisted(address)
        burn_topic=None,
        indexed=True,
        check_selector="0xfe575a87",  # isBlacklisted(address)
    ),
]
TETHER_TRON = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
# TronGrid event name -> kind. Each name is paged, and cursored, on its own.
TRON_EVENTS = {"AddedBlackList": "freeze", "RemovedBlackList": "unfreeze", "DestroyedBlackFunds": "burn"}
# The explorer API returns at most this many pages of 1,000 logs for one query.
MAX_LOG_PAGES = 10


@dataclass(frozen=True, slots=True)
class FreezeEvent:
    issuer: str
    chain: Chain
    symbol: str
    node: str
    tx: str
    block: int
    ts: int
    kind: str  # "freeze", "unfreeze" or "burn"
    amount: int = 0  # burned amount, in base units, for "burn"


async def _rpc(client: PoliteClient, method: str, params: list, url: str = RPC) -> object:
    data = await client.post_json(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    if not isinstance(data, dict) or "error" in data:
        raise RuntimeError(f"{method}: {data.get('error') if isinstance(data, dict) else data}")
    return data.get("result")


async def head(client: PoliteClient) -> int:
    return int(str(await _rpc(client, "eth_blockNumber", [])), 16)


async def evm_events(client: PoliteClient, issuer: Issuer, from_block: int, to_block: int) -> list[FreezeEvent]:
    """Freeze, unfreeze and burn events in a block range, through an Etherscan-style logs API.

    Public RPC nodes refuse log queries this far back without an account; the explorer's logs
    endpoint answers them and includes each log's timestamp. Any answer other than a list of logs
    (a rate limit, a malformed query, an outage) raises FreezeReadError.
    """
    out: list[FreezeEvent] = []
    topics = ((issuer.freeze_topic, "freeze"), (issuer.unfreeze_topic, "unfreeze"), (issuer.burn_topic, "burn"))
    for topic, kind in topics:
        if not topic:
            continue
        page = 1
        while True:
            if page > MAX_LOG_PAGES:
                raise FreezeReadError(f"{issuer.name} {kind}: more than {MAX_LOG_PAGES} pages; read a shorter range")
            data = await client.get_json(
                LOGS_API,
                {
                    "module": "logs",
                    "action": "getLogs",
                    "address": issuer.contract,
                    "topic0": topic,
                    "fromBlock": from_block,
                    "toBlock": to_block,
                    "page": page,
                    "offset": 1000,
                },
            )
            if not isinstance(data, dict):
                raise FreezeReadError(f"{issuer.name} {kind}: unexpected answer {str(data)[:120]!r}")
            rows = data.get("result")
            if str(data.get("status")) != "1" or not isinstance(rows, list):
                if "no records found" in str(data.get("message", "")).lower():
                    break  # Etherscan's way of saying the range is empty
                raise FreezeReadError(f"{issuer.name} {kind}: {data.get('message')}: {str(rows)[:120]}")
            if not rows:
                break
            for log in rows:
                data_hex = str(log.get("data", "0x"))[2:]
                if kind in ("freeze", "unfreeze"):
                    word = log["topics"][1][2:] if issuer.indexed else data_hex[:64]
                    amount = 0
                else:
                    word, amount = data_hex[:64], int(data_hex[64:128] or "0", 16)
                out.append(
                    FreezeEvent(
                        issuer.name,
                        issuer.chain,
                        issuer.symbol,
                        node_id(issuer.chain, "0x" + word[-40:]),
                        str(log["transactionHash"]).lower(),
                        int(log["blockNumber"], 16),
                        int(log["timeStamp"], 16),
                        kind,
                        amount,
                    )
                )
            if len(rows) < 1000:
                break
            page += 1
    return sorted(out, key=lambda e: (e.ts, e.tx))


async def tron_events(client: PoliteClient, name: str, since_ms: int, max_pages: int = 50) -> list[FreezeEvent]:
    """One kind of Tether event on Tron (see TRON_EVENTS), oldest first, from TronGrid's index.

    At most `max_pages` pages are read. The events come oldest first, so a caller that resumes from
    the newest one returned loses nothing when a backlog takes more than one run.
    """
    from goldenhour.chains.tron import to_base58

    kind = TRON_EVENTS[name]
    out: list[FreezeEvent] = []
    params: dict[str, object] = {
        "event_name": name,
        "min_block_timestamp": since_ms,
        "limit": 200,
        "order_by": "block_timestamp,asc",
    }
    for _ in range(max_pages):
        resp = await client.request("GET", f"{TRONGRID}/v1/contracts/{TETHER_TRON}/events", params=params)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict) or data.get("success") is False or not isinstance(data.get("data", []), list):
            raise FreezeReadError(f"Tron {name}: {str(data)[:160]}")
        for row in data.get("data") or []:
            result = row.get("result") or {}
            raw = result.get("_user") or result.get("_blackListedUser") or result.get("0") or ""
            try:
                address = to_base58(raw)
            except ValueError:
                continue
            amount = int(result.get("_balance") or result.get("1") or 0) if kind == "burn" else 0
            out.append(
                FreezeEvent(
                    "Tether",
                    Chain.TRON,
                    "USDT",
                    node_id(Chain.TRON, address),
                    row.get("transaction_id", ""),
                    int(row.get("block_number") or 0),
                    int(row.get("block_timestamp") or 0) // 1000,
                    kind,
                    amount,
                )
            )
        fingerprint = (data.get("meta") or {}).get("fingerprint")
        if not fingerprint:
            break
        params["fingerprint"] = fingerprint
    return out


async def evm_view(client: PoliteClient, contract: str, selector: str, address: str) -> int:
    data = selector + address.lower().removeprefix("0x").rjust(64, "0")
    result = await _rpc(client, "eth_call", [{"to": contract, "data": data}, "latest"])
    return int(str(result or "0x0"), 16) if result not in (None, "0x") else 0


async def evm_balance(client: PoliteClient, contract: str, address: str) -> int:
    return await evm_view(client, contract, "0x70a08231", address)
