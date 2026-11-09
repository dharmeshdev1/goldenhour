"""Every action the decoder knows, and what it refuses to guess."""

from __future__ import annotations

from typing import Any

import pytest

from goldenhour.guard.decode import (
    Approval,
    Batch,
    BlindSignature,
    ContractCreation,
    DecodeError,
    DelegateCall,
    Delegation,
    PersonalMessage,
    ProxyUpgrade,
    SafeChange,
    SafeExec,
    SafeGasRefund,
    Transfer,
    UnknownCall,
    UnknownTypedData,
    decode,
)
from goldenhour.guard.evm import ZERO_ADDRESS, keccak256
from goldenhour.guard.transaction import authorization_preimage

from .helpers import (
    ATTACKER,
    CUSTOMER,
    HOT,
    HOT_KEY,
    MAX_UINT256,
    MULTISEND_CALL_ONLY,
    PERMIT2,
    ROUTER,
    SAFE,
    USDC,
    VAULT,
    call,
    exec_transaction,
    hexed,
    message,
    multisend,
    sign,
    tx,
    typed,
)


def kinds(actions: list[Any]) -> list[str]:
    return [a.kind for a in actions]


def test_native_transfer_and_empty_call() -> None:
    (transfer,) = decode(tx(value=5))
    assert transfer == Transfer(path="tx", actor=HOT, asset="native", source=HOT, destination=CUSTOMER, amount=5)
    assert decode(tx()) == []  # nothing moves: a nonce bump


def test_erc20_calls() -> None:
    (transfer,) = decode(tx(to=USDC, data=hexed(call("transfer(address,uint256)", CUSTOMER, 7))))
    assert (transfer.kind, transfer.asset, transfer.source, transfer.destination, transfer.amount) == (
        "erc20_transfer",
        USDC,
        HOT,
        CUSTOMER,
        7,
    )
    (pulled,) = decode(tx(to=USDC, data=hexed(call("transferFrom(address,address,uint256)", SAFE, CUSTOMER, 9))))
    assert (pulled.kind, pulled.source, pulled.actor) == ("erc20_transfer_from", SAFE, HOT)
    (approval,) = decode(tx(to=USDC, data=hexed(call("approve(address,uint256)", ROUTER, 100))))
    assert approval == Approval(path="tx", actor=HOT, token=USDC, owner=HOT, spender=ROUTER, amount=100)
    (increase,) = decode(tx(to=USDC, data=hexed(call("increaseAllowance(address,uint256)", ROUTER, 0))))
    assert (increase.kind, increase.amount) == ("erc20_increase_allowance", 0)


def test_set_approval_for_all() -> None:
    (granted,) = decode(tx(to=ATTACKER, data=hexed(call("setApprovalForAll(address,bool)", ROUTER, True))))
    assert granted.kind == "set_approval_for_all" and granted.amount is None
    (revoked,) = decode(tx(to=ATTACKER, data=hexed(call("setApprovalForAll(address,bool)", ROUTER, False))))
    assert revoked.amount == 0


def test_value_on_a_call_is_a_transfer_too() -> None:
    actions = decode(tx(to=ATTACKER, value=3, data="0x12345678aa"))
    assert kinds(actions) == ["native_transfer", "unknown_call"]
    assert actions[1] == UnknownCall(path="tx", actor=HOT, target=ATTACKER, selector="0x12345678", data_size=5)
    (short,) = decode(tx(to=ATTACKER, data="0x1234"))
    assert short.selector == "0x1234"


def test_safe_exec_transaction_decodes_all_ten_parameters_and_the_inner_call() -> None:
    inner = call("transfer(address,uint256)", CUSTOMER, 11)
    actions = decode(tx(to=SAFE, data=hexed(exec_transaction(USDC, inner))))
    assert kinds(actions) == ["safe_exec", "erc20_transfer"]
    safe_exec = actions[0]
    assert isinstance(safe_exec, SafeExec)
    assert (safe_exec.safe, safe_exec.to, safe_exec.value, safe_exec.data, safe_exec.operation) == (
        SAFE,
        USDC,
        0,
        inner,
        0,
    )
    assert (safe_exec.safe_tx_gas, safe_exec.base_gas, safe_exec.gas_price) == (50_000, 0, 0)
    assert (safe_exec.gas_token, safe_exec.refund_receiver, safe_exec.signatures) == (
        ZERO_ADDRESS,
        ZERO_ADDRESS,
        b"\x01" * 65,
    )
    transfer = actions[1]
    assert (transfer.actor, transfer.source, transfer.path) == (SAFE, SAFE, "tx.call")


