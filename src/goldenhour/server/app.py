"""The HTTP surface of the self-hosted server.

It serves the same documents as the public static edition (so the web app works against either),
and adds what a static site cannot: a direct screening endpoint for integrations, the Guard's
policy decisions, a live stream of movements, health probes and Prometheus metrics. A background
worker runs the tracer every few minutes, republishes, and sends alerts for new movements.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse

from goldenhour import __version__
from goldenhour.models import InvalidAddress
from goldenhour.screen import address_key, candidates, lookup, shard_of
from goldenhour.server import notify

log = logging.getLogger("goldenhour.server")

TICKS = Counter("goldenhour_ticks_total", "Tracer runs completed", ["outcome"])
ALERTS = Counter("goldenhour_alerts_total", "Alerts sent", ["outcome"])
TRAIL = Gauge("goldenhour_trail_addresses", "Addresses on the trail")
HELD = Gauge("goldenhour_held_usd", "Value still held by trail addresses, in USD")
LAST_TICK = Gauge("goldenhour_last_tick_timestamp", "Unix time of the last completed tracer run")
DOCS = ("case", "summary", "flows", "feed", "graph", "golden-hour", "detector", "guard", "meta")
ALERT_MEMORY = 20_000  # announced movement ids kept; the feed holds far fewer


@dataclass(slots=True)
class Settings:
    state: Path = Path("state")
    web: Path | None = None  # the exported web app, served at the root when present
    case: str = "binance-2026-10"
    interval_s: int = 300
    notify_config: Path | None = None
    run_worker: bool = True

    @property
    def api_dir(self) -> Path:
        # The API is data: it lives with the rest of the state, on the writable volume.
        return self.state / "api" / "v1"


@dataclass(slots=True)
class Context:
    settings: Settings
    config: notify.Config = field(default_factory=notify.Config)
    seen_alerts: set[str] = field(default_factory=set)
    alerts_known: bool = False  # whether seen_alerts came from an earlier run
    feed_version: int = 0
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None

    def doc(self, name: str) -> Any:
        path = self.settings.api_dir / f"{name}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())

    @property
    def alerts_file(self) -> Path:
        return self.settings.state / "alerts-sent.json"

    def load_alerts(self) -> None:
        """Which movements were already announced, kept across restarts. Without the file this is a
        new install, and the first run learns what already happened instead of announcing it."""
        try:
            self.seen_alerts = set(json.loads(self.alerts_file.read_text()))
            self.alerts_known = True
        except (OSError, ValueError):
            self.seen_alerts, self.alerts_known = set(), False

    def save_alerts(self) -> None:
        tmp = self.alerts_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(sorted(self.seen_alerts)[-ALERT_MEMORY:]))
        os.replace(tmp, self.alerts_file)

    async def tick(self) -> None:
        """One tracer run, a republish, and alerts for movements not announced before.

        The run happens on its own thread with its own event loop: judging and publishing are
        CPU work, and on the server's loop they would stall health checks and the API meanwhile.
        """
        from goldenhour.cli import publish_once, trace_once

        budget = max(60, self.settings.interval_s - 60)
        await asyncio.to_thread(lambda: asyncio.run(trace_once(self.settings.case, self.settings.state, budget=budget)))
        await asyncio.to_thread(publish_once, self.settings.case, self.settings.state, self.settings.api_dir)
        summary = self.doc("summary") or {}
        TRAIL.set(float((summary.get("trail") or {}).get("addresses") or 0))
        HELD.set(float(((summary.get("now") or {}).get("held") or {}).get("usd") or 0))
        feed = self.doc("feed") or {}
        fresh = [notify.Alert.from_feed(i) for i in feed.get("items", [])]
        fresh = [a for a in fresh if a.id not in self.seen_alerts]
        self.seen_alerts.update(a.id for a in fresh)
        if fresh and self.alerts_known:  # a new install only learns what already happened
            for outcome in await notify.dispatch(self.config, fresh):
                ALERTS.labels("ok" if outcome.ok else "failed").inc()
        self.alerts_known = True
        await asyncio.to_thread(self.save_alerts)
        self.feed_version += 1
        self.changed.set()
        self.changed = asyncio.Event()

    async def loop(self) -> None:
        while True:
            try:
                await self.tick()
                TICKS.labels("ok").inc()
                LAST_TICK.set_to_current_time()
            except Exception:
                TICKS.labels("failed").inc()
                log.exception("tracer run failed")
            await asyncio.sleep(self.settings.interval_s)


def create_app(settings: Settings) -> FastAPI:
    ctx = Context(settings, notify.Config.load(settings.notify_config))
    ctx.load_alerts()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if settings.run_worker:
            ctx.task = asyncio.create_task(ctx.loop())
        try:
            yield
        finally:
            if ctx.task:
                ctx.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await ctx.task

    app = FastAPI(
        title="Goldenhour", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.ctx = ctx
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "HEAD", "POST"],
        allow_headers=["Content-Type", "Last-Event-ID"],
    )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> Response:
        ready = (settings.api_dir / "summary.json").exists()
        return JSONResponse({"ready": ready}, status_code=200 if ready else 503)

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/api/v1/{name}.json")
    def document(name: str) -> Response:
        if name not in DOCS:
            raise HTTPException(404)
        path = settings.api_dir / f"{name}.json"
        if not path.exists():
            raise HTTPException(404, "not published yet")
        return FileResponse(path, media_type="application/json", headers={"Cache-Control": "public, max-age=10"})

    @app.get("/api/v1/screen/{shard}.json")
    def shard(shard: str) -> Response:
        if len(shard) != 3 or any(c not in "0123456789abcdef" for c in shard):
            raise HTTPException(404)
        path = settings.api_dir / "screen" / f"{shard}.json"
        if not path.exists():
            return JSONResponse({"v": 2, "entries": {}})
        return FileResponse(path, media_type="application/json")

    @app.get("/v1/screen/{chain}/{address}")
    def screen(chain: str, address: str) -> dict[str, Any]:
        """Direct screening for integrations that run inside your own network."""
        try:
            nodes = candidates(chain, address)
        except (InvalidAddress, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        path = settings.api_dir / "screen" / f"{shard_of(address_key(address))}.json"
        matches = lookup(json.loads(path.read_text()), address, nodes) if path.exists() else []
        return {
            "checked": nodes,
            "found": bool(matches),
            "matches": matches,
            "note": "A miss means the address is not on this trail. It does not mean the address is clean.",
        }

    @app.get("/v1/cases/{case_id}/summary")
    def case_summary(case_id: str) -> Any:
        if case_id != settings.case:
            raise HTTPException(404, "unknown case")
        doc = ctx.doc("summary")
        if doc is None:
            raise HTTPException(503, "not published yet")
        return doc

    @app.post("/v1/guard/evaluate")
    async def guard_evaluate(request: Request) -> Any:
        try:
            from goldenhour.guard.service import evaluate_json
        except ImportError as exc:
            raise HTTPException(501, "the Guard is not installed in this build") from exc
        body = await request.json()
        return evaluate_json(body)

    @app.get("/v1/stream")
    async def stream(request: Request) -> StreamingResponse:
        """Server-sent events: one `movement` event per new feed item, as the worker finds them."""

        async def events() -> AsyncIterator[bytes]:
            sent: set[str] = set()
            yield b"retry: 5000\n\n"
            while not await request.is_disconnected():
                for item in reversed((ctx.doc("feed") or {}).get("items", [])[:50]):
                    alert_id = f"{item.get('chain')}:{item.get('tx')}:{item.get('kind')}"
                    if alert_id in sent:
                        continue
                    sent.add(alert_id)
                    data = json.dumps(item, separators=(",", ":"))
                    yield f"id: {alert_id}\nevent: movement\ndata: {data}\n\n".encode()
                waiter = ctx.changed
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(waiter.wait(), timeout=15)
                yield b": keepalive\n\n"

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    web = settings.web
    if web is not None and web.exists():
        root = web.resolve()

        @app.get("/{path:path}")
        def static(path: str) -> Response:
            target = (root / path).resolve()
            if not target.is_relative_to(root):  # a prefix check would let "/web2/..." through
                raise HTTPException(404)
            if target.is_dir():
                target = target / "index.html"
            if not target.exists():
                target = root / "404.html"
                if not target.exists():
                    raise HTTPException(404)
            return FileResponse(target)

    return app
