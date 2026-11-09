"""Builders shared by the Guard tests: a policy, requests, call data, intents and approvals."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from goldenhour.guard import secp256k1
from goldenhour.guard.cases import demo_address, demo_approver, demo_private_key
from goldenhour.guard.digest import approval_message, request_digest
from goldenhour.guard.evm import address_to_bytes, selector
from goldenhour.guard.keys import public_key_hex
from goldenhour.guard.ledger import Intent
from goldenhour.guard.policy import Policy
from goldenhour.guard.request import SigningRequest

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
ETH = 10**18

HOT = demo_address("hot wallet")
HOT_KEY = demo_private_key("hot wallet")
TREASURY = demo_address("treasury wallet")
SAFE = "0x5afe5afe5afe5afe5afe5afe5afe5afe5afe5afe"
CUSTOMER = "0x00000000000000000000000000000000c0ffee01"
COLD = "0x00000000000000000000000000000000000c01d0"
ATTACKER = "0xbad0000000000000000000000000000000000bad"
ROUTER = "0x0000000000000000000000000000000000007007"
VAULT = "0x0000000000000000000000000000000000000ba5"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
MULTISEND_CALL_ONLY = "0x40a2accbd92bca938b02010e17a5b8929b49130d"
PERMIT2 = "0x000000000022d473030f116ddee9f6b43ac78ba3"
MAX_UINT256 = 2**256 - 1


def base_policy() -> dict[str, Any]:
    return {
        "version": 1,
        "name": "test",
        "wallets": [
            {"address": HOT, "label": "hot", "role": "hot"},
            {"address": TREASURY, "label": "treasury", "role": "treasury", "max_per_tx_usd": 1_000_000},
            {"address": SAFE, "label": "safe", "role": "cold"},
        ],
        "assets": [
            {"chain_id": 1, "address": "native", "symbol": "ETH", "decimals": 18, "price_floor_usd": 1000},
            {"chain_id": 1, "address": USDC, "symbol": "USDC", "decimals": 6, "price_floor_usd": "0.9"},
        ],
        "rules": {
            "no-eip7702-delegation": {"severity": "deny"},
            "no-delegatecall": {"severity": "deny", "allow_targets": [MULTISEND_CALL_ONLY]},
            "no-blind-signing": {"severity": "deny"},
            "no-upgrade-or-owner-change": {"severity": "deny", "change_windows": []},
            "no-unlimited-approval": {"severity": "deny"},
            "approval-allowlist": {"severity": "deny", "spenders": [{"address": ROUTER, "label": "router"}]},
            "intent-required": {"severity": "deny"},
            "velocity": {"severity": "hold", "window": "24h", "limit_usd": 100_000},
            "max-per-tx": {"severity": "hold", "limit_usd": 50_000},
            "destination-allowlist": {"severity": "hold", "roles": ["treasury"], "destinations": [COLD]},
            "explainable": {"severity": "hold", "contracts": []},
        },
        "quorum": {
            "threshold": 2,
            "approvers": [{"id": n, "public_key": public_key_hex(demo_approver(n))} for n in ("alice", "bob", "carol")],
        },
    }


def _merge(base: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in changes.items():
        out[key] = _merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def make_policy(changes: dict[str, Any] | None = None) -> Policy:
    return Policy.model_validate(_merge(base_policy(), changes or {}))


def tx(**fields: Any) -> SigningRequest:
    """A type 2 transaction from the hot wallet; any field can be overridden."""
    body: dict[str, Any] = {
        "kind": "evm_tx",
        "chain_id": 1,
        "wallet": HOT,
        "type": 2,
        "nonce": 1,
        "gas": 100_000,
        "max_fee_per_gas": 30 * 10**9,
        "max_priority_fee_per_gas": 10**9,
        "to": CUSTOMER,
        "context": {"prices_usd": {"ETH": "3000", "USDC": "1"}},
    }
    body.update(fields)
    return SigningRequest.model_validate(body)


def typed(typed_data: dict[str, Any], **fields: Any) -> SigningRequest:
    return SigningRequest.model_validate(
        {"kind": "evm_typed_data", "chain_id": 1, "wallet": HOT, "typed_data": typed_data, **fields}
    )


def message(data: bytes, **fields: Any) -> SigningRequest:
    return SigningRequest.model_validate(
        {"kind": "evm_message", "chain_id": 1, "wallet": HOT, "message": "0x" + data.hex(), **fields}
    )


def _word(kind: str, value: Any) -> bytes:
    if kind == "address":
        return address_to_bytes(value).rjust(32, b"\x00")
    if kind == "bool":
        return int(bool(value)).to_bytes(32, "big")
    if kind.startswith("bytes"):
        return bytes(value).ljust(32, b"\x00")
    return int(value).to_bytes(32, "big")


def _dynamic(value: bytes) -> bytes:
    return len(value).to_bytes(32, "big") + value + b"\x00" * (-len(value) % 32)


def encode_args(types: list[str], args: tuple[Any, ...]) -> bytes:
    heads, tails = b"", b""
    offset = 32 * len(types)
    for kind, value in zip(types, args, strict=True):
        if kind == "bytes":
            tail = _dynamic(value)
        elif kind == "bytes[]":
            items = [_dynamic(item) for item in value]
            pointers, cursor = b"", 32 * len(items)
            for item in items:
                pointers += cursor.to_bytes(32, "big")
                cursor += len(item)
            tail = len(items).to_bytes(32, "big") + pointers + b"".join(items)
        else:
            heads += _word(kind, value)
            continue
        heads += offset.to_bytes(32, "big")
        tails += tail
        offset += len(tail)
    return heads + tails


def call(signature: str, *args: Any) -> bytes:
    types = signature[signature.index("(") + 1 : -1].split(",") if not signature.endswith("()") else []
    return selector(signature) + encode_args(types, args)


def exec_transaction(
    to: str,
    data: bytes = b"",
    *,
    value: int = 0,
    operation: int = 0,
    gas_price: int = 0,
    gas_token: str = "0x" + "00" * 20,
    refund_receiver: str = "0x" + "00" * 20,
) -> bytes:
    return call(
        "execTransaction(address,uint256,bytes,uint8,uint256,uint256,uint256,address,address,bytes)",
        to,
        value,
        data,
        operation,
        50_000,
        0,
        gas_price,
        gas_token,
        refund_receiver,
        b"\x01" * 65,
    )


def multisend(*entries: tuple[int, str, int, bytes]) -> bytes:
    payload = b"".join(
        bytes([op]) + address_to_bytes(to) + value.to_bytes(32, "big") + len(data).to_bytes(32, "big") + data
        for op, to, value, data in entries
    )
    return call("multiSend(bytes)", payload)


def hexed(data: bytes) -> str:
    return "0x" + data.hex()


def intent(**fields: Any) -> Intent:
    body: dict[str, Any] = {
        "id": "wd-1",
        "wallet": HOT,
        "chain_id": 1,
        "asset": "native",
        "destination": CUSTOMER,
        "amount": ETH,
        "expires_at": NOW + timedelta(hours=1),
    }
    body.update(fields)
    return Intent.model_validate(body)


def approved(request: SigningRequest, *names: str) -> SigningRequest:
    """The request with valid approvals from the named demo approvers."""
    signed = approval_message(request_digest(request))
    approvals = [{"approver": n, "signature": demo_approver(n).sign(signed).hex()} for n in names]
    return SigningRequest.model_validate({**request.to_json(), "approvals": approvals})


def bybit_fixture() -> dict[str, Any]:
    """The Bybit transaction as eth_getTransactionByHash returned it."""
    return json.loads((FIXTURES / "bybit-2025-02-21.json").read_text())


def binance_fixture() -> dict[str, Any]:
    """Binance 14's delegation transaction as eth_getTransactionByHash returned it."""
    return json.loads((FIXTURES / "binance-2026-10-29-delegation.json").read_text())


