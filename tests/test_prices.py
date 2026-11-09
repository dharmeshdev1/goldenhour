"""Current quotes stay fresh, survive an outage, and an unpriced asset is asked about again."""

import json
from pathlib import Path

import httpx
import respx

from goldenhour.http import PoliteClient
from goldenhour.prices import QUOTE_MAX_AGE_S, QUOTE_TTL_S, UNPRICED_RETRY_S, PriceBook, Quote

ETH = "ethereum:native"
TOKEN = "ethereum:0x" + "7" * 40
ETH_ID, TOKEN_ID = "coingecko:ethereum", TOKEN
NOW = 1_800_000_000


def client() -> PoliteClient:
    return PoliteClient(rates={"coins.llama.fi": 1000.0}, retries=0)


def answer(prices: dict[str, float]) -> httpx.Response:
    coins = {lid: {"price": p, "decimals": 18, "symbol": "X", "confidence": 0.99} for lid, p in prices.items()}
    return httpx.Response(200, json={"coins": coins})


async def test_quotes_refresh_when_stale_and_not_before() -> None:
    book = PriceBook()
    book.quotes[ETH_ID] = Quote(2400.0, 18, "ETH", 0.99, NOW - QUOTE_TTL_S + 60)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(host="coins.llama.fi").mock(return_value=answer({ETH_ID: 2500.0}))
        async with client() as c:
            await book.load_current(c, [ETH], now=NOW)
            assert not route.called and book.price(ETH) == 2400.0  # still fresh
            await book.load_current(c, [ETH], now=NOW + 60)
        assert route.called and book.price(ETH) == 2500.0
        assert book.quotes[ETH_ID].at == NOW + 60


async def test_an_outage_keeps_the_quotes_and_a_week_without_one_drops_them() -> None:
    book = PriceBook()
    book.quotes[ETH_ID] = Quote(2400.0, 18, "ETH", 0.99, NOW - 3600)
    with respx.mock(assert_all_called=False) as mock:
        mock.get(host="coins.llama.fi").mock(return_value=httpx.Response(500))
        async with client() as c:
            await book.load_current(c, [ETH], now=NOW)
            assert book.price(ETH) == 2400.0
            await book.load_current(c, [ETH], now=NOW - 3600 + QUOTE_MAX_AGE_S + 1)
    assert book.price(ETH) is None and ETH_ID in book.unpriced


async def test_an_unpriced_asset_is_asked_about_again_after_a_day() -> None:
    book = PriceBook()
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(host="coins.llama.fi").mock(return_value=answer({}))
        async with client() as c:
            await book.load_current(c, [TOKEN], now=NOW)
            assert TOKEN_ID in book.unpriced and route.call_count == 1
            await book.load_current(c, [TOKEN], now=NOW + 3600)
            assert route.call_count == 1  # not yet
            route.mock(return_value=answer({TOKEN_ID: 1.0}))
            await book.load_current(c, [TOKEN], now=NOW + UNPRICED_RETRY_S)
    assert book.price(TOKEN) == 1.0 and TOKEN_ID not in book.unpriced


def test_an_older_cache_loads_and_is_due(tmp_path: Path) -> None:
    cache = tmp_path / "prices.json"
    cache.write_text(json.dumps({"quotes": {ETH_ID: [2400.0, 18, "ETH", 0.99]}, "unpriced": [TOKEN_ID]}))
    book = PriceBook(cache)
    assert book.quotes[ETH_ID].at == 0 and book.unpriced == {TOKEN_ID: 0}
    assert book._due(ETH_ID, NOW) and book._due(TOKEN_ID, NOW)
    book.save()
    assert PriceBook(cache).quotes[ETH_ID] == book.quotes[ETH_ID]
