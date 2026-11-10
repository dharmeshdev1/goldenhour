"""Command line: `goldenhour trace`, `publish`, `detect`, `screen`, `serve`, `mcp` and `guard`."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    no_args_is_help=True, add_completion=False, help="Crypto theft response, from the signer to the last hop."
)
console = Console()

DEFAULT_CASE = "binance-2026-10"

try:  # the Guard ships in the same package; mount its commands when present
    from goldenhour.guard.cli import guard_app

    app.add_typer(guard_app, name="guard", help="Evaluate signing requests against a policy.")
except ImportError:  # pragma: no cover
    pass


def _state_dir() -> Path:
    return Path(os.environ.get("GOLDENHOUR_STATE", "state"))


def _db_url(state: Path) -> str:
    return os.environ.get("GOLDENHOUR_DATABASE_URL") or f"sqlite:///{state / 'goldenhour.db'}"


# Reusable steps (the server's worker calls these too)


async def trace_once(
    case_id: str,
    state: Path,
    *,
    budget: float,
    max_judge: int = 200,
    min_usd: float = 1000.0,
    live: bool = True,
    progress: Callable | None = None,
) -> dict:
    from goldenhour.case import Case
    from goldenhour.http import PoliteClient
    from goldenhour.prices import PriceBook
    from goldenhour.store import Store
    from goldenhour.tracer import Tracer
    from goldenhour.trail import Policy

    store = Store(_db_url(state))
    c = Case.load(case_id)
    prices = PriceBook(state / "prices.json")
    async with PoliteClient() as client:
        tracer = Tracer(store, client, c, prices, Policy(start_ts=c.start_ts, min_usd=min_usd))
        started = time.monotonic()
        try:
            # The tracer stops starting work at its budget; reads already in flight may run on. This
            # is the hard stop: whatever is still in flight a little later is dropped, unstored.
            stats = await asyncio.wait_for(
                tracer.run(budget_s=budget, max_judge=max_judge, progress=progress, live=live),
                timeout=budget + HARD_STOP_GRACE_S,
            )
        except TimeoutError:
            logging.getLogger("goldenhour").warning("stopped at the hard limit; reads in flight were dropped")
            stats = {**tracer.stats, "hard_stop": 1}
        stats["requests"] = client.requests
        stats["duration_s"] = round(time.monotonic() - started, 1)
        stats["hosts"] = client.health()
        store.put(f"trace:{case_id}:last_run", {"at": int(time.time()), **stats})
        return stats


HARD_STOP_GRACE_S = 45  # seconds a trace may run past its budget before reads in flight are dropped

# Each read starts this far before its cursor: explorers index the chain head late, and events read
# twice are dropped by (tx, node, kind).
FREEZE_MARGIN_BLOCKS = 300
FREEZE_MARGIN_MS = 3_600_000


async def _freezes(store, client, case) -> list:
    """Issuer freeze events since the theft, cached in the store and read incrementally.

    Every source (each issuer on Ethereum, each Tron event name) keeps its own cursor and moves it
    only after a complete read. A source that fails keeps the cached events and its cursor, and the
    next run catches up: a publish never stops because an explorer was down.
    """
    from goldenhour import freeze
    from goldenhour.freeze import FreezeEvent
    from goldenhour.models import Chain

    log = logging.getLogger("goldenhour.freeze")
    key = f"freezes:{case.id}"
    cached = store.get(key, {}) or {}
    events = [FreezeEvent(**{**e, "chain": Chain(e["chain"])}) for e in cached.get("events", [])]
    seen = {(e.tx, e.node, e.kind) for e in events}
    # Caches from before per-source cursors had one cursor and never read unfreezes: start over.
    cursors: dict[str, int] = dict(cached.get("cursors") or {})

    def add(new: list[FreezeEvent]) -> None:
        for e in new:
            if (e.tx, e.node, e.kind) not in seen:
                seen.add((e.tx, e.node, e.kind))
                events.append(e)

    try:
        head = await freeze.head(client)
    except Exception as exc:
        head = None
        log.warning("Ethereum head unknown, freeze events on Ethereum not read this run: %s", exc)
    if head is not None:
        # Before the theft, measured back from the head at one block per 12 s slot. Missed slots
        # only push this earlier.
        theft_block = head - (int(time.time()) - case.start_ts) // 12 - 300
        for issuer in freeze.ISSUERS:
            name = f"evm:{issuer.name}"
            start = int(cursors.get(name) or theft_block)
            try:
                add(await freeze.evm_events(client, issuer, max(start - FREEZE_MARGIN_BLOCKS, 0), head))
            except Exception as exc:
                log.warning("%s freeze events not read, kept the cache: %s", issuer.name, exc)
                continue
            cursors[name] = head + 1
    for event_name in freeze.TRON_EVENTS:
        name = f"tron:{event_name}"
        since = int(cursors.get(name) or case.start_ts * 1000)
        try:
            new = await freeze.tron_events(client, event_name, since - FREEZE_MARGIN_MS)
        except Exception as exc:
            log.warning("Tron %s events not read, kept the cache: %s", event_name, exc)
            continue
        add(new)
        cursors[name] = max([since, *(e.ts * 1000 for e in new)])
    store.put(
        key,
        {
            "events": [
                {
                    "issuer": e.issuer,
                    "chain": str(e.chain),
                    "symbol": e.symbol,
                    "node": e.node,
                    "tx": e.tx,
                    "block": e.block,
                    "ts": e.ts,
                    "kind": e.kind,
                    "amount": e.amount,
                }
                for e in events
            ],
            "cursors": cursors,
        },
    )
    return events


def publish_once(case_id: str, state: Path, out: Path, *, offline: bool = False) -> dict[str, int]:
    from goldenhour import __version__
    from goldenhour.analysis import iso
    from goldenhour.case import Case
    from goldenhour.freeze import FreezeEvent
    from goldenhour.http import PoliteClient
    from goldenhour.models import Chain
    from goldenhour.prices import PriceBook
    from goldenhour.publish import Publisher
    from goldenhour.store import Store, now_ts

    store = Store(_db_url(state))
    c = Case.load(case_id)
    prices = PriceBook(state / "prices.json")

    async def load() -> list:
        async with PoliteClient() as client:
            events = await _freezes(store, client, c)
            with store.engine.connect() as conn:
                assets = sorted({row[0] for row in conn.exec_driver_sql("select distinct asset from transfers")})
            await prices.load_current(client, assets)
            prices.save()
            return events

    if offline:
        cached = cast(dict[str, Any], store.get(f"freezes:{c.id}", {}) or {})
        events = [FreezeEvent(**{**e, "chain": Chain(e["chain"])}) for e in cached.get("events", [])]
    else:
        events = asyncio.run(load())
    extra: dict[str, object] = {}
    try:
        from goldenhour.guard.cases import build_cases

        extra["guard.json"] = build_cases()
    except Exception as exc:
        logging.getLogger("goldenhour").warning("guard cases skipped: %s", exc)
    detector = store.get(f"detector:{c.id}")
    if detector:
        extra["detector.json"] = detector
    publisher = Publisher(store, c, prices, events, now_ts())
    tick = dict(cast(dict[str, Any], store.get(f"trace:{c.id}:last_run", {}) or {}))
    hosts = tick.pop("hosts", {}) or {}
    if isinstance(tick.get("at"), int):
        tick["at"] = iso(tick["at"])
    extra["meta.json"] = {
        "generated_at": iso(now_ts()),
        "version": __version__,
        "tick": tick,
        # A source is healthy when it answered and failed no more often than it answered.
        "sources_ok": {host: bool(h.get("ok")) and h.get("failed", 0) <= h.get("ok", 0) for host, h in hosts.items()},
    }
    return publisher.write(out, extra)


# Commands


@app.command()
def trace(
    case: str = typer.Option(DEFAULT_CASE, help="Case id under cases/."),
    budget: float = typer.Option(3000.0, help="Seconds to spend before stopping cleanly."),
    max_judge: int = typer.Option(400, help="Addresses to judge per pass."),
    min_usd: float = typer.Option(1000.0, help="Smallest outflow the trail follows, in USD."),
    live: bool = typer.Option(False, help="Re-read trail addresses that moved since their last read."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Follow the stolen money: fetch, judge, follow, until the trail stops growing or time runs out."""
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    def progress(stats: dict, pending: int) -> None:
        console.print(f"[dim]pass:[/] {pending} to judge, " + ", ".join(f"{k} {v}" for k, v in stats.items()))

    stats = asyncio.run(
        trace_once(
            case, _state_dir(), budget=budget, max_judge=max_judge, min_usd=min_usd, live=live, progress=progress
        )
    )
    console.print("[bold]done[/] " + ", ".join(f"{k} {v}" for k, v in stats.items()))


