from __future__ import annotations

import pytest

from goldenhour.guard import rlp

LOREM = b"Lorem ipsum dolor sit amet, consectetur adipisicing elit"  # 56 bytes: the first long string

# Encodings from the Ethereum RLP specification.
VECTORS: list[tuple[rlp.Item | int, str]] = [
    (b"dog", "83646f67"),
    ([b"cat", b"dog"], "c88363617483646f67"),
    (b"", "80"),
    ([], "c0"),
    (0, "80"),
    (b"\x00", "00"),
    (15, "0f"),
    (1024, "820400"),
    ([[], [[]], [[], [[]]]], "c7c0c1c0c3c0c1c0"),
    (LOREM, "b838" + LOREM.hex()),
]


@pytest.mark.parametrize(("item", "encoded"), VECTORS)
def test_encode_matches_specification(item: rlp.Item | int, encoded: str) -> None:
    assert rlp.encode(item).hex() == encoded


@pytest.mark.parametrize(("item", "encoded"), [v for v in VECTORS if not isinstance(v[0], int)])
def test_decode_inverts_encode(item: rlp.Item, encoded: str) -> None:
    assert rlp.decode(bytes.fromhex(encoded)) == item


def test_long_list_round_trips() -> None:
    item: rlp.Item = [b"x" * 40, [b"y" * 30, b""], b"\x7f"]
    encoded = rlp.encode(item)
    assert encoded[0] == 0xF8  # payload of 56 bytes or more takes the long list form
    assert rlp.decode(encoded) == item


def test_integers() -> None:
    assert rlp.decode_int(rlp.decode(rlp.encode(2**255))) == 2**255
    assert rlp.decode_int(b"") == 0
    with pytest.raises(rlp.RLPError, match="leading zero"):
        rlp.decode_int(b"\x00\x01")
    with pytest.raises(rlp.RLPError, match="list"):
        rlp.decode_int([b"\x01"])
    with pytest.raises(rlp.RLPError, match="negative"):
        rlp.encode(-1)


@pytest.mark.parametrize(
    ("encoded", "reason"),
    [
        ("", "unexpected end"),
        ("83646f", "past the end"),
        ("83646f6700", "trailing"),
        ("8105", "must encode itself"),
        ("b805" + "00" * 5, "long form"),
        ("b90038" + "00" * 56, "leading zero"),
        ("b9", "inside a length"),
        ("c283646f67", "past the end"),
        ("c4" + "83646f67" + "00", "trailing"),
        ("f838" + "c0" * 3, "past the end"),
    ],
)
def test_non_canonical_or_truncated_input_is_refused(encoded: str, reason: str) -> None:
    with pytest.raises(rlp.RLPError, match=reason):
        rlp.decode(bytes.fromhex(encoded))


def test_nesting_is_bounded() -> None:
    nested: rlp.Item = []
    for _ in range(rlp.MAX_DEPTH + 5):
        nested = [nested]
    with pytest.raises(rlp.RLPError, match="nested deeper"):
        rlp.decode(rlp.encode(nested))
