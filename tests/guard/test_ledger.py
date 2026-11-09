"""Both ledgers behave the same: intents are single use, spending is counted by window."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from goldenhour.guard.ledger import Intent, Ledger, MemoryLedger, Spend, SqliteLedger

from .helpers import HOT, NOW, TREASURY, USDC, intent


@pytest.fixture(params=["memory", "sqlite"])
def ledger(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Ledger]:
    if request.param == "memory":
        yield MemoryLedger()
        return
    store = SqliteLedger(tmp_path / "ledger.db")
    yield store
    store.close()


def test_intents_are_stored_as_written(ledger: Ledger) -> None:
    written = intent(asset=USDC, amount=2**200, note="large")
    ledger.add_intent(written)
    assert ledger.intent("wd-1") == written
    assert ledger.intent("missing") is None
    with pytest.raises(ValueError, match="already exists"):
        ledger.add_intent(intent())
    with pytest.raises(ValueError, match="already used"):
        ledger.add_intent(intent(id="wd-2", used_at=NOW))


def test_an_intent_is_consumed_exactly_once(ledger: Ledger) -> None:
    ledger.add_intent(intent())
    assert ledger.commit(intent_id="wd-1", digest="0xaa", spends=[Spend(HOT, Decimal(5))], at=NOW)
    used = ledger.intent("wd-1")
    assert used is not None and (used.used_at, used.used_by) == (NOW, "0xaa")
    assert not ledger.commit(intent_id="wd-1", digest="0xbb", spends=[Spend(HOT, Decimal(7))], at=NOW)
    assert not ledger.commit(intent_id="missing", digest="0xcc", spends=[Spend(HOT, Decimal(9))], at=NOW)
    # The refused commits recorded nothing.
    assert ledger.spent_usd(HOT, NOW - timedelta(hours=1), NOW) == Decimal(5)


def test_spending_is_counted_per_wallet_inside_the_window(ledger: Ledger) -> None:
    ledger.commit(
        intent_id=None, digest="0x1", spends=[Spend(HOT, Decimal("1.5")), Spend(TREASURY, Decimal(100))], at=NOW
    )
    ledger.commit(intent_id=None, digest="0x2", spends=[Spend(HOT, Decimal(10))], at=NOW - timedelta(hours=24))
    ledger.commit(intent_id=None, digest="0x3", spends=[Spend(HOT, Decimal(20))], at=NOW + timedelta(seconds=1))
    since = NOW - timedelta(hours=24)
    assert ledger.spent_usd(HOT, since, NOW) == Decimal("1.5")  # the window excludes its start
    assert ledger.spent_usd(HOT, since - timedelta(seconds=1), NOW) == Decimal("11.5")
    assert ledger.spent_usd(TREASURY, since, NOW) == Decimal(100)


def test_sqlite_ledger_persists(tmp_path: Path) -> None:
    path = tmp_path / "ledger.db"
    first = SqliteLedger(path)
    first.add_intent(intent())
    first.commit(intent_id="wd-1", digest="0xaa", spends=[Spend(HOT, Decimal(3))], at=NOW)
    first.close()
    second = SqliteLedger(path)
    stored = second.intent("wd-1")
    assert stored is not None and stored.used_by == "0xaa"
    assert second.spent_usd(HOT, NOW - timedelta(days=1), NOW) == Decimal(3)
    second.close()


def test_intent_validation() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        Intent.model_validate({**intent().model_dump(), "expires_at": "2026-11-01T12:00:00"})
    with pytest.raises(ValidationError, match="not an address"):
        intent(asset="USDC")


def test_a_commit_that_would_break_a_velocity_limit_is_refused(ledger: Ledger) -> None:
    ledger.add_intent(intent())
    since = NOW - timedelta(hours=24)
    assert ledger.commit(
        intent_id=None, digest="0x1", spends=[Spend(HOT, Decimal(60_000), Decimal(100_000), since)], at=NOW
    )
    second = Spend(HOT, Decimal(60_000), Decimal(100_000), since)
    assert not ledger.commit(intent_id="wd-1", digest="0x2", spends=[second], at=NOW)
    # Nothing from the refused commit was kept: the intent is unused and the total is unchanged.
    stored = ledger.intent("wd-1")
    assert stored is not None and stored.used_at is None
    assert ledger.spent_usd(HOT, since, NOW) == Decimal(60_000)
    # Without a limit, as after a quorum released a velocity hold, the same spend is recorded.
    assert ledger.commit(intent_id="wd-1", digest="0x3", spends=[Spend(HOT, Decimal(60_000))], at=NOW)