def test_safe_delegatecall_is_flagged_and_never_read_as_its_disguise() -> None:
    disguise = call("transfer(address,uint256)", CUSTOMER, 0)
    actions = decode(tx(to=SAFE, data=hexed(exec_transaction(ATTACKER, disguise, operation=1))))
    assert kinds(actions) == ["safe_exec", "delegatecall"]
    assert actions[1] == DelegateCall(
        path="tx.call", actor=SAFE, target=ATTACKER, selector="0xa9059cbb", function="transfer(address,uint256)"
    )
    with pytest.raises(DecodeError, match="operation must be 0"):
        decode(tx(to=SAFE, data=hexed(exec_transaction(ATTACKER, disguise, operation=2))))


def test_safe_gas_refund_is_a_payment() -> None:
    native = decode(tx(to=SAFE, data=hexed(exec_transaction(CUSTOMER, value=1, gas_price=10))))
    assert kinds(native) == ["safe_exec", "safe_gas_refund", "native_transfer"]
    assert native[1] == SafeGasRefund(
        path="tx", actor=SAFE, safe=SAFE, asset="native", receiver=ZERO_ADDRESS, gas_price=10
    )
    token = decode(
        tx(to=SAFE, data=hexed(exec_transaction(CUSTOMER, gas_price=3, gas_token=USDC, refund_receiver=ATTACKER)))
    )
    assert (token[1].asset, token[1].receiver) == (USDC, ATTACKER)


@pytest.mark.parametrize(
    ("signature", "args", "kind"),
    [
        ("addOwnerWithThreshold(address,uint256)", (ATTACKER, 1), "safe_owner_change"),
        ("removeOwner(address,address,uint256)", (CUSTOMER, ROUTER, 1), "safe_owner_change"),
        ("swapOwner(address,address,address)", (CUSTOMER, ROUTER, ATTACKER), "safe_owner_change"),
        ("changeThreshold(uint256)", (1,), "safe_owner_change"),
        ("enableModule(address)", (ATTACKER,), "safe_config_change"),
        ("disableModule(address,address)", (CUSTOMER, ATTACKER), "safe_config_change"),
        ("setGuard(address)", (ZERO_ADDRESS,), "safe_config_change"),
        ("setFallbackHandler(address)", (ATTACKER,), "safe_config_change"),
    ],
)
def test_safe_owner_and_control_changes(signature: str, args: tuple[Any, ...], kind: str) -> None:
    actions = decode(tx(to=SAFE, data=hexed(exec_transaction(SAFE, call(signature, *args)))))
    change = actions[-1]
    assert isinstance(change, SafeChange)
    assert (change.kind, change.safe, change.method) == (kind, SAFE, signature.split("(", maxsplit=1)[0])
    assert list(change.args.values()) == list(args)


@pytest.mark.parametrize(
    ("data", "method", "init"),
    [
        (call("upgradeTo(address)", ATTACKER), "upgradeTo", b""),
        (call("upgradeToAndCall(address,bytes)", ATTACKER, b"\x01\x02"), "upgradeToAndCall", b"\x01\x02"),
        (call("changeMasterCopy(address)", ATTACKER), "changeMasterCopy", b""),
    ],
)
def test_proxy_upgrades(data: bytes, method: str, init: bytes) -> None:
    (upgrade,) = decode(tx(to=SAFE, data=hexed(data)))
    assert upgrade == ProxyUpgrade(
        path="tx", actor=HOT, proxy=SAFE, implementation=ATTACKER, method=method, init_data=init
    )


