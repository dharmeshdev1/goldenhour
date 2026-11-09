"""Turn a signing request into the explicit actions it would perform.

The decoder never guesses. A call it recognizes becomes a typed action with every argument named;
a call it does not recognize becomes an `unknown_call` that carries its selector, and the policy
decides what to do with it. Decoding is a pure function of the request and knows nothing about the
policy, so what Guard says a request does can never depend on what Guard is configured to allow.

Three details matter for the incidents Guard was built for:

* A delegatecall is never decoded as the call its data appears to make. Bybit's signers approved
  data that read as an ERC-20 `transfer`; under delegatecall that name meant nothing, and the code
  that ran rewrote the Safe. The only delegatecall data the decoder opens is a MultiSend batch,
  and only the policy can say whether the batch's target is a genuine MultiSend.
* Every EIP-7702 authorization becomes a delegation action, whether it arrives in a type 4
  transaction or as a bare authorization message, and its signer is recovered so the reason can
  say which wallet would be handed over.
* ERC-20 and ERC-721 share the selectors of `transferFrom` and `approve`. Both are decoded as token
  movements or approvals, which is the stricter reading for both standards.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from typing import Any

from goldenhour.guard import rlp
from goldenhour.guard.abi import ABIError, decode_args
from goldenhour.guard.eip712 import types_of
from goldenhour.guard.evm import (
    NATIVE,
    ZERO_ADDRESS,
    address_from_bytes,
    address_to_bytes,
    keccak256,
    normalize_address,
    parse_hex,
    parse_uint,
    selector,
    to_checksum_address,
)
from goldenhour.guard.request import SigningRequest
from goldenhour.guard.transaction import authority, parse_authorization_message

PERMIT2 = "0x000000000022d473030f116ddee9f6b43ac78ba3"  # Uniswap's Permit2, at this address on every chain
MAX_DEPTH = 8
# Integer fields that can exceed what a JSON number holds exactly.
_LARGE = frozenset({"amount", "value", "gas_price"})


class DecodeError(ValueError):
    """Part of the request cannot be read, so none of it can be vouched for."""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path


def _json(name: str, value: object) -> object:
    if isinstance(value, bytes):
        return "0x" + value.hex()
    if isinstance(value, str) and len(value) == 42 and value.startswith("0x"):
        return to_checksum_address(value)
    if isinstance(value, int) and not isinstance(value, bool) and name in _LARGE:
        return str(value)
    if isinstance(value, dict):
        return {k: _json(k, v) for k, v in value.items()}
    return value


@dataclass(frozen=True, kw_only=True)
class Action:
    """Something a request would do. `actor` is the account whose authority it uses."""

    path: str
    actor: str
    kind: str = "action"

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {"kind": self.kind}
        for item in fields(self):
            if item.name != "kind":
                out[item.name] = _json(item.name, getattr(self, item.name))
        return out


@dataclass(frozen=True, kw_only=True)
class Transfer(Action):
    """Value leaving `source`: the native coin (`asset` is `native`) or a token (`asset` is its address)."""

    kind: str = "native_transfer"
    asset: str
    source: str
    destination: str
    amount: int


@dataclass(frozen=True, kw_only=True)
class Approval(Action):
    """Permission for `spender` to move the owner's tokens later. `amount` None means all of them.

    Whether an amount of 0 revokes is for the rules to say: `approve(spender, 0)` clears an ERC-20
    allowance but approves token id 0 on an ERC-721 contract, and only the policy knows which
    contracts are ERC-20 tokens.
    """

    kind: str = "erc20_approve"
    token: str
    owner: str
    spender: str
    amount: int | None
    expires: int | None = None


@dataclass(frozen=True, kw_only=True)
class SafeExec(Action):
    """A Safe executing a transaction, with all ten `execTransaction` parameters.

    `signatures` comes from an on-chain `execTransaction` call and `nonce` from `SafeTx` typed data;
    each form carries one of the two.
    """

    kind: str = "safe_exec"
    safe: str
    to: str
    value: int
    data: bytes
    operation: int
    safe_tx_gas: int
    base_gas: int
    gas_price: int
    gas_token: str
    refund_receiver: str
    signatures: bytes | None = None
    nonce: int | None = None


@dataclass(frozen=True, kw_only=True)
class SafeGasRefund(Action):
    """A Safe paying gas back to `receiver` (zero means whoever submits the transaction)."""

    kind: str = "safe_gas_refund"
    safe: str
    asset: str
    receiver: str
    gas_price: int


@dataclass(frozen=True, kw_only=True)
class DelegateCall(Action):
    """`actor` running `target`'s code as itself. `function` is what the data appears to call."""

    kind: str = "delegatecall"
    target: str
    selector: str
    function: str | None


