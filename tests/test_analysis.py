"""Ledger balances, freezes, exits and the golden hour, on a small invented theft."""

from conftest import T0, USDT, eth, transfer
from goldenhour.analysis import Analysis, is_coinjoin
from goldenhour.case import Case
from goldenhour.freeze import FreezeEvent
from goldenhour.models import Chain, Kind
from goldenhour.trail import Graph, Policy, Trail

VICTIM = "ethereum:0x" + "1" * 40
HOP = "ethereum:0x" + "2" * 40
HOP2 = "ethereum:0x" + "3" * 40
POOL = "ethereum:0x" + "4" * 40
EXCHANGE = "ethereum:0x" + "5" * 40


def case() -> Case:
    return Case(id="t", title="test", victim="Victim", attribution="", start_ts=T0, end_ts=T0 + 4 * 3600)


def build(prices, tokens, freezes=()):  # type: ignore[no-untyped-def]
    transfers = [
        # minute 0: 100 ETH and 1,000,000 USDT leave the victim for the first hop
        transfer("a", VICTIM, HOP, eth(100), ts=T0 + 60),
        transfer("b", VICTIM, HOP, 1_000_000 * 10**6, asset=USDT, kind=Kind.TOKEN, ts=T0 + 60),
        # minute 10: 400,000 USDT is swapped in a pool for ETH, in one transaction
        transfer("c", HOP, POOL, 400_000 * 10**6, asset=USDT, kind=Kind.TOKEN, ts=T0 + 660, tx="swap"),
        transfer("d", POOL, HOP, eth(160), kind=Kind.INTERNAL, ts=T0 + 660, tx="swap"),
        # minute 20: 50 ETH moves to a second hop, which sends 10 ETH to an exchange
        transfer("e", HOP, HOP2, eth(50), ts=T0 + 1260),
        transfer("f", HOP2, EXCHANGE, eth(10), ts=T0 + 2000),
    ]
    g = Graph(prices, tokens)
    g.add(transfers)
    roles = {
        VICTIM: {"node": VICTIM, "role": "victim"},
        HOP: {"node": HOP, "role": "trail", "hop": 0},
        HOP2: {"node": HOP2, "role": "trail", "hop": 1, "parent": HOP},
        POOL: {"node": POOL, "role": "service", "category": "dex", "label": "Pool"},
        EXCHANGE: {
            "node": EXCHANGE,
            "role": "service",
            "category": "exchange",
            "label": "Exchange X",
            "info": {"entity": "Exchange X"},
        },
    }
    trail = Trail(g, roles, Policy(start_ts=T0))
    return Analysis(case(), g, trail, list(freezes), now=T0 + 86_400)


def test_ledger_balances_follow_every_transfer(prices, tokens) -> None:  # type: ignore[no-untyped-def]
    a = build(prices, tokens)
    assert a.balances(HOP) == {"ethereum:native": eth(210), USDT: 600_000 * 10**6}
    assert a.balances(HOP2) == {"ethereum:native": eth(40)}


def test_theft_counts_only_victim_to_trail_inside_the_window(prices, tokens) -> None:  # type: ignore[no-untyped-def]
    a = build(prices, tokens)
    theft = a.theft()
    assert len(theft) == 2
    assert round(sum(v for _, v in theft)) == 100 * 2400 + 1_000_000


def test_swaps_are_not_exits_but_exchange_deposits_are(prices, tokens) -> None:  # type: ignore[no-untyped-def]
    a = build(prices, tokens)
    exits = a.exits()
    assert [c["category"] for c in exits["by_category"]] == ["exchange"]
    assert round(exits["usd"]) == 10 * 2400
    assert exits["by_category"][0]["entities"][0]["name"] == "Exchange X"


def test_frozen_balance_is_reported_and_removed_from_holdings(prices, tokens) -> None:  # type: ignore[no-untyped-def]
    freeze = FreezeEvent("Tether", Chain.ETHEREUM, "USDT", HOP, "0xf", 1, T0 + 37 * 60, "freeze")
    a = build(prices, tokens, [freeze])
    assert round(a.frozen()["usd"]) == 600_000
    held = a.holdings()
    assert round(held["usd"]) == (210 + 40) * 2400


