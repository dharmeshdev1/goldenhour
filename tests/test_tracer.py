"""The tracer's whole loop on a small invented theft, with fake sources instead of the network."""

from pathlib import Path

import pytest

from conftest import T0, USDT, eth, transfer
from goldenhour.case import Case
from goldenhour.identify import Identity
from goldenhour.models import Chain, Kind, Page, Transfer
from goldenhour.store import Store
from goldenhour.tracer import Tracer
from goldenhour.trail import Policy

VICTIM = "ethereum:0x" + "1" * 40
FIRST_HOP = "ethereum:0x" + "2" * 40
SEED = "ethereum:0x" + "3" * 40
FRESH = "ethereum:0x" + "4" * 40
DEPOSIT = "ethereum:0x" + "5" * 40
EXCHANGE_HOT = "ethereum:0x" + "6" * 40
BYSTANDER = "ethereum:0x" + "7" * 40
OLD_FRIEND = "ethereum:0x" + "8" * 40

HISTORY = {
    VICTIM: [],
    FIRST_HOP: [
        transfer("v1", VICTIM, FIRST_HOP, eth(1000), ts=T0 + 3700),
        transfer("h1", FIRST_HOP, SEED, eth(999), ts=T0 + 4000),
    ],
    SEED: [
        transfer("h1", FIRST_HOP, SEED, eth(999), ts=T0 + 4000),
        transfer("s1", SEED, FRESH, eth(600), ts=T0 + 5000),
        transfer("s2", SEED, DEPOSIT, eth(300), ts=T0 + 5100),
        transfer("s3", SEED, BYSTANDER, eth(5), ts=T0 + 5200),
    ],
    FRESH: [transfer("s1", SEED, FRESH, eth(600), ts=T0 + 5000)],
    DEPOSIT: [
        transfer("s2", SEED, DEPOSIT, eth(300), ts=T0 + 5100),
        transfer("d1", DEPOSIT, EXCHANGE_HOT, eth(299), ts=T0 + 5400),
    ],
    BYSTANDER: [
        transfer("b0", OLD_FRIEND, BYSTANDER, eth(500), ts=T0 - 400 * 86_400),
        transfer("s3", SEED, BYSTANDER, eth(5), ts=T0 + 5200),
    ],
}
IDENTITIES = {
    VICTIM: Identity(
        VICTIM, is_contract=False, name="Victim Exchange 14", tags=["Victim Exchange"], category="exchange"
    ),
}


@pytest.fixture
def tracer(tmp_path: Path, prices) -> Tracer:  # type: ignore[no-untyped-def]
    store = Store.at(tmp_path / "t.db")
    case = Case(
        id="t",
        title="t",
        victim="Victim Exchange",
        attribution="",
        start_ts=T0,
        end_ts=T0 + 4 * 3600,
        seeds=[SEED],
        seed_reasons={SEED: "named by a test"},
    )
    t = Tracer(store, client=None, case=case, prices=prices, policy=Policy(start_ts=T0, min_usd=1000))  # type: ignore[arg-type]
    t.bridging = False

    async def fetch(node: str) -> Page:
        page = Page(transfers=list(HISTORY.get(node, [])), cursor={"block": 1})
        t.store.add_transfers(page.transfers)
        t.graph.add(page.transfers)
        return page

    async def identify(node: str) -> Identity:
        return IDENTITIES.get(node, Identity(node, is_contract=False))

    async def no_prices() -> None:
        return None

    async def scale(node: str) -> int | None:
        return 4_200_000 if node == EXCHANGE_HOT else 3

    t.fetch = fetch  # type: ignore[method-assign]
    t.identifier.identify = identify  # type: ignore[method-assign]
    t.refresh_prices = no_prices  # type: ignore[method-assign]
    t.identifier.scale = scale  # type: ignore[method-assign]
    return t


async def test_the_whole_loop(tracer: Tracer) -> None:
    await tracer.run(budget_s=60)
    role = {n: r["role"] for n, r in tracer.trail.roles.items()}
    assert role[SEED] == "seed"
    assert role[VICTIM] == "victim"  # recognized by its label while walking back from the seed
    assert role[FIRST_HOP] == "trail"  # took the money straight out of the victim
    assert role[FRESH] == "trail"  # all its money came from the trail
    assert role[DEPOSIT] == "service"  # swept everything into an exchange: a customer deposit address
    assert tracer.trail.roles[EXCHANGE_HOT]["role"] == "service"  # unlabeled, recognized by its scale
    assert role[BYSTANDER] == "counterparty"  # $12,000 from the trail against $1.2 million of its own
    assert "came from the trail" in tracer.trail.roles[FRESH]["reason"]


