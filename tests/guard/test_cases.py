"""The public cases, and the real Bybit transaction they start from (stored offline as a fixture)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from goldenhour.guard import cases, secp256k1
from goldenhour.guard.decode import DelegateCall, SafeExec, decode
from goldenhour.guard.engine import evaluate
from goldenhour.guard.evm import keccak256
from goldenhour.guard.ledger import MemoryLedger
from goldenhour.guard.policy import load_policy
from goldenhour.guard.request import SigningRequest
from goldenhour.guard.transaction import Signature, authorization_preimage, signed_payload, unsigned_payload

from .helpers import NOW, binance_fixture, bybit_fixture, call, exec_transaction, hexed, sign

EXPECTED = {
    "bybit-2025-02-21": ("deny", ["no-delegatecall"]),
    "binance-2026-10-29": ("deny", ["no-eip7702-delegation"]),
    "withdrawal-with-intent": ("allow", ["intent-required"]),
    "withdrawal-without-intent": ("deny", ["intent-required"]),
    "unlimited-approval": ("deny", ["no-unlimited-approval", "approval-allowlist"]),
    "treasury-new-destination": ("hold", ["max-per-tx", "destination-allowlist", "intent-required", "quorum"]),
    "treasury-new-destination-approved": (
        "allow",
        ["max-per-tx", "destination-allowlist", "intent-required", "quorum"],
    ),
}


def offline(tx_hash: str) -> dict[str, Any]:
    return {cases.BYBIT_TX: bybit_fixture, cases.BINANCE_TX: binance_fixture}[tx_hash]()


@pytest.fixture(scope="module")
def document() -> dict[str, Any]:
    return cases.build_cases(fetch=offline)


def test_document_shape(document: dict[str, Any]) -> None:
    assert set(document) == {"policy_yaml", "cases"}
    assert document["policy_yaml"] == (cases.POLICIES_DIR / "demo.yaml").read_text()
    for case in document["cases"]:
        assert set(case) == {"id", "title", "story", "chain", "tx", "request", "decision", "reasons"}
        assert case["chain"] == "ethereum"
        assert all(set(reason) == {"rule", "message"} for reason in case["reasons"])
        assert SigningRequest.model_validate(case["request"])  # every request can be replayed


def test_decisions_and_reasons(document: dict[str, Any]) -> None:
    got = {c["id"]: (c["decision"], [r["rule"] for r in c["reasons"]]) for c in document["cases"]}
    assert got == EXPECTED


def test_the_attack_cases_name_their_real_transactions(document: dict[str, Any]) -> None:
    by_id = {c["id"]: c for c in document["cases"]}
    assert by_id["bybit-2025-02-21"]["tx"] == cases.BYBIT_TX
    assert by_id["binance-2026-10-29"]["tx"] == cases.BINANCE_TX
    assert all(c["tx"] is None for i, c in by_id.items() if i not in {"bybit-2025-02-21", "binance-2026-10-29"})
    binance = by_id["binance-2026-10-29"]["request"]
    assert (binance["kind"], binance["wallet"]) == ("evm_message", "0x28C6c06298d514Db089934071355E5743bf21d60")


def test_the_document_is_deterministic_and_plain(document: dict[str, Any]) -> None:
    text = json.dumps(document, sort_keys=True)
    assert text == json.dumps(cases.build_cases(fetch=offline), sort_keys=True)
    assert chr(0x2014) not in text and chr(0x2013) not in text  # no em or en dashes in public text


def test_demo_policy_reproduces_the_held_and_approved_pair(document: dict[str, Any]) -> None:
    by_id = {c["id"]: c for c in document["cases"]}
    held = SigningRequest.model_validate(by_id["treasury-new-destination"]["request"])
    approved = SigningRequest.model_validate(by_id["treasury-new-destination-approved"]["request"])
    policy = load_policy(document["policy_yaml"])
    ledger = MemoryLedger(cases.demo_intents())
    first = evaluate(held, policy, ledger, cases.DEMO_NOW)
    second = evaluate(approved, policy, ledger, cases.DEMO_NOW)
    assert first.request_digest == second.request_digest
    assert second.quorum.approvers == ("alice", "bob")


def test_bybit_fixture_is_the_real_transaction() -> None:
    fixture = bybit_fixture()
    request = cases.bybit_request(fixture)
    signature = Signature(int(fixture["v"], 16) - 37, int(fixture["r"], 16), int(fixture["s"], 16))
    assert "0x" + keccak256(signed_payload(request, signature)).hex() == fixture["hash"]
    sender = secp256k1.recover_address(
        keccak256(unsigned_payload(request)), signature.y_parity, signature.r, signature.s
    )
    assert sender == fixture["from"] == "0x0fa09c3a328792253f8dee7116848723b72a6d2e"


def test_bybit_transaction_decodes_as_a_disguised_delegatecall() -> None:
    actions = decode(cases.bybit_request(bybit_fixture()))
    safe_exec, delegatecall = actions
    assert isinstance(safe_exec, SafeExec) and isinstance(delegatecall, DelegateCall)
    assert (safe_exec.safe, safe_exec.to, safe_exec.value, safe_exec.operation) == (
        cases.BYBIT_SAFE,
        cases.BYBIT_DELEGATE_TARGET,
        0,
        1,
    )
    assert (safe_exec.safe_tx_gas, safe_exec.base_gas, safe_exec.gas_price) == (45746, 0, 0)
    assert safe_exec.gas_token == safe_exec.refund_receiver == "0x" + "00" * 20
    assert safe_exec.data == call("transfer(address,uint256)", "0xbdd077f651ebe7f7b3ce16fe5f2b025be2969516", 0)
    assert len(safe_exec.signatures or b"") == 3 * 65  # three owner signatures
    assert delegatecall.function == "transfer(address,uint256)"


def test_bybit_case_is_denied_by_no_delegatecall_alone() -> None:
    policy = load_policy(cases.POLICIES_DIR / "demo.yaml")
    decision = evaluate(cases.bybit_request(bybit_fixture()), policy, MemoryLedger(), NOW)
    assert decision.outcome == "deny"
    assert [(f.rule, f.effect) for f in decision.findings] == [("no-delegatecall", "deny")]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"hash": "0x" + "00" * 32}, "not the legacy transaction"),
        ({"type": "0x2"}, "not the legacy transaction"),
        ({"nonce": "0x2b"}, "do not hash"),
        ({"input": bybit_fixture()["input"].replace("0000000001", "0000000000", 1)}, "do not hash"),
    ],
)
def test_a_tampered_fixture_is_refused(change: dict[str, str], reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        cases.bybit_request({**bybit_fixture(), **change})


def test_structural_checks_after_the_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transaction that hashes correctly but is not a delegatecall to the target is refused too."""
    key = cases.demo_private_key("hot wallet")
    body = {
        "kind": "evm_tx",
        "chain_id": 1,
        "wallet": cases.BYBIT_SAFE,
        "type": 0,
        "nonce": 1,
        "gas_price": 1,
        "gas": 100_000,
        "to": cases.BYBIT_SAFE,
        "data": hexed(exec_transaction(cases.BYBIT_DELEGATE_TARGET, b"", operation=0)),
    }
    request = SigningRequest.model_validate(body)
    y_parity, r, s = sign(keccak256(unsigned_payload(request)), key)
    tx_hash = "0x" + keccak256(signed_payload(request, Signature(y_parity, r, s))).hex()
    rpc = {
        "hash": tx_hash,
        "type": "0x0",
        "chainId": "0x1",
        "nonce": "0x1",
        "gasPrice": "0x1",
        "gas": hex(100_000),
        "to": cases.BYBIT_SAFE,
        "value": "0x0",
        "input": body["data"],
        "v": hex(37 + y_parity),
        "r": hex(r),
        "s": hex(s),
    }
    monkeypatch.setattr(cases, "BYBIT_TX", tx_hash)
    with pytest.raises(ValueError, match="not an execTransaction with operation 1"):
        cases.bybit_request(rpc)