@pytest.mark.parametrize("signature", ["multicall(bytes[])", "multicall(uint256,bytes[])"])
def test_multicall_recurses_with_the_caller_as_actor(signature: str) -> None:
    items = [call("transfer(address,uint256)", CUSTOMER, 1), call("approve(address,uint256)", ROUTER, 2)]
    args = (items,) if signature == "multicall(bytes[])" else (99, items)
    actions = decode(tx(to=USDC, data=hexed(call(signature, *args))))
    assert kinds(actions) == ["batch", "erc20_transfer", "erc20_approve"]
    assert actions[0] == Batch(path="tx", actor=HOT, target=USDC, method=signature, calls=2)
    assert [a.path for a in actions[1:]] == ["tx[0]", "tx[1]"]
    assert all(a.actor == HOT for a in actions[1:])


def test_multisend_through_a_safe_delegatecall() -> None:
    batch = multisend(
        (0, USDC, 0, call("transfer(address,uint256)", CUSTOMER, 5)),
        (0, CUSTOMER, 7, b""),
        (1, ATTACKER, 0, call("upgradeTo(address)", ATTACKER)),
    )
    actions = decode(tx(to=SAFE, data=hexed(exec_transaction(MULTISEND_CALL_ONLY, batch, operation=1))))
    assert kinds(actions) == ["safe_exec", "delegatecall", "batch", "erc20_transfer", "native_transfer", "delegatecall"]
    assert actions[1].function == "multiSend(bytes)"
    assert actions[2] == Batch(
        path="tx.call", actor=SAFE, target=MULTISEND_CALL_ONLY, method="multiSend(bytes)", calls=3
    )
    assert all(a.actor == SAFE for a in actions[3:])
    assert actions[5].target == ATTACKER  # a delegatecall inside the batch is flagged, not opened


def test_multisend_called_directly_sends_from_its_own_address() -> None:
    batch = multisend((0, CUSTOMER, 7, b""))
    actions = decode(tx(to=MULTISEND_CALL_ONLY, data=hexed(batch)))
    assert kinds(actions) == ["batch", "native_transfer"]
    assert actions[1].source == MULTISEND_CALL_ONLY


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (b"\x00" * 50, "truncated"),
        (b"\x00" + bytes(20) + bytes(32) + (5).to_bytes(32, "big") + b"\x01", "runs past"),
        (b"\x02" + bytes(20) + bytes(32) + bytes(32), "operation must be 0 or 1"),
    ],
)
def test_malformed_multisend(payload: bytes, reason: str) -> None:
    with pytest.raises(DecodeError, match=reason):
        decode(tx(to=MULTISEND_CALL_ONLY, data=hexed(call("multiSend(bytes)", payload))))


def _authorization(delegate: str, nonce: int = 5, key: int = HOT_KEY) -> dict[str, Any]:
    y_parity, r, s = sign(keccak256(authorization_preimage(1, delegate, nonce)), key)
    return {"chain_id": 1, "address": delegate, "nonce": nonce, "y_parity": y_parity, "r": r, "s": s}


def test_type4_authorizations_become_delegations_with_their_signer() -> None:
    forged = {**_authorization(ATTACKER), "s": 1}
    actions = decode(
        tx(type=4, to=HOT, authorization_list=[_authorization(ATTACKER), _authorization(ZERO_ADDRESS), forged])
    )
    assert kinds(actions) == ["eip7702_delegation", "eip7702_revocation", "eip7702_delegation"]
    assert actions[0] == Delegation(
        path="authorization_list[0]", actor=HOT, authority=HOT, delegate=ATTACKER, chain_id=1, nonce=5
    )
    assert actions[1].revokes
    assert actions[2].authority != HOT  # a signature over other data recovers to someone else, or no one


def test_authorization_messages() -> None:
    (delegation,) = decode(message(authorization_preimage(0, ATTACKER, 3)))
    assert (delegation.kind, delegation.authority, delegation.chain_id, delegation.path) == (
        "eip7702_delegation",
        HOT,
        0,
        "message",
    )
    (revocation,) = decode(message(authorization_preimage(1, ZERO_ADDRESS, 3)))
    assert revocation.kind == "eip7702_revocation"