@app.command()
def publish(
    case: str = typer.Option(DEFAULT_CASE),
    out: Path = typer.Option(Path("site/api/v1"), help="Where the static API is written."),
    offline: bool = typer.Option(False, help="Use cached freeze events and prices only."),
) -> None:
    """Rebuild every public JSON document from the store."""
    stats = publish_once(case, _state_dir(), out, offline=offline)
    console.print(f"[bold]published[/] to {out}: " + ", ".join(f"{k} {v}" for k, v in stats.items()))


@app.command()
def detect(
    case: str = typer.Option(DEFAULT_CASE),
    days: int = typer.Option(14, help="Days of normal history before the theft: half calibrates, half tests."),
    limit: int = typer.Option(12, help="Victim wallets to replay, largest losses first."),
) -> None:
    """Replay the victim's wallets through the drain detector and store the report."""
    from collections import defaultdict

    from goldenhour.analysis import Analysis
    from goldenhour.case import Case
    from goldenhour.detect_eval import WalletData, evaluate, fetch_wallet
    from goldenhour.freeze import head
    from goldenhour.http import PoliteClient
    from goldenhour.models import Chain, Kind, split_node
    from goldenhour.prices import PriceBook
    from goldenhour.store import Store, now_ts
    from goldenhour.trail import Graph, Policy, Trail, stolen_assets
    from goldenhour.watch import outflows

    state = _state_dir()
    store = Store(_db_url(state))
    c = Case.load(case)
    prices = PriceBook(state / "prices.json")
    graph = Graph(prices, store.token_table())
    graph.add(store.all_transfers())
    roles = {r["node"]: r for r in store.nodes_by_role()}
    graph.stolen = stolen_assets(graph, roles, c.start_ts, c.end_ts + 7200)
    trail = Trail(graph, roles, Policy(start_ts=c.start_ts))
    analysis = Analysis(c, graph, trail, [], now_ts())
    theft: dict[str, list[tuple[int, float]]] = defaultdict(list)
    receivers: dict[str, set[str]] = defaultdict(set)
    for t, usd in analysis.theft():
        if t.sender.startswith("bitcoin:@tx:"):
            continue  # bitcoin wallets are not replayed: their history is too large to read for free
        theft[t.sender].append((t.ts, usd))
        receivers[t.sender].add(t.receiver)
    victims = sorted(
        (w for w in theft if split_node(w)[0] is Chain.ETHEREUM), key=lambda w: -sum(v for _, v in theft[w])
    )
    victims = victims[:limit]
    if not victims:
        console.print("[yellow]no Ethereum victim wallets on the trail yet[/]")
        raise typer.Exit(1)
    first_theft = min(ts for w in victims for ts, _ in theft[w])

    async def go() -> list[WalletData]:
        out = []
        async with PoliteClient() as client:
            top = await head(client)
            now = now_ts()
            for w in victims:
                console.print(f"reading {w} ...")
                transfers, tokens, skipped = await fetch_wallet(
                    client, prices, w, first_theft - days * 86_400, c.end_ts + 3600, top, now
                )
                unread[w] = skipped
                graph.tokens.update(tokens)
                assets = sorted({t.asset for t in transfers if graph.valued(t.asset)})
                await prices.load_history(client, assets, first_theft - days * 86_400, c.end_ts)
                prices.save()
                graph._usd.clear()
                own = {t.tx for t in transfers if t.kind is Kind.FEE and t.sender == w}
                flows = [(t, graph.usd(t)) for t in outflows(w, transfers)]
                label = (trail.roles.get(w) or {}).get("label") or c.victim
                out.append(WalletData(w, label, flows, own))
                console.print(f"  {len(flows)} outgoing transfers, {len(own)} sent by the wallet itself")
        return out

    unread: dict[str, int] = {}
    wallets = asyncio.run(go())
    report = evaluate(wallets, theft, receivers, first_theft, days=days)
    # Blocks the explorer could not serve for these wallets: skipped, and said so with the result.
    report["unread_blocks"] = sum(unread.values())
    store.put(f"detector:{c.id}", report)
    for row in report["replay"]:
        console.print(row)
    console.print(report["baseline"], report.get("learned"))