def _binance_request(tx: dict[str, Any]) -> SigningRequest:
    return SigningRequest.model_validate(
        {
            "kind": "evm_tx",
            "chain_id": 1,
            "wallet": tx["from"],
            "type": 4,
            "nonce": tx["nonce"],
            "gas": tx["gas"],
            "max_fee_per_gas": tx["maxFeePerGas"],
            "max_priority_fee_per_gas": tx["maxPriorityFeePerGas"],
            "to": tx["to"],
            "value": tx["value"],
            "data": tx["input"],
            "authorization_list": [
                {
                    "chain_id": a["chainId"],
                    "address": a["address"],
                    "nonce": a["nonce"],
                    "y_parity": a["yParity"],
                    "r": a["r"],
                    "s": a["s"],
                }
                for a in tx["authorizationList"]
            ],
        }
    )


def test_binance_fixture_is_the_real_transaction() -> None:
    fixture = binance_fixture()
    request = _binance_request(fixture)
    signature = Signature(int(fixture["yParity"], 16), int(fixture["r"], 16), int(fixture["s"], 16))
    assert "0x" + keccak256(signed_payload(request, signature)).hex() == cases.BINANCE_TX == fixture["hash"]
    sender = secp256k1.recover_address(
        keccak256(unsigned_payload(request)), signature.y_parity, signature.r, signature.s
    )
    assert sender == fixture["from"] == "0x7c8601d4645b93cea45f71e118a729332e6b5ca0"
    assert (int(fixture["blockNumber"], 16), request.to, request.data, request.value) == (
        26_300_753,
        cases.BINANCE_14,
        b"",
        0,
    )


