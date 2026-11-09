"""Screening shards, alert signing and formatting, the MCP server and the HTTP API."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from goldenhour.mcp import Server
from goldenhour.models import InvalidAddress
from goldenhour.screen import address_key, candidates, node_key, shard_of
from goldenhour.server import notify
from goldenhour.server.app import Settings, create_app

ADDR = "0x8532FA14ACAf483729C449b8458aedDb900543D4"
NODE = "ethereum:0x8532fa14acaf483729c449b8458aeddb900543d4"


def test_evm_addresses_are_checked_on_every_evm_chain_and_normalized() -> None:
    nodes = candidates("evm", ADDR)
    assert NODE in nodes and len(nodes) == 6
    assert candidates("ethereum", ADDR) == [NODE]
    with pytest.raises(InvalidAddress):
        candidates("bitcoin", ADDR)


def test_every_reading_of_an_address_shares_one_key_and_shard() -> None:
    key = node_key(NODE)
    assert len(key) == 64 and shard_of(key) == key[:3]
    # The same EVM address on any chain, in any case, hashes to the same key: one shard per lookup.
    assert node_key("base:" + ADDR.lower()) == key == address_key(ADDR)
    assert address_key("BC1Q0TS8GAF9HT0RR4U02HR56F8THNDQSC4KMKMFV3") == address_key(
        "bc1q0ts8gaf9ht0rr4u02hr56f8thndqsc4kmkmfv3"
    )


@pytest.fixture
def site(tmp_path: Path) -> Path:
    (tmp_path / "site").mkdir()
    api = tmp_path / "state" / "api" / "v1"
    (api / "screen").mkdir(parents=True)
    key = node_key(NODE)
    entry = {"node": NODE, "chain": "ethereum", "address": NODE.split(":")[1], "role": "seed", "exposure": "trail"}
    (api / "screen" / f"{key[:3]}.json").write_text(json.dumps({"v": 2, "entries": {key: [entry]}}))
    (api / "summary.json").write_text(json.dumps({"stolen": {"usd_at_theft": 5.0}, "now": {}, "trail": {}}))
    (api / "feed.json").write_text(
        json.dumps(
            {
                "items": [
                    {"chain": "ethereum", "tx": "0x1", "kind": "move", "usd": 5e6, "note": "between trail addresses"}
                ]
            }
        )
    )
    return tmp_path / "site"


def test_api_serves_documents_and_screens(site: Path) -> None:
    app = create_app(Settings(state=site.parent / "state", web=site, run_worker=False))
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/readyz").status_code == 200
        assert client.get("/api/v1/summary.json").json()["stolen"]["usd_at_theft"] == 5.0
        assert client.get("/api/v1/../../etc/passwd.json").status_code == 404
        hit = client.get(f"/v1/screen/evm/{ADDR}").json()
        assert hit["found"] and hit["matches"][0]["role"] == "seed"
        miss = client.get("/v1/screen/ethereum/0x" + "1" * 40).json()
        assert not miss["found"] and "does not mean the address is clean" in miss["note"]
        assert client.get("/v1/screen/ethereum/not-an-address").status_code == 422
        assert client.get("/metrics").status_code == 200


def test_static_files_cannot_escape_the_site(site: Path, tmp_path: Path) -> None:
    (tmp_path / "secret.txt").write_text("no")
    sibling = tmp_path / "site2"
    sibling.mkdir()
    (sibling / "leak.txt").write_text("no")
    (site / "index.html").write_text("ok")
    with TestClient(create_app(Settings(state=tmp_path / "state", web=site, run_worker=False))) as client:
        assert client.get("/").text == "ok"
        assert client.get("/..%2Fsecret.txt").status_code == 404
        assert client.get("/..%2Fsite2%2Fleak.txt").status_code == 404


def test_webhook_signatures_verify_and_expire() -> None:
    body = b'{"x":1}'
    sig = notify.sign("k", "1000", body)
    assert notify.verify("k", "1000", body, sig, now=1100)
    assert not notify.verify("k", "1000", body + b" ", sig, now=1100)
    assert not notify.verify("other", "1000", body, sig, now=1100)
    assert not notify.verify("k", "1000", body, sig, now=5000)


def test_alert_formatting_and_filters() -> None:
    alert = notify.Alert.from_feed(
        {
            "chain": "bitcoin",
            "tx": "ab",
            "kind": "bridge",
            "usd": 12_500_000.0,
            "note": "THORChain swap to bitcoin",
            "explorer_url": "https://mempool.space/tx/ab",
        }
    )
    assert alert.headline() == "$12,500,000 bridged on bitcoin: THORChain swap to bitcoin"
    pd = notify.payload(notify.Channel(kind="pagerduty", routing_key="r"), alert)
    assert pd["payload"]["severity"] == "critical" and pd["dedup_key"] == "bitcoin:ab:bridge"
    assert "blocks" in notify.payload(notify.Channel(kind="slack", url="u"), alert)
    hook = notify.payload(notify.Channel(kind="webhook", url="u"), alert)
    assert hook["alert"]["usd"] == 12_500_000.0
    small = notify.Channel(kind="slack", min_usd=50_000_000)
    assert not small.wants(alert)
    assert not notify.Channel(kind="slack", kinds=("freeze",)).wants(alert)


class FakeHttp:
    """A static host: documents it has, 404 for everything else."""

    def __init__(self, docs: dict[str, object]) -> None:
        self.docs = docs
        self.requested: list[str] = []

    def get(self, url: str):  # type: ignore[no-untyped-def]
        path = url.rsplit("/v1/", 1)[1]
        self.requested.append(path)
        doc = self.docs.get(path)

        class Resp:
            status_code = 200 if doc is not None else 404

            def raise_for_status(self) -> None:
                if doc is None:
                    raise AssertionError(f"404 for {path} was treated as an error")

            def json(self) -> object:
                return doc

        return Resp()


def test_mcp_screen_and_protocol() -> None:
    key = node_key(NODE)
    http = FakeHttp({f"screen/{key[:3]}.json": {"v": 2, "entries": {key: [{"node": NODE, "role": "trail"}]}}})
    server = Server("https://example.test/api/v1", client=http)  # type: ignore[arg-type]
    result = server.call("screen_address", {"chain": "evm", "address": ADDR})
    assert result["found"] and result["matches"][0]["node"] == NODE
    assert len(result["checked"]) == 6 and http.requested == [f"screen/{key[:3]}.json"]  # six chains, one shard
    # A shard the static host does not have means no trail address shares the prefix.
    miss = server.call("screen_address", {"chain": "evm", "address": "0x" + "1" * 40})
    assert miss["found"] is False and miss["matches"] == []
    assert "error" in server.call("screen_address", {"chain": "ethereum", "address": "nope"})
    init = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init and init["result"]["serverInfo"]["name"] == "goldenhour"
    tools = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert tools and {t["name"] for t in tools["result"]["tools"]} == {
        "screen_address",
        "case_status",
        "recent_movements",
    }
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


async def test_a_host_that_keeps_refusing_is_skipped_for_a_while() -> None:
    import httpx
    import respx

    from goldenhour.http import CIRCUIT_AFTER, CircuitOpen, PoliteClient

    with respx.mock(assert_all_called=False) as mock:
        route = mock.get("https://refuser.test/x").mock(return_value=httpx.Response(429))
        async with PoliteClient(rates={"refuser.test": 1000.0}) as client:
            client._buckets["refuser.test"].rate = 1000.0
            import goldenhour.http as h

            original = h._backoff
            h._backoff = lambda *_: 0.0  # type: ignore[assignment]
            try:
                with pytest.raises(CircuitOpen):
                    await client.request("GET", "https://refuser.test/x")
                calls = route.call_count
                with pytest.raises(CircuitOpen):
                    await client.request("GET", "https://refuser.test/x")
                assert route.call_count == calls == CIRCUIT_AFTER  # the second request never went out
            finally:
                h._backoff = original


async def test_alerts_survive_a_restart_and_a_new_install_stays_quiet(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import goldenhour.cli
    from goldenhour.server import app as server_app
    from goldenhour.server import notify

    async def no_trace(*args: object, **kwargs: object) -> dict:
        return {}

    monkeypatch.setattr(goldenhour.cli, "trace_once", no_trace)
    monkeypatch.setattr(goldenhour.cli, "publish_once", lambda *args, **kwargs: {})
    sent: list[list[str]] = []

    async def capture(config: object, alerts: list) -> list:  # type: ignore[type-arg]
        sent.append([a.id for a in alerts])
        return []

    monkeypatch.setattr(notify, "dispatch", capture)
    settings = server_app.Settings(state=tmp_path, run_worker=False)
    settings.api_dir.mkdir(parents=True)

    def feed(*txs: str) -> None:
        items = [
            {"chain": "ethereum", "tx": tx, "kind": "move", "usd": 5e6, "ts": "2026-11-01T00:00:00Z"} for tx in txs
        ]
        (settings.api_dir / "feed.json").write_text(json.dumps({"items": items}))

    def context() -> server_app.Context:
        ctx = server_app.Context(settings, notify.Config())
        ctx.load_alerts()
        return ctx

    feed("0xa", "0xb")
    await context().tick()
    assert sent == []  # a new install learns what already happened
    feed("0xa", "0xb", "0xc")  # 0xc moved while the server was down
    await context().tick()  # a restarted server remembers what it announced
    assert sent == [["ethereum:0xc:move"]]


def test_a_placeholder_webhook_secret_is_refused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from goldenhour.server import notify

    example = Path(__file__).parents[1] / "deploy" / "notify.example.yaml"
    with pytest.raises(ValueError, match="real secret"):
        notify.Config.load(example)  # copied without editing
    (tmp_path / "notify.yaml").mkdir()  # what Docker leaves when the file to mount is missing
    assert notify.Config.load(tmp_path / "notify.yaml").channels == []