@app.command()
def screen(
    address: str = typer.Argument(..., help="The address to check."),
    chain: str = typer.Option("evm", help="Chain name, or evm for every EVM chain."),
    api: str = typer.Option("https://dharmeshdev1.github.io/goldenhour/api/v1", help="Goldenhour API to ask."),
) -> None:
    """Check an address against the trail, fetching only a 1-in-4,096 shard of the index."""
    from goldenhour.mcp import Server

    console.print_json(json.dumps(Server(api).call("screen_address", {"chain": chain, "address": address})))


@app.command("verify-site")
def verify_site(
    site: Path = typer.Argument(Path("site"), help="The directory about to be deployed."),
    live: str = typer.Option(
        "https://dharmeshdev1.github.io/goldenhour/api/v1", help="The API live now, to compare with. Empty skips."
    ),
    allow_shrink: bool = typer.Option(False, help="Deploy even if the trail shrank by more than a fifth."),
) -> None:
    """Refuse a deploy that would break the site or replace a fuller trail with a smaller one."""
    from goldenhour.deploycheck import live_summary, problems

    current = live_summary(live) if live else None
    if current is None:
        console.print("no live summary to compare with: shrink check skipped")
    found = problems(site, current, allow_shrink=allow_shrink)
    for problem in found:
        console.print(f"[red]refused:[/red] {problem}")
    if found:
        raise typer.Exit(1)
    console.print("site verified")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8080),
    case: str = typer.Option(DEFAULT_CASE),
    interval: int = typer.Option(300, help="Seconds between tracer runs."),
    web: Path | None = typer.Option(None, help="Exported web app to serve (default: /app/web in the image)."),
    notify_config: Path | None = typer.Option(None, "--notify", help="YAML file of alert channels."),
    worker: bool = typer.Option(True, help="Run the tracer in the background."),
) -> None:
    """Run the self-hosted server: API, screening, Guard, alerts and a live stream."""
    import uvicorn

    from goldenhour.server.app import Settings, create_app

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if web is None and Path("/app/web").exists():
        web = Path("/app/web")
    settings = Settings(
        state=_state_dir(), web=web, case=case, interval_s=interval, notify_config=notify_config, run_worker=worker
    )
    uvicorn.run(create_app(settings), host=host, port=port, log_level="info", proxy_headers=True)


@app.command()
def mcp(api: str = typer.Option("https://dharmeshdev1.github.io/goldenhour/api/v1")) -> None:
    """Serve the Model Context Protocol over stdio for agents."""
    from goldenhour.mcp import serve as run

    run(api)


@app.command()
def status(case: str = typer.Option(DEFAULT_CASE)) -> None:
    """How many addresses are on the trail, by chain and role."""
    from collections import Counter

    from goldenhour.store import Store

    store = Store(_db_url(_state_dir()))
    counts = Counter((r["chain"], r["role"]) for r in store.nodes_by_role())
    table = Table("chain", "role", "addresses")
    for (chain, role), n in sorted(counts.items()):
        table.add_row(chain, role, str(n))
    console.print(table)
    console.print(f"last run: {store.get(f'trace:{case}:last_run')}")


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