def test_all_fifty_authorizations_are_binance_14_delegating_with_consecutive_nonces() -> None:
    actions = decode(_binance_request(binance_fixture()))
    assert len(actions) == 50
    assert {(a.kind, a.authority, a.delegate, a.chain_id) for a in actions} == {
        ("eip7702_delegation", cases.BINANCE_14, cases.BINANCE_DELEGATE, 1)
    }
    nonces = [a.nonce for a in actions]
    assert nonces == list(range(18_914_824, 18_914_774, -1))  # highest first, so at most one can apply
    assert cases.BINANCE_EFFECTIVE_NONCE in nonces


def test_guard_refuses_every_one_of_the_fifty_before_signing() -> None:
    policy = load_policy(cases.POLICIES_DIR / "demo.yaml")
    for entry in _binance_request(binance_fixture()).authorization_list:
        message = authorization_preimage(entry.chain_id, entry.address, entry.nonce)
        request = SigningRequest.model_validate(
            {"kind": "evm_message", "chain_id": 1, "wallet": cases.BINANCE_14, "message": hexed(message)}
        )
        decision = evaluate(request, policy, MemoryLedger(), NOW)
        assert decision.outcome == "deny"
        assert [(f.rule, f.effect) for f in decision.findings] == [("no-eip7702-delegation", "deny")]
        # The signing hash Guard computes is the hash Binance 14's key actually signed.
        assert secp256k1.recover_address(
            bytes.fromhex(decision.signing_hash[2:]), entry.y_parity, entry.r, entry.s
        ) == (cases.BINANCE_14)


