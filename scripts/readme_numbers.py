"""Fill the README's numbers block from the published API, so the README never quotes a number by hand.

Usage: uv run python scripts/readme_numbers.py site/api/v1 README.md
Rewrites everything between `<!-- NUMBERS -->` and `<!-- /NUMBERS -->`.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def money(v: float) -> str:
    if v >= 999.5e6:  # 999.6 million reads as "$1.00 billion", not "$1,000 million"
        return f"${v / 1e9:.2f} billion"
    if v >= 1e6:
        return f"${v / 1e6:,.0f} million"
    return f"${v:,.0f}"


def block(api: Path) -> str:
    s = json.loads((api / "summary.json").read_text())
    gh = json.loads((api / "golden-hour.json").read_text())
    stolen = s["stolen"]
    now = s["now"]
    date = s["generated_at"][:10]
    first_freeze = next((e for e in gh.get("events", []) if e["kind"] == "freeze"), None)
    escaped = gh["escaped_usd"][-1] if gh.get("escaped_usd") else 0.0
    rows = [
        (
            "Stolen, verified on-chain",
            f"{money(stolen['usd_at_theft'])} of the {money(stolen['reported_usd'])} reported, "
            f"in {stolen['transfers']} transfers from {stolen['victim_wallets']} Binance wallets",
        ),
        ("Frozen by issuers, read from their contracts", money(now["frozen"]["usd"])),
        ("Still held at trail addresses", f"{money(now['held']['usd'])} across {now['held']['addresses']:,} addresses"),
        ("Moved out to services", money(now["exited"]["usd"])),
        ("Stablecoins swapped out of reach before a freeze", money(escaped)),
        ("First freeze", f"minute {first_freeze['minute']} ({first_freeze['label']})" if first_freeze else "none yet"),
        (
            "Trail",
            f"{s['trail']['addresses']:,} addresses on {len(s['trail']['chains'])} chains, "
            f"{s['trail']['transfers']:,} transfers followed",
        ),
    ]
    lines = [
        f"**The Binance case, from chain data, as of {date}** "
        "(the [live site](https://dharmeshdev1.github.io/goldenhour/) updates every five minutes):",
        "",
        "| | |",
        "| --- | --- |",
    ]
    lines += [f"| {k} | {v} |" for k, v in rows]
    return "\n".join(lines)


def main() -> None:
    api, readme = Path(sys.argv[1]), Path(sys.argv[2])
    text = readme.read_text()
    replacement = f"<!-- NUMBERS -->\n{block(api)}\n<!-- /NUMBERS -->"
    if "<!-- /NUMBERS -->" in text:
        new = re.sub(r"<!-- NUMBERS -->.*?<!-- /NUMBERS -->", lambda _: replacement, text, count=1, flags=re.S)
    else:
        new = text.replace("<!-- NUMBERS -->", replacement, 1)
    readme.write_text(new)
    print(block(api))


if __name__ == "__main__":
    main()
