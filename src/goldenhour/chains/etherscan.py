"""EVM history through explorer APIs that speak Etherscan's `account` module.

Blockscout, Routescan and Etherscan itself all answer the same three questions for an address:
its top-level transactions (`txlist`), the value contracts moved for it inside calls
(`txlistinternal`) and its token transfers (`tokentx`). Together they are the address's complete
value history. One adapter covers all three providers; only the base URL, an optional key and the
chain differ.

Results come back oldest first in pages, and a query may return at most 10,000 rows however it is
paged, so long histories are walked by moving the start block forward and dropping the overlap.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from goldenhour.http import PoliteClient, RateLimited, with_retry
from goldenhour.models import (
    Chain,
    Kind,
    Page,
    Token,
    Transfer,
    fee_node,
    native_asset,
    node_id,
    token_asset,
)

PAGE_SIZE = 1000
MAX_WINDOW = 10_000
# An address's history comes as three lists, fetched one after another and indexed at different
# speeds (internal transactions last). Each list keeps its own cursor, and each read starts this
# many blocks (about 13 minutes) below it, so a row indexed late is still picked up. Rows read twice
# have the same transfer id and are dropped by the store.
LISTS = ("normal", "internal", "token")
MARGIN_BLOCKS = 64


def resume_from(lists: dict | None, start_block: int) -> dict[str, int]:
    """Each list's cursor. A cursor saved before lists had their own applies to all three."""
    return {name: int((lists or {}).get(name, start_block) or 0) for name in LISTS}


def read_from(cursor: int) -> int:
    return max(cursor - MARGIN_BLOCKS, 0) if cursor else 0


class ExplorerError(RuntimeError):
    pass


class QueryTooHeavy(ExplorerError):
    """The explorer failed a query without saying why. Routescan does this for busy addresses
    over wide block ranges; the same query over a narrower range succeeds."""


MIN_SPAN = 64  # blocks: the narrowest window a failing query is cut down to
MIN_PAGE = 100  # rows: the smallest page asked for once the window is as narrow as it goes


@dataclass(frozen=True, slots=True)
class Explorer:
    chain: Chain
    base_url: str
    api_key: str | None = None
    chain_param: int | None = None  # Etherscan V2 serves every chain from one URL with ?chainid=


EXPLORERS: dict[Chain, list[Explorer]] = {
    # Routescan first: Blockscout's Etherscan-style endpoint puts unauthenticated clients in a
    # penalty box for minutes after a short burst, which stalls a backfill.
    Chain.ETHEREUM: [
        Explorer(Chain.ETHEREUM, "https://api.routescan.io/v2/network/mainnet/evm/1/etherscan/api"),
        Explorer(Chain.ETHEREUM, "https://eth.blockscout.com/api"),
    ],
    Chain.OPTIMISM: [Explorer(Chain.OPTIMISM, "https://optimism.blockscout.com/api")],
    Chain.AVALANCHE: [Explorer(Chain.AVALANCHE, "https://api.routescan.io/v2/network/mainnet/evm/43114/etherscan/api")],
}

ETHERSCAN_V2 = "https://api.etherscan.io/v2/api"
ETHERSCAN_CHAIN_IDS = {
    Chain.ETHEREUM: 1,
    Chain.OPTIMISM: 10,
    Chain.ARBITRUM: 42161,
    Chain.BASE: 8453,
    Chain.POLYGON: 137,
    Chain.AVALANCHE: 43114,
}


def explorers_for(chain: Chain, etherscan_key: str | None = None) -> list[Explorer]:
    """Free explorers first; Etherscan V2 when a key is configured, which also covers the chains
    whose free explorers refuse automated clients."""
    found = list(EXPLORERS.get(chain, []))
    if etherscan_key and chain in ETHERSCAN_CHAIN_IDS:
        found.append(Explorer(chain, ETHERSCAN_V2, etherscan_key, ETHERSCAN_CHAIN_IDS[chain]))
    return found


