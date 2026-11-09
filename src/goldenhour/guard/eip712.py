"""EIP-712 typed data hashing, so Guard can name the exact hash a signer is asked to sign.

A typed data request is only explainable if Guard computes its hash itself: a signer that signs a
hash the caller supplied would sign anything. Hashing follows the EIP exactly. Two choices are
stricter than some wallets: a message field the type does not declare is refused, because it would
be displayed to reviewers without being covered by the signature, and every declared field must be
present.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from goldenhour.guard.evm import address_to_bytes, keccak256, parse_hex, parse_uint

type Types = dict[str, list[tuple[str, str]]]

# The domain fields EIP-712 defines, in the order the EIP lists them. Used when a request omits
# the EIP712Domain type, as most wallets allow.
DOMAIN_FIELDS = (
    ("name", "string"),
    ("version", "string"),
    ("chainId", "uint256"),
    ("verifyingContract", "address"),
    ("salt", "bytes32"),
)

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ARRAY = re.compile(r"(.+)\[(\d*)\]")


class TypedDataError(ValueError):
    """Typed data that cannot be hashed unambiguously."""


def types_of(typed: Mapping[str, object]) -> Types:
    """The struct definitions, with EIP712Domain inferred from the domain when it is missing."""
    raw_types = typed.get("types")
    domain = typed.get("domain")
    if not isinstance(raw_types, Mapping) or not isinstance(domain, Mapping):
        raise TypedDataError("typed data needs `types` and `domain` objects")
    types: Types = {}
    for name, fields in raw_types.items():
        if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name) or not isinstance(fields, list):
            raise TypedDataError(f"bad type definition {name!r}")
        parsed: list[tuple[str, str]] = []
        for field in fields:
            if not isinstance(field, Mapping) or not isinstance(field.get("name"), str):
                raise TypedDataError(f"bad field in type {name}")
            if not isinstance(field.get("type"), str):
                raise TypedDataError(f"field {field['name']} of {name} has no type")
            parsed.append((field["name"], field["type"]))
        if len({n for n, _ in parsed}) != len(parsed):
            raise TypedDataError(f"type {name} declares a field twice")
        types[name] = parsed
    if "EIP712Domain" not in types:
        types["EIP712Domain"] = [(n, t) for n, t in DOMAIN_FIELDS if n in domain]
    return types


def _base(kind: str) -> str:
    match = _ARRAY.fullmatch(kind)
    return _base(match.group(1)) if match else kind


def _dependencies(primary: str, types: Types, found: set[str]) -> set[str]:
    if primary in found or primary not in types:
        return found
    found.add(primary)
    for _, kind in types[primary]:
        _dependencies(_base(kind), types, found)
    return found


def encode_type(primary: str, types: Types) -> str:
    others = sorted(_dependencies(primary, types, set()) - {primary})
    return "".join(f"{name}({','.join(f'{t} {n}' for n, t in types[name])})" for name in [primary, *others])


def type_hash(primary: str, types: Types) -> bytes:
    return keccak256(encode_type(primary, types).encode())


def hash_struct(primary: str, data: object, types: Types) -> bytes:
    if not isinstance(data, Mapping):
        raise TypedDataError(f"value for {primary} must be an object")
    declared = {name for name, _ in types[primary]}
    undeclared = set(data) - declared
    if undeclared:
        raise TypedDataError(f"{primary} has fields its type does not declare: {sorted(undeclared)}")
    missing = declared - set(data)
    if missing:
        raise TypedDataError(f"{primary} is missing fields: {sorted(missing)}")
    encoded = b"".join(_encode_value(kind, data[name], types) for name, kind in types[primary])
    return keccak256(type_hash(primary, types) + encoded)


def _encode_value(kind: str, value: object, types: Types) -> bytes:
    if kind in types:
        return hash_struct(kind, value, types)
    array = _ARRAY.fullmatch(kind)
    if array:
        if not isinstance(value, list):
            raise TypedDataError(f"value for {kind} must be a list")
        if array.group(2) and len(value) != int(array.group(2)):
            raise TypedDataError(f"{kind} needs exactly {array.group(2)} elements")
        return keccak256(b"".join(_encode_value(array.group(1), item, types) for item in value))
    try:
        return _encode_atomic(kind, value)
    except ValueError as exc:
        raise TypedDataError(f"bad {kind} value {value!r}: {exc}") from exc


def _encode_atomic(kind: str, value: object) -> bytes:
    if kind == "string":
        if not isinstance(value, str):
            raise ValueError("expected a string")
        return keccak256(value.encode())
    if kind == "bytes":
        return keccak256(parse_hex(value))
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError("expected true or false")
        return int(value).to_bytes(32, "big")
    if kind == "address":
        return address_to_bytes(str(value)).rjust(32, b"\x00")
    if kind.startswith("uint") and kind[4:].isdigit():
        number = parse_uint(value)
        if number >> _bits(kind[4:]):
            raise ValueError("out of range")
        return number.to_bytes(32, "big")
    if kind.startswith("int") and kind[3:].isdigit():
        bits = _bits(kind[3:])
        number = _parse_signed(value)
        if not -(1 << (bits - 1)) <= number < 1 << (bits - 1):
            raise ValueError("out of range")
        return (number % (1 << 256)).to_bytes(32, "big")
    if kind.startswith("bytes") and kind[5:].isdigit() and 1 <= int(kind[5:]) <= 32:
        raw = parse_hex(value)
        if len(raw) != int(kind[5:]):
            raise ValueError(f"expected {kind[5:]} bytes")
        return raw.ljust(32, b"\x00")
    raise ValueError("unknown type")


def _bits(size: str) -> int:
    """The width of an integer type, which Solidity allows only in steps of 8 from 8 to 256."""
    bits = int(size)
    if bits % 8 or not 8 <= bits <= 256:
        raise ValueError(f"no integer type is {bits} bits wide")
    return bits


def _parse_signed(value: object) -> int:
    if isinstance(value, str) and value.startswith("-"):
        return -parse_uint(value[1:])
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return parse_uint(value)


def signing_preimage(typed: Mapping[str, object]) -> bytes:
    """`0x1901 || domainSeparator || hashStruct(message)`: the bytes whose keccak a signer signs."""
    types = types_of(typed)
    primary = typed.get("primaryType")
    if not isinstance(primary, str) or primary not in types or primary == "EIP712Domain":
        raise TypedDataError(f"primaryType {primary!r} is not a declared struct")
    domain = hash_struct("EIP712Domain", typed["domain"], types)
    return b"\x19\x01" + domain + hash_struct(primary, typed.get("message"), types)
