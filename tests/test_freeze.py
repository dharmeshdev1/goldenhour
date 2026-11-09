"""Issuer freeze events: a read is complete or an error, every source keeps its own cursor, and an
explorer that is down never stops a publish."""

import time
from pathlib import Path

import httpx
import pytest
import respx

from goldenhour import freeze
from goldenhour.case import Case
from goldenhour.cli import FREEZE_MARGIN_BLOCKS, _freezes
from goldenhour.freeze import ISSUERS, FreezeReadError
from goldenhour.http import PoliteClient
from goldenhour.store import Store

TETHER, CIRCLE = ISSUERS
ADDR = "0x" + "ab" * 20
HOSTS = ("api.routescan.io", "ethereum-rpc.publicnode.com", "api.trongrid.io")


def client() -> PoliteClient:
    return PoliteClient(rates=dict.fromkeys(HOSTS, 1000.0), retries=0)


def usdt_log(ts: int, tx: str) -> dict:
    # Tether's events carry the address in the data, not in an indexed topic.
    return {
        "topics": ["0x00"],
        "data": "0x" + ADDR[2:].rjust(64, "0"),
        "transactionHash": tx,
        "blockNumber": hex(100),
        "timeStamp": hex(ts),
    }


def ok(rows: list) -> httpx.Response:
    return httpx.Response(200, json={"status": "1", "message": "OK", "result": rows})


LIMITED = httpx.Response(200, json={"status": "0", "message": "NOTOK", "result": "Max rate limit reached"})
NO_TRON_EVENTS = httpx.Response(200, json={"data": [], "success": True, "meta": {}})


async def test_reads_freezes_and_unfreezes_and_refuses_an_error_answer() -> None:
    answers = {
        TETHER.freeze_topic: ok([usdt_log(1000, "0xa")]),
        TETHER.unfreeze_topic: ok([usdt_log(2000, "0xb")]),
        TETHER.burn_topic: httpx.Response(200, json={"status": "0", "message": "No records found", "result": []}),
    }
    with respx.mock(assert_all_called=False) as mock:
        mock.get(host="api.routescan.io").mock(side_effect=lambda r: answers[r.url.params["topic0"]])
        async with client() as c:
            events = await freeze.evm_events(c, TETHER, 1, 2)
        assert [(e.kind, e.node, e.ts) for e in events] == [
            ("freeze", f"ethereum:{ADDR}", 1000),
            ("unfreeze", f"ethereum:{ADDR}", 2000),
        ]
        # A rate limit is not an empty range: the read fails instead of looking complete.
        answers[TETHER.burn_topic] = LIMITED
        async with client() as c:
            with pytest.raises(FreezeReadError, match="NOTOK"):
                await freeze.evm_events(c, TETHER, 1, 2)


async def test_tron_error_answer_raises() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(host="api.trongrid.io").mock(return_value=httpx.Response(200, json={"success": False, "error": "x"}))
        async with client() as c:
            with pytest.raises(FreezeReadError):
                await freeze.tron_events(c, "RemovedBlackList", 0)


def case() -> Case:
    now = int(time.time())
    return Case(id="t", title="t", victim="v", attribution="", start_ts=now - 3600, end_ts=now)


CACHED = {
    "issuer": "Circle",
    "chain": "ethereum",
    "symbol": "USDC",
    "node": f"ethereum:{ADDR}",
    "tx": "0xold",
    "block": 1,
    "ts": 5,
    "kind": "freeze",
    "amount": 0,
}


async def test_a_failing_source_keeps_its_cursor_and_the_others_advance(tmp_path: Path) -> None:
    store = Store.at(tmp_path / "s.db")
    store.put("freezes:t", {"events": [CACHED], "cursors": {"evm:Tether": 900, "evm:Circle": 900}})
    reads: list[tuple[str, int]] = []

    def logs(request: httpx.Request) -> httpx.Response:
        p = request.url.params
        reads.append((p["address"], int(p["fromBlock"])))
        if p["address"] == CIRCLE.contract:
            return LIMITED
        return ok([usdt_log(1000, "0xa")] if p["topic0"] == TETHER.freeze_topic else [])

    with respx.mock(assert_all_called=False) as mock:
        mock.post(host="ethereum-rpc.publicnode.com").mock(
            return_value=httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": hex(1000)})
        )
        mock.get(host="api.routescan.io").mock(side_effect=logs)
        mock.get(host="api.trongrid.io").mock(return_value=NO_TRON_EVENTS)
        async with client() as c:
            events = await _freezes(store, c, case())

    assert {e.tx for e in events} == {"0xold", "0xa"}
    cursors = store.get("freezes:t")["cursors"]  # type: ignore[index]
    assert cursors["evm:Tether"] == 1001  # read to the head
    assert cursors["evm:Circle"] == 900  # failed: read again next time
    # Every read starts a margin before its cursor, because the logs index trails the chain head.
    assert (TETHER.contract, 900 - FREEZE_MARGIN_BLOCKS) in reads
    assert set(cursors) >= {f"tron:{name}" for name in freeze.TRON_EVENTS}


async def test_every_source_down_still_returns_the_cache(tmp_path: Path) -> None:
    store = Store.at(tmp_path / "s.db")
    store.put("freezes:t", {"events": [CACHED], "cursors": {}})
    with respx.mock(assert_all_called=False) as mock:
        mock.post(host="ethereum-rpc.publicnode.com").mock(
            return_value=httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": {"message": "down"}})
        )
        mock.get(host="api.trongrid.io").mock(return_value=httpx.Response(200, json={"success": False}))
        async with client() as c:
            events = await _freezes(store, c, case())
    assert [e.tx for e in events] == ["0xold"]
    assert store.get("freezes:t")["cursors"] == {}  # type: ignore[index]
