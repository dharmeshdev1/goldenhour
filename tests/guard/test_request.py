"""Request validation, and raw transactions: legacy, type 2 and type 4, signed and unsigned."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from goldenhour.guard import rlp, secp256k1
from goldenhour.guard.evm import keccak256
from goldenhour.guard.request import SigningRequest
from goldenhour.guard.transaction import Signature, authorization_preimage, signed_payload, unsigned_payload

from .helpers import ATTACKER, CUSTOMER, HOT, HOT_KEY, bybit_fixture, hexed, sign, tx

EIP155_SIGNED = (
    "f86c098504a817c800825208943535353535353535353535353535353535353535880de0b6b3a76400008025a028ef61340bd939bc2195fe"
    "537567866003e1a15d3c71ff63e1590620aa636276a067cbe9d8997f761aecb703304b3800ccf555c9f3dc64214b297fb1966a3b6d83"
)
EIP155_SENDER = "0x9d8a62f656a8d1615c1294fd71e9cfb3e4855a4f"


def raw_request(raw: bytes | str, **fields: Any) -> SigningRequest:
    raw_hex = raw if isinstance(raw, str) else hexed(raw)
    return SigningRequest.model_validate({"kind": "evm_tx", "chain_id": 1, "wallet": HOT, "raw": raw_hex, **fields})


def signed(request: SigningRequest, key: int = HOT_KEY) -> bytes:
    return signed_payload(request, Signature(*sign(keccak256(unsigned_payload(request)), key)))


def test_signed_legacy_transaction_from_eip155() -> None:
    request = raw_request("0x" + EIP155_SIGNED, wallet=EIP155_SENDER)
    assert (request.type, request.nonce, request.gas, request.gas_price) == (0, 9, 21000, 20 * 10**9)
    assert request.to == "0x" + "35" * 20
    assert request.value == 10**18
    assert request.data == b""
    with pytest.raises(ValidationError, match="signed by 0x9d8a62f656a8d1615c1294fd71e9cfb3e4855a4f"):
        raw_request("0x" + EIP155_SIGNED)


def test_bybit_transaction_parses_and_names_its_real_sender() -> None:
    fixture = bybit_fixture()
    request = SigningRequest.model_validate(
        {
            "kind": "evm_tx",
            "chain_id": 1,
            "wallet": fixture["to"],
            "type": 0,
            "nonce": fixture["nonce"],
            "gas_price": fixture["gasPrice"],
            "gas": fixture["gas"],
            "to": fixture["to"],
            "value": fixture["value"],
            "data": fixture["input"],
        }
    )
    signature = Signature(int(fixture["v"], 16) - 37, int(fixture["r"], 16), int(fixture["s"], 16))
    raw = signed_payload(request, signature)
    assert hexed(keccak256(raw)) == fixture["hash"]
    parsed = raw_request(raw, wallet=fixture["from"])
    assert parsed.data == request.data
    assert parsed.nonce == 42
    # The transaction was submitted by an outside account, not by the Safe.
    with pytest.raises(ValidationError, match="not by the wallet"):
        raw_request(raw, wallet=fixture["to"])


def test_unsigned_legacy_forms() -> None:
    fields = [
        rlp.encode_int(3),
        rlp.encode_int(10**9),
        rlp.encode_int(21000),
        bytes.fromhex(CUSTOMER[2:]),
        rlp.encode_int(5),
        b"",
    ]
    six = raw_request(rlp.encode(fields))
    nine = raw_request(rlp.encode([*fields, rlp.encode_int(1), b"", b""]))
    assert six.nonce == nine.nonce == 3
    assert unsigned_payload(six) == unsigned_payload(nine) == rlp.encode([*fields, rlp.encode_int(1), b"", b""])
    with pytest.raises(ValidationError, match="chain 5"):
        raw_request(rlp.encode([*fields, rlp.encode_int(5), b"", b""]))


def test_legacy_signature_without_replay_protection_is_refused() -> None:
    fields = [rlp.encode_int(0), rlp.encode_int(1), rlp.encode_int(21000), bytes(20), b"", b""]
    with pytest.raises(ValidationError, match="replay protection"):
        raw_request(rlp.encode([*fields, rlp.encode_int(27), rlp.encode_int(5), rlp.encode_int(5)]))


def test_type2_round_trip_signed_and_unsigned() -> None:
    request = tx(value=5, data="0xabcd", access_list=[{"address": CUSTOMER, "storage_keys": ["0x" + "01" * 32]}])
    unsigned = unsigned_payload(request)
    assert unsigned[0] == 2
    parsed = raw_request(unsigned)
    assert unsigned_payload(parsed) == unsigned
    assert parsed.access_list == request.access_list
    assert raw_request(signed(request)).value == 5


def _authorization(delegate: str, nonce: int, key: int = HOT_KEY) -> dict[str, Any]:
    y_parity, r, s = sign(keccak256(authorization_preimage(1, delegate, nonce)), key)
    return {"chain_id": 1, "address": delegate, "nonce": nonce, "y_parity": y_parity, "r": r, "s": s}


def test_type4_round_trip() -> None:
    request = tx(type=4, to=HOT, authorization_list=[_authorization(ATTACKER, 2)])
    raw = signed(request)
    assert raw[0] == 4
    parsed = raw_request(raw)
    assert parsed.authorization_list == request.authorization_list
    assert parsed.type == 4


def test_raw_and_fields_must_agree() -> None:
    raw = unsigned_payload(tx(value=7))
    assert raw_request(raw, value=7, to=CUSTOMER).value == 7
    with pytest.raises(ValidationError, match="disagrees with the given fields: value"):
        raw_request(raw, value=8)
    with pytest.raises(ValidationError, match="chain 1"):
        raw_request(raw, chain_id=5)


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("0x03c0", "unsupported transaction type 3"),
        ("0x", "empty"),
        ("0x02c3010203", "has 9 fields"),
        ("0xc0", "6 or 9 fields"),
        ("0x02c5", "not a valid transaction encoding"),
        ("0x0201", "must be a list"),
    ],
)
def test_malformed_raw_transactions(raw: str, reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        raw_request(raw)


def test_high_s_signature_is_refused() -> None:
    request = tx()
    y_parity, r, s = sign(keccak256(unsigned_payload(request)), HOT_KEY)
    raw = signed_payload(request, Signature(y_parity ^ 1, r, secp256k1.N - s))
    with pytest.raises(ValidationError, match="high s"):
        raw_request(raw)


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({"to": None, "type": 4}, "destination"),
        ({"type": 4, "to": HOT}, "at least one authorization"),
        ({"authorization_list": [_authorization(ATTACKER, 1)]}, "only a type 4"),
        ({"type": 0, "gas_price": 1}, "gas_price only"),
        ({"type": 2, "max_fee_per_gas": None}, "needs max_fee_per_gas"),
        ({"max_priority_fee_per_gas": 10**12}, "exceeds"),
        (
            {
                "type": 0,
                "max_fee_per_gas": None,
                "max_priority_fee_per_gas": None,
                "gas_price": 1,
                "access_list": [{"address": CUSTOMER}],
            },
            "no access list",
        ),
        ({"nonce": None}, "nonce and gas"),
        ({"message": "0x01"}, "not typed_data or message"),
        ({"intent_id": "  "}, "blank"),
        ({"context": {"prices_usd": {"ETH": "0"}}}, "greater than 0"),
        ({"surprise": 1}, "Extra inputs"),
        ({"type": 3}, "Input should be 0, 1, 2 or 4"),
        ({"chain_id": 0}, "greater than or equal to 1"),
    ],
)
def test_transaction_shape_is_checked(fields: dict[str, Any], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        tx(**fields)


def test_destination_must_be_explicit() -> None:
    with pytest.raises(ValidationError, match="to is required"):
        SigningRequest.model_validate(
            {"kind": "evm_tx", "chain_id": 1, "wallet": HOT, "nonce": 0, "gas": 1, "gas_price": 1, "type": 0}
        )
    deployment = tx(to=None, data="0x6000")
    assert deployment.to is None
    assert deployment.to_json()["to"] is None
    assert SigningRequest.model_validate(deployment.to_json()) == deployment


def test_typed_data_and_message_requests() -> None:
    from .test_primitives import MAIL

    request = SigningRequest.model_validate(
        {"kind": "evm_typed_data", "chain_id": 1, "wallet": HOT, "typed_data": MAIL}
    )
    assert set(request.to_json()) == {"kind", "chain_id", "wallet", "typed_data", "approvals"}
    with pytest.raises(ValidationError, match="typed data is for chain 1"):
        SigningRequest.model_validate({"kind": "evm_typed_data", "chain_id": 5, "wallet": HOT, "typed_data": MAIL})
    with pytest.raises(ValidationError, match="carry no transaction fields"):
        SigningRequest.model_validate(
            {"kind": "evm_typed_data", "chain_id": 1, "wallet": HOT, "typed_data": MAIL, "nonce": 1}
        )
    with pytest.raises(ValidationError, match="primaryType"):
        SigningRequest.model_validate(
            {"kind": "evm_typed_data", "chain_id": 1, "wallet": HOT, "typed_data": {**MAIL, "primaryType": "X"}}
        )
    with pytest.raises(ValidationError, match="carries message"):
        SigningRequest.model_validate({"kind": "evm_message", "chain_id": 1, "wallet": HOT})
    elsewhere = authorization_preimage(56, ATTACKER, 0)
    with pytest.raises(ValidationError, match="for chain 56"):
        SigningRequest.model_validate(
            {"kind": "evm_message", "chain_id": 1, "wallet": HOT, "message": hexed(elsewhere)}
        )
    everywhere = authorization_preimage(0, ATTACKER, 0)
    assert (
        SigningRequest.model_validate(
            {"kind": "evm_message", "chain_id": 1, "wallet": HOT, "message": hexed(everywhere)}
        ).message
        == everywhere
    )


def test_to_json_round_trips_and_drops_empty_fields() -> None:
    request = tx(value=10**18, intent_id="wd-1")
    out = request.to_json()
    assert out["value"] == "1000000000000000000"
    assert out["wallet"] == "0x66A2f4A63310aCa888F8e578BA5a7093BA3Bb8b3"
    assert "access_list" not in out and "raw" not in out
    assert SigningRequest.model_validate(out) == request


@pytest.mark.parametrize("kind", ["uint999", "uint7", "int0", "int264", "uint"])
def test_impossible_integer_types_are_refused_not_crashed_on(kind: str) -> None:
    from .test_primitives import MAIL

    types = {**MAIL["types"], "Mail": [*MAIL["types"]["Mail"], {"name": "amount", "type": kind}]}
    typed_data = {**MAIL, "types": types, "message": {**MAIL["message"], "amount": 2**300}}
    with pytest.raises(ValidationError, match=r"integer type|unknown type"):
        SigningRequest.model_validate(
            {"kind": "evm_typed_data", "chain_id": 1, "wallet": HOT, "typed_data": typed_data}
        )
