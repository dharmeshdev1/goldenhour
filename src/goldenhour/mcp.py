"""A Model Context Protocol server, so payment and compliance agents can screen addresses.

It speaks MCP over stdio (JSON-RPC 2.0, one message per line) with no dependencies beyond httpx,
and reads a Goldenhour API: the public one by default, or a self-hosted server. Screening uses the
same k-anonymous shards as the website, so the API host never learns which address was checked.

Tools:
- `screen_address`: is an address on the trail of a theft, or did it receive money from it?
- `case_status`: how much was stolen, frozen, still held and moved out, and when it last moved.
- `recent_movements`: the latest movements of the stolen money, filterable by size and kind.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import httpx

from goldenhour import __version__
from goldenhour.models import InvalidAddress
from goldenhour.screen import address_key, candidates, lookup, shard_of

PROTOCOL = "2025-06-18"
PUBLIC_API = "https://dharmeshdev1.github.io/goldenhour/api/v1"

TOOLS = [
    {
        "name": "screen_address",
        "description": "Check whether a crypto address is on the trail of the October 2026 Binance theft or "
        "received money from it. Use before accepting a deposit or sending a payment. chain is one of "
        "ethereum, optimism, arbitrum, base, polygon, avalanche, bitcoin, tron, xrpl, solana, or evm to check "
        "every EVM chain. A miss means not on this trail, which is not the same as clean.",
        "inputSchema": {
            "type": "object",
            "required": ["chain", "address"],
            "properties": {"chain": {"type": "string"}, "address": {"type": "string"}},
        },
    },
    {
        "name": "case_status",
        "description": "Where the stolen money is now: total stolen, frozen by issuers, still held by the "
        "attacker's addresses, moved to exchanges, mixers and bridges, and the time of the last movement.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "recent_movements",
        "description": "The most recent movements of the stolen money, newest first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "min_usd": {"type": "number", "default": 100000},
                "kind": {"type": "string", "enum": ["any", "move", "swap", "bridge", "exit", "mix", "freeze"]},
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 200},
            },
        },
    },
]


class Server:
    def __init__(self, api: str = PUBLIC_API, client: httpx.Client | None = None) -> None:
        self.api = api.rstrip("/")
        self.http = client or httpx.Client(timeout=15, headers={"User-Agent": f"goldenhour-mcp/{__version__}"})

    def _get(self, path: str) -> Any:
        resp = self.http.get(f"{self.api}/{path}")
        resp.raise_for_status()
        return resp.json()

    def _shard(self, address: str) -> dict:
        """The one shard an address can be in. A static host answers 404 for a shard no trail
        address falls in, which is an answer, not an error."""
        resp = self.http.get(f"{self.api}/screen/{shard_of(address_key(address))}.json")
        if resp.status_code == 404:
            return {"v": 2, "entries": {}}
        resp.raise_for_status()
        return resp.json()

    def call(self, name: str, args: dict[str, Any]) -> Any:
        if name == "screen_address":
            try:
                nodes = candidates(str(args.get("chain", "")), str(args.get("address", "")))
            except (InvalidAddress, ValueError) as exc:
                return {"error": str(exc)}
            # One shard, whatever the chain: the server sees a single 3-character prefix.
            address = str(args.get("address", ""))
            hits = lookup(self._shard(address), address, nodes)
            return {
                "checked": nodes,
                "found": bool(hits),
                "matches": hits,
                "note": "A miss means the address is not on this trail. It does not mean the address is clean.",
            }
        if name == "case_status":
            s = self._get("summary.json")
            now = s.get("now", {})
            return {
                "generated_at": s.get("generated_at"),
                "last_movement_at": s.get("last_movement_at"),
                "stolen_usd_verified_on_chain": s.get("stolen", {}).get("usd_at_theft"),
                "stolen_usd_reported": s.get("stolen", {}).get("reported_usd"),
                "frozen_usd": now.get("frozen", {}).get("usd"),
                "held_by_attacker_usd": now.get("held", {}).get("usd"),
                "moved_out": {c["category"]: c["usd"] for c in now.get("exited", {}).get("by_category", [])},
                "converted_to_btc": s.get("converted", {}).get("to_btc"),
                "trail_addresses": s.get("trail", {}).get("addresses"),
            }
        if name == "recent_movements":
            feed = self._get("feed.json")
            kind = args.get("kind", "any")
            floor = float(args.get("min_usd", 100000))
            items = [i for i in feed.get("items", []) if i.get("usd", 0) >= floor and kind in ("any", i.get("kind"))]
            return {"generated_at": feed.get("generated_at"), "items": items[: int(args.get("limit", 20))]}
        raise KeyError(name)

    def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        method = msg.get("method")
        mid = msg.get("id")
        if mid is None:  # a notification
            return None
        if method == "initialize":
            result: Any = {
                "protocolVersion": PROTOCOL,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "goldenhour", "version": __version__},
            }
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = msg.get("params") or {}
            try:
                data = self.call(str(params.get("name")), params.get("arguments") or {})
                result = {"content": [{"type": "text", "text": json.dumps(data, indent=1)}], "isError": False}
            except KeyError:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "unknown tool"}}
            except httpx.HTTPError as exc:
                result = {"content": [{"type": "text", "text": f"API unavailable: {exc}"}], "isError": True}
        elif method == "ping":
            result = {}
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve(api: str = PUBLIC_API) -> None:  # pragma: no cover - exercised through handle() in tests
    server = Server(api)
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            reply = server.handle(json.loads(line))
        except json.JSONDecodeError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
