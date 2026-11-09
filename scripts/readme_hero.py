"""Draw the README hero from the published golden-hour data: docs/media/hero.svg.

A sky that sets from gold to night across the first four hours of the theft, the value drained
from the victim's wallets rising across it, and the stablecoins an issuer could still have frozen
draining away. The curves are the real ones from `golden-hour.json`; the SVG animates with SMIL so
it moves on GitHub without scripts.

Usage: uv run python scripts/readme_hero.py site/api/v1 docs/media/hero.svg
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

W, H = 1200, 420
PLOT = (60, 170, 1140, 365)  # left, top, right, bottom
INK = "#f5efe2"
GOLD = "#f2b33d"
RED = "#ff5a4f"
ICE = "#8fd8ff"
DIM = "#a59a86"


def money(v: float) -> str:
    if v >= 999.5e6:
        return f"${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:.0f}M"
    return f"${v:,.0f}"


def path(points: list[tuple[float, float]]) -> str:
    return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in points)


def build(gh: dict, summary: dict) -> str:
    minutes = gh["minutes"]
    drained = gh["drained_usd"]
    freezable = gh["freezable_usd"]
    horizon = min(len(minutes) - 1, 240)
    top = (max(drained[: horizon + 1]) or 1.0) * 1.06
    left, up, right, bottom = PLOT

    def xy(m: int, v: float) -> tuple[float, float]:
        return left + (right - left) * m / horizon, bottom - (bottom - up) * v / top

    drain = [xy(m, drained[m]) for m in range(0, horizon + 1)]
    free = [xy(m, freezable[m]) for m in range(0, horizon + 1)]
    free_area = path(free) + f" L{free[-1][0]:.1f},{bottom} L{free[0][0]:.1f},{bottom} Z"
    first_freeze = next((e for e in gh.get("events", []) if e["kind"] == "freeze"), None)
    marker = ""
    if first_freeze and first_freeze["minute"] <= horizon:
        fx = xy(first_freeze["minute"], 0)[0]
        marker = (
            f'<line x1="{fx:.1f}" y1="{up - 10}" x2="{fx:.1f}" y2="{bottom}" stroke="{ICE}" stroke-width="1.5" '
            f'stroke-dasharray="4 5" opacity="0.9"/>'
            f'<text x="{fx + 8:.1f}" y="{up + 8}" fill="{ICE}" font-size="15" font-family="ui-monospace,Menlo,monospace">'
            f"first freeze, minute {first_freeze['minute']}</text>"
        )
    stolen = (summary.get("stolen") or {}).get("usd_at_theft") or drained[horizon]
    frozen = ((summary.get("now") or {}).get("frozen") or {}).get("usd") or 0
    held = ((summary.get("now") or {}).get("held") or {}).get("usd") or 0
    ticks = "".join(
        f'<text x="{xy(m, 0)[0]:.1f}" y="{bottom + 22}" fill="{DIM}" font-size="13" text-anchor="middle" '
        f'font-family="ui-monospace,Menlo,monospace">{m // 60}h{m % 60:02d}</text>'
        for m in range(0, horizon + 1, 60)
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" role="img" aria-label="Goldenhour: value drained from the victim and stablecoins still freezable, minute by minute">
  <defs>
    <linearGradient id="sky" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#3a2510"><animate attributeName="stop-color" values="#4a2e12;#2a1a0c;#4a2e12" dur="14s" repeatCount="indefinite"/></stop>
      <stop offset="0.35" stop-color="#1c1410"/>
      <stop offset="1" stop-color="#07080d"/>
    </linearGradient>
    <radialGradient id="sun" cx="0.5" cy="0.5" r="0.5">
      <stop offset="0" stop-color="{GOLD}" stop-opacity="0.95"/>
      <stop offset="1" stop-color="{GOLD}" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="gold" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{GOLD}" stop-opacity="0.55"/>
      <stop offset="1" stop-color="{GOLD}" stop-opacity="0.04"/>
    </linearGradient>
    <clipPath id="reveal"><rect x="{left}" y="0" width="0" height="{H}">
      <animate attributeName="width" values="0;{right - left};{right - left}" keyTimes="0;0.55;1" dur="14s" repeatCount="indefinite"/></rect></clipPath>
  </defs>
  <rect width="{W}" height="{H}" rx="18" fill="url(#sky)"/>
  <circle cx="{left + 40}" cy="{bottom - 30}" r="120" fill="url(#sun)">
    <animate attributeName="cy" values="{bottom - 60};{bottom + 90};{bottom - 60}" keyTimes="0;0.55;1" dur="14s" repeatCount="indefinite"/>
    <animate attributeName="opacity" values="1;0.15;1" keyTimes="0;0.55;1" dur="14s" repeatCount="indefinite"/>
  </circle>
  <text x="{left}" y="62" fill="{INK}" font-size="40" font-weight="700" font-family="Georgia,'Times New Roman',serif">The first hour decides how much comes back.</text>
  <text x="{left}" y="98" fill="{DIM}" font-size="17" font-family="system-ui,-apple-system,Segoe UI,sans-serif">Binance, 29 October 2026. Minute by minute, from chain data: value drained (red) and stolen stablecoins an issuer could still freeze (gold).</text>
  <line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" stroke="{DIM}" stroke-opacity="0.35"/>
  {ticks}
  <g clip-path="url(#reveal)">
    <path d="{free_area}" fill="url(#gold)"/>
    <path d="{path(free)}" fill="none" stroke="{GOLD}" stroke-width="2.5"/>
    <path d="{path(drain)}" fill="none" stroke="{RED}" stroke-width="3"/>
  </g>
  {marker}
  <g font-family="ui-monospace,Menlo,monospace" font-size="16">
    <text x="{left}" y="132"><tspan fill="{RED}">verified stolen {money(stolen)}</tspan><tspan fill="{DIM}">   </tspan><tspan fill="{ICE}">frozen {money(frozen)}</tspan><tspan fill="{DIM}">   </tspan><tspan fill="{GOLD}">still held at trail addresses {money(held)}</tspan></text>
  </g>
</svg>
"""


def main() -> None:
    api, out = Path(sys.argv[1]), Path(sys.argv[2])
    gh = json.loads((api / "golden-hour.json").read_text())
    summary = json.loads((api / "summary.json").read_text())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(gh, summary))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
