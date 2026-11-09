"""Recursive Length Prefix, the byte encoding inside every Ethereum transaction.

The decoder sits on the trust boundary: it reads bytes that a compromised caller may have crafted.
So it accepts exactly the canonical encodings an Ethereum node accepts and nothing else. Two
decoders that disagree about the same bytes are how a reviewer is shown one transaction while the
signer signs another, so leniency here would be a vulnerability, not a convenience.
"""

from __future__ import annotations

type Item = bytes | list[Item]

MAX_DEPTH = 32


class RLPError(ValueError):
    """Bytes that are not one canonical RLP item."""


def encode(item: Item | int) -> bytes:
    """Encode bytes, non-negative integers (big-endian, no leading zeros) and nested lists of them."""
    if isinstance(item, int):
        return encode(encode_int(item))
    if isinstance(item, bytes):
        if len(item) == 1 and item[0] < 0x80:
            return item
        return _length_prefix(len(item), 0x80) + item
    payload = b"".join(encode(child) for child in item)
    return _length_prefix(len(payload), 0xC0) + payload


def encode_int(value: int) -> bytes:
    if value < 0:
        raise RLPError("RLP has no negative integers")
    return value.to_bytes((value.bit_length() + 7) // 8, "big")


def _length_prefix(length: int, offset: int) -> bytes:
    if length < 56:
        return bytes([offset + length])
    size = encode_int(length)
    return bytes([offset + 55 + len(size)]) + size


def decode(data: bytes) -> Item:
    """Decode one item and require that it spans the whole input."""
    item, end = _decode_at(data, 0, len(data), 0)
    if end != len(data):
        raise RLPError(f"{len(data) - end} trailing bytes after the RLP item")
    return item


def decode_int(item: Item) -> int:
    """An integer field: a byte string with no leading zero byte, as nodes require."""
    if not isinstance(item, bytes):
        raise RLPError("expected an integer, got a list")
    if item[:1] == b"\x00":
        raise RLPError("integer has a leading zero byte")
    return int.from_bytes(item, "big")


def _read_length(data: bytes, start: int, size: int, limit: int) -> int:
    if start + size > limit:
        raise RLPError("input ends inside a length")
    raw = data[start : start + size]
    if raw[0] == 0:
        raise RLPError("length has a leading zero byte")
    length = int.from_bytes(raw, "big")
    if length < 56:
        raise RLPError("long form used for a length under 56")
    return length


def _decode_at(data: bytes, pos: int, limit: int, depth: int) -> tuple[Item, int]:
    if depth > MAX_DEPTH:
        raise RLPError(f"lists nested deeper than {MAX_DEPTH}")
    if pos >= limit:
        raise RLPError("unexpected end of input")
    prefix = data[pos]
    if prefix < 0x80:
        return data[pos : pos + 1], pos + 1
    if prefix < 0xB8:
        start, length = pos + 1, prefix - 0x80
        if length == 1 and start < limit and data[start] < 0x80:
            raise RLPError("a single byte below 0x80 must encode itself")
    elif prefix < 0xC0:
        size = prefix - 0xB7
        start, length = pos + 1 + size, _read_length(data, pos + 1, size, limit)
    elif prefix < 0xF8:
        start, length = pos + 1, prefix - 0xC0
    else:
        size = prefix - 0xF7
        start, length = pos + 1 + size, _read_length(data, pos + 1, size, limit)
    end = start + length
    if end > limit:
        raise RLPError("item runs past the end of its container")
    if prefix < 0xC0:
        return data[start:end], end
    items: list[Item] = []
    cursor = start
    while cursor < end:
        child, cursor = _decode_at(data, cursor, end, depth + 1)
        items.append(child)
    return items, end
