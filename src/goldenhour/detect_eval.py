"""Would Watch have caught it? A replay of the victim's wallets through the detector.

For each wallet the theft was taken from, read its own outgoing transfers for two weeks before the
theft and through the theft window. Calibrate the thresholds on the first week, count false alarms
on the second (days the calibration never saw), then replay the theft window and record when the
first alert would have fired and how much had already been taken by then.

A second, learned detector is evaluated beside the rules: an isolation forest over simple features
of each transfer, trained on the calibration days only. It reports how highly the stolen transfers
rank among everything the wallet sent, which says how separable a drain is from ordinary business.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from goldenhour.analysis import iso
from goldenhour.chains.etherscan import EXPLORERS, EtherscanAdapter
from goldenhour.http import PoliteClient
from goldenhour.models import Chain, Kind, Transfer, split_node
from goldenhour.prices import PriceBook
from goldenhour.watch import RULES, calibrate, detect, drained_before, first_alert

log = logging.getLogger("goldenhour.detect")
DAY = 86_400
BLOCK_S = 12


@dataclass(slots=True)
class WalletData:
    wallet: str
    label: str
    flows: list[tuple[Transfer, float]]
    own_txs: set[str]


async def fetch_wallet(
    client: PoliteClient, prices: PriceBook, wallet: str, start_ts: int, end_ts: int, head_block: int, now: int
) -> tuple[list[Transfer], dict, int]:
    """A busy wallet's history between two times, read through block ranges, and how many blocks
    the explorer could not serve at all (skipped, and reported with the replay)."""
    adapter = EtherscanAdapter(client, EXPLORERS[Chain.ETHEREUM][0])
    adapter.skip_unreadable = True
    start_block = head_block - (now - start_ts) // BLOCK_S - 300
    end_block = head_block - (now - end_ts) // BLOCK_S + 300
    page = await adapter.history(split_node(wallet)[1], start_block, end_block)
    return [t for t in page.transfers if start_ts <= t.ts <= end_ts], page.tokens, len(adapter.unread)


def features(t: Transfer, usd: float, seen: set[str], last_ts: int, own: bool) -> list[float]:
    return [
        math.log10(usd + 1.0),
        0.0 if t.receiver in seen else 1.0,  # first payment to this address
        math.log10(max(1, t.ts - last_ts) + 1.0),  # quiet time before this transfer
        1.0 if t.kind is Kind.INTERNAL else 0.0,
        0.0 if own else 1.0,  # moved in a transaction the wallet did not send
    ]


def evaluate(
    wallets: list[WalletData],
    theft: dict[str, list[tuple[int, float]]],
    theft_receivers: dict[str, set[str]],
    first_theft_ts: int,
    days: int = 14,
) -> dict:
    """The detector report for `api/v1/detector.json`. The first half of `days` calibrates, the second
    half (less the hour before the theft) counts false alarms."""
    replay = []
    half = days / 2
    calib_end = first_theft_ts - int(half * DAY)
    test_alerts = 0
    test_txs = 0
    test_wallet_days = 0.0
    rows_x: list[list[float]] = []
    rows_y: list[int] = []
    rows_split: list[str] = []
    for w in wallets:
        calib = [(t, u) for t, u in w.flows if t.ts < calib_end]
        test = [(t, u) for t, u in w.flows if calib_end <= t.ts < first_theft_ts - 3600]
        window = [(t, u) for t, u in w.flows if t.ts >= first_theft_ts - 3600]
        cal = calibrate(calib)
        test_found = detect(w.wallet, test, cal, w.own_txs)
        test_alerts += len(test_found)
        test_txs += len(test)
        test_wallet_days += half - 1 / 24  # the held-out stretch, less the hour before the theft
        alerts = detect(w.wallet, test + window, cal, w.own_txs)
        stolen = sorted(theft.get(w.wallet, []))
        start = stolen[0][0] if stolen else first_theft_ts
        first = first_alert(alerts, start - 3600)
        replay.append(
            {
                "chain": str(split_node(w.wallet)[0]),
                "wallet": split_node(w.wallet)[1],
                "label": w.label,
                "first_theft_at": iso(start),
                "first_alert_at": iso(first.ts) if first else None,
                "minutes_after_first_theft": round((first.ts - start) / 60, 1) if first else None,
                "usd_before_alert": round(drained_before(stolen, first.ts), 2)
                if first
                else round(sum(v for _, v in stolen), 2),
                "rule": first.rule if first else "",
                "rules_fired": sorted({a.rule for a in alerts if a.ts >= start - 3600}),
            }
        )
        # Learned detector rows: label 1 for transfers that went to the trail during the theft.
        seen: set[str] = set()
        last = 0
        receivers = theft_receivers.get(w.wallet, set())
        for t, u in w.flows:
            own = t.tx in w.own_txs or t.kind is Kind.NATIVE
            x = features(t, u, seen, last, own)
            label = 1 if (t.ts >= first_theft_ts - 3600 and t.receiver in receivers) else 0
            split = "calib" if t.ts < calib_end else "eval"
            rows_x.append(x)
            rows_y.append(label)
            rows_split.append(split)
            seen.add(t.receiver)
            last = t.ts
    learned = _learned(rows_x, rows_y, rows_split)
    return {
        "replay": replay,
        "baseline": {
            "days": half,
            "wallets": len(wallets),
            "transactions": test_txs,
            "alerts": test_alerts,
            "alerts_per_wallet_day": round(test_alerts / test_wallet_days, 3) if test_wallet_days else 0.0,
        },
        "learned": learned,
        "rules": RULES,
    }


def _learned(x: list[list[float]], y: list[int], split: list[str]) -> dict:
    try:
        import numpy as np
        from sklearn.ensemble import IsolationForest
        from sklearn.metrics import roc_auc_score
    except ImportError:
        return {}
    xs = np.array(x)
    ys = np.array(y)
    calib = np.array([s == "calib" for s in split])
    if calib.sum() < 100 or ys[~calib].sum() == 0:
        return {}
    model = IsolationForest(n_estimators=300, random_state=7, contamination="auto").fit(xs[calib])
    score = -model.score_samples(xs[~calib])
    y_eval = ys[~calib]
    auc = float(roc_auc_score(y_eval, score))
    normal = np.sort(score[y_eval == 0])
    thefts = score[y_eval == 1]
    # Recall at the threshold that lets through one false alarm per thousand normal transfers.
    cut = normal[int(len(normal) * 0.999)] if len(normal) >= 1000 else normal[-1]
    return {
        "model": "isolation forest, 5 features, trained on the calibration days",
        "roc_auc": round(auc, 4),
        "recall_at_0_1_percent_false_alarms": round(float((thefts > cut).mean()), 4),
        "stolen_transfers": int(y_eval.sum()),
        "normal_transfers": int((y_eval == 0).sum()),
    }