def test_personal_messages() -> None:
    (text,) = decode(message(b"Sign in to example.com\nNonce: 7"))
    assert text == PersonalMessage(path="message", actor=HOT, text="Sign in to example.com\nNonce: 7", size=31)
    (binary,) = decode(message(b"\xff\x00\x05"))
    assert binary.text is None
    (control,) = decode(message(b"hi\x07"))
    assert control.text is None
    (not_an_authorization,) = decode(message(b"\x05\x01"))
    assert isinstance(not_an_authorization, PersonalMessage)


def test_contract_creation_lands_at_the_create_address() -> None:
    # The CREATE address of account 0x6ac7...dbf0 at nonce 0 is a widely published example.
    request = tx(wallet="0x6ac7ea33f8831ea9dcc53393aaa88b25a785dbf0", nonce=0, to=None, data="0x6000", value=4)
    creation, transfer = decode(request)
    assert creation == ContractCreation(
        path="tx", actor=request.wallet, address="0xcd234a471b72ba2f1ccf0a70fcaba648a5eecd8d", code_size=2
    )
    assert transfer.destination == creation.address and transfer.amount == 4


def test_malformed_arguments_and_depth() -> None:
    dirty = bytes.fromhex("a9059cbb") + b"\x01" + b"\x00" * 31 + (1).to_bytes(32, "big")
    with pytest.raises(DecodeError, match="high bytes"):
        decode(tx(to=USDC, data=hexed(dirty)))
    data = call("transfer(address,uint256)", CUSTOMER, 1)
    for _ in range(9):
        data = call("multicall(bytes[])", [data])
    with pytest.raises(DecodeError, match="nested more than 8"):
        decode(tx(to=USDC, data=hexed(data)))


def permit_types(primary: str, fields: list[tuple[str, str]], **extra: list[tuple[str, str]]) -> dict[str, Any]:
    types = {primary: [{"name": n, "type": t} for n, t in fields]}
    types.update({name: [{"name": n, "type": t} for n, t in decl] for name, decl in extra.items()})
    return types


PERMIT_FIELDS = [
    ("owner", "address"),
    ("spender", "address"),
    ("value", "uint256"),
    ("nonce", "uint256"),
    ("deadline", "uint256"),
]
DETAILS = [("token", "address"), ("amount", "uint160"), ("expiration", "uint48"), ("nonce", "uint48")]
TOKEN_PERMISSIONS = [("token", "address"), ("amount", "uint256")]
PERMIT2_DOMAIN = {"name": "Permit2", "chainId": 1, "verifyingContract": PERMIT2}


def test_eip2612_permit() -> None:
    data = {
        "types": permit_types("Permit", PERMIT_FIELDS),
        "primaryType": "Permit",
        "domain": {"name": "USD Coin", "version": "2", "chainId": 1, "verifyingContract": USDC},
        "message": {"owner": HOT, "spender": ROUTER, "value": str(MAX_UINT256), "nonce": 0, "deadline": 1_800_000_000},
    }
    (permit,) = decode(typed(data))
    assert permit == Approval(
        kind="permit",
        path="typed_data",
        actor=HOT,
        token=USDC,
        owner=HOT,
        spender=ROUTER,
        amount=MAX_UINT256,
        expires=1_800_000_000,
    )


def test_permit2_allowances() -> None:
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
    (allowance,) = decode(typed(single))
    assert (allowance.kind, allowance.token, allowance.spender, allowance.amount, allowance.expires) == (
        "permit2_allowance",
        USDC,
        ROUTER,
        2**160 - 1,
        1,
    )
    batch = {
        **single,
        "types": permit_types(
            "PermitBatch",
            [("details", "PermitDetails[]"), ("spender", "address"), ("sigDeadline", "uint256")],
            PermitDetails=DETAILS,
        ),
        "primaryType": "PermitBatch",
        "message": {
            "details": [single["message"]["details"], {**single["message"]["details"], "token": ATTACKER}],
            "spender": ROUTER,
            "sigDeadline": 2,
        },
    }
    assert [(a.token, a.path) for a in decode(typed(batch))] == [
        (USDC, "typed_data.details[0]"),
        (ATTACKER, "typed_data.details[1]"),
    ]


