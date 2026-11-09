"""Just enough of the Solidity ABI to read the calls Guard explains.

Offsets are followed the way Solidity follows them, so Guard reads the same values the contract
will. Where old contracts were lax the decoder is strict: an address word with nonzero high bytes,
or a bool that is neither 0 nor 1, is refused rather than masked, because an argument that a
reviewer and a contract read differently is an opening.
"""

from __future__ import annotations

from collections.abc import Sequence

from goldenhour.guard.evm import address_from_bytes


class ABIError(ValueError):
    """Call data that does not decode as the arguments its selector promises."""


def _word(data: bytes, offset: int) -> bytes:
    if offset < 0 or offset + 32 > len(data):
        raise ABIError(f"call data ends before the word at byte {offset}")
    return data[offset : offset + 32]


def _uint(data: bytes, offset: int) -> int:
    return int.from_bytes(_word(data, offset), "big")


def _bytes_at(data: bytes, offset: int) -> bytes:
    length = _uint(data, offset)
    start = offset + 32
    if length > len(data) - start:
        raise ABIError(f"a bytes value of {length} bytes runs past the end of the call data")
    return data[start : start + length]


def _bytes_array_at(data: bytes, offset: int) -> list[bytes]:
    count = _uint(data, offset)
    base = offset + 32
    if count > (len(data) - base) // 32:
        raise ABIError(f"an array of {count} elements cannot fit in the call data")
    return [_bytes_at(data, base + _uint(data, base + 32 * i)) for i in range(count)]


def _static(kind: str, word: bytes) -> object:
    value = int.from_bytes(word, "big")
    if kind == "address":
        if word[:12] != bytes(12):
            raise ABIError("an address argument has nonzero high bytes")
        return address_from_bytes(word[12:])
    if kind == "bool":
        if value > 1:
            raise ABIError("a bool argument is neither 0 nor 1")
        return bool(value)
    if kind.startswith("uint"):
        bits = int(kind[4:] or 256)
        if value >> bits:
            raise ABIError(f"a {kind} argument is out of range")
        return value
    if kind.startswith("bytes") and kind[5:].isdigit() and 1 <= int(kind[5:]) <= 32:
        size = int(kind[5:])
        if word[size:] != bytes(32 - size):
            raise ABIError(f"a {kind} argument has nonzero padding")
        return word[:size]
    raise ABIError(f"unsupported ABI type {kind}")


def decode_args(types: Sequence[str], data: bytes) -> list[object]:
    """Decode an argument tuple, the call data after its 4-byte selector.

    Supports address, bool, uintN, bytesN, bytes and bytes[]: every type the calls Guard explains use.
    Trailing bytes are ignored, as Solidity ignores them.
    """
    values: list[object] = []
    for index, kind in enumerate(types):
        head = 32 * index
        if kind == "bytes":
            values.append(_bytes_at(data, _uint(data, head)))
        elif kind == "bytes[]":
            values.append(_bytes_array_at(data, _uint(data, head)))
        else:
            values.append(_static(kind, _word(data, head)))
    return values