@dataclass(frozen=True, kw_only=True)
class SafeChange(Action):
    """A change to who controls a Safe: owners and threshold, or modules, guard and fallback handler."""

    kind: str = "safe_owner_change"
    safe: str
    method: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class ProxyUpgrade(Action):
    kind: str = "proxy_upgrade"
    proxy: str
    implementation: str
    method: str
    init_data: bytes = b""


@dataclass(frozen=True, kw_only=True)
class AdminChange(Action):
    """Handing over who may upgrade or administer a contract: a proxy's admin, or a contract's owner."""

    kind: str = "admin_change"
    contract: str
    new_admin: str
    method: str


@dataclass(frozen=True, kw_only=True)
class BlindSignature(Action):
    """Approval of something Guard can see only as a 32-byte hash.

    A Safe accepts an owner's signature of the EIP-191 message holding its transaction hash (with 4
    added to `v`), and `approveHash` records the same approval on-chain. Either way the owner
    approves whatever the hash stands for, which is how a Safe transaction can be approved without
    anyone reading it.
    """

    kind: str = "blind_signature"
    hash: str
    via: str
    safe: str | None = None


@dataclass(frozen=True, kw_only=True)
class Batch(Action):
    """A multicall or MultiSend container. Its calls follow it as their own actions."""

    kind: str = "batch"
    target: str
    method: str
    calls: int


@dataclass(frozen=True, kw_only=True)
class Delegation(Action):
    """An EIP-7702 authorization: `authority` would run `delegate`'s code. A zero delegate revokes.

    `authority` is None when the signature is invalid, in which case the chain would skip it.
    `chain_id` 0 makes the authorization valid on every chain.
    """

    kind: str = "eip7702_delegation"
    authority: str | None
    delegate: str
    chain_id: int
    nonce: int

    @property
    def revokes(self) -> bool:
        return self.delegate == ZERO_ADDRESS


@dataclass(frozen=True, kw_only=True)
class UnknownCall(Action):
    kind: str = "unknown_call"
    target: str
    selector: str
    data_size: int


@dataclass(frozen=True, kw_only=True)
class ContractCreation(Action):
    kind: str = "contract_creation"
    address: str
    code_size: int


@dataclass(frozen=True, kw_only=True)
class UnknownTypedData(Action):
    kind: str = "unknown_typed_data"
    primary_type: str
    verifying_contract: str | None
    domain_name: str | None


@dataclass(frozen=True, kw_only=True)
class PersonalMessage(Action):
    """A free-form message, signed with the EIP-191 prefix. `text` is set when it is printable."""

    kind: str = "personal_message"
    text: str | None
    size: int


@dataclass(frozen=True)
class _Function:
    signature: str
    names: tuple[str, ...]
    handler: str

    @property
    def name(self) -> str:
        return self.signature.split("(", 1)[0]

    @property
    def types(self) -> tuple[str, ...]:
        return tuple(self.signature[self.signature.index("(") + 1 : -1].split(","))


