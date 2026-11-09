"""The signing hash a key signs, and the request digest approvers sign."""

from __future__ import annotations

from goldenhour.guard import rlp, secp256k1
from goldenhour.guard.digest import REQUEST_DOMAIN, request_digest, signing_hash, signing_preimage
from goldenhour.guard.evm import address_to_bytes, keccak256
from goldenhour.guard.request import SigningRequest
from goldenhour.guard.transaction import authorization_preimage, unsigned_payload

from .helpers import ATTACKER, CUSTOMER, HOT, bybit_fixture, message, tx, typed
from .test_primitives import EIP155_HASH, MAIL


def test_legacy_signing_hash_matches_eip155() -> None:
    request = SigningRequest.model_validate(
        {
            "kind": "evm_tx",
            "chain_id": 1,
            "wallet": HOT,
            "type": 0,
            "nonce": 9,
            "gas_price": 20 * 10**9,
            "gas": 21000,
            "to": "0x" + "35" * 20,
            "value": 10**18,
        }
    )
    assert signing_hash(request) == EIP155_HASH


def test_bybit_signing_hash_recovers_the_account_that_sent_it() -> None:
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
    sender = secp256k1.recover_address(
        signing_hash(request), int(fixture["v"], 16) - 37, int(fixture["r"], 16), int(fixture["s"], 16)
    )
    assert sender == fixture["from"]


def test_typed_data_signing_hash_is_the_eip712_digest() -> None:
    assert signing_hash(typed(MAIL)).hex() == "be609aee343fb3c4b28e1df9e632fca64fcfaede20f02e86244efddf30957bd2"


def test_messages_get_the_eip191_prefix_except_authorizations() -> None:
    # The personal_sign hash of "hello", as wallets compute it.
    assert signing_hash(message(b"hello")).hex() == "50b2c43fd39106bafbba0da34fc430e1f91e3c96ea2acee2bc34119f92b37750"
    authorization = authorization_preimage(1, ATTACKER, 3)
    assert signing_preimage(message(authorization)) == authorization
    assert signing_hash(message(authorization)) == keccak256(authorization)


def test_request_digest_is_the_documented_encoding() -> None:
    request = tx(value=1, intent_id="wd-1")
    expected = keccak256(
        rlp.encode([REQUEST_DOMAIN, b"evm_tx", 1, address_to_bytes(HOT), b"wd-1", unsigned_payload(request)])
    )
    assert request_digest(request) == expected


def test_request_digest_binds_context_but_not_approvals_or_prices() -> None:
    base = tx(value=1, intent_id="wd-1")
    digest = request_digest(base)
    same = [
        tx(value=1, intent_id="wd-1", approvals=[{"approver": "alice", "signature": "00"}]),
        tx(value=1, intent_id="wd-1", context={"prices_usd": {"ETH": "1"}}),
    ]
    assert all(request_digest(r) == digest for r in same)
    different = [
        tx(value=1, intent_id="wd-2"),
        tx(value=1),
        tx(value=2, intent_id="wd-1"),
        tx(value=1, intent_id="wd-1", to=ATTACKER),
        tx(value=1, intent_id="wd-1", wallet=CUSTOMER),
        tx(value=1, intent_id="wd-1", chain_id=10),
    ]
    assert len({request_digest(r) for r in different} | {digest}) == len(different) + 1
