from __future__ import annotations

import pytest

from goldenhour.models import Chain, Kind, Token, Transfer, native_asset
from goldenhour.prices import PriceBook, Quote

USDT = "ethereum:0xdac17f958d2ee523a2206206994597c13d831ec7"
ETH = native_asset(Chain.ETHEREUM)
BTC = native_asset(Chain.BITCOIN)
T0 = 1_793_300_400  # 2026-10-29T19:00:00Z


@pytest.fixture
def prices() -> PriceBook:
    """Flat prices: ETH 2,400, BTC 75,000, USDT 1. Good enough to test the logic that uses them."""
    book = PriceBook()
    book.quotes["coingecko:ethereum"] = Quote(2400.0, 18, "ETH", 0.99)
    book.quotes["coingecko:bitcoin"] = Quote(75000.0, 8, "BTC", 0.99)
    book.quotes["ethereum:0xdac17f958d2ee523a2206206994597c13d831ec7"] = Quote(1.0, 6, "USDT", 0.99)
    return book


@pytest.fixture
def tokens() -> dict[str, Token]:
    return {USDT: Token(USDT, "USDT", 6, "Tether USD")}


def transfer(
    uid: str,
    sender: str,
    receiver: str,
    amount: int,
    *,
    asset: str = ETH,
    ts: int = T0 + 3600,
    kind: Kind = Kind.NATIVE,
    tx: str | None = None,
    chain: Chain = Chain.ETHEREUM,
    meta: dict | None = None,
) -> Transfer:
    return Transfer(
        uid=uid,
        chain=chain,
        tx=tx or uid,
        block=1,
        ts=ts,
        position=0,
        sender=sender,
        receiver=receiver,
        asset=asset,
        amount=amount,
        kind=kind,
        meta=meta or {},
    )


def eth(n: float) -> int:
    return int(n * 10**18)