_FUNCTIONS = (
    _Function("transfer(address,uint256)", ("to", "amount"), "_token_transfer"),
    _Function("transferFrom(address,address,uint256)", ("from", "to", "amount"), "_token_transfer"),
    _Function("approve(address,uint256)", ("spender", "amount"), "_approval"),
    _Function("increaseAllowance(address,uint256)", ("spender", "amount"), "_approval"),
    _Function("setApprovalForAll(address,bool)", ("operator", "approved"), "_approval"),
    _Function(
        "approve(address,address,uint160,uint48)", ("token", "spender", "amount", "expiration"), "_permit2_approve"
    ),
    _Function(
        "permit(address,address,uint256,uint256,uint8,bytes32,bytes32)",
        ("owner", "spender", "amount", "deadline", "v", "r", "s"),
        "_permit_call",
    ),
    _Function(
        "permit(address,address,uint256,uint256,bool,uint8,bytes32,bytes32)",
        ("owner", "spender", "nonce", "deadline", "allowed", "v", "r", "s"),
        "_permit_call",
    ),
    *(
        _Function(f"{name}(address,address,uint256,uint256,uint256,bytes32,{signature})", fields, "_token_transfer")
        for name in ("transferWithAuthorization", "receiveWithAuthorization")
        for signature, fields in (
            ("uint8,bytes32,bytes32", ("from", "to", "amount", "validAfter", "validBefore", "nonce", "v", "r", "s")),
            ("bytes", ("from", "to", "amount", "validAfter", "validBefore", "nonce", "signature")),
        )
    ),
    _Function(
        "execTransaction(address,uint256,bytes,uint8,uint256,uint256,uint256,address,address,bytes)",
        (
            "to",
            "value",
            "data",
            "operation",
            "safeTxGas",
            "baseGas",
            "gasPrice",
            "gasToken",
            "refundReceiver",
            "signatures",
        ),
        "_exec_transaction",
    ),
    _Function("addOwnerWithThreshold(address,uint256)", ("owner", "threshold"), "_safe_change"),
    _Function("removeOwner(address,address,uint256)", ("prevOwner", "owner", "threshold"), "_safe_change"),
    _Function("swapOwner(address,address,address)", ("prevOwner", "oldOwner", "newOwner"), "_safe_change"),
    _Function("changeThreshold(uint256)", ("threshold",), "_safe_change"),
    _Function("enableModule(address)", ("module",), "_safe_change"),
    _Function("disableModule(address,address)", ("prevModule", "module"), "_safe_change"),
    _Function("setGuard(address)", ("guard",), "_safe_change"),
    _Function("setFallbackHandler(address)", ("handler",), "_safe_change"),
    _Function("approveHash(bytes32)", ("hash",), "_approve_hash"),
    _Function("upgradeTo(address)", ("implementation",), "_upgrade"),
    _Function("upgradeToAndCall(address,bytes)", ("implementation", "data"), "_upgrade"),
    _Function("changeMasterCopy(address)", ("implementation",), "_upgrade"),
    _Function("upgrade(address,address)", ("proxy", "implementation"), "_upgrade"),
    _Function("upgradeAndCall(address,address,bytes)", ("proxy", "implementation", "data"), "_upgrade"),
    _Function("changeProxyAdmin(address,address)", ("proxy", "newAdmin"), "_admin_change"),
    _Function("changeAdmin(address)", ("newAdmin",), "_admin_change"),
    _Function("transferOwnership(address)", ("newAdmin",), "_admin_change"),
    _Function("multicall(bytes[])", ("data",), "_multicall"),
    _Function("multicall(uint256,bytes[])", ("deadline", "data"), "_multicall"),
    _Function("multiSend(bytes)", ("transactions",), "_multisend"),
)
FUNCTIONS = {selector(f.signature): f for f in _FUNCTIONS}
OWNER_METHODS = frozenset({"addOwnerWithThreshold", "removeOwner", "swapOwner", "changeThreshold"})

