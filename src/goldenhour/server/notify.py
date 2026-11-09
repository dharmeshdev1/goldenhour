"""Alerts: tell people when the stolen money moves, in the tools they already watch.

Each channel gets the same `Alert`, formatted its own way. Webhooks are signed with HMAC-SHA256 so
the receiver can check that an alert really came from this server and was not altered: the header
`X-Goldenhour-Signature: sha256=<hex>` covers the exact body, and `X-Goldenhour-Timestamp` lets the
receiver refuse replays.

Formatting is pure (tests pin every payload); sending turns every result into an `Outcome`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml

KINDS = ("move", "swap", "bridge", "exit", "mix", "freeze", "unfreeze")
PLACEHOLDERS = {"change-me", "changeme", "secret", "REPLACE_ME"}
log = logging.getLogger("goldenhour.notify")


@dataclass(frozen=True, slots=True)
class Alert:
    """One movement of the stolen money worth telling someone about."""

    id: str
    ts: str
    kind: str
    chain: str
    usd: float
    symbol: str
    amount: float
    note: str
    tx_url: str
    from_label: str = ""
    to_label: str = ""

    @classmethod
    def from_feed(cls, item: dict[str, Any]) -> Alert:
        return cls(
            id=f"{item.get('chain')}:{item.get('tx')}:{item.get('kind')}",
            ts=str(item.get("ts", "")),
            kind=str(item.get("kind", "")),
            chain=str(item.get("chain", "")),
            usd=float(item.get("usd") or 0),
            symbol=str(item.get("symbol", ""))[:16],
            amount=float(item.get("amount") or 0),
            note=str(item.get("note", ""))[:200],
            tx_url=str(item.get("explorer_url", "")),
            from_label=str(item.get("from_label", ""))[:80],
            to_label=str(item.get("to_label", ""))[:80],
        )

    def headline(self) -> str:
        verb = {
            "move": "moved",
            "swap": "swapped",
            "bridge": "bridged",
            "exit": "left the trail",
            "mix": "mixed",
            "freeze": "frozen",
        }.get(self.kind, self.kind)
        return f"${self.usd:,.0f} {verb} on {self.chain}: {self.note}"


@dataclass(slots=True)
class Channel:
    kind: str  # webhook | slack | discord | teams | pagerduty
    url: str = ""
    secret: str = ""  # webhook signing key
    routing_key: str = ""  # pagerduty
    min_usd: float = 1_000_000.0
    kinds: tuple[str, ...] = KINDS

    def wants(self, alert: Alert) -> bool:
        return alert.usd >= self.min_usd and alert.kind in self.kinds


@dataclass(slots=True)
class Config:
    channels: list[Channel] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path | None) -> Config:
        """Alert channels from a YAML file. No file means no alerts. A webhook secret that is the
        example's placeholder, or too short to resist guessing, is refused: a receiver would accept
        alerts signed by anyone who has read the example."""
        if not path or not path.is_file():
            if path:
                log.warning("no alert configuration at %s: alerts are off", path)
            return cls()
        data = yaml.safe_load(path.read_text()) or {}
        channels = []
        for raw in data.get("channels") or []:
            kinds = tuple(k for k in raw.get("kinds", KINDS) if k in KINDS) or KINDS
            channels.append(
                Channel(
                    kind=str(raw["kind"]),
                    url=str(raw.get("url", "")),
                    secret=str(raw.get("secret", "")),
                    routing_key=str(raw.get("routing_key", "")),
                    min_usd=float(raw.get("min_usd", 1_000_000)),
                    kinds=kinds,
                )
            )
        for channel in channels:
            if channel.kind == "webhook" and (channel.secret in PLACEHOLDERS or 0 < len(channel.secret) < 16):
                raise ValueError(f"webhook {channel.url}: set a real secret of at least 16 characters")
        return cls(channels)


def sign(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def verify(
    secret: str, timestamp: str, body: bytes, signature: str, *, now: float | None = None, tolerance_s: int = 300
) -> bool:
    """For receivers: true when the signature matches and the timestamp is fresh."""
    if not timestamp.isdigit() or abs((now or time.time()) - int(timestamp)) > tolerance_s:
        return False
    return hmac.compare_digest(sign(secret, timestamp, body), signature)


def payload(channel: Channel, alert: Alert) -> dict[str, Any]:
    text = alert.headline()
    link = f" <{alert.tx_url}|transaction>" if alert.tx_url else ""
    if channel.kind == "slack":
        return {"text": text, "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": f"*{text}*{link}"}}]}
    if channel.kind == "discord":
        return {"content": f"**{text}** {alert.tx_url}".strip()}
    if channel.kind == "teams":
        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": [{"type": "TextBlock", "text": text, "wrap": True, "weight": "Bolder"}],
                        "actions": [{"type": "Action.OpenUrl", "title": "Transaction", "url": alert.tx_url}]
                        if alert.tx_url
                        else [],
                    },
                }
            ],
        }
    if channel.kind == "pagerduty":
        return {
            "routing_key": channel.routing_key,
            "event_action": "trigger",
            "dedup_key": alert.id,
            "payload": {
                "summary": text[:1024],
                "source": "goldenhour",
                "timestamp": alert.ts,
                "severity": "critical" if alert.usd >= 10_000_000 else "warning",
                "custom_details": {"chain": alert.chain, "kind": alert.kind, "usd": alert.usd},
            },
            "links": [{"href": alert.tx_url, "text": "transaction"}] if alert.tx_url else [],
        }
    return {"type": "goldenhour.movement", "alert": asdict(alert)}


@dataclass(frozen=True, slots=True)
class Outcome:
    ok: bool
    detail: str
    retryable: bool = False


async def send(client: httpx.AsyncClient, channel: Channel, alert: Alert) -> Outcome:
    body = json.dumps(payload(channel, alert), separators=(",", ":"), default=str).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "goldenhour-alerts"}
    url = "https://events.pagerduty.com/v2/enqueue" if channel.kind == "pagerduty" else channel.url
    if channel.kind == "webhook" and channel.secret:
        stamp = str(int(time.time()))
        headers["X-Goldenhour-Timestamp"] = stamp
        headers["X-Goldenhour-Signature"] = sign(channel.secret, stamp, body)
    try:
        resp = await client.post(url, content=body, headers=headers, timeout=10)
    except httpx.HTTPError as exc:
        return Outcome(False, f"{type(exc).__name__}", retryable=True)
    if resp.status_code < 300:
        return Outcome(True, str(resp.status_code))
    return Outcome(False, f"HTTP {resp.status_code}", retryable=resp.status_code == 429 or resp.status_code >= 500)


async def dispatch(config: Config, alerts: list[Alert], client: httpx.AsyncClient | None = None) -> list[Outcome]:
    if not config.channels or not alerts:
        return []
    owned = client is None
    client = client or httpx.AsyncClient()
    try:
        outcomes = []
        for alert in alerts:
            for channel in config.channels:
                if channel.wants(alert):
                    outcomes.append(await send(client, channel, alert))
        return outcomes
    finally:
        if owned:
            await client.aclose()
