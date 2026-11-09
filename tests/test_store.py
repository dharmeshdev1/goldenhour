"""Storage on SQLite by default, and on PostgreSQL when GOLDENHOUR_TEST_DB points at one."""

import os
import uuid
from pathlib import Path

import pytest

from conftest import USDT, eth, transfer
from goldenhour.models import Kind, Token
from goldenhour.store import Store


@pytest.fixture
def store(tmp_path: Path) -> Store:
    url = os.environ.get("GOLDENHOUR_TEST_DB")
    if url:
        s = Store(url)
        with s.begin() as conn:  # a clean slate per test on a shared server
            for table in ("transfers", "tokens", "nodes", "kv", "prices"):
                conn.exec_driver_sql(f"DELETE FROM {table}")
        return s
    return Store.at(tmp_path / "test.db")


def test_transfers_are_stored_once_and_round_trip(store: Store) -> None:
    a, b = "ethereum:0x" + "1" * 40, "ethereum:0x" + "2" * 40
    big = 10**30  # wider than 64 bits, as 18-decimal token amounts often are
    items = [transfer("u1", a, b, eth(1)), transfer("u2", a, b, big, asset=USDT, kind=Kind.TOKEN, meta={"memo": "=:x"})]
    assert store.add_transfers(items) == 2
    assert store.add_transfers(items) == 0
    back = {t.uid: t for t in store.all_transfers()}
    assert back["u2"].amount == big and back["u2"].kind is Kind.TOKEN and back["u2"].meta == {"memo": "=:x"}


def test_nodes_upsert_merges_fields(store: Store) -> None:
    node = f"ethereum:0x{uuid.uuid4().hex[:40]}"
    store.upsert_node(node, role="trail", hop=2, reason="first", cursor={"block": 1})
    store.upsert_node(node, role="trail", hop=2, reason="second", cursor={"block": 5})
    row = store.node(node)
    assert row is not None and row["reason"] == "second" and row["cursor"] == {"block": 5}
    assert [r["node"] for r in store.nodes_by_role("trail")] == [node]


def test_tokens_and_key_values(store: Store) -> None:
    store.add_tokens([Token(USDT, "USDT", 6, "Tether USD")])
    assert store.token_table()[USDT].decimals == 6
    store.put("k", {"a": [1, 2]})
    assert store.get("k") == {"a": [1, 2]}
    assert store.get("missing", 7) == 7
