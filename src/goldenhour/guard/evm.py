"""The few EVM primitives every Guard module shares: keccak, addresses, selectors and hex.

Guard implements these itself instead of depending on a large Ethereum library, so that the code
deciding whether a key signs stays small enough to read in one sitting. Every parser here is strict:
input that a lenient parser would quietly repair is rejected, because a value that two parsers read
differently is how a reviewer is shown one thing while the signer signs another.
"""

from __future__ import annotations

import re
from functools import cache

from Crypto.Hash import keccak

ZERO_ADDRESS = "0x" + "00" * 20
NATIVE = "native"

_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
_HEX = re.compile(r"0x(?:[0-9a-fA-F]{2})*")
_DECIMAL = re.compile(r"[0-9]+")
_HEX_INT = re.compile(r"0x[0-9a-fA-F]+")


def keccak256(data: bytes) -> bytes:
    return keccak.new(digest_bits=256, data=data).digest()


@cache
def selector(signature: str) -> bytes:
    """The 4-byte function selector for a canonical signature such as `transfer(address,uint256)`."""
    return keccak256(signature.encode("ascii"))[:4]


def to_checksum_address(address: str) -> str:
    """EIP-55 mixed-case form, used for display so a reader can spot a mistyped address."""
    body = address[2:].lower()
    digest = keccak256(body.encode("ascii")).hex()
    return "0x" + "".join(c.upper() if int(digest[i], 16) >= 8 else c for i, c in enumerate(body))


def normalize_address(value: object) -> str:
    """Return a lowercase address after checking its shape and, when it is mixed case, its checksum.

    A mixed-case address with a wrong checksum is almost always a typo, and a typo in an allowlist or
    a destination is exactly the kind of mistake Guard exists to catch.
    """
    if not isinstance(value, str) or not _ADDRESS.fullmatch(value):
        hint = " (quote addresses in YAML, or it reads them as numbers)" if isinstance(value, int) else ""
        raise ValueError(f"not an address: {value!r}{hint}")
    body = value[2:]
    if body not in (body.lower(), body.upper()) and to_checksum_address(value) != value:
        raise ValueError(f"address fails its EIP-55 checksum: {value}")
    return "0x" + body.lower()


def address_from_bytes(raw: bytes) -> str:
    if len(raw) != 20:
        raise ValueError(f"an address is 20 bytes, got {len(raw)}")
    return "0x" + raw.hex()


def address_to_bytes(address: str) -> bytes:
    return bytes.fromhex(normalize_address(address)[2:])


def parse_hex(value: object) -> bytes:
    """Bytes from a `0x`-prefixed hex string with an even number of digits."""
    if isinstance(value, bytes):
        return value
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise ValueError(f"expected 0x-prefixed hex bytes, got {value!r}")
    return bytes.fromhex(value[2:])


def parse_uint(value: object) -> int:
    """A non-negative integer from a JSON number, a decimal string or a 0x-prefixed hex string.

    Booleans and floats are refused: `true` is not 1, and a float has already lost the low digits
    of any amount large enough to matter.
    """
    if isinstance(value, bool):
        raise ValueError("expected an integer, got a boolean")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and _DECIMAL.fullmatch(value):
        number = int(value)
    elif isinstance(value, str) and _HEX_INT.fullmatch(value):
        number = int(value, 16)
    else:
        raise ValueError(f"expected a non-negative integer, got {value!r}")
    if number < 0:
        raise ValueError(f"expected a non-negative integer, got {number}")
    return number


def normalize_asset(value: object) -> str:
    """An asset on an EVM chain: the word `native` for the chain's own coin, or a token address."""
    if value == NATIVE:
        return NATIVE
    return normalize_address(value)
