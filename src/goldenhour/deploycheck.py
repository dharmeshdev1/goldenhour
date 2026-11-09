"""The last gate before a deploy: the site must be whole, and the trail must not shrink sharply.

The pipeline deploys every five minutes, so one broken run would replace a good site within
minutes. These checks refuse a deploy that would serve the API without the web app, an API with a
missing or unreadable file, or a trail much smaller than the one already live, which is what a run
that lost its state and started again from the seeds would publish. The live site keeps serving
the last good version, and the run fails where someone will see it.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from goldenhour import __version__

REQUIRED = ("summary.json", "meta.json", "case.json", "feed.json", "graph.json", "flows.json", "golden-hour.json")
SHRINK_FLOOR = 0.8  # a deploy may not drop below this share of the live trail without a person saying so

log = logging.getLogger("goldenhour.deploycheck")


def problems(site: Path, live: dict | None, *, allow_shrink: bool = False) -> list[str]:
    """Everything wrong with the directory about to be deployed. Empty means it may go out."""
    found: list[str] = []
    if not (site / "index.html").is_file():
        found.append("index.html is missing: the web app was not laid out")
    docs: dict[str, dict] = {}
    for name in REQUIRED:
        path = site / "api" / "v1" / name
        try:
            doc = json.loads(path.read_text())
        except FileNotFoundError:
            found.append(f"api/v1/{name} is missing")
            continue
        except (OSError, ValueError) as exc:
            found.append(f"api/v1/{name} is not readable JSON: {exc}")
            continue
        if not isinstance(doc, dict):
            found.append(f"api/v1/{name} is not a JSON object")
            continue
        docs[name] = doc
    summary = docs.get("summary.json")
    if summary is None:
        return found
    addresses = _number(summary, "trail", "addresses")
    stolen = _number(summary, "stolen", "usd_at_theft")
    if addresses <= 0:
        found.append("summary.json has no trail addresses")
    if stolen <= 0:
        found.append("summary.json values the theft at zero")
    if live and not allow_shrink:
        for label, new, old in (
            ("trail addresses", addresses, _number(live, "trail", "addresses")),
            ("value stolen", stolen, _number(live, "stolen", "usd_at_theft")),
        ):
            if old > 0 and new < SHRINK_FLOOR * old:
                found.append(
                    f"{label} would fall from {old:,.0f} on the live site to {new:,.0f}, below "
                    f"{SHRINK_FLOOR:.0%}; deploy with allow_shrink if that is intended"
                )
    return found


def live_summary(api: str, client: httpx.Client | None = None) -> dict | None:
    """The summary the live site serves now, or None when there is none to compare with."""
    http = client or httpx.Client(timeout=15, headers={"User-Agent": f"goldenhour/{__version__}"})
    try:
        resp = http.get(f"{api.rstrip('/')}/summary.json")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        doc = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("live summary not read, no shrink check this run: %s", exc)
        return None
    return doc if isinstance(doc, dict) else None


def _number(doc: dict, *path: str) -> float:
    cur: object = doc
    for key in path:
        cur = cur.get(key) if isinstance(cur, dict) else None
    try:
        return float(cur)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
