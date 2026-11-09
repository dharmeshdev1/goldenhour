"""A case: one theft, its seeds, its victim's wallets, and what the public record says about it."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml

from goldenhour.models import Chain, node_id

CASES_DIR = Path(__file__).resolve().parents[2] / "cases"
if not CASES_DIR.exists():  # installed wheel
    CASES_DIR = Path(__file__).resolve().parent / "_cases"


def _ts(value: object) -> int:
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=UTC)
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return int(dt.timestamp())


@dataclass(slots=True)
class Case:
    id: str
    title: str
    victim: str
    attribution: str
    start_ts: int
    end_ts: int
    seeds: list[str] = field(default_factory=list)  # node ids
    seed_reasons: dict[str, str] = field(default_factory=dict)
    victims: dict[str, str] = field(default_factory=dict)  # node id -> label
    reported: dict[str, object] = field(default_factory=dict)
    sources: list[dict[str, str]] = field(default_factory=list)
    timeline: list[dict[str, str]] = field(default_factory=list)
    directory: Path = Path()

    @classmethod
    def load(cls, case_id: str, root: Path = CASES_DIR) -> Case:
        directory = root / case_id
        data = yaml.safe_load((directory / "case.yaml").read_text())
        seeds: list[str] = []
        for name in data.get("seed_files", []):
            for raw in (directory / name).read_text().splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                chain, address = line.split()[:2]
                seeds.append(node_id(Chain(chain), address))
        reasons = dict.fromkeys(seeds, "named by the FBI alert of 31 October 2026")
        for entry in data.get("extra_seeds", []) or []:
            node = node_id(Chain(entry["chain"]), entry["address"])
            seeds.append(node)
            reasons[node] = str(entry.get("reason") or "first hop verified on-chain")
        victims = {
            node_id(Chain(v["chain"]), v["address"]): v.get("label", data.get("victim", ""))
            for v in data.get("victim_wallets", []) or []
        }
        window = data["window"]
        return cls(
            id=data["id"],
            title=data["title"],
            victim=data["victim"],
            attribution=data.get("attribution", ""),
            start_ts=_ts(window["start"]),
            end_ts=_ts(window["end"]),
            seeds=list(dict.fromkeys(seeds)),
            seed_reasons=reasons,
            victims=victims,
            reported=data.get("reported", {}) or {},
            sources=data.get("sources", []) or [],
            timeline=data.get("timeline", []) or [],
            directory=directory,
        )
