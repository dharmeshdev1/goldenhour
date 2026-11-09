"""The trail rule: who joins, who is a service, who is only a counterparty."""

from conftest import BTC, T0, USDT, eth, transfer
from goldenhour.models import Chain, Kind
from goldenhour.trail import Graph, Policy, Trail

SEED = "ethereum:0x" + "a" * 40
FRESH = "ethereum:0x" + "b" * 40
BUSY = "ethereum:0x" + "c" * 40
DEPOSIT = "ethereum:0x" + "d" * 40
EXCHANGE = "ethereum:0x" + "e" * 40


def make(prices, tokens, transfers, roles) -> Trail:  # type: ignore[no-untyped-def]
    g = Graph(prices, tokens)
    g.add(transfers)
    return Trail(g, {n: {"node": n, **r} for n, r in roles.items()}, Policy(start_ts=T0, min_usd=1000))


def test_fresh_address_funded_by_the_trail_is_a_candidate_and_qualifies(prices, tokens) -> None:  # type: ignore[no-untyped-def]
    trail = make(prices, tokens, [transfer("t1", SEED, FRESH, eth(10))], {SEED: {"role": "seed", "hop": 0}})
    [c] = trail.candidates()
    assert c.node == FRESH and round(c.usd) == 24_000 and c.parent == SEED
    from_trail, total = trail.inflow(FRESH)
    assert from_trail == total


def test_busy_address_with_its_own_history_does_not_qualify(prices, tokens) -> None:  # type: ignore[no-untyped-def]
    other = "ethereum:0x" + "9" * 40
    trail = make(
        prices,
        tokens,
        [transfer("t1", SEED, BUSY, eth(1)), transfer("t0", other, BUSY, eth(50), ts=T0 - 86400)],
        {SEED: {"role": "seed", "hop": 0}},
    )
    from_trail, total = trail.inflow(BUSY)
    assert from_trail / total < 0.5


def test_dust_and_history_before_the_theft_are_not_followed(prices, tokens) -> None:  # type: ignore[no-untyped-def]
    trail = make(
        prices,
        tokens,
        [transfer("t1", SEED, FRESH, eth(0.0001)), transfer("t2", SEED, BUSY, eth(10), ts=T0 - 60)],
        {SEED: {"role": "seed", "hop": 0}},
    )
    assert trail.candidates() == []


def test_spam_tokens_are_worth_nothing(prices, tokens) -> None:  # type: ignore[no-untyped-def]
    fake = "ethereum:0x" + "f" * 40  # a token nobody prices, whatever it calls itself
    trail = make(
        prices,
        tokens,
        [transfer("t1", SEED, FRESH, 10**30, asset=fake, kind=Kind.TOKEN)],
        {SEED: {"role": "seed", "hop": 0}},
    )
    assert trail.candidates() == []


def test_exchange_deposit_address_is_recognized_by_where_it_sweeps(prices, tokens) -> None:  # type: ignore[no-untyped-def]
    trail = make(
        prices,
        tokens,
        [
            transfer("t1", SEED, DEPOSIT, 5_000_000 * 10**6, asset=USDT, kind=Kind.TOKEN),
            transfer("t2", DEPOSIT, EXCHANGE, 5_000_000 * 10**6, asset=USDT, kind=Kind.TOKEN),
        ],
        {
            SEED: {"role": "seed", "hop": 0},
            EXCHANGE: {"role": "service", "category": "exchange", "label": "Some Exchange"},
        },
    )
    assert trail.sweeps_to(DEPOSIT, trail.roles) == "Some Exchange"


def test_bitcoin_outputs_carry_the_share_of_inputs_from_the_trail(prices, tokens) -> None:  # type: ignore[no-untyped-def]
    seed = "bitcoin:bc1qseed000000000000000000000000000000000"
    other = "bitcoin:bc1qother00000000000000000000000000000000"
    out_a = "bitcoin:bc1qouta000000000000000000000000000000000"
    hub = "bitcoin:@tx:ff"
    ts = T0 + 7200
    transfers = [
        transfer("i0", seed, hub, 3 * 10**8, asset=BTC, kind=Kind.UTXO, chain=Chain.BITCOIN, tx="ff", ts=ts),
        transfer("i1", other, hub, 1 * 10**8, asset=BTC, kind=Kind.UTXO, chain=Chain.BITCOIN, tx="ff", ts=ts),
        transfer("o0", hub, out_a, 4 * 10**8, asset=BTC, kind=Kind.UTXO, chain=Chain.BITCOIN, tx="ff", ts=ts),
    ]
    trail = make(prices, tokens, transfers, {seed: {"role": "seed", "hop": 0}})
    assert trail.fraction(hub) == 0.75
    [c] = trail.candidates()
    assert c.node == out_a and round(c.usd) == round(4 * 75_000 * 0.75)