def test_binance_request_is_the_authorization_that_took_effect() -> None:
    request = cases.binance_request(binance_fixture())
    assert request.message == authorization_preimage(1, cases.BINANCE_DELEGATE, 18_914_809)
    (delegation,) = decode(request)
    assert (delegation.authority, delegation.delegate, delegation.nonce) == (
        cases.BINANCE_14,
        cases.BINANCE_DELEGATE,
        18_914_809,
    )


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"hash": "0x" + "00" * 32}, "not the type 4 transaction"),
        ({"type": "0x2"}, "not the type 4 transaction"),
        ({"maxFeePerGas": "0x2e90edd001"}, "do not hash"),
    ],
)
def test_a_tampered_binance_fixture_is_refused(change: dict[str, str], reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        cases.binance_request({**binance_fixture(), **change})
    tampered = binance_fixture()
    tampered["authorizationList"][7]["nonce"] = "0x1"
    with pytest.raises(ValueError, match="do not hash"):
        cases.binance_request(tampered)


def test_binance_checks_after_the_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    """A type 4 transaction that hashes correctly but is not fifty Binance 14 delegations is refused."""
    key = cases.demo_private_key("hot wallet")
    hot = cases.HOT

    def authorization(nonce: int) -> dict[str, Any]:
        y_parity, r, s = sign(keccak256(authorization_preimage(1, cases.BINANCE_DELEGATE, nonce)), key)
        return {
            "chainId": "0x1",
            "address": cases.BINANCE_DELEGATE,
            "nonce": hex(nonce),
            "yParity": hex(y_parity),
            "r": hex(r),
            "s": hex(s),
        }

    def signed(authorizations: list[dict[str, Any]]) -> dict[str, Any]:
        rpc = {
            "type": "0x4",
            "chainId": "0x1",
            "from": hot,
            "nonce": "0x0",
            "gas": "0x100000",
            "maxFeePerGas": "0x2",
            "maxPriorityFeePerGas": "0x1",
            "to": cases.BINANCE_14,
            "value": "0x0",
            "input": "0x",
            "accessList": [],
            "authorizationList": authorizations,
        }
        request = _binance_request(rpc)
        y_parity, r, s = sign(keccak256(unsigned_payload(request)), key)
        tx_hash = "0x" + keccak256(signed_payload(request, Signature(y_parity, r, s))).hex()
        monkeypatch.setattr(cases, "BINANCE_TX", tx_hash)
        return {**rpc, "hash": tx_hash, "yParity": hex(y_parity), "r": hex(r), "s": hex(s)}

    with pytest.raises(ValueError, match="not Binance 14 delegating"):
        cases.binance_request(signed([authorization(n) for n in range(50)]))
    with pytest.raises(ValueError, match="not fifty authorizations"):
        cases.binance_request(signed([authorization(1)]))


@respx.mock
def test_fetch_transaction_identifies_itself_and_falls_back() -> None:
    first, second = cases.RPC_URLS
    primary = respx.post(first).mock(return_value=httpx.Response(200, json={"result": bybit_fixture()}))
    assert cases.fetch_transaction(cases.BYBIT_TX)["hash"] == cases.BYBIT_TX
    sent = primary.calls.last.request
    assert sent.headers["User-Agent"].startswith("Goldenhour/")
    assert json.loads(sent.content)["method"] == "eth_getTransactionByHash"
    respx.post(first).mock(return_value=httpx.Response(200, json={"result": None}))
    backup = respx.post(second).mock(return_value=httpx.Response(200, json={"result": binance_fixture()}))
    assert cases.fetch_transaction(cases.BINANCE_TX)["hash"] == cases.BINANCE_TX
    assert backup.calls.last.request.headers["User-Agent"].startswith("Goldenhour/")
    respx.post(second).mock(return_value=httpx.Response(200, json={"result": None}))
    with pytest.raises(ValueError, match="no public node returned"):
        cases.fetch_transaction(cases.BINANCE_TX)


def test_the_shipped_copies_build_the_document_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_network(tx_hash: str) -> dict[str, Any]:
        raise AssertionError(f"tried to fetch {tx_hash}")

    monkeypatch.setattr(cases, "fetch_transaction", no_network)
    assert cases.stored_transaction(cases.BYBIT_TX) == bybit_fixture()
    assert cases.stored_transaction(cases.BINANCE_TX) == binance_fixture()
    assert cases.build_cases() == cases.build_cases(fetch=offline)
