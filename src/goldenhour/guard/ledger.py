"""The withdrawal ledger: intents that authorize transfers, and the spending that velocity limits count.

An intent is the withdrawal system's statement that a specific payment should happen: this wallet
pays this destination at most this amount of this asset, on this chain, before this time. Guard
lets a transfer through only against an unused, unexpired intent that matches it, and consumes the
intent when it allows the request, so one intent can never pay twice.

Evaluation only reads the ledger (`LedgerView`). Writing happens in a single atomic `commit` after
an allow, which also re-checks each wallet's velocity limit against the spending recorded by then.
A request that loses a race, for the same intent or for the last of a wallet's daily allowance, is
decided again on the new state rather than paid twice.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from goldenhour.guard.policy import AssetId
from goldenhour.guard.request import Address, Quantity


class Intent(BaseModel):
    """One authorized withdrawal. `asset` is `native` or a token address; `amount` is in base units."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    wallet: Address
    chain_id: int = Field(ge=1)
    asset: AssetId
    destination: Address
    amount: Quantity
    expires_at: AwareDatetime
    note: str = ""
    used_at: AwareDatetime | None = None
    used_by: str | None = None


@dataclass(frozen=True)
class Spend:
    """USD leaving one wallet. With a `limit`, the commit is refused if the wallet's spending after
    `since`, plus this, would exceed it: the velocity check, repeated where no race can slip past."""

    wallet: str
    usd: Decimal
    limit: Decimal | None = None
    since: datetime | None = None


class LedgerView(Protocol):
    """What evaluation may read. Nothing here changes state, which keeps evaluation pure."""

    def intent(self, intent_id: str) -> Intent | None: ...

    def spent_usd(self, wallet: str, since: datetime, until: datetime) -> Decimal: ...


class Ledger(LedgerView, Protocol):
    def add_intent(self, intent: Intent) -> None: ...

    def commit(self, *, intent_id: str | None, digest: str, spends: Sequence[Spend], at: datetime) -> bool:
        """Consume the intent, if any, and record the spending, all or nothing.

        Returns False, changing nothing, when the intent is missing or already used, or when a
        spend would take its wallet past its limit.
        """
        ...


def _over_limit(spend: Spend, earlier: Decimal) -> bool:
    return spend.limit is not None and earlier + spend.usd > spend.limit


def _check_new(intent: Intent) -> None:
    if intent.used_at is not None or intent.used_by is not None:
        raise ValueError(f"intent {intent.id} is already used; only new intents can be added")


class MemoryLedger:
    """An in-memory ledger for tests and the demonstration cases."""

    def __init__(self, intents: Iterable[Intent] = ()) -> None:
        self._intents: dict[str, Intent] = {}
        self._spends: list[tuple[str, Decimal, datetime]] = []
        for intent in intents:
            self.add_intent(intent)

    def add_intent(self, intent: Intent) -> None:
        _check_new(intent)
        if intent.id in self._intents:
            raise ValueError(f"intent {intent.id} already exists")
        self._intents[intent.id] = intent

    def intent(self, intent_id: str) -> Intent | None:
        return self._intents.get(intent_id)

    def spent_usd(self, wallet: str, since: datetime, until: datetime) -> Decimal:
        return sum((usd for w, usd, at in self._spends if w == wallet and since < at <= until), Decimal(0))

    def commit(self, *, intent_id: str | None, digest: str, spends: Sequence[Spend], at: datetime) -> bool:
        if any(_over_limit(spend, self.spent_usd(spend.wallet, spend.since, at)) for spend in spends if spend.since):
            return False
        if intent_id is not None:
            current = self._intents.get(intent_id)
            if current is None or current.used_at is not None:
                return False
            self._intents[intent_id] = current.model_copy(update={"used_at": at, "used_by": digest})
        self._spends.extend((spend.wallet, spend.usd, at) for spend in spends)
        return True


_SCHEMA = """
CREATE TABLE IF NOT EXISTS intents (
    id TEXT PRIMARY KEY,
    body TEXT NOT NULL,
    used_at TEXT,
    used_by TEXT
);
CREATE TABLE IF NOT EXISTS spends (
    wallet TEXT NOT NULL,
    usd TEXT NOT NULL,
    at REAL NOT NULL,
    digest TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS spends_by_wallet ON spends (wallet, at);
"""


class SqliteLedger:
    """A ledger in one SQLite file. Amounts are stored as text: they do not fit SQLite's integers."""

    def __init__(self, path: Path | str) -> None:
        self._db = sqlite3.connect(path, isolation_level=None)
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def add_intent(self, intent: Intent) -> None:
        _check_new(intent)
        try:
            self._db.execute("INSERT INTO intents (id, body) VALUES (?, ?)", (intent.id, intent.model_dump_json()))
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"intent {intent.id} already exists") from exc

    def intent(self, intent_id: str) -> Intent | None:
        row = self._db.execute("SELECT body, used_at, used_by FROM intents WHERE id = ?", (intent_id,)).fetchone()
        if row is None:
            return None
        body, used_at, used_by = row
        return Intent.model_validate_json(body).model_copy(
            update={"used_at": datetime.fromisoformat(used_at) if used_at else None, "used_by": used_by}
        )

    def spent_usd(self, wallet: str, since: datetime, until: datetime) -> Decimal:
        rows = self._db.execute(
            "SELECT usd FROM spends WHERE wallet = ? AND at > ? AND at <= ?",
            (wallet, since.timestamp(), until.timestamp()),
        )
        return sum((Decimal(usd) for (usd,) in rows), Decimal(0))

    def commit(self, *, intent_id: str | None, digest: str, spends: Sequence[Spend], at: datetime) -> bool:
        # BEGIN IMMEDIATE takes the write lock up front, so two guards sharing the file cannot both
        # read an intent as unused and both consume it, or both spend a wallet's last allowance.
        self._db.execute("BEGIN IMMEDIATE")
        try:
            for spend in spends:
                if spend.since and _over_limit(spend, self.spent_usd(spend.wallet, spend.since, at)):
                    self._db.execute("ROLLBACK")
                    return False
            if intent_id is not None:
                cursor = self._db.execute(
                    "UPDATE intents SET used_at = ?, used_by = ? WHERE id = ? AND used_at IS NULL",
                    (at.isoformat(), digest, intent_id),
                )
                if cursor.rowcount != 1:
                    self._db.execute("ROLLBACK")
                    return False
            self._db.executemany(
                "INSERT INTO spends (wallet, usd, at, digest) VALUES (?, ?, ?, ?)",
                [(spend.wallet, str(spend.usd), at.timestamp(), digest) for spend in spends],
            )
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        return True
