"""Watch: noticing a drain while it is happening.

The detector reads one custody wallet's outgoing transfers in time order and raises an alert the
first time a rule fires. The rules are deliberately few and explainable, because the people who
receive these alerts at 20:05 on a Thursday must act on them in minutes, and must be able to say
why the system woke them up.

- **delegated-code-outflow** (critical). The wallet is an ordinary account (an EOA), yet value is
  leaving it from inside contract code: an internal transfer whose sender is the wallet. An EOA
  cannot do that unless it is running delegated code (EIP-7702). For a hot wallet that has never
  delegated, there is no innocent explanation.
- **foreign-token-outflow** (high). Tokens leave the wallet in a transaction the wallet did not
  send. Hot wallets send their own withdrawals; somebody else moving their tokens means an
  approval or a delegation is being used.
- **outflow-burst** (high). More value left in ten minutes than in any ten minutes of the
  calibration period, by a margin.
- **outsized-transfer** (medium). One transfer larger than any in the calibration period, to an
  address the wallet has never paid before.

Thresholds come from each wallet's own history, calibrated on one stretch of normal days and
tested on a later stretch, so the false alarm rate reported is measured on days the detector did
not see during calibration.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import deque
from dataclasses import dataclass

from goldenhour.models import Kind, Transfer

WINDOW_S = 600
BURST_MARGIN = 1.5
OUTSIZED_MARGIN = 1.5
MIN_ALERT_USD = 250_000.0  # nothing below this wakes anyone up, whatever the wallet's history

RULES = [
    {
        "id": "delegated-code-outflow",
        "severity": "critical",
        "title": "Value leaving an account from inside code",
        "description": "An ordinary account (EOA) is sending value from inside contract execution. It can only do that "
        "while running delegated code (EIP-7702), which a custody wallet should never do.",
    },
    {
        "id": "foreign-token-outflow",
        "severity": "high",
        "title": "Tokens moved by someone else",
        "description": "Tokens left the wallet in a transaction the wallet did not send: an approval or a "
        "delegation is being used by another account.",
    },
    {
        "id": "outflow-burst",
        "severity": "high",
        "title": "Ten-minute outflow beyond anything seen",
        "description": "More value left in ten minutes than in any ten minutes of the calibration period, "
        "by half again.",
    },
    {
        "id": "outsized-transfer",
        "severity": "medium",
        "title": "Largest transfer ever, to a new address",
        "description": "A single transfer larger than any in the calibration period, to an address the wallet "
        "never paid.",
    },
]


@dataclass(frozen=True, slots=True)
class Alert:
    wallet: str
    rule: str
    ts: int
    usd: float
    tx: str


@dataclass(slots=True)
class Calibration:
    burst_usd: float
    outsized_usd: float
    known: set[str]


def outflows(wallet: str, transfers: list[Transfer]) -> list[Transfer]:
    return sorted(
        (t for t in transfers if t.sender == wallet and t.kind is not Kind.FEE), key=lambda t: (t.ts, t.position)
    )


def calibrate(flows: list[tuple[Transfer, float]]) -> Calibration:
    """Thresholds from a wallet's normal days: its largest ten-minute outflow and largest transfer."""
    window: deque[tuple[int, float]] = deque()
    running = 0.0
    burst = 0.0
    largest = 0.0
    known: set[str] = set()
    for t, usd in flows:
        window.append((t.ts, usd))
        running += usd
        while window and window[0][0] <= t.ts - WINDOW_S:
            running -= window.popleft()[1]
        burst = max(burst, running)
        largest = max(largest, usd)
        known.add(t.receiver)
    return Calibration(max(burst * BURST_MARGIN, MIN_ALERT_USD), max(largest * OUTSIZED_MARGIN, MIN_ALERT_USD), known)


def detect(
    wallet: str, flows: list[tuple[Transfer, float]], cal: Calibration, own_txs: set[str], wallet_is_eoa: bool = True
) -> list[Alert]:
    """Every alert the rules raise over these flows, each rule re-arming after an hour of quiet."""
    alerts: list[Alert] = []
    last_fired: dict[str, int] = {}
    window: deque[tuple[int, float]] = deque()
    running = 0.0
    known = set(cal.known)

    def fire(rule: str, t: Transfer, usd: float) -> None:
        if t.ts - last_fired.get(rule, -(10**12)) >= 3600:
            alerts.append(Alert(wallet, rule, t.ts, usd, t.tx))
        last_fired[rule] = t.ts

    for t, usd in flows:
        window.append((t.ts, usd))
        running += usd
        while window and window[0][0] <= t.ts - WINDOW_S:
            running -= window.popleft()[1]
        if wallet_is_eoa and t.kind is Kind.INTERNAL and usd > 0:
            fire("delegated-code-outflow", t, usd)
        if t.kind is Kind.TOKEN and own_txs and t.tx not in own_txs and usd >= MIN_ALERT_USD / 10:
            fire("foreign-token-outflow", t, usd)
        if running >= cal.burst_usd:
            fire("outflow-burst", t, running)
        if usd >= cal.outsized_usd and t.receiver not in known:
            fire("outsized-transfer", t, usd)
        known.add(t.receiver)
    return alerts


def first_alert(alerts: list[Alert], after: int) -> Alert | None:
    later = [a for a in alerts if a.ts >= after]
    return min(later, key=lambda a: a.ts) if later else None


def drained_before(theft: list[tuple[int, float]], ts: int) -> float:
    """Dollars stolen from this wallet before `ts` (theft sorted by time)."""
    times = [x for x, _ in theft]
    i = bisect_left(times, ts)
    return sum(v for _, v in theft[:i])