def test_an_unfreeze_ends_the_freeze(prices, tokens) -> None:  # type: ignore[no-untyped-def]
    freeze = FreezeEvent("Tether", Chain.ETHEREUM, "USDT", HOP, "0xf", 1, T0 + 60 + 37 * 60, "freeze")
    lift = FreezeEvent("Tether", Chain.ETHEREUM, "USDT", HOP, "0xu", 2, T0 + 60 + 50 * 60, "unfreeze")
    a = build(prices, tokens, [lift, freeze])  # the order events arrive in does not matter
    assert not a.is_frozen(HOP, USDT)
    assert a.frozen()["usd"] == 0
    assert round(a.holdings()["usd"]) == (210 + 40) * 2400 + 600_000
    gh = a.golden_hour()
    assert gh["frozen_usd"][40] == 600_000  # frozen from minute 37
    assert gh["frozen_usd"][55] == 0 and gh["freezable_usd"][55] == 600_000  # free again from minute 50
    again = FreezeEvent("Tether", Chain.ETHEREUM, "USDT", HOP, "0xg", 3, T0 + 3 * 3600, "freeze")
    assert build(prices, tokens, [freeze, lift, again]).is_frozen(HOP, USDT)


def test_golden_hour_curves(prices, tokens) -> None:  # type: ignore[no-untyped-def]
    freeze = FreezeEvent("Tether", Chain.ETHEREUM, "USDT", HOP, "0xf", 1, T0 + 60 + 37 * 60, "freeze")
    gh = build(prices, tokens, [freeze]).golden_hour()
    assert gh["drained_usd"][0] == 1_240_000
    assert gh["freezable_usd"][5] == 1_000_000  # nothing swapped yet
    assert gh["freezable_usd"][15] == 600_000  # 400,000 swapped at minute 10
    assert gh["escaped_usd"][15] == 400_000
    assert gh["freezable_usd"][40] == 0 and gh["frozen_usd"][40] == 600_000
    five = next(s for s in gh["scenarios"] if s["response_minutes"] == 5)
    # A continuous response from minute 5 would have frozen the 400,000 USDT swapped at minute 10.
    assert five["freezable_saved_usd"] == 400_000
    kinds = [e["kind"] for e in gh["events"]]
    assert "freeze" in kinds and "revoke" in kinds


def test_coinjoin_shape() -> None:
    out = transfer(
        "x",
        "bitcoin:@tx:1",
        "bitcoin:bc1qa",
        1,
        chain=Chain.BITCOIN,
        kind=Kind.UTXO,
        meta={"n_in": "60", "n_out": "80", "equal_out": "30"},
    )
    assert is_coinjoin([out])
    small = transfer(
        "y",
        "bitcoin:@tx:2",
        "bitcoin:bc1qa",
        1,
        chain=Chain.BITCOIN,
        kind=Kind.UTXO,
        meta={"n_in": "1", "n_out": "2", "equal_out": "1"},
    )
    assert not is_coinjoin([small])


def test_airdropped_spam_does_not_count_as_held(prices, tokens) -> None:  # type: ignore[no-untyped-def]
    from goldenhour.prices import Quote

    spam = "ethereum:0x" + "ab" * 20
    prices.quotes[f"ethereum:{spam.partition(':')[2]}"] = Quote(1000.0, 18, "ETH", 0.99)  # a feed that prices it
    a = build(prices, tokens)
    a.graph.add([transfer("spam", "ethereum:0x" + "9" * 40, HOP, 10**24, asset=spam, kind=Kind.TOKEN)])
    a._balances.clear()
    assert a.value(spam, 10**24) == 0.0
    assert round(a.holdings()["usd"]) == (210 + 40) * 2400 + 600_000


def test_bitcoin_exits_are_judged_per_output(prices, tokens) -> None:  # type: ignore[no-untyped-def]
    from conftest import BTC

    seed = "bitcoin:bc1qseed000000000000000000000000000000000"
    change = "bitcoin:bc1qchng000000000000000000000000000000000"
    deposit = "bitcoin:bc1qdpst000000000000000000000000000000000"
    hub, ts = "bitcoin:@tx:ee", T0 + 7200
    transfers = [
        transfer("i0", seed, hub, 100 * 10**8, asset=BTC, kind=Kind.UTXO, chain=Chain.BITCOIN, tx="ee", ts=ts),
        transfer("o0", hub, change, 90 * 10**8, asset=BTC, kind=Kind.UTXO, chain=Chain.BITCOIN, tx="ee", ts=ts),
        transfer("o1", hub, deposit, 10 * 10**8, asset=BTC, kind=Kind.UTXO, chain=Chain.BITCOIN, tx="ee", ts=ts),
    ]
    g = Graph(prices, tokens)
    g.add(transfers)
    roles = {
        seed: {"node": seed, "role": "seed", "hop": 0},
        change: {"node": change, "role": "trail", "hop": 1},
        deposit: {"node": deposit, "role": "service", "category": "exchange-deposit", "label": "X"},
    }
    a = Analysis(case(), g, Trail(g, roles, Policy(start_ts=T0)), [], now=T0 + 86_400)
    [(o, usd, bucket)] = a.net_outflows()
    assert o.receiver == deposit and round(usd) == 10 * 75_000 and bucket == "exchange"