def test_permit2_signature_transfers() -> None:
    one_time = [("spender", "address"), ("nonce", "uint256"), ("deadline", "uint256")]
    single = {
        "types": permit_types(
            "PermitTransferFrom", [("permitted", "TokenPermissions"), *one_time], TokenPermissions=TOKEN_PERMISSIONS
        ),
        "primaryType": "PermitTransferFrom",
        "domain": PERMIT2_DOMAIN,
        "message": {"permitted": {"token": USDC, "amount": 10}, "spender": ROUTER, "nonce": 1, "deadline": 9},
    }
    (transfer,) = decode(typed(single))
    assert (transfer.kind, transfer.amount, transfer.expires) == ("permit2_transfer", 10, 9)
    witness = {
        "types": permit_types(
            "PermitBatchWitnessTransferFrom",
            [("permitted", "TokenPermissions[]"), *one_time, ("witness", "Order")],
            TokenPermissions=TOKEN_PERMISSIONS,
            Order=[("recipient", "address")],
        ),
        "primaryType": "PermitBatchWitnessTransferFrom",
        "domain": PERMIT2_DOMAIN,
        "message": {
            "permitted": [{"token": USDC, "amount": 10}],
            "spender": ROUTER,
            "nonce": 1,
            "deadline": 9,
            "witness": {"recipient": CUSTOMER},
        },
    }
    assert kinds(decode(typed(witness))) == ["permit2_transfer"]
    # Permit2's shape for any other verifying contract is not a Permit2 permit: Guard will not vouch for it.
    lookalike = {**single, "domain": {**PERMIT2_DOMAIN, "verifyingContract": ATTACKER}}
    (unknown,) = decode(typed(lookalike))
    assert unknown == UnknownTypedData(
        path="typed_data",
        actor=HOT,
        primary_type="PermitTransferFrom",
        verifying_contract=ATTACKER,
        domain_name="Permit2",
    )
    missing_witness = {**witness, "message": {k: v for k, v in witness["message"].items() if k != "witness"}}
    missing_witness["types"] = {
        **witness["types"],
        "PermitBatchWitnessTransferFrom": [
            f for f in witness["types"]["PermitBatchWitnessTransferFrom"] if f["name"] != "witness"
        ],
    }
    assert kinds(decode(typed(missing_witness))) == ["unknown_typed_data"]


SAFE_TX_FIELDS = [
    ("to", "address"),
    ("value", "uint256"),
    ("data", "bytes"),
    ("operation", "uint8"),
    ("safeTxGas", "uint256"),
    ("baseGas", "uint256"),
    ("gasPrice", "uint256"),
    ("gasToken", "address"),
    ("refundReceiver", "address"),
    ("nonce", "uint256"),
]


def safe_tx(to: str, data: bytes, operation: int) -> dict[str, Any]:
    return {
        "types": permit_types("SafeTx", SAFE_TX_FIELDS),
        "primaryType": "SafeTx",
        "domain": {"chainId": 1, "verifyingContract": SAFE},
        "message": {
            "to": to,
            "value": 0,
            "data": hexed(data),
            "operation": operation,
            "safeTxGas": 0,
            "baseGas": 0,
            "gasPrice": 0,
            "gasToken": ZERO_ADDRESS,
            "refundReceiver": ZERO_ADDRESS,
            "nonce": 12,
        },
    }


def test_safe_tx_typed_data_is_what_safe_owners_sign() -> None:
    actions = decode(typed(safe_tx(ATTACKER, call("transfer(address,uint256)", CUSTOMER, 0), 1)))
    assert kinds(actions) == ["safe_exec", "delegatecall"]
    assert (actions[0].nonce, actions[0].signatures, actions[0].safe) == (12, None, SAFE)
    assert actions[1].actor == SAFE


def test_unrecognised_typed_data() -> None:
    from .test_primitives import MAIL

    (unknown,) = decode(typed(MAIL))
    assert (unknown.primary_type, unknown.domain_name) == ("Mail", "Ether Mail")