async def test_case_seeds_override_earlier_verdicts(tracer: Tracer) -> None:
    tracer.store.upsert_node(SEED, role="counterparty", reason="judged before the case named it")
    tracer.trail.roles[SEED] = {"node": SEED, **tracer.store.node(SEED)}  # type: ignore[dict-item]
    await tracer.seed()
    assert tracer.trail.roles[SEED]["role"] == "seed"
    assert tracer.trail.roles[SEED]["reason"] == "named by a test"


def test_usdt_fixture_is_priced(prices) -> None:  # type: ignore[no-untyped-def]
    assert prices.price(USDT) == 1.0
    assert Kind.TOKEN.value == "token"


def test_a_parser_change_reads_the_chain_again(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from goldenhour.store import Store
    from goldenhour.tracer import PARSERS, reparse

    store = Store.at(tmp_path / "s.db")
    sol_seed, sol_hop, xrp_seed, xrp_hub = (
        "solana:AffzFDKZbyMiU11zmVBoAqD25ugpr7xoWWyRyGigBEaU",
        "solana:24xu7h76wz75DaUkLcGrMTJzoMF6DVcNtz8FjQ5KWwni",
        "xrpl:rK21t6nGLy4MNDqhvy3pGwcLFy25qgfzMx",
        "xrpl:rEb8TK3gBgk5auZkwc6sHnwrGVJH8DuaLh",
    )
    store.upsert_node(sol_seed, role="seed", cursor={"newest": "x", "read": 450}, fetched_at=5)
    store.upsert_node(sol_hop, role="trail", cursor={"newest": "y"}, fetched_at=5)
    store.upsert_node(xrp_seed, role="seed", cursor={"ledger": 9}, fetched_at=5)
    store.upsert_node(xrp_hub, role="victim", cursor={"hub": True}, fetched_at=5)
    store.add_transfers(
        [
            Transfer("s1", Chain.SOLANA, "t", 1, 1, 0, sol_seed, sol_hop, "solana:native", 5, Kind.NATIVE),
            Transfer("x1", Chain.XRPL, "u", 1, 1, 0, xrp_hub, xrp_seed, "xrpl:native", 5, Kind.NATIVE),
        ]
    )
    assert reparse(store) == 3
    assert [t.uid for t in store.all_transfers()] == ["x1"]  # Solana's meaning changed; XRPL's did not
    assert store.node(sol_hop) is None  # judged from dropped transfers: found and judged again
    assert store.node(sol_seed)["cursor"] == {} and store.node(sol_seed)["fetched_at"] == 0  # type: ignore[index]
    assert store.node(xrp_seed)["cursor"] == {}  # type: ignore[index]
    assert store.node(xrp_hub)["cursor"] == {"hub": True}  # type: ignore[index]
    assert {str(c): store.get(f"parser:{c}") for c in PARSERS} == {str(c): v for c, (v, _) in PARSERS.items()}
    assert reparse(store) == 0  # once only


async def test_a_burn_address_is_a_service_and_never_read() -> None:
    from goldenhour.identify import Identifier

    class NoNetwork:
        def __getattr__(self, name: str) -> object:
            raise AssertionError("a burn address must not cost a request")

    ident = await Identifier(NoNetwork()).identify("ethereum:0x0000000000000000000000000000000000000000")  # type: ignore[arg-type]
    assert ident.is_service and ident.category == "burn" and ident.available


async def test_a_step_without_its_own_deadline_is_cut_at_the_run_deadline() -> None:
    import asyncio
    import time

    from goldenhour.tracer import Tracer

    tracer = Tracer.__new__(Tracer)
    tracer.deadline = time.monotonic() + 0.05

    async def slow() -> str:
        await asyncio.sleep(5)
        return "done"

    started = time.monotonic()
    assert await tracer._within_deadline(slow(), "slow step") is None
    assert time.monotonic() - started < 1
    tracer.deadline = time.monotonic() - 1  # already out of time: the step does not even start
    assert await tracer._within_deadline(slow(), "slow step") is None
    tracer.deadline = time.monotonic() + 5

    async def quick() -> str:
        return "done"

    assert await tracer._within_deadline(quick(), "quick step") == "done"
