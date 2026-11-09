"""One HTTP client for every outbound request, with manners.

Every data source here is a free public service: block explorers, public RPC nodes and price
feeds. Each one publishes, or quietly enforces, a request rate. The client keeps a token bucket per
host so a burst of work never exceeds that rate, caps how many requests are in flight per host and
overall, retries the failures that are worth retrying, and identifies itself with a link back to
the project.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx

from goldenhour import __version__

USER_AGENT = f"Goldenhour/{__version__} (+https://github.com/dharmeshdev1/goldenhour)"
CIRCUIT_AFTER = 6  # consecutive 429s before a host is skipped
CIRCUIT_COOLDOWN_S = 600.0

# Requests per second per host. Conservative on purpose: a tracer that gets itself banned in the
# middle of an incident is worse than a slow one.
DEFAULT_RATES: dict[str, float] = {
    "eth.blockscout.com": 2.8,
    "optimism.blockscout.com": 4.0,
    "api.routescan.io": 3.0,
    "api.etherscan.io": 4.0,
    "mempool.space": 4.0,
    "blockstream.info": 3.0,
    "api.trongrid.io": 3.0,
    "xrplcluster.com": 4.0,
    "s1.ripple.com": 4.0,
    "api.mainnet-beta.solana.com": 0.9,  # getTransaction is limited to 10 per 10 seconds
    "gateway.liquify.com": 4.0,
    "coins.llama.fi": 3.0,
    "api.relay.link": 3.0,
    "ethereum-rpc.publicnode.com": 8.0,
}


class TokenBucket:
    """Allows `rate` acquisitions per second on average, with a small burst."""

    def __init__(self, rate: float, burst: float = 1.0) -> None:
        self.rate = rate
        self.base_rate = rate
        self.capacity = max(burst, 1.0)
        self._tokens = self.capacity
        self._stamp = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._stamp) * self.rate)
                self._stamp = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                await asyncio.sleep((1.0 - self._tokens) / self.rate)

    def slow_down(self, factor: float = 0.7, floor: float = 0.25) -> None:
        """Called after a 429: the host told us our rate was too high."""
        self.rate = max(floor, self.rate * factor)

    def recover(self) -> None:
        """Called after a success: creep back toward the configured rate (additive increase after the
        multiplicative decrease in `slow_down`), so one burst of 429s does not throttle a run forever."""
        if self.rate < self.base_rate:
            self.rate = min(self.base_rate, self.rate + self.base_rate * 0.02)

    def pause(self, seconds: float) -> None:
        """Hold every request to this host for `seconds`, as the host's own headers asked."""
        self._stamp = time.monotonic() + seconds
        self._tokens = 0.0


class RateLimited(Exception):
    """A response that says "slow down" in its body rather than its status code."""


class CircuitOpen(httpx.HTTPError):
    """A host refused us so persistently that it is skipped for a while instead of retried."""