def test_action_json_is_display_ready() -> None:
    (transfer,) = decode(tx(value=10**30))
    out = transfer.to_json()
    assert out["kind"] == "native_transfer"
    assert out["amount"] == str(10**30)
    assert out["source"] == "0x66A2f4A63310aCa888F8e578BA5a7093BA3Bb8b3"
    safe_exec = decode(tx(to=SAFE, data=hexed(exec_transaction(USDC, b"\xab"))))[0].to_json()
    assert safe_exec["data"] == "0xab"
    assert safe_exec["operation"] == 0


# Calls and signed messages that move or approve tokens without looking like transfer or approve.

AUTH_FIELDS = [
    ("from", "address"),
    ("to", "address"),
    ("value", "uint256"),
    ("validAfter", "uint256"),
    ("validBefore", "uint256"),
    ("nonce", "bytes32"),
]
USDC_DOMAIN = {"name": "USD Coin", "version": "2", "chainId": 1, "verifyingContract": USDC}


def transfer_authorization(primary: str = "TransferWithAuthorization", value: int = 5) -> dict[str, Any]:
    return {
        "types": permit_types(primary, AUTH_FIELDS),
        "primaryType": primary,
        "domain": USDC_DOMAIN,
        "message": {
            "from": HOT,
            "to": ATTACKER,
            "value": value,
            "validAfter": 0,
            "validBefore": 2**40,
            "nonce": "0x" + "11" * 32,
        },
    }


def test_known_selectors() -> None:
    from goldenhour.guard.evm import selector

    expected = {
        "permit(address,address,uint256,uint256,uint8,bytes32,bytes32)": "d505accf",
        "permit(address,address,uint256,uint256,bool,uint8,bytes32,bytes32)": "8fcbaf0c",
        "transferWithAuthorization(address,address,uint256,uint256,uint256,bytes32,uint8,bytes32,bytes32)": "e3ee160e",
        "approveHash(bytes32)": "d4d9bdcd",
        "transferOwnership(address)": "f2fde38b",
        "approve(address,address,uint160,uint48)": "87517c45",
    }
    assert {signature: selector(signature).hex() for signature in expected} == expected


@pytest.mark.parametrize(
    ("primary", "kind"),
    [("TransferWithAuthorization", "eip3009_transfer"), ("ReceiveWithAuthorization", "eip3009_receive")],
)
def test_eip3009_authorizations_are_transfers(primary: str, kind: str) -> None:
    (transfer,) = decode(typed(transfer_authorization(primary)))
    assert transfer == Transfer(
        kind=kind, path="typed_data", actor=HOT, asset=USDC, source=HOT, destination=ATTACKER, amount=5
    )


@pytest.mark.parametrize(
    "signature",
    [
        "transferWithAuthorization(address,address,uint256,uint256,uint256,bytes32,uint8,bytes32,bytes32)",
        "receiveWithAuthorization(address,address,uint256,uint256,uint256,bytes32,uint8,bytes32,bytes32)",
        "transferWithAuthorization(address,address,uint256,uint256,uint256,bytes32,bytes)",
    ],
)
def test_eip3009_calls_move_the_signers_tokens(signature: str) -> None:
    tail = (27, b"\x01" * 32, b"\x02" * 32) if signature.endswith("bytes32)") else (b"\x03" * 65,)
    data = call(signature, SAFE, ATTACKER, 9, 0, 2**40, b"\x11" * 32, *tail)
    (transfer,) = decode(tx(to=USDC, data=hexed(data)))
    assert (transfer.source, transfer.destination, transfer.amount, transfer.asset) == (SAFE, ATTACKER, 9, USDC)


def test_dai_permits_grant_everything_or_nothing() -> None:
    fields = [
        ("holder", "address"),
        ("spender", "address"),
        ("nonce", "uint256"),
        ("expiry", "uint256"),
        ("allowed", "bool"),
    ]
    dai = "0x6b175474e89094c44da98b954eedeac495271d0f"
    permit = {
        "types": permit_types("Permit", fields),
        "primaryType": "Permit",
        "domain": {"name": "Dai Stablecoin", "version": "1", "chainId": 1, "verifyingContract": dai},
        "message": {"holder": HOT, "spender": ROUTER, "nonce": 0, "expiry": 0, "allowed": True},
    }
    (granted,) = decode(typed(permit))
    assert (granted.kind, granted.token, granted.amount, granted.expires) == ("dai_permit", dai, None, None)
    (revoked,) = decode(typed({**permit, "message": {**permit["message"], "allowed": False}}))
    assert revoked.amount == 0
    on_chain = call(
        "permit(address,address,uint256,uint256,bool,uint8,bytes32,bytes32)",
        HOT,
        ROUTER,
        0,
        7,
        True,
        27,
        b"\x01" * 32,
        b"\x02" * 32,
    )
    (submitted,) = decode(tx(to=dai, data=hexed(on_chain)))
    assert (submitted.kind, submitted.owner, submitted.amount, submitted.expires) == ("dai_permit", HOT, None, 7)


