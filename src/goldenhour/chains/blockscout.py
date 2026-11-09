"""EVM history through Blockscout's REST API (v2).

It answers the same questions as the Etherscan-style API with richer rows: every transaction
carries its type and, for EIP-7702 transactions, the authorization list. That is how the tracer
sees a delegation being installed. Its rate budget is also separate from the Etherscan-style
endpoint on the same host, so the two can run side by side.

Pages come newest first, 50 rows at a time. An incremental read walks back until it reaches blocks
older than the cursor. Transfer ids match the Etherscan adapter exactly, so either source can
refresh an address the other one fetched.
"""

from __future__ import annotations

from datetime import datetime

from goldenhour.chains.etherscan import ExplorerError, read_from, resume_from
from goldenhour.http import PoliteClient
from goldenhour.models import Chain, Kind, Page, Token, Transfer, fee_node, native_asset, node_id, token_asset

BASES = {
    Chain.ETHEREUM: "https://eth.blockscout.com/api/v2",
    Chain.OPTIMISM: "https://optimism.blockscout.com/api/v2",
}
MAX_PAGES = 120  # 6,000 rows per kind: beyond that the address is a service, not a laundering hop


def _ts(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def _hash(party: dict | None) -> str:
    return ((party or {}).get("hash") or "").lower()


class BlockscoutAdapter:
    def __init__(self, client: PoliteClient, chain: Chain) -> None:
        self.client = client
        self.chain = chain
        self.base = BASES[chain]

    async def _walk(self, path: str, stop_block: int) -> tuple[list[dict], bool]:
        rows: list[dict] = []
        params: dict[str, object] | None = None
        for _ in range(MAX_PAGES):
            data = await self.client.get_json(f"{self.base}{path}", params)
            if not isinstance(data, dict):
                raise ExplorerError(f"unexpected response from {self.base}{path}")
            items = data.get("items") or []
            for item in items:
                if int(item.get("block_number") or 0) < stop_block:
                    return rows, True
                rows.append(item)
            params = data.get("next_page_params")
            if not params:
                return rows, True
        return rows, False

    async def history(self, address: str, start_block: int = 0, lists: dict | None = None) -> Page:
        """Like the Etherscan adapter: per-list cursors, each read a margin below its own."""
        chain = self.chain
        subject = address.lower()
        at = resume_from(lists, start_block)
        txs, ok1 = await self._walk(f"/addresses/{address}/transactions", read_from(at["normal"]))
        internal, ok2 = await self._walk(f"/addresses/{address}/internal-transactions", read_from(at["internal"]))
        transfers, ok3 = await self._walk(f"/addresses/{address}/token-transfers", read_from(at["token"]))
        page = Page(complete=ok1 and ok2 and ok3)
        top = start_block
        delegations: list[dict[str, str]] = []
        for x in txs:
            tx = x["hash"].lower()
            block = int(x.get("block_number") or 0)
            top = max(top, block)
            ts = _ts(x["timestamp"])
            sender = _hash(x.get("from"))
            receiver = _hash(x.get("to")) or _hash(x.get("created_contract"))
            ok = x.get("status") == "ok"
            value = int(x.get("value") or 0)
            if ok and value > 0 and sender and receiver:
                page.transfers.append(
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
                        meta={"method": str(x.get("method") or "")[:40]},
                    )
                )
            fee = int(((x.get("fee") or {}).get("value")) or 0)
            if fee > 0 and sender == subject:
                page.transfers.append(
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
            if x.get("type") == 4:
                for auth in x.get("authorization_list") or []:
                    delegations.append(
                        {
                            "tx": tx,
                            "ts": str(ts),
                            "authority": str(auth.get("authority", "")).lower(),
                            "delegate": str(auth.get("address_hash") or auth.get("address") or "").lower(),
                            "sender": sender,
                            "valid": str(auth.get("validity", "")),
                        }
                    )
        seen: dict[tuple[str, ...], int] = {}

        def occurrence(*key: str) -> int:
            n = seen.get(key, 0)
            seen[key] = n + 1
            return n

        for x in internal:
            if x.get("success") is False or x.get("error"):
                continue
            value = int(x.get("value") or 0)
            src, dst = _hash(x.get("from")), _hash(x.get("to"))
            if value <= 0 or not src or not dst:
                continue
            tx = x["transaction_hash"].lower()
            block = int(x.get("block_number") or 0)
            top = max(top, block)
            k = occurrence("i", tx, src, dst, str(value))
            page.transfers.append(
                Transfer(
                    uid=f"{chain}:{tx}:i:{src}:{dst}:{value}:{k}",
                    chain=chain,
                    tx=tx,
                    block=block,
                    ts=_ts(x["timestamp"]),
                    position=100_000 + int(x.get("index") or 0),
                    sender=node_id(chain, src),
                    receiver=node_id(chain, dst),
                    asset=native_asset(chain),
                    amount=value,
                    kind=Kind.INTERNAL,
                )
            )
        for x in transfers:
            token = x.get("token") or {}
            if (token.get("type") or x.get("token_type")) not in ("ERC-20", None):
                continue
            contract = str(token.get("address_hash") or token.get("address") or "").lower()
            total = x.get("total") or {}
            src, dst = _hash(x.get("from")), _hash(x.get("to"))
            try:
                value = int(total.get("value") or 0)
            except (TypeError, ValueError):
                continue
            if not contract or value <= 0 or not src or not dst:
                continue
            tx = x["transaction_hash"].lower()
            block = int(x.get("block_number") or 0)
            top = max(top, block)
            asset = token_asset(chain, contract)
            page.tokens[asset] = Token(
                asset=asset,
                symbol=str(token.get("symbol") or "")[:32],
                decimals=int(token.get("decimals") or total.get("decimals") or 0),
                name=str(token.get("name") or "")[:64],
            )
            k = occurrence("t", tx, contract, src, dst, str(value))
            page.transfers.append(
                Transfer(
                    uid=f"{chain}:{tx}:t:{contract}:{src}:{dst}:{value}:{k}",
                    chain=chain,
                    tx=tx,
                    block=block,
                    ts=_ts(x["timestamp"]),
                    position=int(x.get("log_index") or 0),
                    sender=node_id(chain, src),
                    receiver=node_id(chain, dst),
                    asset=asset,
                    amount=value,
                    kind=Kind.TOKEN,
                )
            )
        tops = {
            "normal": max([at["normal"], *(int(x.get("block_number") or 0) for x in txs)]),
            "internal": max([at["internal"], *(int(x.get("block_number") or 0) for x in internal)]),
            "token": max([at["token"], *(int(x.get("block_number") or 0) for x in transfers)]),
        }
        page.cursor = {"block": max(top, *tops.values()), "lists": tops}
        if delegations:
            page.cursor["delegations"] = delegations[:200]
        return page
