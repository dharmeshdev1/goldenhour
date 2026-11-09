"""The XRP Ledger through public rippled servers.

Two details matter for following money here. Payments can be partial, so the amount a sender asked
to deliver is not always what arrived: the ledger's `delivered_amount` is the truth. And exchanges
share one account across all customers, telling them apart by a destination tag, so the tag is kept
with every transfer: it is the difference between "sent to Binance" and "sent to one Binance user".

Payments are not the only way XRP moves. A trade on the ledger's own exchange, an AMM deposit, an
account deletion, an escrow or a check changes balances without a Payment. Every transaction's
metadata states the account's balance before and after, so each one is reconciled: XRP the
recorded transfers do not explain becomes a transfer of its own. An account deletion pays the
rest of the balance to its destination; trades go to and come from `xrpl:@dex`; anything else is
booked against `xrpl:@ledger`. Each address's ledger therefore matches the chain to the drop.
"""

from __future__ import annotations

from decimal import Decimal

from goldenhour.http import PoliteClient
from goldenhour.models import Chain, Kind, Page, Token, Transfer, fee_node, native_asset, node_id

SERVERS = ("https://xrplcluster.com", "https://s1.ripple.com:51234")
DEX = "xrpl:@dex"  # the ledger's order books and AMM pools
LEDGER = "xrpl:@ledger"  # escrows, checks, payment channels: XRP held by the ledger itself
TRADES = {"OfferCreate", "OfferCancel", "Payment", "AMMCreate", "AMMDeposit", "AMMWithdraw", "AMMBid", "AMMDelete"}
RIPPLE_EPOCH = 946_684_800
ASSET = native_asset(Chain.XRPL)
IOU_DECIMALS = 15


def _memos(tx: dict) -> str:
    texts = []
    for m in tx.get("Memos") or []:
        data = (m.get("Memo") or {}).get("MemoData", "")
        try:
            texts.append(bytes.fromhex(data).decode("utf-8", "replace"))
        except ValueError:
            continue
    return " | ".join(texts)[:240]


def _iou(currency: str) -> str:
    # Three-letter codes are plain text. Longer codes are 40 hex characters, often ASCII padded.
    if len(currency) == 40:
        try:
            text = bytes.fromhex(currency).rstrip(b"\x00").decode("ascii")
            if text.isprintable():
                return text
        except ValueError:
            pass
    return currency


def balance_changes(meta: dict, account: str) -> dict[str, int]:
    """The account's balance changes in one transaction, read from its metadata: XRP in drops from
    the AccountRoot entry, and each issued currency (scaled by 10^15) from its trust line."""
    out: dict[str, int] = {}
    for wrapper in meta.get("AffectedNodes") or []:
        for kind, node in wrapper.items():
            if not isinstance(node, dict):
                continue
            entry = node.get("LedgerEntryType")
            final = node.get("FinalFields") or node.get("NewFields") or {}
            previous = node.get("PreviousFields") or {}
            if entry == "AccountRoot" and final.get("Account") == account:
                if kind == "CreatedNode":
                    out[ASSET] = out.get(ASSET, 0) + int(final.get("Balance") or 0)
                elif "Balance" in previous:
                    out[ASSET] = out.get(ASSET, 0) + int(final.get("Balance") or 0) - int(previous["Balance"])
            elif entry == "RippleState":
                low = (final.get("LowLimit") or {}).get("issuer")
                high = (final.get("HighLimit") or {}).get("issuer")
                balance = final.get("Balance") or {}
                if account not in (low, high) or not isinstance(balance, dict):
                    continue
                if kind == "CreatedNode":
                    before = Decimal(0)
                elif isinstance(previous.get("Balance"), dict):
                    before = Decimal(str(previous["Balance"].get("value", "0")))
                else:
                    continue
                change = Decimal(str(balance.get("value", "0"))) - before
                # A trust line's balance is stated from the low account's side.
                if account == high:
                    change = -change
                other = high if account == low else low
                asset = f"xrpl:{_iou(str(balance.get('currency', '')))}.{other}"
                out[asset] = out.get(asset, 0) + int(change * (10**IOU_DECIMALS))
    return {asset: amount for asset, amount in out.items() if amount}


def _reconcile(
    out: list[Transfer], tokens: list[Token], item: dict, subject: str, txid: str, ledger: int, ts: int
) -> None:
    """Book whatever the subject gained or lost that the transfers so far do not explain."""
    tx = item.get("tx") or item.get("tx_json") or {}
    meta = item.get("meta") or {}
    me = node_id(Chain.XRPL, subject)
    kind = str(tx.get("TransactionType", ""))
    account, destination = tx.get("Account", ""), tx.get("Destination", "")
    for asset, change in balance_changes(meta, subject).items():
        booked = sum(t.amount for t in out if t.asset == asset and t.receiver == me) - sum(
            t.amount for t in out if t.asset == asset and t.sender == me
        )
        rest = change - booked
        if rest == 0:
            continue
        if asset == ASSET and kind == "AccountDelete" and account and destination:
            # A deleted account's balance goes to the destination: one transfer, the same whichever
            # side of it is read.
            uid, sender, receiver = f"xrpl:{txid}:d", node_id(Chain.XRPL, account), node_id(Chain.XRPL, destination)
        else:
            other = DEX if kind in TRADES else LEDGER
            uid = f"xrpl:{txid}:r:{subject}:{asset.removeprefix('xrpl:')}"
            sender, receiver = (me, other) if rest < 0 else (other, me)
        if asset != ASSET:
            symbol = asset.removeprefix("xrpl:").rsplit(".", 1)[0]
            tokens.append(Token(asset=asset, symbol=symbol[:32], decimals=IOU_DECIMALS))
        out.append(
            Transfer(
                uid=uid,
                chain=Chain.XRPL,
                tx=txid,
                block=ledger,
                ts=ts,
                position=int(meta.get("TransactionIndex", 0)),
                sender=sender,
                receiver=receiver,
                asset=asset,
                amount=abs(rest),
                kind=Kind.NATIVE if asset == ASSET else Kind.TOKEN,
                meta={"type": kind},
            )
        )