_PERMIT = {"owner": "address", "spender": "address", "value": "uint256", "nonce": "uint256", "deadline": "uint256"}
_PERMIT_DETAILS = {"token": "address", "amount": "uint160", "expiration": "uint48", "nonce": "uint48"}
_TOKEN_PERMISSIONS = {"token": "address", "amount": "uint256"}
_DAI_PERMIT = {"holder": "address", "spender": "address", "nonce": "uint256", "expiry": "uint256", "allowed": "bool"}
# EIP-3009, as USDC implements it: a signed authorization that moves tokens when anyone submits it.
_TRANSFER_AUTHORIZATION = {
    "from": "address",
    "to": "address",
    "value": "uint256",
    "validAfter": "uint256",
    "validBefore": "uint256",
    "nonce": "bytes32",
}
_TRANSFER_KINDS = {
    "transfer": "erc20_transfer",
    "transferFrom": "erc20_transfer_from",
    "transferWithAuthorization": "eip3009_transfer",
    "receiveWithAuthorization": "eip3009_receive",
    "TransferWithAuthorization": "eip3009_transfer",
    "ReceiveWithAuthorization": "eip3009_receive",
}
_HASH_TEXT = re.compile(r"(0x)?[0-9a-fA-F]{64}")
_SAFE_TX = {
    "to": "address",
    "value": "uint256",
    "data": "bytes",
    "operation": "uint8",
    "safeTxGas": "uint256",
    "baseGas": "uint256",
    "gasPrice": "uint256",
    "gasToken": "address",
    "refundReceiver": "address",
    "nonce": "uint256",
}
_ALLOWANCE = {"spender": "address", "sigDeadline": "uint256"}
_ONE_TIME = {"spender": "address", "nonce": "uint256", "deadline": "uint256"}
# Permit2's signed messages, with any witness field removed: what each primary type must declare.
_PERMIT2 = {
    "PermitSingle": {"details": "PermitDetails", **_ALLOWANCE},
    "PermitBatch": {"details": "PermitDetails[]", **_ALLOWANCE},
    "PermitTransferFrom": {"permitted": "TokenPermissions", **_ONE_TIME},
    "PermitBatchTransferFrom": {"permitted": "TokenPermissions[]", **_ONE_TIME},
    "PermitWitnessTransferFrom": {"permitted": "TokenPermissions", **_ONE_TIME},
    "PermitBatchWitnessTransferFrom": {"permitted": "TokenPermissions[]", **_ONE_TIME},
}


def _permit2_parts(primary: str, shapes: dict[str, dict[str, str]]) -> bool:
    """The nested struct a Permit2 message uses is declared exactly as Permit2 declares it."""
    if "Witness" in primary and "witness" not in shapes[primary]:
        return False
    if "details" in _PERMIT2[primary]:
        return shapes.get("PermitDetails") == _PERMIT_DETAILS
    return shapes.get("TokenPermissions") == _TOKEN_PERMISSIONS


@dataclass(frozen=True)
class _Call:
    path: str
    actor: str
    target: str
    function: _Function
    args: dict[str, Any]
    depth: int
    size: int


def decode(request: SigningRequest) -> list[Action]:
    """Every action `request` would perform, in execution order. Raises DecodeError."""
    decoder = _Decoder(request.wallet)
    if request.kind == "evm_tx":
        decoder.transaction(request)
    elif request.kind == "evm_typed_data":
        assert request.typed_data is not None
        decoder.typed_data(request.typed_data)
    else:
        assert request.message is not None
        decoder.message(request.message)
    return decoder.actions


