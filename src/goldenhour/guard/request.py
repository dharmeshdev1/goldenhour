"""The signing request: what a caller asks a custody wallet to sign, in a form Guard can check.

A request names the wallet, the chain and exactly what is to be signed: a transaction, EIP-712
typed data, or a message. A transaction may arrive as raw bytes, as decoded fields, or as both;
when both are given they must agree, so a caller cannot show reviewers benign fields while the
signer receives different bytes. Validation refuses anything ambiguous, because Guard can only
vouch for what it can read completely.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    PlainSerializer,
    model_validator,
)

from goldenhour.guard.eip712 import signing_preimage
from goldenhour.guard.evm import keccak256, normalize_address, parse_hex, parse_uint, to_checksum_address
from goldenhour.guard.secp256k1 import recover_address
from goldenhour.guard.transaction import TransactionError, parse_authorization_message, parse_raw, unsigned_payload


def _hash32(value: object) -> bytes:
    raw = parse_hex(value)
    if len(raw) != 32:
        raise ValueError("expected 32 bytes")
    return raw


Address = Annotated[str, BeforeValidator(normalize_address), PlainSerializer(to_checksum_address, when_used="json")]
# Amounts in base units can exceed what a JSON number holds exactly, so they serialize as strings.
Quantity = Annotated[int, BeforeValidator(parse_uint), PlainSerializer(str, when_used="json")]
Count = Annotated[int, BeforeValidator(parse_uint)]
Word = Annotated[int, BeforeValidator(parse_uint), PlainSerializer(lambda v: f"0x{v:064x}", when_used="json")]
Hex = Annotated[bytes, BeforeValidator(parse_hex), PlainSerializer(lambda b: "0x" + b.hex(), when_used="json")]
Hash32 = Annotated[bytes, BeforeValidator(_hash32), PlainSerializer(lambda b: "0x" + b.hex(), when_used="json")]
TxType = Annotated[Literal[0, 1, 2, 4], BeforeValidator(parse_uint)]

TX_FIELDS = (
    "type",
    "nonce",
    "to",
    "value",
    "data",
    "gas",
    "gas_price",
    "max_fee_per_gas",
    "max_priority_fee_per_gas",
    "access_list",
    "authorization_list",
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AccessListEntry(_Model):
    address: Address
    storage_keys: list[Hash32] = Field(default_factory=list)


class Authorization(_Model):
    """One EIP-7702 authorization: `address` is the code the signing account would run."""

    chain_id: Count
    address: Address
    nonce: Count
    y_parity: Count
    r: Word
    s: Word


class Approval(_Model):
    """An approver's Ed25519 signature over the request digest, as hex.

    The signature stays a plain string so that a malformed one is counted as invalid by the quorum
    check instead of making the whole request unreadable.
    """

    approver: str = Field(min_length=1)
    signature: str


class RequestContext(_Model):
    """Facts the caller supplies that are not part of what gets signed: USD prices by asset symbol.

    The policy's price floors bound them, because a compromised caller supplies them too.
    """

    prices_usd: dict[str, Annotated[Decimal, Field(gt=0)]] = Field(default_factory=dict)


class SigningRequest(_Model):
    """One request to sign. The transaction fields apply to `evm_tx` only and must be absent otherwise."""

    kind: Literal["evm_tx", "evm_typed_data", "evm_message"]
    chain_id: Count = Field(ge=1)
    wallet: Address
    raw: Hex | None = None
    type: TxType = 2
    nonce: Count | None = None
    to: Address | None = None
    value: Quantity = 0
    data: Hex = b""
    gas: Count | None = None
    gas_price: Quantity | None = None
    max_fee_per_gas: Quantity | None = None
    max_priority_fee_per_gas: Quantity | None = None
    access_list: list[AccessListEntry] = Field(default_factory=list)
    authorization_list: list[Authorization] = Field(default_factory=list)
    typed_data: dict[str, Any] | None = None
    message: Hex | None = None
    intent_id: str | None = None
    approvals: list[Approval] = Field(default_factory=list)
    context: RequestContext = Field(default_factory=RequestContext)

    @model_validator(mode="wrap")
    @classmethod
    def _validate(cls, data: Any, handler: ModelWrapValidatorHandler[SigningRequest]) -> SigningRequest:
        request = handler(data)
        if request.raw is not None:
            request = request._merge_raw()
        request._check_shape()
        return request

    def _merge_raw(self) -> SigningRequest:
        """Fill the transaction fields from `raw`, refusing any field the caller set differently."""
        assert self.raw is not None
        if self.kind != "evm_tx":
            raise ValueError("raw bytes are only accepted for evm_tx requests")
        try:
            fields, signature = parse_raw(self.raw)
        except TransactionError as exc:
            raise ValueError(f"raw transaction: {exc}") from exc
        chain_id = fields.pop("chain_id", self.chain_id)
        if chain_id != self.chain_id:
            raise ValueError(f"raw transaction is for chain {chain_id}, the request says {self.chain_id}")
        decoded = SigningRequest.model_validate({**self.model_dump(exclude={"raw"}), **fields})
        conflicts = [n for n in fields if n in self.model_fields_set and getattr(self, n) != getattr(decoded, n)]
        if conflicts:
            raise ValueError(f"raw transaction disagrees with the given fields: {', '.join(conflicts)}")
        if signature is not None:
            sender = recover_address(keccak256(unsigned_payload(decoded)), signature.y_parity, signature.r, signature.s)
            if sender != self.wallet:
                raise ValueError(f"raw transaction is signed by {sender}, not by the wallet {self.wallet}")
        return decoded.model_copy(update={"raw": self.raw})

    def _check_shape(self) -> None:
        if self.intent_id is not None and not self.intent_id.strip():
            raise ValueError("intent_id must not be blank")
        if self.kind == "evm_tx":
            self._check_transaction()
            return
        given = [name for name in TX_FIELDS if name in self.model_fields_set]
        if self.raw is not None or given:
            raise ValueError(f"{self.kind} requests carry no transaction fields, got {', '.join(given) or 'raw'}")
        if self.kind == "evm_typed_data":
            if self.typed_data is None or self.message is not None:
                raise ValueError("an evm_typed_data request carries typed_data and no message")
            try:
                signing_preimage(self.typed_data)
            except (TypeError, KeyError, OverflowError, RecursionError) as exc:
                raise ValueError(f"typed data cannot be hashed: {type(exc).__name__}: {exc}") from exc
            domain_chain = self.typed_data["domain"].get("chainId")
            if domain_chain is not None and parse_uint(domain_chain) != self.chain_id:
                raise ValueError(f"typed data is for chain {domain_chain}, the request says {self.chain_id}")
            return
        if self.message is None or self.typed_data is not None:
            raise ValueError("an evm_message request carries message and no typed_data")
        authorization = parse_authorization_message(self.message)
        if authorization is not None and authorization[0] not in (0, self.chain_id):
            raise ValueError(f"the authorization is for chain {authorization[0]}, the request says {self.chain_id}")

    def _check_transaction(self) -> None:
        if self.typed_data is not None or self.message is not None:
            raise ValueError("an evm_tx request carries a transaction, not typed_data or message")
        if self.nonce is None or self.gas is None:
            raise ValueError("an evm_tx request needs nonce and gas, or raw")
        if self.raw is None and "to" not in self.model_fields_set:
            raise ValueError("to is required: give null only to deploy a contract")
        if self.type in (0, 1):
            if self.gas_price is None or self.max_fee_per_gas is not None or self.max_priority_fee_per_gas is not None:
                raise ValueError(f"a type {self.type} transaction prices gas with gas_price only")
        elif self.gas_price is not None or self.max_fee_per_gas is None or self.max_priority_fee_per_gas is None:
            raise ValueError(f"a type {self.type} transaction needs max_fee_per_gas and max_priority_fee_per_gas")
        elif self.max_priority_fee_per_gas > self.max_fee_per_gas:
            raise ValueError("max_priority_fee_per_gas exceeds max_fee_per_gas")
        if self.type == 0 and self.access_list:
            raise ValueError("a legacy transaction has no access list")
        if self.type != 4 and self.authorization_list:
            raise ValueError("only a type 4 transaction carries an authorization list")
        if self.type == 4 and (not self.authorization_list or self.to is None):
            raise ValueError("a type 4 transaction needs a destination and at least one authorization (EIP-7702)")

    def to_json(self) -> dict[str, Any]:
        """The request as JSON, without empty optional fields, for audit records and the public cases."""
        out = self.model_dump(mode="json", exclude_none=True)
        for name in ("access_list", "authorization_list"):
            if not out.get(name):
                out.pop(name, None)
        if not out["context"]["prices_usd"]:
            out.pop("context")
        if self.kind == "evm_tx":
            out["to"] = out.get("to")  # null means a deployment, so it is kept
        else:
            for name in ("type", "value", "data"):
                out.pop(name, None)
        return out
