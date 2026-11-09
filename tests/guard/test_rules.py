"""Every rule, how findings combine, and the quorum."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest

from goldenhour.guard import keys
from goldenhour.guard.digest import approval_message, request_digest
from goldenhour.guard.engine import Decision, evaluate
from goldenhour.guard.evm import ZERO_ADDRESS, keccak256
from goldenhour.guard.ledger import MemoryLedger, Spend
from goldenhour.guard.policy import Policy
from goldenhour.guard.request import SigningRequest
from goldenhour.guard.transaction import authorization_preimage

from .helpers import (
    ATTACKER,
    COLD,
    CUSTOMER,
    ETH,
    HOT,
    HOT_KEY,
    MAX_UINT256,
    MULTISEND_CALL_ONLY,
    NOW,
    PERMIT2,
    ROUTER,
    SAFE,
    TREASURY,
    USDC,
    VAULT,
    approved,
    call,
    exec_transaction,
    hexed,
    intent,
    make_policy,
    message,
    multisend,
    sign,
    tx,
    typed,
)


def decide(
    request: SigningRequest,
    policy: Policy | None = None,
    ledger: MemoryLedger | None = None,
    now: Any = NOW,
) -> Decision:
    return evaluate(request, policy or make_policy(), ledger if ledger is not None else MemoryLedger(), now)


def rules(decision: Decision, effect: str | None = None) -> list[str]:
    return [f.rule for f in decision.findings if effect is None or f.effect == effect]


def blocking(decision: Decision) -> list[str]:
    return [f.rule for f in decision.findings if f.effect != "note"]


def test_a_plain_withdrawal_with_its_intent_is_allowed() -> None:
    decision = decide(tx(value=ETH, intent_id="wd-1"), ledger=MemoryLedger([intent()]))
    assert decision.outcome == "allow"
    assert rules(decision) == ["intent-required"]
    assert decision.intent == "wd-1"
    assert [(s.wallet, s.usd, s.limit) for s in decision.spends] == [(HOT, Decimal(3000), Decimal(100_000))]


# no-eip7702-delegation


def _authorization(delegate: str, nonce: int = 2) -> dict[str, Any]:
    y_parity, r, s = sign(keccak256(authorization_preimage(1, delegate, nonce)), HOT_KEY)
    return {"chain_id": 1, "address": delegate, "nonce": nonce, "y_parity": y_parity, "r": r, "s": s}


def test_type4_delegation_is_denied() -> None:
    decision = decide(tx(type=4, to=HOT, authorization_list=[_authorization(ATTACKER)]))
    assert decision.outcome == "deny"
    assert blocking(decision) == ["no-eip7702-delegation"]
    assert "0x66A2f4A63310aCa888F8e578BA5a7093BA3Bb8b3" in decision.findings[0].message


def test_authorization_message_is_denied_and_revocation_allowed() -> None:
    assert decide(message(authorization_preimage(0, ATTACKER, 0))).outcome == "deny"
    revocation = decide(tx(type=4, to=HOT, authorization_list=[_authorization(ZERO_ADDRESS)]))
    assert revocation.outcome == "allow"
    assert rules(revocation, "note") == ["no-eip7702-delegation"]


def test_delegation_rule_severity_and_switch() -> None:
    request = tx(type=4, to=HOT, authorization_list=[_authorization(ATTACKER)])
    assert decide(request, make_policy({"rules": {"no-eip7702-delegation": {"severity": "hold"}}})).outcome == "hold"
    assert decide(request, make_policy({"rules": {"no-eip7702-delegation": {"enabled": False}}})).outcome == "allow"


# no-delegatecall


def test_delegatecall_to_an_unlisted_target_is_denied() -> None:
    disguise = call("transfer(address,uint256)", CUSTOMER, 0)
    decision = decide(tx(to=SAFE, data=hexed(exec_transaction(ATTACKER, disguise, operation=1))))
    assert decision.outcome == "deny"
    assert blocking(decision) == ["no-delegatecall"]
    assert "transfer(address,uint256)" in decision.findings[0].message


def test_multisend_call_only_batch_is_checked_call_by_call() -> None:
    batch = multisend((0, CUSTOMER, ETH, b""))
    request = tx(to=SAFE, data=hexed(exec_transaction(MULTISEND_CALL_ONLY, batch, operation=1)), intent_id="safe-1")
    ledger = MemoryLedger([intent(id="safe-1", wallet=SAFE)])
    assert decide(request, ledger=ledger).outcome == "allow"
    nested = multisend((0, CUSTOMER, ETH, b""), (1, ATTACKER, 0, b""))
    denied = decide(tx(to=SAFE, data=hexed(exec_transaction(MULTISEND_CALL_ONLY, nested, operation=1))))
    assert "no-delegatecall" in rules(denied, "deny")


# no-upgrade-or-owner-change

WINDOW = {"start": "2026-11-01T11:00:00Z", "end": "2026-11-01T13:00:00Z", "reason": "signer rotation"}


@pytest.mark.parametrize(
    "data",
    [
        call("addOwnerWithThreshold(address,uint256)", ATTACKER, 1),
        call("removeOwner(address,address,uint256)", CUSTOMER, ROUTER, 1),
        call("swapOwner(address,address,address)", CUSTOMER, ROUTER, ATTACKER),
        call("changeThreshold(uint256)", 1),
        call("enableModule(address)", ATTACKER),
        call("disableModule(address,address)", CUSTOMER, ATTACKER),
        call("setGuard(address)", ZERO_ADDRESS),
        call("setFallbackHandler(address)", ATTACKER),
        call("upgradeTo(address)", ATTACKER),
        call("upgradeToAndCall(address,bytes)", ATTACKER, b"\x01"),
        call("changeMasterCopy(address)", ATTACKER),
    ],
)
def test_upgrades_and_owner_changes_need_a_window_and_a_quorum(data: bytes) -> None:
    request = tx(to=SAFE, data=hexed(exec_transaction(SAFE, data)))
    outside = decide(request)
    assert outside.outcome == "deny" and blocking(outside) == ["no-upgrade-or-owner-change"]
    assert "none is open" in outside.findings[0].message
    in_window = make_policy({"rules": {"no-upgrade-or-owner-change": {"change_windows": [WINDOW]}}})
    held = decide(request, in_window)
    assert held.outcome == "hold" and "signer rotation" in held.findings[0].message
    assert decide(approved(request, "alice", "carol"), in_window).outcome == "allow"


def test_change_window_bounds() -> None:
    policy = make_policy({"rules": {"no-upgrade-or-owner-change": {"change_windows": [WINDOW]}}})
    request = tx(to=SAFE, data=hexed(call("upgradeTo(address)", ATTACKER)))
    start, end = NOW - timedelta(hours=1), NOW + timedelta(hours=1)
    assert decide(request, policy, now=start).outcome == "hold"
    assert decide(request, policy, now=end).outcome == "deny"
    assert decide(request, policy, now=end - timedelta(seconds=1)).outcome == "hold"


# no-unlimited-approval and approval-allowlist


@pytest.mark.parametrize("amount", [MAX_UINT256, 2**160 - 1, 2**96 - 1])
def test_unlimited_approvals_are_denied_even_to_listed_spenders(amount: int) -> None:
    decision = decide(tx(to=USDC, data=hexed(call("approve(address,uint256)", ROUTER, amount))))
    assert decision.outcome == "deny" and blocking(decision) == ["no-unlimited-approval"]


def test_bounded_approval_to_a_listed_spender_is_allowed() -> None:
    assert decide(tx(to=USDC, data=hexed(call("approve(address,uint256)", ROUTER, 2**96 - 2)))).outcome == "allow"
    custom = make_policy({"rules": {"no-unlimited-approval": {"threshold": 1000}}})
    assert decide(tx(to=USDC, data=hexed(call("approve(address,uint256)", ROUTER, 1000))), custom).outcome == "deny"


def test_unknown_spender_is_denied_whatever_the_amount() -> None:
    decision = decide(tx(to=USDC, data=hexed(call("increaseAllowance(address,uint256)", ATTACKER, 5))))
    assert decision.outcome == "deny" and blocking(decision) == ["approval-allowlist"]
    both = decide(tx(to=USDC, data=hexed(call("approve(address,uint256)", ATTACKER, MAX_UINT256))))
    assert blocking(both) == ["no-unlimited-approval", "approval-allowlist"]


def test_operator_approval_is_unlimited_and_revocations_are_free() -> None:
    granted = decide(tx(to=VAULT, data=hexed(call("setApprovalForAll(address,bool)", ROUTER, True))))
    assert blocking(granted) == ["no-unlimited-approval"]
    assert decide(tx(to=VAULT, data=hexed(call("setApprovalForAll(address,bool)", ATTACKER, False)))).outcome == "allow"
    assert decide(tx(to=USDC, data=hexed(call("approve(address,uint256)", ATTACKER, 0)))).outcome == "allow"


def test_signed_permits_are_approvals_too() -> None:
    from .test_decode import DETAILS, PERMIT2_DOMAIN, PERMIT_FIELDS, permit_types

    permit = {
        "types": permit_types("Permit", PERMIT_FIELDS),
        "primaryType": "Permit",
        "domain": {"name": "USD Coin", "version": "2", "chainId": 1, "verifyingContract": USDC},
        "message": {"owner": HOT, "spender": ATTACKER, "value": 10, "nonce": 0, "deadline": 1},
    }
    assert blocking(decide(typed(permit))) == ["approval-allowlist"]
    single = {
        "types": permit_types(
            "PermitSingle",
            [("details", "PermitDetails"), ("spender", "address"), ("sigDeadline", "uint256")],
            PermitDetails=DETAILS,
        ),
        "primaryType": "PermitSingle",
        "domain": PERMIT2_DOMAIN,
        "message": {
            "details": {"token": USDC, "amount": 2**160 - 1, "expiration": 1, "nonce": 0},
            "spender": ROUTER,
            "sigDeadline": 2,
        },
    }
    assert blocking(decide(typed(single))) == ["no-unlimited-approval"]


# intent-required


def _withdrawal(**fields: Any) -> SigningRequest:
    return tx(**{"value": ETH, "intent_id": "wd-1", **fields})


@pytest.mark.parametrize(
    ("stored", "fields", "reason"),
    [
        ([], {"intent_id": None}, "names no withdrawal intent"),
        ([], {}, "is not in the ledger"),
        ([{"expires_at": NOW}], {}, "expired"),
        ([{"wallet": TREASURY}], {}, "pays from treasury"),
        ([{"chain_id": 10}], {}, "for chain 10"),
        ([{"asset": USDC, "amount": 10**6}], {}, "is for 1 USDC"),
        ([{"destination": ATTACKER}], {}, "pays 0xBAd0"),
        ([{"amount": ETH - 1}], {}, "allows at most 0.999999999999999999 ETH"),
    ],
)
def test_transfers_need_a_matching_intent(stored: list[dict[str, Any]], fields: dict[str, Any], reason: str) -> None:
    decision = decide(_withdrawal(**fields), ledger=MemoryLedger([intent(**changes) for changes in stored]))
    assert decision.outcome == "deny"
    assert blocking(decision) == ["intent-required"]
    assert reason in decision.findings[0].message
    assert decision.intent is None


def test_intent_amount_is_a_ceiling_and_expiry_is_exclusive() -> None:
    ledger = MemoryLedger([intent(amount=2 * ETH)])
    assert decide(_withdrawal(), ledger=ledger).outcome == "allow"
    expiring = MemoryLedger([intent(expires_at=NOW + timedelta(seconds=1))])
    assert decide(_withdrawal(), ledger=expiring).outcome == "allow"


def test_an_intent_is_used_once() -> None:
    ledger = MemoryLedger([intent()])
    first = decide(_withdrawal(), ledger=ledger)
    assert ledger.commit(intent_id=first.intent, digest=first.request_digest, spends=first.spends, at=NOW)
    second = decide(_withdrawal(), ledger=ledger)
    assert second.outcome == "deny" and "already used" in second.findings[0].message


def test_one_intent_pays_one_transfer() -> None:
    batch = multisend((0, CUSTOMER, ETH, b""), (0, CUSTOMER, ETH, b""))
    request = tx(to=SAFE, data=hexed(exec_transaction(MULTISEND_CALL_ONLY, batch, operation=1)), intent_id="s")
    decision = decide(request, ledger=MemoryLedger([intent(id="s", wallet=SAFE)]))
    assert decision.outcome == "deny"
    assert [f.effect for f in decision.findings if f.rule == "intent-required"] == ["deny", "note"]
    assert "one intent pays one" in decision.findings[0].message


def test_transfer_from_pulls_from_its_source() -> None:
    data = hexed(call("transferFrom(address,address,uint256)", SAFE, CUSTOMER, 5))
    ledger = MemoryLedger([intent(wallet=SAFE, asset=USDC, amount=5)])
    assert decide(tx(to=USDC, data=data, intent_id="wd-1"), ledger=ledger).outcome == "allow"


def test_safe_gas_refunds_are_refused() -> None:
    decision = decide(tx(to=SAFE, data=hexed(exec_transaction(SAFE, b"", gas_price=10))))
    assert decision.outcome == "deny"
    assert "whoever submits the transaction" in decision.findings[0].message


# velocity and max-per-tx


def test_velocity_counts_recent_spending_only() -> None:
    ledger = MemoryLedger([intent()])
    ledger.commit(intent_id=None, digest="0x1", spends=[Spend(HOT, Decimal(98_000))], at=NOW - timedelta(hours=2))
    ledger.commit(intent_id=None, digest="0x2", spends=[Spend(HOT, Decimal(500_000))], at=NOW - timedelta(hours=24))
    held = decide(_withdrawal(), ledger=ledger)
    assert held.outcome == "hold" and blocking(held) == ["velocity"]
    assert "$98,000.00" in held.findings[0].message
    assert decide(_withdrawal(context={"prices_usd": {"ETH": "1500"}}), ledger=ledger).outcome == "allow"


def test_wallet_overrides_and_price_floors() -> None:
    raised = make_policy({"rules": {"velocity": {"limit_usd": 1}, "max-per-tx": {"limit_usd": 1}}})
    ledger = MemoryLedger([intent()])
    assert blocking(decide(_withdrawal(), raised, ledger)) == ["velocity", "max-per-tx"]
    data = raised.model_dump(by_alias=True)
    data["wallets"][0] |= {"velocity_limit_usd": 10_000, "max_per_tx_usd": 10_000}
    assert decide(_withdrawal(), Policy.model_validate(data), ledger).outcome == "allow"
    # A caller that prices ETH at one cent is overruled by the policy's floor of $1,000.
    cheap = make_policy({"rules": {"max-per-tx": {"limit_usd": 999}}})
    decision = decide(_withdrawal(context={"prices_usd": {"ETH": "0.01"}}), cheap, ledger)
    assert blocking(decision) == ["max-per-tx"] and "$1,000.00" in decision.findings[0].message


def test_max_per_tx_is_inclusive_and_per_wallet() -> None:
    at_limit = tx(value=10 * ETH, intent_id="wd-1", context={"prices_usd": {"ETH": "5000"}})
    ledger = MemoryLedger([intent(amount=20 * ETH)])
    assert decide(at_limit, ledger=ledger).outcome == "allow"
    over = tx(value=11 * ETH, intent_id="wd-1", context={"prices_usd": {"ETH": "5000"}})
    assert blocking(decide(over, ledger=ledger)) == ["max-per-tx"]


def test_unvalued_transfers_are_held() -> None:
    no_price = decide(_withdrawal(context={"prices_usd": {}}), ledger=MemoryLedger([intent()]))
    assert no_price.outcome == "hold" and blocking(no_price) == ["velocity", "max-per-tx"]
    token = "0x00000000000000000000000000000000000a11ce"
    unknown = decide(
        tx(to=token, data=hexed(call("transfer(address,uint256)", CUSTOMER, 5)), intent_id="wd-1"),
        ledger=MemoryLedger([intent(asset=token, amount=5)]),
    )
    assert blocking(unknown) == ["velocity", "max-per-tx"]
    assert "5 base units of token" in unknown.findings[0].message


# destination-allowlist


def test_treasury_destinations() -> None:
    def treasury_move(destination: str) -> Decision:
        request = tx(wallet=TREASURY, to=destination, value=ETH, intent_id="t")
        return decide(request, ledger=MemoryLedger([intent(id="t", wallet=TREASURY, destination=destination)]))

    assert blocking(treasury_move(CUSTOMER)) == ["destination-allowlist"]
    assert treasury_move(COLD).outcome == "allow"
    assert treasury_move(HOT).outcome == "allow"  # moving funds between the policy's own wallets
    assert decide(_withdrawal(), ledger=MemoryLedger([intent()])).outcome == "allow"  # hot wallets are not limited


def test_destination_roles_are_configurable() -> None:
    policy = make_policy({"rules": {"destination-allowlist": {"roles": ["hot"]}}})
    assert blocking(decide(_withdrawal(), policy, MemoryLedger([intent()]))) == ["destination-allowlist"]


# explainable


def test_unknown_calls_are_held_unless_the_contract_is_listed() -> None:
    deposit = hexed(call("deposit(uint256)", 5))
    assert blocking(decide(tx(to=VAULT, data=deposit))) == ["explainable"]
    listed = make_policy({"rules": {"explainable": {"contracts": [{"address": VAULT, "label": "vault"}]}}})
    assert decide(tx(to=VAULT, data=deposit), listed).outcome == "allow"
    scoped = make_policy(
        {"rules": {"explainable": {"contracts": [{"address": VAULT, "functions": ["deposit(uint256)"]}]}}}
    )
    assert decide(tx(to=VAULT, data=deposit), scoped).outcome == "allow"
    withdraw = hexed(call("withdraw(uint256,address)", 5, ATTACKER))
    assert blocking(decide(tx(to=VAULT, data=withdraw), scoped)) == ["explainable"]


def test_deployments_unknown_typed_data_and_messages_are_held() -> None:
    from .test_primitives import MAIL

    assert blocking(decide(tx(to=None, data="0x6000"))) == ["explainable"]
    held = decide(typed(MAIL))
    assert blocking(held) == ["explainable"] and "Mail typed data" in held.findings[0].message
    mail_contract = MAIL["domain"]["verifyingContract"]
    listed = make_policy({"rules": {"explainable": {"contracts": [mail_contract]}}})
    assert decide(typed(MAIL), listed).outcome == "allow"
    scoped = make_policy({"rules": {"explainable": {"contracts": [{"address": mail_contract, "functions": ["f()"]}]}}})
    assert decide(typed(MAIL), scoped).outcome == "hold"
    text = decide(message(b"I authorize the transfer"))
    assert blocking(text) == ["explainable"] and '"I authorize the transfer"' in text.findings[0].message


# built-in checks


def test_unknown_wallet_and_unreadable_requests_are_denied() -> None:
    stranger = decide(tx(wallet=CUSTOMER))
    assert stranger.outcome == "deny" and blocking(stranger) == ["known-wallet"]
    dirty = bytes.fromhex("a9059cbb") + b"\x01" + b"\x00" * 31 + (1).to_bytes(32, "big")
    unreadable = decide(tx(to=USDC, data=hexed(dirty)))
    assert unreadable.outcome == "deny" and blocking(unreadable) == ["well-formed"]
    assert unreadable.findings[0].path == "tx"
    assert unreadable.actions == ()


# precedence and quorum


def test_deny_beats_hold_and_approvals_cannot_release_it() -> None:
    request = tx(to=VAULT, value=ETH, data=hexed(call("deposit(uint256)", 5)))  # no intent, unknown call
    decision = decide(approved(request, "alice", "bob", "carol"))
    assert decision.outcome == "deny"
    assert [f.effect for f in decision.findings] == ["deny", "hold", "note"]
    assert decision.findings[-1].message.startswith("Approvals were ignored")


def _held() -> SigningRequest:
    return tx(to=VAULT, data=hexed(call("deposit(uint256)", 5)))


def test_hold_is_released_by_a_quorum_of_valid_approvals() -> None:
    held = decide(_held())
    assert held.outcome == "hold"
    assert held.findings[-1].message.startswith("Held for 2 approvals of request digest 0x")
    released = decide(approved(_held(), "bob", "carol"))
    assert released.outcome == "allow"
    assert released.quorum.approvers == ("bob", "carol")
    assert "Released by 2 valid approvals" in released.findings[-1].message


def _with_approvals(request: SigningRequest, approvals: list[dict[str, str]]) -> SigningRequest:
    return SigningRequest.model_validate({**request.to_json(), "approvals": approvals})


def _signature(request: SigningRequest, name: str) -> str:
    return approved(request, name).approvals[0].signature


def test_a_second_approval_from_the_same_approver_counts_once() -> None:
    request = _held()
    alice = {"approver": "alice", "signature": _signature(request, "alice")}
    decision = decide(_with_approvals(request, [alice, alice]))
    assert decision.outcome == "hold"
    assert decision.quorum.approvers == ("alice",)
    assert decision.quorum.rejected == (("alice", "approved more than once, counted once"),)


def test_approvers_the_policy_does_not_list_do_not_count() -> None:
    request = _held()
    mallory = keys.from_seed(b"m" * 32).sign(approval_message(request_digest(request))).hex()
    approvals = [
        {"approver": "alice", "signature": _signature(request, "alice")},
        {"approver": "mallory", "signature": mallory},
    ]
    decision = decide(_with_approvals(request, approvals))
    assert decision.outcome == "hold"
    assert decision.quorum.rejected == (("mallory", "not an approver in this policy"),)


@pytest.mark.parametrize("forgery", ["not hex", "too short", "random bytes", "another approver's", "another request's"])
def test_invalid_signatures_do_not_count(forgery: str) -> None:
    request = _held()
    other = tx(to=VAULT, data=hexed(call("deposit(uint256)", 6)))
    signature = {
        "not hex": "zz",
        "too short": "ab" * 63,
        "random bytes": "ab" * 64,
        "another approver's": _signature(request, "carol"),
        "another request's": _signature(other, "bob"),
    }[forgery]
    approvals = [
        {"approver": "alice", "signature": _signature(request, "alice")},
        {"approver": "bob", "signature": signature},
    ]
    decision = decide(_with_approvals(request, approvals))
    assert decision.outcome == "hold"
    assert decision.quorum.approvers == ("alice",)
    assert decision.quorum.rejected == (("bob", "signature does not verify against this request's digest"),)


def test_signatures_may_carry_a_0x_prefix() -> None:
    request = _held()
    approvals = [{"approver": n, "signature": "0x" + _signature(request, n)} for n in ("alice", "bob")]
    assert decide(_with_approvals(request, approvals)).outcome == "allow"


def test_without_approvers_nothing_held_is_ever_released() -> None:
    policy = make_policy({"quorum": {"threshold": 1, "approvers": []}})
    assert decide(approved(_held(), "alice"), policy).outcome == "hold"


# purity


def test_evaluation_is_deterministic_and_reads_without_writing() -> None:
    ledger = MemoryLedger([intent()])
    request = _withdrawal()
    first, second = decide(request, ledger=ledger), decide(request, ledger=ledger)
    assert first == second
    assert first.to_json() == second.to_json()
    assert ledger.intent("wd-1").used_at is None
    assert ledger.spent_usd(HOT, NOW - timedelta(days=1), NOW) == 0
    with pytest.raises(ValueError, match="timezone"):
        decide(request, now=NOW.replace(tzinfo=None))


def test_decision_json() -> None:
    decision = decide(_withdrawal(), ledger=MemoryLedger([intent()]))
    out = decision.to_json()
    assert out["decision"] == "allow"
    assert out["intent"] == "wd-1"
    assert out["spends"] == [
        {"wallet": "0x66A2f4A63310aCa888F8e578BA5a7093BA3Bb8b3", "usd": "3000", "limit_usd": "100000"}
    ]
    assert out["evaluated_at"] == "2026-11-01T12:00:00Z"
    assert out["actions"][0]["kind"] == "native_transfer"
    assert out["reasons"][0]["path"] == "tx"
    assert decision.reasons() == [{"rule": "intent-required", "message": decision.findings[0].message}]


# no-blind-signing


def test_a_bare_32_byte_hash_is_denied_and_approvals_cannot_release_it() -> None:
    for blind in (bytes(range(32)), ("0x" + "ab" * 32).encode()):
        decision = decide(approved(message(blind), "alice", "bob", "carol"))
        assert decision.outcome == "deny"
        assert blocking(decision) == ["no-blind-signing"]
        assert "a Safe accepts such a signature" in decision.findings[0].message


def test_the_bybit_transaction_is_denied_by_every_route_a_safe_owner_can_be_asked() -> None:
    """As SafeTx typed data, as the bare SafeTx hash, or as an on-chain approveHash: all denied."""
    from goldenhour.guard import eip712

    from .test_decode import safe_tx

    owner_request = typed(safe_tx(ATTACKER, call("transfer(address,uint256)", CUSTOMER, 0), 1))
    safe_tx_hash = keccak256(eip712.signing_preimage(owner_request.typed_data or {}))
    routes = [
        owner_request,
        message(safe_tx_hash),
        tx(to=SAFE, data=hexed(call("approveHash(bytes32)", safe_tx_hash))),
    ]
    rules_hit = []
    for request in routes:
        decision = decide(approved(request, "alice", "bob"))
        assert decision.outcome == "deny"
        rules_hit.append(blocking(decision))
    assert rules_hit == [["no-delegatecall"], ["no-blind-signing"], ["no-blind-signing"]]


def test_messages_that_are_not_hashes_are_still_only_held() -> None:
    assert decide(message(b"\x01" * 33)).outcome == "hold"
    assert blocking(decide(message(b"\x01" * 31))) == ["explainable"]


# ERC-721 approve(spender, 0)

NFT = "0x00000000000000000000000000000000000000f7"


def test_zero_approval_on_a_contract_not_listed_as_erc20_approves_token_id_zero() -> None:
    decision = decide(tx(to=NFT, data=hexed(call("approve(address,uint256)", ATTACKER, 0))))
    assert decision.outcome == "deny" and blocking(decision) == ["approval-allowlist"]
    assert "the NFT with id 0" in decision.findings[0].message


def test_zero_approval_revokes_on_listed_tokens_and_for_the_zero_spender() -> None:
    assert decide(tx(to=USDC, data=hexed(call("approve(address,uint256)", ATTACKER, 0)))).outcome == "allow"
    assert decide(tx(to=NFT, data=hexed(call("approve(address,uint256)", ZERO_ADDRESS, 0)))).outcome == "allow"
    increase = decide(tx(to=NFT, data=hexed(call("increaseAllowance(address,uint256)", ATTACKER, 0))))
    assert blocking(increase) == ["approval-allowlist"]


# Dangerous actions that used to be merely held are now decoded, so the deny rules apply.


def test_eip3009_authorization_needs_an_intent_even_when_usdc_is_explainable() -> None:
    from .test_decode import transfer_authorization

    listed = make_policy({"rules": {"explainable": {"contracts": [USDC]}}})
    prices = {"prices_usd": {"USDC": "1"}}
    denied = decide(approved(typed(transfer_authorization(), context=prices), "alice", "bob"), listed)
    assert denied.outcome == "deny" and blocking(denied) == ["intent-required"]
    request = typed(transfer_authorization(value=10**6), intent_id="wd-1", context=prices)
    ledger = MemoryLedger([intent(asset=USDC, destination=ATTACKER, amount=10**6)])
    assert decide(request, listed, ledger).outcome == "allow"


def test_dai_permit_and_maximum_permit2_approve_are_unlimited_approvals() -> None:
    from .test_decode import permit_types

    fields = [
        ("holder", "address"),
        ("spender", "address"),
        ("nonce", "uint256"),
        ("expiry", "uint256"),
        ("allowed", "bool"),
    ]
    dai_permit = {
        "types": permit_types("Permit", fields),
        "primaryType": "Permit",
        "domain": {"name": "Dai Stablecoin", "version": "1", "chainId": 1, "verifyingContract": USDC},
        "message": {"holder": HOT, "spender": ROUTER, "nonce": 0, "expiry": 0, "allowed": True},
    }
    permit2 = tx(to=PERMIT2, data=hexed(call("approve(address,address,uint160,uint48)", USDC, ROUTER, 2**160 - 1, 1)))
    for request in (typed(dai_permit), permit2):
        decision = decide(approved(request, "alice", "bob"))
        assert decision.outcome == "deny" and blocking(decision) == ["no-unlimited-approval"]


@pytest.mark.parametrize(
    "data",
    [
        call("upgrade(address,address)", SAFE, ATTACKER),
        call("upgradeAndCall(address,address,bytes)", SAFE, ATTACKER, b"\x01"),
        call("changeProxyAdmin(address,address)", SAFE, ATTACKER),
        call("changeAdmin(address)", ATTACKER),
        call("transferOwnership(address)", ATTACKER),
    ],
)
def test_proxy_admin_upgrades_are_denied_outside_a_change_window(data: bytes) -> None:
    decision = decide(approved(tx(to=VAULT, data=hexed(data)), "alice", "bob"))
    assert decision.outcome == "deny" and blocking(decision) == ["no-upgrade-or-owner-change"]


# Unreadable input


def test_any_decoding_failure_is_a_deny_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    from goldenhour.guard import engine

    def broken(request: SigningRequest) -> list[Any]:
        raise KeyError("message")

    monkeypatch.setattr(engine, "decode", broken)
    decision = decide(tx(value=ETH))
    assert decision.outcome == "deny" and blocking(decision) == ["well-formed"]
    assert "KeyError" in decision.findings[0].message
