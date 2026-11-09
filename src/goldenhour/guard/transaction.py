"""Ethereum transaction envelopes: read a raw transaction, and rebuild the bytes a signer signs.

Supported: legacy transactions with EIP-155 replay protection (type 0), access list transactions
(type 1), dynamic fee transactions (type 2) and EIP-7702 set code transactions (type 4). Anything
else, blob transactions included, is refused: refusing what Guard does not model is the safe default
for a co-signer.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from goldenhour.guard import rlp
from goldenhour.guard.evm import address_from_bytes, address_to_bytes, keccak256
from goldenhour.guard.secp256k1 import HALF_N, N, recover_address

SET_CODE_MAGIC = b"\x05"  # EIP-7702: authorizations sign keccak256(0x05 || rlp([chain_id, address, nonce]))


class TransactionError(ValueError):
    """Bytes that are not a transaction Guard will consider."""


# Members are read-only properties, so the request model's narrower field types (lists, a Literal)
# still satisfy these protocols.
class AccessEntry(Protocol):
    @property
    def address(self) -> str: ...
    @property
    def storage_keys(self) -> Sequence[bytes]: ...


class AuthorizationTuple(Protocol):
    @property
    def chain_id(self) -> int: ...
    @property
    def address(self) -> str: ...
    @property
    def nonce(self) -> int: ...
    @property
    def y_parity(self) -> int: ...
    @property
    def r(self) -> int: ...
    @property
    def s(self) -> int: ...


class Envelope(Protocol):
    """What the encoder needs to know about a transaction. The request model provides it."""

    @property
    def type(self) -> int: ...
    @property
    def chain_id(self) -> int: ...
    @property
    def nonce(self) -> int | None: ...
    @property
    def gas_price(self) -> int | None: ...
    @property
    def max_priority_fee_per_gas(self) -> int | None: ...
    @property
    def max_fee_per_gas(self) -> int | None: ...
    @property
    def gas(self) -> int | None: ...
    @property
    def to(self) -> str | None: ...
    @property
    def value(self) -> int: ...
    @property
    def data(self) -> bytes: ...
    @property
    def access_list(self) -> Sequence[AccessEntry]: ...
    @property
    def authorization_list(self) -> Sequence[AuthorizationTuple]: ...


@dataclass(frozen=True)
class Signature:
    y_parity: int
    r: int
    s: int


def _access_list(entries: Sequence[AccessEntry]) -> list[rlp.Item]:
    return [[address_to_bytes(e.address), list(e.storage_keys)] for e in entries]


def _authorizations(entries: Sequence[AuthorizationTuple]) -> list[rlp.Item]:
    return [
        [
            rlp.encode_int(a.chain_id),
            address_to_bytes(a.address),
            rlp.encode_int(a.nonce),
            rlp.encode_int(a.y_parity),
            rlp.encode_int(a.r),
            rlp.encode_int(a.s),
        ]
        for a in entries
    ]


def _fields(tx: Envelope) -> list[rlp.Item]:
    if tx.nonce is None or tx.gas is None:
        raise TransactionError("a transaction needs a nonce and a gas limit")
    to = address_to_bytes(tx.to) if tx.to else b""
    common = [rlp.encode_int(tx.gas), to, rlp.encode_int(tx.value), tx.data]
    if tx.type in (0, 1):
        if tx.gas_price is None:
            raise TransactionError(f"a type {tx.type} transaction needs gas_price")
        fees = [rlp.encode_int(tx.gas_price)]
    else:
        if tx.max_fee_per_gas is None or tx.max_priority_fee_per_gas is None:
            raise TransactionError(f"a type {tx.type} transaction needs both max fee fields")
        fees = [rlp.encode_int(tx.max_priority_fee_per_gas), rlp.encode_int(tx.max_fee_per_gas)]
    body: list[rlp.Item] = [rlp.encode_int(tx.nonce), *fees, *common]
    if tx.type == 0:
        return body
    body = [rlp.encode_int(tx.chain_id), *body, _access_list(tx.access_list)]
    if tx.type == 4:
        body.append(_authorizations(tx.authorization_list))
    return body


def unsigned_payload(tx: Envelope) -> bytes:
    """The exact bytes whose keccak256 the sender signs (EIP-155 form for legacy transactions)."""
    fields = _fields(tx)
    if tx.type == 0:
        return rlp.encode([*fields, rlp.encode_int(tx.chain_id), b"", b""])
    return bytes([tx.type]) + rlp.encode(fields)


def signed_payload(tx: Envelope, signature: Signature) -> bytes:
    """The raw signed transaction, whose keccak256 is the transaction hash."""
    fields = _fields(tx)
    if tx.type == 0:
        v = signature.y_parity + 35 + 2 * tx.chain_id
        return rlp.encode([*fields, rlp.encode_int(v), rlp.encode_int(signature.r), rlp.encode_int(signature.s)])
    tail = [rlp.encode_int(signature.y_parity), rlp.encode_int(signature.r), rlp.encode_int(signature.s)]
    return bytes([tx.type]) + rlp.encode([*fields, *tail])


def authorization_preimage(chain_id: int, address: str, nonce: int) -> bytes:
    return SET_CODE_MAGIC + rlp.encode([rlp.encode_int(chain_id), address_to_bytes(address), rlp.encode_int(nonce)])


def parse_authorization_message(message: bytes) -> tuple[int, str, int] | None:
    """(chain_id, address, nonce) when `message` is an EIP-7702 authorization preimage, else None."""
    if message[:1] != SET_CODE_MAGIC:
        return None
    try:
        item = rlp.decode(message[1:])
        if not isinstance(item, list) or len(item) != 3 or not isinstance(item[1], bytes):
            return None
        return rlp.decode_int(item[0]), address_from_bytes(item[1]), rlp.decode_int(item[2])
    except ValueError:
        return None


def authority(entry: AuthorizationTuple) -> str | None:
    """Who signed an authorization, or None when the chain would skip it as invalid (EIP-7702)."""
    if entry.y_parity not in (0, 1) or entry.s > HALF_N or entry.nonce >= 2**64:
        return None
    digest = keccak256(authorization_preimage(entry.chain_id, entry.address, entry.nonce))
    try:
        return recover_address(digest, entry.y_parity, entry.r, entry.s)
    except ValueError:
        return None


def _int(item: rlp.Item, name: str) -> int:
    try:
        return rlp.decode_int(item)
    except rlp.RLPError as exc:
        raise TransactionError(f"field {name}: {exc}") from exc


def _bytes(item: rlp.Item, name: str) -> bytes:
    if not isinstance(item, bytes):
        raise TransactionError(f"field {name} must be a byte string")
    return item


def _to(item: rlp.Item) -> str | None:
    raw = _bytes(item, "to")
    if raw == b"":
        return None
    if len(raw) != 20:
        raise TransactionError("field to must be empty or 20 bytes")
    return address_from_bytes(raw)


def _list(item: rlp.Item, name: str) -> list[rlp.Item]:
    if not isinstance(item, list):
        raise TransactionError(f"field {name} must be a list")
    return item


def _parse_access_list(item: rlp.Item) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for entry in _list(item, "access_list"):
        pair = _list(entry, "access_list entry")
        if len(pair) != 2:
            raise TransactionError("an access list entry is [address, storage_keys]")
        address = _bytes(pair[0], "access_list address")
        keys = [_bytes(k, "storage key") for k in _list(pair[1], "storage_keys")]
        if len(address) != 20 or any(len(k) != 32 for k in keys):
            raise TransactionError("access list holds a malformed address or storage key")
        entries.append({"address": address_from_bytes(address), "storage_keys": keys})
    return entries


def _parse_authorizations(item: rlp.Item) -> list[dict[str, object]]:
    entries = []
    for entry in _list(item, "authorization_list"):
        fields = _list(entry, "authorization")
        if len(fields) != 6:
            raise TransactionError("an authorization is [chain_id, address, nonce, y_parity, r, s]")
        address = _bytes(fields[1], "authorization address")
        if len(address) != 20:
            raise TransactionError("authorization address must be 20 bytes")
        entries.append(
            {
                "chain_id": _int(fields[0], "authorization chain_id"),
                "address": address_from_bytes(address),
                "nonce": _int(fields[2], "authorization nonce"),
                "y_parity": _int(fields[3], "authorization y_parity"),
                "r": _int(fields[4], "authorization r"),
                "s": _int(fields[5], "authorization s"),
            }
        )
    return entries


def _signature(y_parity: int, r: int, s: int) -> Signature:
    if y_parity not in (0, 1) or not 0 < r < N or not 0 < s <= HALF_N:
        raise TransactionError("invalid signature: out of range, or a high s value that EIP-2 forbids")
    return Signature(y_parity, r, s)


def parse_raw(raw: bytes) -> tuple[dict[str, object], Signature | None]:
    """Decode a raw transaction, signed or unsigned, into request fields and its signature if any.

    Unsigned legacy transactions may be in their six-field form or in the nine-field EIP-155 form
    ending `[chain_id, 0, 0]`. Signed legacy transactions without EIP-155 replay protection are
    refused: such a signature is valid on every chain at once.
    """
    if not raw:
        raise TransactionError("empty transaction")
    try:
        if raw[0] >= 0xC0:
            return _parse_legacy(rlp.decode(raw))
        if raw[0] not in (1, 2, 4):
            raise TransactionError(f"unsupported transaction type {raw[0]}")
        return _parse_typed(raw[0], rlp.decode(raw[1:]))
    except rlp.RLPError as exc:
        raise TransactionError(f"not a valid transaction encoding: {exc}") from exc


def _parse_legacy(item: rlp.Item) -> tuple[dict[str, object], Signature | None]:
    fields = _list(item, "transaction")
    if len(fields) not in (6, 9):
        raise TransactionError(f"a legacy transaction has 6 or 9 fields, not {len(fields)}")
    parsed: dict[str, object] = {
        "type": 0,
        "nonce": _int(fields[0], "nonce"),
        "gas_price": _int(fields[1], "gas_price"),
        "gas": _int(fields[2], "gas"),
        "to": _to(fields[3]),
        "value": _int(fields[4], "value"),
        "data": _bytes(fields[5], "data"),
    }
    if len(fields) == 6:
        return parsed, None
    v, r, s = (_int(fields[i], name) for i, name in ((6, "v"), (7, "r"), (8, "s")))
    if r == 0 and s == 0:
        parsed["chain_id"] = v
        return parsed, None
    if v < 35:
        raise TransactionError("signed without EIP-155 replay protection, so valid on every chain")
    parsed["chain_id"] = (v - 35) // 2
    return parsed, _signature((v - 35) % 2, r, s)


_TYPED_FIELDS = {
    1: ("chain_id", "nonce", "gas_price", "gas", "to", "value", "data", "access_list"),
    2: (
        "chain_id",
        "nonce",
        "max_priority_fee_per_gas",
        "max_fee_per_gas",
        "gas",
        "to",
        "value",
        "data",
        "access_list",
    ),
    4: (
        "chain_id",
        "nonce",
        "max_priority_fee_per_gas",
        "max_fee_per_gas",
        "gas",
        "to",
        "value",
        "data",
        "access_list",
        "authorization_list",
    ),
}


def _parse_typed(tx_type: int, item: rlp.Item) -> tuple[dict[str, object], Signature | None]:
    names = _TYPED_FIELDS[tx_type]
    fields = _list(item, "transaction")
    if len(fields) not in (len(names), len(names) + 3):
        raise TransactionError(f"a type {tx_type} transaction has {len(names)} fields, or {len(names) + 3} signed")
    parsed: dict[str, object] = {"type": tx_type}
    for name, value in zip(names, fields, strict=False):
        if name == "to":
            parsed[name] = _to(value)
        elif name == "data":
            parsed[name] = _bytes(value, name)
        elif name == "access_list":
            parsed[name] = _parse_access_list(value)
        elif name == "authorization_list":
            parsed[name] = _parse_authorizations(value)
        else:
            parsed[name] = _int(value, name)
    if len(fields) == len(names):
        return parsed, None
    y_parity, r, s = (_int(fields[len(names) + i], name) for i, name in enumerate(("y_parity", "r", "s")))
    return parsed, _signature(y_parity, r, s)
