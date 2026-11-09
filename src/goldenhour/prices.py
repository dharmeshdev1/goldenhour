"""USD prices for every asset the trail touches, from DefiLlama's free coins API.

Two jobs. First, value: a transfer of 195,460 ETH and one of 1 ASTEROID token are not comparable
until both are in dollars at the moment they moved. Second, filtering: attackers' addresses are
showered with worthless spam tokens, some named "ETH" or "USDT". A token only counts if DefiLlama
prices it with high confidence, by contract address, so a fake "USDT" is worth exactly nothing.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from goldenhour.http import PoliteClient
from goldenhour.models import Chain, asset_chain

LLAMA = "https://coins.llama.fi"
HOUR = 3600
MIN_CONFIDENCE = 0.9
QUOTE_TTL_S = 600  # a current quote older than this is asked for again
QUOTE_MAX_AGE_S = 7 * 86_400  # a quote the feed has not confirmed for a week is dropped
UNPRICED_RETRY_S = 86_400  # an asset the feed could not price is asked about again after a day
log = logging.getLogger("goldenhour.prices")

NATIVE_IDS: dict[Chain, str] = {
    Chain.ETHEREUM: "coingecko:ethereum",
    Chain.OPTIMISM: "coingecko:ethereum",
    Chain.ARBITRUM: "coingecko:ethereum",
    Chain.BASE: "coingecko:ethereum",
    Chain.POLYGON: "coingecko:polygon-ecosystem-token",
    Chain.AVALANCHE: "coingecko:avalanche-2",
    Chain.BITCOIN: "coingecko:bitcoin",
    Chain.TRON: "coingecko:tron",
    Chain.XRPL: "coingecko:ripple",
    Chain.SOLANA: "coingecko:solana",
    Chain.NEAR: "coingecko:near",
}
LLAMA_CHAIN: dict[Chain, str] = {
    Chain.ETHEREUM: "ethereum",
    Chain.OPTIMISM: "optimism",
    Chain.ARBITRUM: "arbitrum",
    Chain.BASE: "base",
    Chain.POLYGON: "polygon",
    Chain.AVALANCHE: "avax",
    Chain.TRON: "tron",
    Chain.SOLANA: "solana",
}
WRAPPED_NATIVE = {
    "solana:So11111111111111111111111111111111111111112": "coingecko:solana",
}


def llama_id(asset: str) -> str | None:
    if asset in WRAPPED_NATIVE:
        return WRAPPED_NATIVE[asset]
    chain = asset_chain(asset)
    tail = asset.partition(":")[2]
    if tail == "native":
        return NATIVE_IDS.get(chain)
    if chain in LLAMA_CHAIN:
        return f"{LLAMA_CHAIN[chain]}:{tail}"
    return None


@dataclass(frozen=True, slots=True)
class Quote:
    price: float
    decimals: int | None
    symbol: str
    confidence: float
    at: int = 0  # when the feed last gave this price, unix seconds (0: unknown, from an older cache)


class PriceBook:
    """Hourly price history and current quotes, cached on disk between runs."""

    def __init__(self, cache: Path | None = None) -> None:
        self.cache = cache
        self.hourly: dict[str, dict[int, float]] = defaultdict(dict)
        self.quotes: dict[str, Quote] = {}
        self.unpriced: dict[str, int] = {}  # llama id -> when the feed last failed to price it
        if cache and cache.exists():
            data = json.loads(cache.read_text())
            for lid, series in data.get("hourly", {}).items():
                self.hourly[lid] = {int(h): float(p) for h, p in series.items()}
            for lid, q in data.get("quotes", {}).items():
                self.quotes[lid] = Quote(*q)
            unpriced = data.get("unpriced", {})
            # Older caches kept a bare list and never asked again; those are due now.
            self.unpriced = dict.fromkeys(unpriced, 0) if isinstance(unpriced, list) else dict(unpriced)

    def save(self) -> None:
        if not self.cache:
            return
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "hourly": {lid: {str(h): p for h, p in s.items()} for lid, s in self.hourly.items()},
            "quotes": {lid: [q.price, q.decimals, q.symbol, q.confidence, q.at] for lid, q in self.quotes.items()},
            "unpriced": dict(sorted(self.unpriced.items())),
        }
        # Atomic: write a sibling file and rename it over the old one, so a reader (or another
        # process saving at the same moment) never sees half a file.
        tmp = self.cache.with_name(f"{self.cache.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")))
        os.replace(tmp, self.cache)

    def _due(self, lid: str, now: int) -> bool:
        if lid in self.quotes:
            return now - self.quotes[lid].at >= QUOTE_TTL_S
        return now - self.unpriced.get(lid, -UNPRICED_RETRY_S) >= UNPRICED_RETRY_S

    async def load_current(self, client: PoliteClient, assets: list[str], now: int | None = None) -> None:
        """Current quotes for `assets`, asking the feed only for those that are missing or stale.

        A failed request keeps the quotes already held, so one outage does not zero every
        holding. A priced asset that one answer leaves out keeps its quote and is asked about
        again next time; one the feed has not confirmed for QUOTE_MAX_AGE_S is dropped, because
        an asset the feed stopped pricing is not worth its last price forever.
        """
        now = int(time.time()) if now is None else now
        ids = sorted({lid for a in assets if (lid := llama_id(a)) and self._due(lid, now)})
        for chunk in _chunks(ids, 60):
            try:
                data = await client.get_json(f"{LLAMA}/prices/current/{','.join(chunk)}", {"searchWidth": "6h"})
            except Exception as exc:
                log.warning("current prices not refreshed for %d assets: %s", len(chunk), exc)
                continue
            coins = (data or {}).get("coins", {}) if isinstance(data, dict) else {}
            for lid in chunk:
                c = coins.get(lid)
                if c and float(c.get("confidence", 0) or 0) >= MIN_CONFIDENCE:
                    self.quotes[lid] = Quote(
                        float(c["price"]),
                        c.get("decimals"),
                        str(c.get("symbol", ""))[:24],
                        float(c.get("confidence", 0)),
                        now,
                    )
                    self.unpriced.pop(lid, None)
                elif lid not in self.quotes:
                    self.unpriced[lid] = now
        for lid in ids:
            q = self.quotes.get(lid)
            if q and q.at and now - q.at > QUOTE_MAX_AGE_S:
                del self.quotes[lid]
                self.unpriced[lid] = now

    async def load_history(self, client: PoliteClient, assets: list[str], start: int, end: int) -> None:
        """Hourly prices for every priced asset between `start` and `end` (unix seconds)."""
        await self.load_current(client, assets)
        ids = sorted({lid for a in assets if (lid := llama_id(a)) and lid in self.quotes})
        first = start - start % HOUR
        for chunk in _chunks(ids, 20):
            need = [lid for lid in chunk if not _covered(self.hourly.get(lid, {}), first, end)]
            if need:
                await self._chart(client, need, first, end)

    async def _chart(self, client: PoliteClient, ids: list[str], first: int, end: int) -> None:
        """Hourly points for `ids`. A rejected request is split in half until the id that the
        feed refuses is isolated; that one asset keeps its current quote and nothing else breaks."""
        cursor = first
        while cursor <= end:
            span = min(500, (end - cursor) // HOUR + 2)
            try:
                data = await client.get_json(
                    f"{LLAMA}/chart/{','.join(ids)}",
                    {"start": cursor, "span": span, "period": "1h", "searchWidth": "2h"},
                )
            except Exception:
                if len(ids) > 1:
                    half = len(ids) // 2
                    await self._chart(client, ids[:half], cursor, end)
                    await self._chart(client, ids[half:], cursor, end)
                return
            coins = (data or {}).get("coins", {}) if isinstance(data, dict) else {}
            for lid, c in coins.items():
                for point in c.get("prices") or []:
                    ts = int(point["timestamp"])
                    self.hourly[lid][ts - ts % HOUR] = float(point["price"])
            cursor += span * HOUR

    def is_priced(self, asset: str) -> bool:
        lid = llama_id(asset)
        return bool(lid and lid in self.quotes)

    def price(self, asset: str, ts: int | None = None) -> float | None:
        """The asset's USD price at `ts` (the nearest hour on record), or now when `ts` is None."""
        lid = llama_id(asset)
        if not lid or lid not in self.quotes:
            return None
        if ts is None:
            return self.quotes[lid].price
        series = self.hourly.get(lid)
        if not series:
            return self.quotes[lid].price
        hour = ts - ts % HOUR
        for delta in (0, -HOUR, HOUR, -2 * HOUR, 2 * HOUR, -3 * HOUR, 3 * HOUR, -6 * HOUR, 6 * HOUR):
            p = series.get(hour + delta)
            if p is not None:
                return p
        return self.quotes[lid].price

    def usd(self, asset: str, amount: int, decimals: int, ts: int | None = None) -> float:
        p = self.price(asset, ts)
        if p is None or amount <= 0:
            return 0.0
        return amount / 10**decimals * p


def _covered(series: dict[int, float], start: int, end: int) -> bool:
    if not series:
        return False
    return min(series) <= start + HOUR and max(series) >= end - 2 * HOUR


def _chunks[T](items: list[T], n: int) -> list[list[T]]:
    return [items[i : i + n] for i in range(0, len(items), n)]
