"""The drain detector's rules, and its replay report."""

from conftest import T0, eth, transfer
from goldenhour.detect_eval import WalletData, evaluate
from goldenhour.models import Kind
from goldenhour.watch import calibrate, detect, drained_before, first_alert

WALLET = "ethereum:0x" + "1" * 40
DAY = 86_400


def normal_day(start: int, n: int = 20) -> list:
    # Withdrawals of 10 ETH (about $24,000) to returning customers, sent by the wallet itself.
    return [
        (
            transfer(
                f"n{start}-{i}", WALLET, f"ethereum:0x{i % 7:040x}", eth(10), ts=start + i * 600, tx=f"t{start}{i}"
            ),
            24_000.0,
        )
        for i in range(n)
    ]


def test_delegated_code_outflow_fires_on_the_first_internal_transfer() -> None:
    history = normal_day(T0 - 3 * DAY)
    cal = calibrate(history)
    drain = [
        (transfer("d1", WALLET, "ethereum:0x" + "f" * 40, eth(5), kind=Kind.INTERNAL, ts=T0 + 100, tx="d1"), 12_000.0)
    ]
    alerts = detect(WALLET, history + drain, cal, own_txs={t.tx for t, _ in history})
    assert [a.rule for a in alerts] == ["delegated-code-outflow"]
    assert alerts[0].ts == T0 + 100


def test_quiet_history_raises_nothing_and_a_burst_raises_one_alert() -> None:
    history = normal_day(T0 - 3 * DAY)
    cal = calibrate(history)
    assert detect(WALLET, normal_day(T0 - DAY), cal, own_txs={f"t{T0 - DAY}{i}" for i in range(20)}) == []
    burst = [
        (transfer(f"b{i}", WALLET, "ethereum:0x" + "e" * 40, eth(2000), ts=T0 + i * 30, tx=f"b{i}"), 4_800_000.0)
        for i in range(5)
    ]
    alerts = detect(WALLET, burst, cal, own_txs={f"b{i}" for i in range(5)})
    rules = [a.rule for a in alerts]
    assert rules.count("outflow-burst") == 1  # re-arms only after an hour of quiet
    assert "outsized-transfer" in rules  # largest ever, to an address never paid before


def test_tokens_moved_in_someone_elses_transaction_are_flagged() -> None:
    cal = calibrate(normal_day(T0 - 3 * DAY))
    foreign = [
        (
            transfer(
                "f1",
                WALLET,
                "ethereum:0x" + "d" * 40,
                10**12,
                asset="ethereum:0x" + "a" * 40,
                kind=Kind.TOKEN,
                ts=T0,
                tx="sent-by-attacker",
            ),
            1_000_000.0,
        )
    ]
    alerts = detect(WALLET, foreign, cal, own_txs={"some-other-tx"})
    assert "foreign-token-outflow" in [a.rule for a in alerts]


def test_drained_before_and_first_alert() -> None:
    theft = [(T0, 100.0), (T0 + 60, 50.0), (T0 + 120, 25.0)]
    assert drained_before(theft, T0 + 61) == 150.0
    cal = calibrate([])
    alerts = detect(
        WALLET,
        [(transfer("x", WALLET, "ethereum:0x" + "c" * 40, 1, kind=Kind.INTERNAL, ts=T0 + 30), 1.0)],
        cal,
        own_txs=set(),
    )
    assert first_alert(alerts, T0).ts == T0 + 30


def test_replay_report_splits_calibration_and_held_out_days() -> None:
    days = 6
    first_theft = T0
    history = []
    for d in range(days, 0, -1):
        history += normal_day(first_theft - d * DAY)
    drain = (
        transfer("d1", WALLET, "ethereum:0x" + "f" * 40, eth(50_000), kind=Kind.INTERNAL, ts=first_theft + 60, tx="d1"),
        120_000_000.0,
    )
    wallet = WalletData(WALLET, "Victim 1", [*history, drain], own_txs={t.tx for t, _ in history})
    report = evaluate(
        [wallet],
        {WALLET: [(first_theft + 60, 120_000_000.0)]},
        {WALLET: {"ethereum:0x" + "f" * 40}},
        first_theft,
        days=days,
    )
    row = report["replay"][0]
    assert row["rule"] == "delegated-code-outflow" and row["minutes_after_first_theft"] == 0.0
    assert row["usd_before_alert"] == 0.0
    assert report["baseline"]["days"] == 3 and report["baseline"]["alerts"] == 0
    assert {r["id"] for r in report["rules"]} >= {"delegated-code-outflow", "outflow-burst"}