def tx_transfers(item: dict, subject: str) -> tuple[list[Transfer], list[Token]]:
    tx = item.get("tx") or item.get("tx_json") or {}
    meta = item.get("meta") or {}
    txid = tx.get("hash") or item.get("hash", "")
    ledger = int(tx.get("ledger_index") or item.get("ledger_index") or 0)
    date = tx.get("date")
    ts = int(date) + RIPPLE_EPOCH if date is not None else 0
    account = tx.get("Account", "")
    out: list[Transfer] = []
    tokens: list[Token] = []
    if not txid or not account:
        return out, tokens
    ok = meta.get("TransactionResult") == "tesSUCCESS"
    if ok and tx.get("TransactionType") == "Payment" and tx.get("Destination"):
        delivered = meta.get("delivered_amount", meta.get("DeliveredAmount"))
        extra = {"memo": _memos(tx)}
        if "DestinationTag" in tx:
            extra["tag"] = str(tx["DestinationTag"])
        asset = ASSET
        amount = 0
        if isinstance(delivered, str) and delivered.isdigit():
            amount = int(delivered)
        elif isinstance(delivered, dict) and delivered.get("issuer"):
            asset = f"xrpl:{_iou(delivered.get('currency', ''))}.{delivered['issuer']}"
            amount = int(Decimal(str(delivered.get("value", "0"))) * (10**IOU_DECIMALS))
            tokens.append(Token(asset=asset, symbol=_iou(delivered.get("currency", ""))[:32], decimals=IOU_DECIMALS))
        if amount > 0:
            out.append(
                Transfer(
                    uid=f"xrpl:{txid}:p",
                    chain=Chain.XRPL,
                    tx=txid,
                    block=ledger,
                    ts=ts,
                    position=int(meta.get("TransactionIndex", 0)),
                    sender=node_id(Chain.XRPL, account),
                    receiver=node_id(Chain.XRPL, tx["Destination"]),
                    asset=asset,
                    amount=amount,
                    kind=Kind.NATIVE if asset == ASSET else Kind.TOKEN,
                    meta={k: v for k, v in extra.items() if v},
                )
            )
    fee = int(tx.get("Fee") or 0)
    if fee > 0 and account == subject:
        out.append(
            Transfer(
                uid=f"xrpl:{txid}:f",
                chain=Chain.XRPL,
                tx=txid,
                block=ledger,
                ts=ts,
                position=1_000_000,
                sender=node_id(Chain.XRPL, account),
                receiver=fee_node(Chain.XRPL),
                asset=ASSET,
                amount=fee,
                kind=Kind.FEE,
            )
        )
    _reconcile(out, tokens, item, subject, txid, ledger, ts)
    return out, tokens


class XrplAdapter:
    chain = Chain.XRPL

    def __init__(self, client: PoliteClient, servers: tuple[str, ...] = SERVERS) -> None:
        self.client = client
        self.servers = servers

    async def _rpc(self, method: str, params: dict) -> dict:
        last: Exception | None = None
        for url in self.servers:
            try:
                data = await self.client.post_json(url, {"method": method, "params": [params]})
                result = (data or {}).get("result") if isinstance(data, dict) else None
                if isinstance(result, dict) and result.get("status") != "error":
                    return result
                last = RuntimeError(f"{method}: {result}")
            except Exception as exc:
                last = exc
        assert last is not None
        raise last

    async def history(self, address: str, cursor: dict | None = None) -> Page:
        cursor = dict(cursor or {})
        start = int(cursor.get("ledger", -1))
        params: dict[str, object] = {
            "account": address,
            "ledger_index_min": start,
            "ledger_index_max": -1,
            "limit": 400,
            "forward": True,
        }
        page = Page()
        top = start
        while True:
            result = await self._rpc("account_tx", params)
            for item in result.get("transactions") or []:
                transfers, tokens = tx_transfers(item, address)
                page.transfers.extend(transfers)
                for token in tokens:
                    page.tokens[token.asset] = token
                tx = item.get("tx") or item.get("tx_json") or {}
                top = max(top, int(tx.get("ledger_index") or item.get("ledger_index") or 0))
            marker = result.get("marker")
            if not marker:
                break
            params["marker"] = marker
        page.cursor = {**cursor, "ledger": top}
        return page

    async def balance(self, address: str) -> int:
        result = await self._rpc("account_info", {"account": address, "ledger_index": "validated"})
        return int((result.get("account_data") or {}).get("Balance", 0))