def test_eip2612_permit_submitted_on_chain() -> None:
    data = call(
        "permit(address,address,uint256,uint256,uint8,bytes32,bytes32)",
        SAFE,
        ROUTER,
        50,
        9,
        27,
        b"\x01" * 32,
        b"\x02" * 32,
    )
    (permit,) = decode(tx(to=USDC, data=hexed(data)))
    assert (permit.kind, permit.owner, permit.spender, permit.amount, permit.expires) == ("permit", SAFE, ROUTER, 50, 9)


def test_permit2_approve_is_an_approval_only_on_permit2() -> None:
    data = hexed(call("approve(address,address,uint160,uint48)", USDC, ROUTER, 2**160 - 1, 99))
    (approval,) = decode(tx(to=PERMIT2, data=data))
    assert (approval.kind, approval.token, approval.spender, approval.amount, approval.expires) == (
        "permit2_approve",
        USDC,
        ROUTER,
        2**160 - 1,
        99,
    )
    (elsewhere,) = decode(tx(to=ATTACKER, data=data))
    assert (elsewhere.kind, elsewhere.selector, elsewhere.data_size) == ("unknown_call", "0x87517c45", 132)


@pytest.mark.parametrize(
    ("data", "kind", "fields"),
    [
        (
            call("upgrade(address,address)", SAFE, ATTACKER),
            "proxy_upgrade",
            {"proxy": SAFE, "implementation": ATTACKER},
        ),
        (
            call("upgradeAndCall(address,address,bytes)", SAFE, ATTACKER, b"\x01"),
            "proxy_upgrade",
            {"proxy": SAFE, "init_data": b"\x01"},
        ),
        (call("changeProxyAdmin(address,address)", SAFE, ATTACKER), "admin_change", {"contract": SAFE}),
        (call("changeAdmin(address)", ATTACKER), "admin_change", {"contract": VAULT, "new_admin": ATTACKER}),
        (call("transferOwnership(address)", ATTACKER), "admin_change", {"method": "transferOwnership"}),
    ],
)
def test_proxy_admin_upgrades_and_handovers(data: bytes, kind: str, fields: dict[str, Any]) -> None:
    (action,) = decode(tx(to=VAULT, data=hexed(data)))
    assert action.kind == kind
    assert {name: getattr(action, name) for name in fields} == fields


def test_blind_hashes() -> None:
    (approve_hash,) = decode(tx(to=SAFE, data=hexed(call("approveHash(bytes32)", b"\xab" * 32))))
    assert approve_hash == BlindSignature(path="tx", actor=HOT, hash="0x" + "ab" * 32, via="approveHash", safe=SAFE)
    for blind in (b"\xcd" * 32, ("0x" + "cd" * 32).encode(), ("CD" * 32).encode()):
        (action,) = decode(message(blind))
        assert (action.kind, action.hash) == ("blind_signature", "0x" + "cd" * 32)
    for readable in (b"\xcd" * 31, b"\xcd" * 33, b"Sign in to example.com, nonce 7"):
        assert kinds(decode(message(readable))) == ["personal_message"]


def test_a_verifying_contract_that_is_not_an_address_is_unreadable() -> None:
    from .test_primitives import MAIL

    types = {**MAIL["types"], "EIP712Domain": [{"name": "verifyingContract", "type": "string"}]}
    with pytest.raises(DecodeError, match="verifyingContract is not an address"):
        decode(typed({**MAIL, "types": types, "domain": {"verifyingContract": "the usual one"}}))