class PoliteClient:
    """An httpx client with per-host rate limits, concurrency caps and retries."""

    def __init__(
        self,
        *,
        rates: Mapping[str, float] | None = None,
        default_rate: float = 3.0,
        concurrency: int = 32,
        per_host: int = 4,
        timeout: float = 30.0,
        retries: int = 5,
        user_agent: str = USER_AGENT,
    ) -> None:
        merged = {**DEFAULT_RATES, **(rates or {})}
        self._buckets: dict[str, TokenBucket] = defaultdict(lambda: TokenBucket(default_rate))
        for host, rate in merged.items():
            self._buckets[host] = TokenBucket(rate)
        self._global = asyncio.Semaphore(concurrency)
        self._per_host: dict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(per_host))
        self._retries = retries
        self.requests = 0
        self._health: dict[str, dict[str, int]] = defaultdict(lambda: {"ok": 0, "failed": 0})
        self._refusals: dict[str, int] = defaultdict(int)  # consecutive 429s per host
        self._open_until: dict[str, float] = {}
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=min(timeout, 10.0)),
            follow_redirects=True,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            limits=httpx.Limits(max_connections=concurrency * 2, max_keepalive_connections=concurrency),
        )

    async def __aenter__(self) -> PoliteClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    @asynccontextmanager
    async def _slot(self, url: str) -> AsyncIterator[None]:
        # Take the host's slot before a global one. The other order lets requests queued behind one
        # slow host hold every global slot, which serializes everything behind that host.
        host = urlsplit(url).hostname or ""
        async with self._per_host[host], self._global:
            await self._buckets[host].acquire()
            yield

    async def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        """Send a request, retrying connection errors, 429 and 5xx responses with backoff.

        A 429 is retried more patiently than other failures, because it is an instruction rather
        than an error: wait as long as the host says, then continue.
        """
        host = urlsplit(url).hostname or ""
        if self._open_until.get(host, 0.0) > time.monotonic():
            raise CircuitOpen(f"{host} is refusing requests; skipped until it cools down")
        last: Exception | None = None
        limited = 0
        attempt = 0
        while attempt <= self._retries:
            try:
                async with self._slot(url):
                    self.requests += 1
                    resp = await self._client.request(method, url, **kwargs)  # type: ignore[arg-type]
                self._obey(host, resp)
                if resp.status_code == 429:
                    self._refusals[host] += 1
                    if self._refusals[host] >= CIRCUIT_AFTER:
                        # The host is not slowing us down, it is shutting us out. Stop asking for a
                        # while: every waiting request would otherwise stall the whole run.
                        self._open_until[host] = time.monotonic() + CIRCUIT_COOLDOWN_S
                        self._refusals[host] = 0
                        self._health[host]["failed"] += 1
                        raise CircuitOpen(f"{host} answered 429 {CIRCUIT_AFTER} times in a row")
                    if limited < 12:
                        limited += 1
                        self._buckets[host].slow_down()
                        await asyncio.sleep(_backoff(min(limited, 4), resp))
                        continue
                else:
                    self._refusals[host] = 0
                if resp.status_code < 400:
                    self._buckets[host].recover()
                    self._health[host]["ok"] += 1
                else:
                    self._health[host]["failed"] += 1
                if resp.status_code >= 500 and attempt < self._retries:
                    attempt += 1
                    await asyncio.sleep(_backoff(attempt, resp))
                    continue
                return resp
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last = exc
                self._health[host]["failed"] += 1
                attempt += 1
                if attempt <= self._retries:
                    await asyncio.sleep(_backoff(attempt, None))
                    continue
        if last is None:
            raise httpx.HTTPError(f"gave up on {url} after repeated failures")
        raise last

    def health(self) -> dict[str, dict[str, int]]:
        """Responses per host in this client's life: ok, and failed (errors, 4xx, 5xx, timeouts)."""
        return {host: dict(counts) for host, counts in self._health.items()}

    def _obey(self, host: str, resp: httpx.Response) -> None:
        """Honor `x-ratelimit-remaining` and `x-ratelimit-reset` when a host sends them.

        Blockscout sends both, with the reset in milliseconds. When the budget is spent, pausing
        until the reset is far cheaper than collecting a 429 and the penalty that can follow it.
        """
        remaining = resp.headers.get("x-ratelimit-remaining", "")
        reset = resp.headers.get("x-ratelimit-reset", "")
        if remaining.isdigit() and int(remaining) == 0 and reset.isdigit():
            millis = int(reset)
            self._buckets[host].pause(min(millis / 1000.0 + 0.05, 900.0))

    async def get_json(self, url: str, params: Mapping[str, object] | None = None) -> object:
        resp = await self.request("GET", url, params=params)
        resp.raise_for_status()
        return resp.json()

    async def post_json(self, url: str, payload: object) -> object:
        resp = await self.request("POST", url, json=payload)
        resp.raise_for_status()
        return resp.json()

    def slow_down(self, url: str) -> None:
        self._buckets[urlsplit(url).hostname or ""].slow_down()


def _backoff(attempt: int, resp: httpx.Response | None) -> float:
    if resp is not None:
        retry_after = resp.headers.get("retry-after", "")
        if retry_after.isdigit():
            return min(float(retry_after), 60.0)
    return min(0.75 * (2**attempt) + random.random() * 0.5, 30.0)


async def with_retry[T](
    call: Callable[[], Awaitable[T]], *, attempts: int = 6, client: PoliteClient | None = None, url: str = ""
) -> T:
    """Retry an async callable that raises RateLimited, slowing the host down each time."""
    for attempt in range(attempts):
        try:
            return await call()
        except RateLimited:
            if client is not None and url:
                client.slow_down(url)
            if attempt == attempts - 1:
                raise
            await asyncio.sleep(_backoff(attempt, None))
    raise AssertionError("unreachable")
