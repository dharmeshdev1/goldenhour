"""Storage: every transfer seen, every address judged, and why.

SQLite for the public edition, which carries its database between runs in the Actions cache, and
PostgreSQL for a self-hosted deployment. The schema is deliberately plain: analysis reads whole
tables into memory and recomputes from scratch each time, so the stored data is facts (transfers,
labels, cursors) and never conclusions that could go stale.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    select,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine, RowMapping

from goldenhour.models import Chain, Kind, Token, Transfer

metadata = MetaData()

transfers = Table(
    "transfers",
    metadata,
    Column("uid", String(400), primary_key=True),
    Column("chain", String(16), nullable=False),
    Column("tx", String(130), nullable=False),
    Column("block", BigInteger, nullable=False),
    Column("ts", BigInteger, nullable=False),
    Column("position", BigInteger, nullable=False),
    Column("sender", String(200), nullable=False),
    Column("receiver", String(200), nullable=False),
    Column("asset", String(200), nullable=False),
    Column("amount", Text, nullable=False),  # base units exceed 64 bits for 18-decimal tokens
    Column("kind", String(12), nullable=False),
    Column("meta", JSON, nullable=False, default=dict),
    Index("ix_transfers_sender", "sender"),
    Index("ix_transfers_receiver", "receiver"),
    Index("ix_transfers_ts", "ts"),
    Index("ix_transfers_tx", "tx"),
)

tokens = Table(
    "tokens",
    metadata,
    Column("asset", String(200), primary_key=True),
    Column("symbol", String(40), nullable=False, default=""),
    Column("decimals", Integer, nullable=False, default=0),
    Column("name", String(80), nullable=False, default=""),
)

# One row per address the tracer has looked at. `role` is the verdict, `reason` says why, and
# `cursor` says where its history was last read up to.
nodes = Table(
    "nodes",
    metadata,
    Column("node", String(200), primary_key=True),
    Column("chain", String(16), nullable=False),
    Column("role", String(16), nullable=False),
    Column("category", String(24), nullable=False, default=""),
    Column("label", String(120), nullable=False, default=""),
    Column("hop", Integer, nullable=False, default=0),
    Column("parent", String(200), nullable=False, default=""),
    Column("reason", String(240), nullable=False, default=""),
    Column("joined_ts", BigInteger, nullable=False, default=0),
    Column("cursor", JSON, nullable=False, default=dict),
    Column("fetched_at", BigInteger, nullable=False, default=0),
    Column("info", JSON, nullable=False, default=dict),
    Index("ix_nodes_role", "role"),
)

kv = Table(
    "kv",
    metadata,
    Column("key", String(120), primary_key=True),
    Column("value", JSON, nullable=False),
)

prices = Table(
    "prices",
    metadata,
    Column("lid", String(120), primary_key=True),
    Column("hour", BigInteger, primary_key=True),
    Column("price", Float, nullable=False),
)


def now_ts() -> int:
    return int(datetime.now(UTC).timestamp())


class Store:
    def __init__(self, url: str) -> None:
        if url.startswith("sqlite:///"):
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        self.url = url
        self.engine: Engine = create_engine(url, future=True)
        self.dialect = self.engine.dialect.name
        metadata.create_all(self.engine)
        if self.dialect == "sqlite":
            with self.engine.begin() as conn:
                conn.exec_driver_sql("PRAGMA journal_mode=WAL")

    @classmethod
    def at(cls, path: Path) -> Store:
        return cls(f"sqlite:///{path}")

    @contextmanager
    def begin(self) -> Iterator[Connection]:
        with self.engine.begin() as conn:
            yield conn

    def _insert(self, table: Table):
        return (pg_insert if self.dialect == "postgresql" else sqlite_insert)(table)

    def add_transfers(self, items: Iterable[Transfer]) -> int:
        rows = [
            {
                "uid": t.uid,
                "chain": str(t.chain),
                "tx": t.tx,
                "block": t.block,
                "ts": t.ts,
                "position": t.position,
                "sender": t.sender,
                "receiver": t.receiver,
                "asset": t.asset,
                "amount": str(t.amount),
                "kind": str(t.kind),
                "meta": t.meta or {},
            }
            for t in items
        ]
        if not rows:
            return 0
        added = 0
        with self.begin() as conn:
            for chunk in _chunks(rows, 2000):
                stmt = self._insert(transfers).values(chunk).on_conflict_do_nothing(index_elements=["uid"])
                if self.dialect == "postgresql":
                    # PostgreSQL reports no row count for a multi-row insert that skips conflicts.
                    added += len(conn.execute(stmt.returning(transfers.c.uid)).fetchall())
                else:
                    added += conn.execute(stmt).rowcount or 0
        return added

    def add_tokens(self, items: Iterable[Token]) -> None:
        rows = [{"asset": t.asset, "symbol": t.symbol, "decimals": t.decimals, "name": t.name} for t in items]
        if not rows:
            return
        with self.begin() as conn:
            for chunk in _chunks(rows, 2000):
                stmt = self._insert(tokens).values(chunk).on_conflict_do_nothing(index_elements=["asset"])
                conn.execute(stmt)

    def upsert_node(self, node: str, **fields: object) -> None:
        chain = node.partition(":")[0]
        row = {"node": node, "chain": chain, **fields}
        row.setdefault("role", "unknown")
        update = {k: v for k, v in row.items() if k != "node"}
        with self.begin() as conn:
            stmt = self._insert(nodes).values(row).on_conflict_do_update(index_elements=["node"], set_=update)
            conn.execute(stmt)

    def delete_transfers(self, chain: str) -> int:
        with self.begin() as conn:
            return conn.execute(transfers.delete().where(transfers.c.chain == chain)).rowcount or 0

    def delete_node(self, node: str) -> None:
        with self.begin() as conn:
            conn.execute(nodes.delete().where(nodes.c.node == node))

    def node(self, node: str) -> dict | None:
        with self.begin() as conn:
            row = conn.execute(select(nodes).where(nodes.c.node == node)).mappings().first()
            return dict(row) if row else None

    def nodes_by_role(self, *roles: str) -> list[dict]:
        with self.begin() as conn:
            q = select(nodes).where(nodes.c.role.in_(roles)) if roles else select(nodes)
            return [dict(r) for r in conn.execute(q).mappings()]

    def all_transfers(self) -> list[Transfer]:
        with self.begin() as conn:
            rows = conn.execute(select(transfers)).mappings().all()
        return [_transfer(r) for r in rows]

    def token_table(self) -> dict[str, Token]:
        with self.begin() as conn:
            return {
                r["asset"]: Token(r["asset"], r["symbol"], r["decimals"], r["name"])
                for r in conn.execute(select(tokens)).mappings()
            }

    def get(self, key: str, default: object = None) -> object:
        with self.begin() as conn:
            row = conn.execute(select(kv.c.value).where(kv.c.key == key)).first()
            return row[0] if row else default

    def put(self, key: str, value: object) -> None:
        with self.begin() as conn:
            stmt = (
                self._insert(kv)
                .values(key=key, value=value)
                .on_conflict_do_update(index_elements=["key"], set_={"value": value})
            )
            conn.execute(stmt)


def _transfer(r: RowMapping) -> Transfer:
    meta = r["meta"]
    if isinstance(meta, str):
        meta = json.loads(meta)
    return Transfer(
        uid=r["uid"],
        chain=Chain(r["chain"]),
        tx=r["tx"],
        block=int(r["block"]),
        ts=int(r["ts"]),
        position=int(r["position"]),
        sender=r["sender"],
        receiver=r["receiver"],
        asset=r["asset"],
        amount=int(r["amount"]),
        kind=Kind(r["kind"]),
        meta=meta or {},
    )


def _chunks[T](items: list[T], n: int) -> list[list[T]]:
    return [items[i : i + n] for i in range(0, len(items), n)]