def test_upstream_finds_who_paid_the_seed_inside_the_window_only(prices, tokens) -> None:  # type: ignore[no-untyped-def]
    first_hop = "ethereum:0x" + "1" * 40
    early = "ethereum:0x" + "2" * 40
    trail = make(
        prices,
        tokens,
        [
            transfer("u1", first_hop, SEED, eth(100), ts=T0 + 1800),
            transfer("u2", early, SEED, eth(100), ts=T0 - 86_400),
        ],
        {SEED: {"role": "seed", "hop": 0}},
    )
    [c] = trail.upstream(T0 + 6 * 3600)
    assert c.node == first_hop


def test_swap_output_does_not_dilute_a_fresh_launderer(prices, tokens) -> None:  # type: ignore[no-untyped-def]
    pool = "ethereum:0x" + "7" * 40
    transfers = [
        transfer("in", SEED, FRESH, 1_000_000 * 10**6, asset=USDT, kind=Kind.TOKEN),
        # swap: the address pays the pool USDT and gets ETH back in the same transaction
        transfer("pay", FRESH, pool, 1_000_000 * 10**6, asset=USDT, kind=Kind.TOKEN, tx="swap", ts=T0 + 7200),
        transfer("get", pool, FRESH, eth(400), kind=Kind.INTERNAL, tx="swap", ts=T0 + 7200),
    ]
    trail = make(prices, tokens, transfers, {SEED: {"role": "seed", "hop": 0}})
    from_trail, total = trail.inflow(FRESH)
    assert total > 0 and from_trail / total == 1.0


def test_outside_money_is_never_excused_as_a_return(prices, tokens) -> None:  # type: ignore[no-untyped-def]
    # The review's framing case: a new address withdraws $50,000 from an exchange (a service), then
    # receives $1,000 from the trail. It must not join the trail.
    exchange = "ethereum:0x" + "e" * 40
    new = "ethereum:0x" + "a1" * 20
    transfers = [
        transfer("w", exchange, new, eth(50_000 / 2400), ts=T0 + 30 * 86_400, kind=Kind.INTERNAL),
        transfer("t", SEED, new, eth(1_000 / 2400), ts=T0 + 31 * 86_400),
    ]
    trail = make(
        prices,
        tokens,
        transfers,
        {SEED: {"role": "seed", "hop": 0}, exchange: {"role": "service", "category": "exchange"}},
    )
    from_trail, total = trail.inflow(new)
    assert from_trail / total < 0.05


def test_a_withdrawal_of_its_own_deposit_is_neutral_but_only_up_to_the_deposit(prices, tokens) -> None:  # type: ignore[no-untyped-def]
    pool = "ethereum:0x" + "b2" * 20
    transfers = [
        transfer("in", SEED, FRESH, eth(100)),
        transfer("dep", FRESH, pool, eth(100), ts=T0 + 7200, tx="dep"),
        transfer("back", pool, FRESH, eth(130), ts=T0 + 9000, tx="back", kind=Kind.INTERNAL),
    ]
    trail = make(prices, tokens, transfers, {SEED: {"role": "seed", "hop": 0}})
    from_trail, total = trail.inflow(FRESH)
    # 100 ETH from the trail; of the 130 that came back, 100 is its own deposit, 30 is outside money.
    assert round(from_trail / total, 3) == round(100 / 130, 3)


def test_a_bitcoin_transaction_with_two_trail_inputs_is_credited_once(prices, tokens) -> None:  # type: ignore[no-untyped-def]
    a, b = "bitcoin:bc1qaaaa00000000000000000000000000000000", "bitcoin:bc1qbbbb00000000000000000000000000000000"
    out = "bitcoin:bc1qcccc00000000000000000000000000000000"
    hub, ts = "bitcoin:@tx:dd", T0 + 7200
    transfers = [
        transfer("i0", a, hub, 2 * 10**8, asset=BTC, kind=Kind.UTXO, chain=Chain.BITCOIN, tx="dd", ts=ts),
        transfer("i1", b, hub, 2 * 10**8, asset=BTC, kind=Kind.UTXO, chain=Chain.BITCOIN, tx="dd", ts=ts),
        transfer("o0", hub, out, 4 * 10**8, asset=BTC, kind=Kind.UTXO, chain=Chain.BITCOIN, tx="dd", ts=ts),
    ]
    trail = make(prices, tokens, transfers, {a: {"role": "seed", "hop": 0}, b: {"role": "seed", "hop": 0}})
    [c] = trail.candidates()
    assert round(c.usd) == 4 * 75_000  # once, not twice