def _nonces(private_key: int, msg_hash: bytes) -> Iterator[int]:
    """Deterministic nonces per RFC 6979 with HMAC-SHA256, so fixtures sign the same way every run."""
    x = private_key.to_bytes(32, "big")
    h = (int.from_bytes(msg_hash, "big") % secp256k1.N).to_bytes(32, "big")
    v, k = b"\x01" * 32, b"\x00" * 32
    k = hmac.new(k, v + b"\x00" + x + h, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    k = hmac.new(k, v + b"\x01" + x + h, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    while True:
        v = hmac.new(k, v, hashlib.sha256).digest()
        candidate = int.from_bytes(v, "big")
        if 0 < candidate < secp256k1.N:
            yield candidate
        k = hmac.new(k, v + b"\x00", hashlib.sha256).digest()
        v = hmac.new(k, v, hashlib.sha256).digest()


def sign(msg_hash: bytes, private_key: int) -> tuple[int, int, int]:
    """(y_parity, r, s) with a low s, as Ethereum requires. Test fixtures only: not constant time."""
    e = int.from_bytes(msg_hash, "big")
    for k in _nonces(private_key, msg_hash):
        point = secp256k1._multiply(k, secp256k1.G)
        assert point is not None
        x, y = point
        s = pow(k, -1, secp256k1.N) * (e + x * private_key) % secp256k1.N
        if x >= secp256k1.N or s == 0:
            continue
        if s > secp256k1.HALF_N:
            return (y & 1) ^ 1, x, secp256k1.N - s
        return y & 1, x, s
    raise AssertionError("unreachable")