class EtherscanAdapter:
    def __init__(self, client: PoliteClient, explorer: Explorer) -> None:
        self.client = client
        self.explorer = explorer
        self.chain = explorer.chain
        # Off for the tracer, where a gap must fail the read. A replay over a busy wallet may turn
        # it on: blocks the explorer cannot serve at all are then skipped and listed here.
        self.skip_unreadable = False
        self.unread: list[tuple[str, int]] = []

    async def _call(self, params: dict[str, object]) -> list[dict[str, str]]:
        query = dict(params)
        if self.explorer.api_key:
            query["apikey"] = self.explorer.api_key
        if self.explorer.chain_param:
            query["chainid"] = self.explorer.chain_param

        async def once() -> list[dict[str, str]]:
            data = await self.client.get_json(self.explorer.base_url, query)
            if not isinstance(data, dict):
                raise ExplorerError(f"unexpected response from {self.explorer.base_url}")
            result = data.get("result")
            if str(data.get("status")) == "1" and isinstance(result, list):
                return result
            message = f"{data.get('message', '')} {result if isinstance(result, str) else ''}".lower()
            if "no transactions found" in message or "no token transfers found" in message or result == []:
                return []
            if "rate limit" in message or "too many" in message:
                raise RateLimited(message)
            if "an error occurred" in message:
                raise QueryTooHeavy(f"{self.explorer.base_url}: {message.strip()}")
            raise ExplorerError(f"{self.explorer.base_url}: {message.strip() or data!r}")

        return await with_retry(once, client=self.client, url=self.explorer.base_url)

    async def _page(
        self, action: str, address: str, start: int, end: int, page: int = 1, size: int = PAGE_SIZE
    ) -> list[dict[str, str]]:
        return await self._call(
            {
                "module": "account",
                "action": action,
                "address": address,
                "startblock": start,
                "endblock": end,
                "page": page,
                "offset": size,
                "sort": "asc",
            }
        )

    async def _walk(
        self, action: str, address: str, start_block: int, end_block: int = 999_999_999
    ) -> list[dict[str, str]]:
        """Every row for one action from `start_block` on, oldest first.

        Explorers fail on deep pages of busy addresses, so the walk pages by block instead: it
        always asks for the first page, keeps the blocks a full page covered completely, and asks
        again from the last block, which it reads whole. Only a single block with more rows than
        a page is paged through. A query the explorer cannot serve is made smaller: the range is cut
        to a quarter down to MIN_SPAN blocks, then the page to a quarter down to MIN_PAGE rows. Both
        grow back after each answer.
        """
        rows: list[dict[str, str]] = []
        block, span, size = start_block, max(end_block - start_block, MIN_SPAN), PAGE_SIZE
        failures = 0
        while block <= end_block:
            upper = min(end_block, block + span)
            try:
                batch = await self._page(action, address, block, upper, size=size)
            except QueryTooHeavy:
                if span > MIN_SPAN:
                    span = max(MIN_SPAN, span // 4)
                    continue
                if size > MIN_PAGE:
                    size = max(MIN_PAGE, size // 4)
                    continue
                failures += 1
                if failures < 3:
                    await asyncio.sleep(2.0 * failures)
                    continue
                if not self.skip_unreadable:
                    raise
                # Read this narrowest window block by block, skipping only what cannot be read.
                for b in range(block, upper + 1):
                    rows.extend(await self._whole_block_or_skip(action, address, b, size))
                block, failures = upper + 1, 0
                continue
            failures = 0
            if len(batch) < size:
                rows.extend(batch)
                block, span, size = upper + 1, span * 2, min(PAGE_SIZE, size * 2)
                continue
            last = max(int(r.get("blockNumber") or 0) for r in batch)
            if last > block:
                # Every block before the last one is complete in this page; the last may not be.
                rows.extend(r for r in batch if int(r.get("blockNumber") or 0) < last)
                block = last
                continue
            rows.extend(await self._whole_block_or_skip(action, address, block, size))
            block += 1
        return rows

    async def _whole_block_or_skip(self, action: str, address: str, block: int, size: int) -> list[dict[str, str]]:
        try:
            return await self._whole_block(action, address, block, size)
        except QueryTooHeavy:
            if not self.skip_unreadable:
                raise
            self.unread.append((action, block))
            return []

    async def _whole_block(self, action: str, address: str, block: int, size: int) -> list[dict[str, str]]:
        """Every row in one block that holds more rows than a page. Which page sizes an explorer
        can serve for a busy block varies, so each size is tried in turn, whole block each time."""
        failure: QueryTooHeavy | None = None
        for attempt in dict.fromkeys((PAGE_SIZE, size, MIN_PAGE)):
            out: list[dict[str, str]] = []
            try:
                for page in range(1, MAX_WINDOW // attempt + 1):
                    batch = await self._page(action, address, block, block, page, attempt)
                    out.extend(batch)
                    if len(batch) < attempt:
                        return out
            except QueryTooHeavy as exc:
                failure = exc
                await asyncio.sleep(1.0)
                continue
            raise ExplorerError(f"more than {MAX_WINDOW} {action} rows in block {block} for {address}")
        assert failure is not None
        raise failure

    async def history(
        self, address: str, start_block: int = 0, end_block: int = 999_999_999, lists: dict | None = None
    ) -> Page:
        """All value moved to or from `address` between two blocks, normalized. `lists` holds the
        per-list cursors of an earlier read; without it every list starts at `start_block`."""
        chain = self.chain
        at = resume_from(lists, start_block)
        normal = await self._walk("txlist", address, read_from(at["normal"]), end_block)
        internal = await self._walk("txlistinternal", address, read_from(at["internal"]), end_block)
        tokens = await self._walk("tokentx", address, read_from(at["token"]), end_block)
        page = Page()
        top = 0
        # Ids must not depend on the provider: Blockscout reports a log index for token rows and
        # Routescan does not. A row's identity is its content plus how many identical rows came
        # before it in the same transaction.
        seen: dict[tuple[str, ...], int] = {}

        def occurrence(*key: str) -> int:
            n = seen.get(key, 0)
            seen[key] = n + 1
            return n

        for row in normal:
            top = max(top, int(row["blockNumber"]))
            page.transfers.extend(normal_transfers(chain, row, address))
        for row in internal:
            top = max(top, int(row["blockNumber"]))
            key: tuple[str, ...] = (
                "i",
                row.get("hash", "").lower(),
                row.get("from", "").lower(),
                row.get("to", "").lower(),
                str(row.get("value", "")),
            )
            t = internal_transfer(chain, row, occurrence(*key))
            if t is not None:
                page.transfers.append(t)
        for row in tokens:
            top = max(top, int(row["blockNumber"]))
            key = (
                "t",
                row.get("hash", "").lower(),
                row.get("contractAddress", "").lower(),
                row.get("from", "").lower(),
                row.get("to", "").lower(),
                str(row.get("value", "")),
            )
            t, token = token_transfer(chain, row, occurrence(*key))
            if t is not None:
                page.transfers.append(t)
                page.tokens[token.asset] = token
        tops = {
            "normal": max([at["normal"], *(int(r["blockNumber"]) for r in normal)]),
            "internal": max([at["internal"], *(int(r["blockNumber"]) for r in internal)]),
            "token": max([at["token"], *(int(r["blockNumber"]) for r in tokens)]),
        }
        page.cursor = {"block": max(top, *tops.values()), "lists": tops}
        return page


def _row_key(action: str, row: dict[str, str]) -> tuple[str, ...]:
    if action == "tokentx":
        return (
            row.get("hash", ""),
            str(row.get("logIndex", "")),
            row.get("contractAddress", ""),
            row.get("from", ""),
            row.get("to", ""),
            row.get("value", ""),
        )
    if action == "txlistinternal":
        return (
            row.get("hash", ""),
            str(row.get("traceId", row.get("index", ""))),
            row.get("from", ""),
            row.get("to", ""),
            row.get("value", ""),
        )
    return (row.get("hash", ""),)


def _failed(row: dict[str, str]) -> bool:
    return row.get("isError") == "1" or row.get("txreceipt_status") == "0"


def normal_transfers(chain: Chain, row: dict[str, str], subject: str) -> list[Transfer]:
    """A top-level transaction: its value, and the fee if the subject paid it.

    Fees are recorded only for transactions the subject sent, because those are the only ones that
    change the subject's balance. A failed transaction still burns its fee but moves no value.
    """
    out: list[Transfer] = []
    tx = row["hash"].lower()
    block = int(row["blockNumber"])
    ts = int(row["timeStamp"])
    sender = row["from"].lower()
    receiver = (row.get("to") or row.get("contractAddress") or "").lower()
    value = int(row.get("value") or 0)
    if value > 0 and receiver and not _failed(row):
        out.append(
            Transfer(
                uid=f"{chain}:{tx}:n",
                chain=chain,
                tx=tx,
                block=block,
                ts=ts,
                position=0,
                sender=node_id(chain, sender),
                receiver=node_id(chain, receiver),
                asset=native_asset(chain),
                amount=value,
                kind=Kind.NATIVE,
                meta={"method": row.get("methodId") or row.get("input", "")[:10]},
            )
        )
    if sender == subject.lower():
        gas_used = int(row.get("gasUsed") or 0)
        price = int(row.get("effectiveGasPrice") or row.get("gasPrice") or 0)
        fee = gas_used * price
        if fee > 0:
            out.append(
                Transfer(
                    uid=f"{chain}:{tx}:f",
                    chain=chain,
                    tx=tx,
                    block=block,
                    ts=ts,
                    position=1_000_000,
                    sender=node_id(chain, sender),
                    receiver=fee_node(chain),
                    asset=native_asset(chain),
                    amount=fee,
                    kind=Kind.FEE,
                )
            )
    return out


def internal_transfer(chain: Chain, row: dict[str, str], occurrence: int = 0) -> Transfer | None:
    value = int(row.get("value") or 0)
    if value <= 0 or _failed(row) or not row.get("from") or not row.get("to"):
        return None
    tx = row["hash"].lower()
    trace = str(row.get("traceId") or row.get("index") or "0")
    return Transfer(
        uid=f"{chain}:{tx}:i:{row['from'].lower()}:{row['to'].lower()}:{value}:{occurrence}",
        chain=chain,
        tx=tx,
        block=int(row["blockNumber"]),
        ts=int(row["timeStamp"]),
        position=100_000 + _trace_order(trace),
        sender=node_id(chain, row["from"]),
        receiver=node_id(chain, row["to"]),
        asset=native_asset(chain),
        amount=value,
        kind=Kind.INTERNAL,
    )


def _trace_order(trace: str) -> int:
    # Trace ids look like "0_1_2"; their numeric parts give a stable order within the call tree.
    order = 0
    for part in trace.replace("-", "_").split("_")[:4]:
        order = order * 256 + (int(part) % 256 if part.isdigit() else 0)
    return order


def token_transfer(chain: Chain, row: dict[str, str], occurrence: int = 0) -> tuple[Transfer | None, Token]:
    contract = row["contractAddress"].lower()
    asset = token_asset(chain, contract)
    try:
        decimals = int(row.get("tokenDecimal") or 0)
    except ValueError:
        decimals = 0
    token = Token(
        asset=asset,
        symbol=(row.get("tokenSymbol") or "")[:32],
        decimals=decimals,
        name=(row.get("tokenName") or "")[:64],
    )
    value = int(row.get("value") or 0)
    if value <= 0 or not row.get("from") or not row.get("to"):
        return None, token
    tx = row["hash"].lower()
    log_index = str(row.get("logIndex") or "")
    position = int(log_index) if log_index.isdigit() else 10_000 + occurrence
    return (
        Transfer(
            uid=f"{chain}:{tx}:t:{contract}:{row['from'].lower()}:{row['to'].lower()}:{value}:{occurrence}",
            chain=chain,
            tx=tx,
            block=int(row["blockNumber"]),
            ts=int(row["timeStamp"]),
            position=position,
            sender=node_id(chain, row["from"]),
            receiver=node_id(chain, row["to"]),
            asset=asset,
            amount=value,
            kind=Kind.TOKEN,
        ),
        token,
    )
