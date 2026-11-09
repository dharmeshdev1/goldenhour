"""The Guard service: commits allowed requests to the ledger and logs every decision."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from goldenhour.guard import keys
from goldenhour.guard.audit import AuditLog, verify
from goldenhour.guard.engine import Guard, evaluate
from goldenhour.guard.ledger import MemoryLedger, Spend, SqliteLedger

from .helpers import ETH, HOT, NOW, VAULT, approved, call, hexed, intent, make_policy, tx

KEY = keys.from_seed(b"g" * 32)


def test_allow_consumes_the_intent_and_records_the_spending() -> None:
    ledger = MemoryLedger([intent()])
    guard = Guard(make_policy(), ledger)
    first = guard.check(tx(value=ETH, intent_id="wd-1"), NOW)
    assert first.outcome == "allow"
    stored = ledger.intent("wd-1")
    assert stored is not None and stored.used_by == first.request_digest
    assert ledger.spent_usd(HOT, NOW.replace(hour=0), NOW) == Decimal(3000)
    second = guard.check(tx(value=ETH, intent_id="wd-1"), NOW)
    assert second.outcome == "deny" and "already used" in second.findings[0].message


def test_a_hold_commits_nothing_until_a_quorum_releases_it() -> None:
    ledger = MemoryLedger([intent(amount=100 * ETH)])
    guard = Guard(make_policy(), ledger)
    request = tx(value=20 * ETH, intent_id="wd-1")  # $60,000 is above the $50,000 limit per request
    assert guard.check(request, NOW).outcome == "hold"
    assert ledger.intent("wd-1").used_at is None
    assert guard.check(approved(request, "alice", "bob"), NOW).outcome == "allow"
    assert ledger.intent("wd-1").used_at == NOW


class RacingLedger(MemoryLedger):
    """Another guard consumes the intent between this guard's evaluation and its commit."""

    def commit(self, *, intent_id: str | None, digest: str, spends: Sequence[Spend], at: datetime) -> bool:
        if intent_id is not None and self.intent(intent_id).used_at is None:
            super().commit(intent_id=intent_id, digest="0xother", spends=[], at=at)
        return super().commit(intent_id=intent_id, digest=digest, spends=spends, at=at)


def test_losing_a_race_for_the_intent_turns_into_a_deny() -> None:
    ledger = RacingLedger([intent()])
    decision = Guard(make_policy(), ledger).check(tx(value=ETH, intent_id="wd-1"), NOW)
    assert decision.outcome == "deny"
    assert "already used" in decision.findings[0].message
    assert ledger.intent("wd-1").used_by == "0xother"
    assert ledger.spent_usd(HOT, NOW.replace(hour=0), NOW) == 0


def test_every_decision_is_logged(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    guard = Guard(make_policy(), MemoryLedger([intent()]), AuditLog(path, KEY))
    outcomes = [
        guard.check(tx(value=ETH, intent_id="wd-1"), NOW).outcome,
        guard.check(tx(to=VAULT, data=hexed(call("deposit(uint256)", 1))), NOW).outcome,
        guard.check(tx(value=ETH), NOW).outcome,
    ]
    assert outcomes == ["allow", "hold", "deny"]
    assert verify(path, KEY.public_key()).records == 3
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert [r["event"]["decision"]["decision"] for r in records] == outcomes
    assert records[0]["event"]["request"]["intent_id"] == "wd-1"
    assert records[0]["event"]["decision"]["policy_digest"] == make_policy().digest


def _sixty_thousand(intent_id: str) -> object:
    return tx(value=20 * ETH, intent_id=intent_id)  # $60,000 at the test price of $3,000


def test_two_requests_decided_together_cannot_both_spend_the_daily_limit(tmp_path: Path) -> None:
    policy = make_policy({"rules": {"max-per-tx": {"limit_usd": 1_000_000}}})  # leave only velocity in play
    ledger = SqliteLedger(tmp_path / "ledger.db")
    for name in ("a", "b"):
        ledger.add_intent(intent(id=name, amount=20 * ETH))
    first = evaluate(_sixty_thousand("a"), policy, ledger, NOW)
    second = evaluate(_sixty_thousand("b"), policy, ledger, NOW)
    assert first.outcome == second.outcome == "allow"  # each alone fits under $100,000 a day
    assert ledger.commit(intent_id="a", digest=first.request_digest, spends=first.spends, at=NOW)
    assert not ledger.commit(intent_id="b", digest=second.request_digest, spends=second.spends, at=NOW)
    retried = Guard(policy, ledger).check(_sixty_thousand("b"), NOW)
    assert retried.outcome == "hold"
    assert [f.rule for f in retried.findings if f.effect == "hold"] == ["velocity"]
    ledger.close()


class ConcurrentSpend(MemoryLedger):
    """Another guard records a $60,000 withdrawal between this guard's evaluation and its commit."""

    raced = False

    def commit(self, *, intent_id: str | None, digest: str, spends: Sequence[Spend], at: datetime) -> bool:
        if not self.raced:
            self.raced = True
            super().commit(intent_id=None, digest="0xother", spends=[Spend(HOT, Decimal(60_000))], at=at)
        return super().commit(intent_id=intent_id, digest=digest, spends=spends, at=at)


def test_guard_decides_again_when_the_limit_was_spent_concurrently() -> None:
    policy = make_policy({"rules": {"max-per-tx": {"limit_usd": 1_000_000}}})
    ledger = ConcurrentSpend([intent(amount=20 * ETH)])
    decision = Guard(policy, ledger).check(_sixty_thousand("wd-1"), NOW)
    assert decision.outcome == "hold"
    assert ledger.intent("wd-1").used_at is None


def test_a_quorum_that_releases_a_velocity_hold_lifts_the_limit_at_commit() -> None:
    policy = make_policy({"rules": {"max-per-tx": {"limit_usd": 1_000_000}, "velocity": {"limit_usd": 50_000}}})
    ledger = MemoryLedger([intent(amount=20 * ETH)])
    decision = Guard(policy, ledger).check(approved(_sixty_thousand("wd-1"), "alice", "bob"), NOW)
    assert decision.outcome == "allow"
    assert [s.limit for s in decision.spends] == [None]
    assert ledger.intent("wd-1").used_at == NOW