class _Decoder:
    def __init__(self, wallet: str) -> None:
        self.wallet = wallet
        self.actions: list[Action] = []

    def transaction(self, request: SigningRequest) -> None:
        assert request.nonce is not None
        for index, entry in enumerate(request.authorization_list):
            kind = "eip7702_revocation" if entry.address == ZERO_ADDRESS else "eip7702_delegation"
            self.actions.append(
                Delegation(
                    kind=kind,
                    path=f"authorization_list[{index}]",
                    actor=self.wallet,
                    authority=authority(entry),
                    delegate=entry.address,
                    chain_id=entry.chain_id,
                    nonce=entry.nonce,
                )
            )
        if request.to is not None:
            self.call("tx", self.wallet, request.to, request.value, request.data, 0)
            return
        # A deployment lands at keccak256(rlp([sender, nonce])), so Guard can say where.
        created = keccak256(rlp.encode([address_to_bytes(self.wallet), rlp.encode_int(request.nonce)]))[12:]
        address = address_from_bytes(created)
        self.actions.append(
            ContractCreation(path="tx", actor=self.wallet, address=address, code_size=len(request.data))
        )
        if request.value:
            self.actions.append(
                Transfer(
                    path="tx",
                    actor=self.wallet,
                    asset=NATIVE,
                    source=self.wallet,
                    destination=address,
                    amount=request.value,
                )
            )

    def call(self, path: str, actor: str, target: str, value: int, data: bytes, depth: int) -> None:
        """A CALL from `actor` to `target`: its value, then whatever its data does."""
        if depth > MAX_DEPTH:
            raise DecodeError(path, f"calls are nested more than {MAX_DEPTH} levels deep")
        if value:
            self.actions.append(
                Transfer(path=path, actor=actor, asset=NATIVE, source=actor, destination=target, amount=value)
            )
        if not data:
            return
        function = FUNCTIONS.get(data[:4])
        if function is None:
            self.actions.append(
                UnknownCall(path=path, actor=actor, target=target, selector="0x" + data[:4].hex(), data_size=len(data))
            )
            return
        args = self._args(path, function, data)
        getattr(self, function.handler)(_Call(path, actor, target, function, args, depth, len(data)))

    def delegatecall(self, path: str, executor: str, target: str, data: bytes, depth: int) -> None:
        """`executor` running `target`'s code as itself. Value is ignored, as Safe and MultiSend ignore it."""
        if depth > MAX_DEPTH:
            raise DecodeError(path, f"calls are nested more than {MAX_DEPTH} levels deep")
        function = FUNCTIONS.get(data[:4]) if len(data) >= 4 else None
        self.actions.append(
            DelegateCall(
                path=path,
                actor=executor,
                target=target,
                selector="0x" + data[:4].hex(),
                function=function.signature if function else None,
            )
        )
        if function is not None and function.handler == "_multisend":
            payload = self._args(path, function, data)["transactions"]
            self.multisend(path, executor, target, payload, depth + 1)

    @staticmethod
    def _args(path: str, function: _Function, data: bytes) -> dict[str, Any]:
        try:
            values = decode_args(function.types, data[4:])
        except ABIError as exc:
            raise DecodeError(path, f"{function.signature}: {exc}") from exc
        return dict(zip(function.names, values, strict=True))

    def _token_transfer(self, c: _Call) -> None:
        """ERC-20 transfers, and EIP-3009 authorizations submitted on-chain, which move `from`'s tokens."""
        source = c.args.get("from", c.actor)
        self.actions.append(
            Transfer(
                kind=_TRANSFER_KINDS[c.function.name],
                path=c.path,
                actor=c.actor,
                asset=c.target,
                source=source,
                destination=c.args["to"],
                amount=c.args["amount"],
            )
        )

    def _approval(self, c: _Call) -> None:
        if c.function.name == "setApprovalForAll":
            kind, spender, amount = "set_approval_for_all", c.args["operator"], None if c.args["approved"] else 0
        else:
            kind = "erc20_approve" if c.function.name == "approve" else "erc20_increase_allowance"
            spender, amount = c.args["spender"], c.args["amount"]
        self.actions.append(
            Approval(
                kind=kind, path=c.path, actor=c.actor, token=c.target, owner=c.actor, spender=spender, amount=amount
            )
        )

    def _permit2_approve(self, c: _Call) -> None:
        """Permit2's own `approve`: only read as an approval when the call really goes to Permit2."""
        if c.target != PERMIT2:
            selector_hex = "0x" + selector(c.function.signature).hex()
            self.actions.append(
                UnknownCall(path=c.path, actor=c.actor, target=c.target, selector=selector_hex, data_size=c.size)
            )
            return
        self.actions.append(
            Approval(
                kind="permit2_approve",
                path=c.path,
                actor=c.actor,
                token=c.args["token"],
                owner=c.actor,
                spender=c.args["spender"],
                amount=c.args["amount"],
                expires=c.args["expiration"],
            )
        )

    def _permit_call(self, c: _Call) -> None:
        """An EIP-2612 or DAI permit submitted on-chain: an approval from the permit's signer."""
        if "allowed" in c.args:  # DAI: allowed means an unlimited allowance, its absence none
            kind, amount = "dai_permit", None if c.args["allowed"] else 0
        else:
            kind, amount = "permit", c.args["amount"]
        self.actions.append(
            Approval(
                kind=kind,
                path=c.path,
                actor=c.actor,
                token=c.target,
                owner=c.args["owner"],
                spender=c.args["spender"],
                amount=amount,
                expires=c.args["deadline"] or None,
            )
        )

    def _approve_hash(self, c: _Call) -> None:
        self.actions.append(
            BlindSignature(
                path=c.path, actor=c.actor, hash="0x" + c.args["hash"].hex(), via="approveHash", safe=c.target
            )
        )

    def _exec_transaction(self, c: _Call) -> None:
        a = c.args
        self.safe_exec(
            SafeExec(
                path=c.path,
                actor=c.actor,
                safe=c.target,
                to=a["to"],
                value=a["value"],
                data=a["data"],
                operation=a["operation"],
                safe_tx_gas=a["safeTxGas"],
                base_gas=a["baseGas"],
                gas_price=a["gasPrice"],
                gas_token=a["gasToken"],
                refund_receiver=a["refundReceiver"],
                signatures=a["signatures"],
            ),
            c.depth,
        )

    def safe_exec(self, action: SafeExec, depth: int) -> None:
        """Record a Safe transaction, its refund if any, and then what the Safe itself would do."""
        self.actions.append(action)
        if action.gas_price:
            asset = NATIVE if action.gas_token == ZERO_ADDRESS else action.gas_token
            self.actions.append(
                SafeGasRefund(
                    path=action.path,
                    actor=action.safe,
                    safe=action.safe,
                    asset=asset,
                    receiver=action.refund_receiver,
                    gas_price=action.gas_price,
                )
            )
        inner = f"{action.path}.call"
        if action.operation == 0:
            self.call(inner, action.safe, action.to, action.value, action.data, depth + 1)
        elif action.operation == 1:
            self.delegatecall(inner, action.safe, action.to, action.data, depth + 1)
        else:
            raise DecodeError(
                action.path, f"Safe operation must be 0 (call) or 1 (delegatecall), not {action.operation}"
            )

    def _safe_change(self, c: _Call) -> None:
        kind = "safe_owner_change" if c.function.name in OWNER_METHODS else "safe_config_change"
        self.actions.append(
            SafeChange(kind=kind, path=c.path, actor=c.actor, safe=c.target, method=c.function.name, args=c.args)
        )

    def _upgrade(self, c: _Call) -> None:
        """A proxy upgraded directly, or through a ProxyAdmin that names the proxy as its first argument."""
        self.actions.append(
            ProxyUpgrade(
                path=c.path,
                actor=c.actor,
                proxy=c.args.get("proxy", c.target),
                implementation=c.args["implementation"],
                method=c.function.name,
                init_data=c.args.get("data", b""),
            )
        )

    def _admin_change(self, c: _Call) -> None:
        self.actions.append(
            AdminChange(
                path=c.path,
                actor=c.actor,
                contract=c.args.get("proxy", c.target),
                new_admin=c.args["newAdmin"],
                method=c.function.name,
            )
        )

    def _multicall(self, c: _Call) -> None:
        """multicall delegatecalls each item into the same contract, so each runs as a call from `actor`."""
        items = c.args["data"]
        self.actions.append(
            Batch(path=c.path, actor=c.actor, target=c.target, method=c.function.signature, calls=len(items))
        )
        for index, item in enumerate(items):
            self.call(f"{c.path}[{index}]", c.actor, c.target, 0, item, c.depth + 1)

    def _multisend(self, c: _Call) -> None:
        """MultiSend reached by a plain call sends its batch from its own address, not the caller's."""
        self.multisend(c.path, c.target, c.target, c.args["transactions"], c.depth + 1)

    def multisend(self, path: str, actor: str, contract: str, payload: bytes, depth: int) -> None:
        """Decode MultiSend's packed batch: operation (1 byte), to (20), value (32), size (32), data."""
        entries = []
        cursor = 0
        while cursor < len(payload):
            if len(payload) - cursor < 85:
                raise DecodeError(path, "a MultiSend entry is truncated")
            operation = payload[cursor]
            to = address_from_bytes(payload[cursor + 1 : cursor + 21])
            value = int.from_bytes(payload[cursor + 21 : cursor + 53], "big")
            size = int.from_bytes(payload[cursor + 53 : cursor + 85], "big")
            start = cursor + 85
            if size > len(payload) - start:
                raise DecodeError(path, "a MultiSend entry's data runs past the end of the batch")
            entries.append((operation, to, value, payload[start : start + size]))
            cursor = start + size
        self.actions.append(
            Batch(path=path, actor=actor, target=contract, method="multiSend(bytes)", calls=len(entries))
        )
        for index, (operation, to, value, data) in enumerate(entries):
            entry = f"{path}[{index}]"
            if operation == 0:
                self.call(entry, actor, to, value, data, depth)
            elif operation == 1:
                self.delegatecall(entry, actor, to, data, depth)
            else:
                raise DecodeError(entry, f"MultiSend operation must be 0 or 1, not {operation}")

    def typed_data(self, typed: dict[str, Any]) -> None:
        types = types_of(typed)
        primary = typed["primaryType"]
        domain = typed["domain"]
        message = typed["message"]
        try:
            verifying = normalize_address(domain["verifyingContract"]) if "verifyingContract" in domain else None
        except ValueError as exc:
            raise DecodeError("typed_data.domain", f"verifyingContract is not an address: {exc}") from exc
        shapes = {name: dict(declared) for name, declared in types.items()}
        shape = shapes[primary]
        if "Witness" in primary and "witness" in shape:
            shape = {name: kind for name, kind in shape.items() if name != "witness"}
        if primary == "Permit" and verifying and shape == _PERMIT:
            self.actions.append(
                Approval(
                    kind="permit",
                    path="typed_data",
                    actor=self.wallet,
                    token=verifying,
                    owner=normalize_address(message["owner"]),
                    spender=normalize_address(message["spender"]),
                    amount=parse_uint(message["value"]),
                    expires=parse_uint(message["deadline"]),
                )
            )
        elif primary == "Permit" and verifying and shape == _DAI_PERMIT:
            expiry = parse_uint(message["expiry"])
            self.actions.append(
                Approval(
                    kind="dai_permit",
                    path="typed_data",
                    actor=self.wallet,
                    token=verifying,
                    owner=normalize_address(message["holder"]),
                    spender=normalize_address(message["spender"]),
                    amount=None if message["allowed"] else 0,
                    expires=expiry or None,
                )
            )
        elif (
            primary in ("TransferWithAuthorization", "ReceiveWithAuthorization")
            and verifying
            and (shape == _TRANSFER_AUTHORIZATION)
        ):
            self.actions.append(
                Transfer(
                    kind=_TRANSFER_KINDS[primary],
                    path="typed_data",
                    actor=self.wallet,
                    asset=verifying,
                    source=normalize_address(message["from"]),
                    destination=normalize_address(message["to"]),
                    amount=parse_uint(message["value"]),
                )
            )
        elif verifying == PERMIT2 and shape == _PERMIT2.get(primary) and _permit2_parts(primary, shapes):
            self._permit2(message)
        elif primary == "SafeTx" and verifying and shape == _SAFE_TX:
            self.safe_exec(
                SafeExec(
                    path="typed_data",
                    actor=self.wallet,
                    safe=verifying,
                    to=normalize_address(message["to"]),
                    value=parse_uint(message["value"]),
                    data=parse_hex(message["data"]),
                    operation=parse_uint(message["operation"]),
                    safe_tx_gas=parse_uint(message["safeTxGas"]),
                    base_gas=parse_uint(message["baseGas"]),
                    gas_price=parse_uint(message["gasPrice"]),
                    gas_token=normalize_address(message["gasToken"]),
                    refund_receiver=normalize_address(message["refundReceiver"]),
                    nonce=parse_uint(message["nonce"]),
                ),
                0,
            )
        else:
            name = domain.get("name")
            self.actions.append(
                UnknownTypedData(
                    path="typed_data",
                    actor=self.wallet,
                    primary_type=primary,
                    verifying_contract=verifying,
                    domain_name=name if isinstance(name, str) else None,
                )
            )

    def _permit2(self, message: dict[str, Any]) -> None:
        """Permit2 allowances (PermitSingle, PermitBatch) and one-time transfers (Permit*TransferFrom)."""
        spender = normalize_address(message["spender"])
        if "details" in message:
            details = message["details"] if isinstance(message["details"], list) else [message["details"]]
            for index, item in enumerate(details):
                self.actions.append(
                    Approval(
                        kind="permit2_allowance",
                        path=f"typed_data.details[{index}]",
                        actor=self.wallet,
                        token=normalize_address(item["token"]),
                        owner=self.wallet,
                        spender=spender,
                        amount=parse_uint(item["amount"]),
                        expires=parse_uint(item["expiration"]),
                    )
                )
            return
        permitted = message["permitted"] if isinstance(message["permitted"], list) else [message["permitted"]]
        for index, item in enumerate(permitted):
            self.actions.append(
                Approval(
                    kind="permit2_transfer",
                    path=f"typed_data.permitted[{index}]",
                    actor=self.wallet,
                    token=normalize_address(item["token"]),
                    owner=self.wallet,
                    spender=spender,
                    amount=parse_uint(item["amount"]),
                    expires=parse_uint(message["deadline"]),
                )
            )

    def message(self, message: bytes) -> None:
        authorization = parse_authorization_message(message)
        if authorization is not None:
            chain_id, delegate, nonce = authorization
            kind = "eip7702_revocation" if delegate == ZERO_ADDRESS else "eip7702_delegation"
            self.actions.append(
                Delegation(
                    kind=kind,
                    path="message",
                    actor=self.wallet,
                    authority=self.wallet,
                    delegate=delegate,
                    chain_id=chain_id,
                    nonce=nonce,
                )
            )
            return
        try:
            text: str | None = message.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        if text is not None and not all(c.isprintable() or c in "\n\t" for c in text):
            text = None
        # A message that is exactly a hash, as 32 bytes or as its hex text, approves whatever it stands for.
        digest: bytes | None = None
        if len(message) == 32:
            digest = message
        elif text is not None and _HASH_TEXT.fullmatch(text.strip()):
            digest = bytes.fromhex(text.strip().removeprefix("0x"))
        if digest is not None:
            self.actions.append(
                BlindSignature(path="message", actor=self.wallet, hash="0x" + digest.hex(), via="message")
            )
            return
        self.actions.append(PersonalMessage(path="message", actor=self.wallet, text=text, size=len(message)))
