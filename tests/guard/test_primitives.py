"""keccak, addresses, secp256k1 recovery, Ed25519 keys, the ABI decoder and EIP-712 hashing."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from goldenhour.guard import abi, eip712, keys, secp256k1
from goldenhour.guard.evm import (
    keccak256,
    normalize_address,
    normalize_asset,
    parse_hex,
    parse_uint,
    selector,
    to_checksum_address,
)

from .helpers import encode_args, sign

# EIP-155's worked example: the signing hash of its transaction and the signature by key 0x4646...46.
EIP155_HASH = bytes.fromhex("daf5a779ae972f972197303d7b574746c7ef83eadac0f2791ad23db92e4c8e53")
EIP155_KEY = int("46" * 32, 16)
EIP155_R = 18515461264373351373200002665853028612451056578545711640558177340181847433846
EIP155_S = 46948507304638947509940763649030358759909902576025900602547168820602576006531
EIP155_SENDER = "0x9d8a62f656a8d1615c1294fd71e9cfb3e4855a4f"


def test_keccak_and_selectors() -> None:
    assert keccak256(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    assert selector("transfer(address,uint256)").hex() == "a9059cbb"
    assert selector("approve(address,uint256)").hex() == "095ea7b3"
    assert selector("setApprovalForAll(address,bool)").hex() == "a22cb465"
    assert selector("multiSend(bytes)").hex() == "8d80ff0a"
    signature = "execTransaction(address,uint256,bytes,uint8,uint256,uint256,uint256,address,address,bytes)"
    assert selector(signature).hex() == "6a761202"


@pytest.mark.parametrize(
    "address",
    [
        "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
        "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
        "0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB",
        "0xD1220A0cf47c7B9Be7A2E6BA89F429762e7b9aDb",
    ],
)
def test_eip55_checksums(address: str) -> None:
    assert to_checksum_address(address.lower()) == address
    assert normalize_address(address) == address.lower()


def test_address_parsing_is_strict() -> None:
    assert normalize_address("0x" + "AB" * 20) == "0x" + "ab" * 20  # one case throughout carries no checksum
    with pytest.raises(ValueError, match="checksum"):
        normalize_address("0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAeD")
    with pytest.raises(ValueError, match="quote addresses"):
        normalize_address(0x1234)
    with pytest.raises(ValueError, match="not an address"):
        normalize_address("0x1234")
    assert normalize_asset("native") == "native"
    with pytest.raises(ValueError, match="not an address"):
        normalize_asset("ETH")


def test_number_and_hex_parsing() -> None:
    assert parse_uint(5) == 5
    assert parse_uint("1000") == 1000
    assert parse_uint("0xff") == 255
    for bad in (True, 1.5, -1, "1.5", "", "0x", "-3"):
        with pytest.raises(ValueError, match="expected"):
            parse_uint(bad)
    assert parse_hex("0x") == b""
    assert parse_hex("0x00ff") == b"\x00\xff"
    for bad_hex in ("00ff", "0x0", "0xzz", 5):
        with pytest.raises(ValueError, match="expected"):
            parse_hex(bad_hex)


def test_secp256k1_reproduces_eip155_example() -> None:
    assert secp256k1.private_key_to_address(EIP155_KEY) == EIP155_SENDER
    assert sign(EIP155_HASH, EIP155_KEY) == (0, EIP155_R, EIP155_S)
    assert secp256k1.recover_address(EIP155_HASH, 0, EIP155_R, EIP155_S) == EIP155_SENDER
    assert secp256k1.recover_address(EIP155_HASH, 1, EIP155_R, EIP155_S) != EIP155_SENDER


def test_secp256k1_signatures_are_low_s_and_recoverable() -> None:
    for index in range(8):
        digest = keccak256(bytes([index]))
        key = int.from_bytes(keccak256(b"key" + bytes([index])), "big") % secp256k1.N
        y_parity, r, s = sign(digest, key)
        assert s <= secp256k1.HALF_N
        assert secp256k1.recover_address(digest, y_parity, r, s) == secp256k1.private_key_to_address(key)


@pytest.mark.parametrize(
    ("y_parity", "r", "s"), [(2, EIP155_R, EIP155_S), (0, 0, EIP155_S), (0, EIP155_R, secp256k1.N), (0, 5, 1)]
)
def test_secp256k1_rejects_impossible_signatures(y_parity: int, r: int, s: int) -> None:
    # r = 5 is not the x coordinate of any point on the curve.
    with pytest.raises(ValueError, match=r"range|curve"):
        secp256k1.recover_address(EIP155_HASH, y_parity, r, s)


def test_ed25519_keys(tmp_path: Path) -> None:
    key = keys.generate()
    path = tmp_path / "guard.pem"
    keys.save_private_key(key, path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        keys.save_private_key(keys.generate(), path)
    loaded = keys.load_private_key(path)
    public_hex = keys.public_key_hex(loaded)
    assert public_hex == keys.public_key_hex(key) == keys.public_key_hex(key.public_key())
    public = keys.parse_public_key("0x" + public_hex)
    assert keys.verify(public, key.sign(b"statement"), b"statement")
    assert not keys.verify(public, key.sign(b"statement"), b"another statement")
    assert keys.public_key_hex(keys.parse_public_key(public_hex.upper())) == public_hex
    with pytest.raises(ValueError, match="32 bytes"):
        keys.parse_public_key("ab" * 31)


def test_abi_decodes_every_supported_type() -> None:
    data = encode_args(
        ["address", "bool", "uint8", "bytes", "bytes[]"], ("0x" + "11" * 20, True, 7, b"\x01\x02", [b"a", b"bc"])
    )
    assert abi.decode_args(["address", "bool", "uint8", "bytes", "bytes[]"], data) == [
        "0x" + "11" * 20,
        True,
        7,
        b"\x01\x02",
        [b"a", b"bc"],
    ]


@pytest.mark.parametrize(
    ("types", "data", "reason"),
    [
        (["address"], b"\x01" + b"\x00" * 31, "high bytes"),
        (["bool"], (2).to_bytes(32, "big"), "neither 0 nor 1"),
        (["uint8"], (256).to_bytes(32, "big"), "out of range"),
        (["uint256", "uint256"], b"\x00" * 40, "ends before"),
        (["bytes"], (2**200).to_bytes(32, "big"), "ends before"),
        (["bytes"], (32).to_bytes(32, "big") + (1000).to_bytes(32, "big"), "runs past"),
        (["bytes[]"], (32).to_bytes(32, "big") + (10**6).to_bytes(32, "big"), "cannot fit"),
        (["int256"], b"\x00" * 32, "unsupported"),
    ],
)
def test_abi_refuses_malformed_arguments(types: list[str], data: bytes, reason: str) -> None:
    with pytest.raises(abi.ABIError, match=reason):
        abi.decode_args(types, data)


MAIL = {
    "types": {
        "EIP712Domain": [
            {"name": "name", "type": "string"},
            {"name": "version", "type": "string"},
            {"name": "chainId", "type": "uint256"},
            {"name": "verifyingContract", "type": "address"},
        ],
        "Person": [{"name": "name", "type": "string"}, {"name": "wallet", "type": "address"}],
        "Mail": [
            {"name": "from", "type": "Person"},
            {"name": "to", "type": "Person"},
            {"name": "contents", "type": "string"},
        ],
    },
    "primaryType": "Mail",
    "domain": {
        "name": "Ether Mail",
        "version": "1",
        "chainId": 1,
        "verifyingContract": "0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC",
    },
    "message": {
        "from": {"name": "Cow", "wallet": "0xCD2a3d9F938E13CD947Ec05AbC7FE734Df8DD826"},
        "to": {"name": "Bob", "wallet": "0xbBbBBBBbbBBBbbbBbbBbbbbBBbBbbbbBbBbbBBbB"},
        "contents": "Hello, Bob!",
    },
}


def test_eip712_matches_the_specification_example() -> None:
    types = eip712.types_of(MAIL)
    assert (
        eip712.encode_type("Mail", types)
        == "Mail(Person from,Person to,string contents)Person(string name,address wallet)"
    )
    assert eip712.hash_struct("Mail", MAIL["message"], types).hex() == (
        "c52c0ee5d84264471806290a3f2c4cecfc5490626bf912d01f240d7a274b371e"
    )
    assert eip712.hash_struct("EIP712Domain", MAIL["domain"], types).hex() == (
        "f2cee375fa42b42143804025fc449deafd50cc031ca257e0b194a650a912090f"
    )
    assert keccak256(eip712.signing_preimage(MAIL)).hex() == (
        "be609aee343fb3c4b28e1df9e632fca64fcfaede20f02e86244efddf30957bd2"
    )


def test_eip712_infers_the_domain_type_when_it_is_missing() -> None:
    inferred = {**MAIL, "types": {k: v for k, v in MAIL["types"].items() if k != "EIP712Domain"}}
    assert eip712.signing_preimage(inferred) == eip712.signing_preimage(MAIL)


def _with(**changes: object) -> dict[str, object]:
    return {**MAIL, **changes}


@pytest.mark.parametrize(
    ("typed", "reason"),
    [
        (_with(message={**MAIL["message"], "note": "not signed"}), "does not declare"),
        (_with(message={"from": MAIL["message"]["from"], "to": MAIL["message"]["to"]}), "missing"),
        (_with(primaryType="EIP712Domain"), "primaryType"),
        (_with(primaryType="Letter"), "primaryType"),
        (
            _with(
                types={
                    **MAIL["types"],
                    "Person": [{"name": "name", "type": "strung"}, {"name": "wallet", "type": "address"}],
                }
            ),
            "unknown type",
        ),
        (_with(message={**MAIL["message"], "contents": 5}), "expected a string"),
        (_with(domain={**MAIL["domain"], "chainId": -1}), "bad uint256"),
        (_with(types="nope"), "needs `types`"),
    ],
)
def test_eip712_refuses_ambiguous_data(typed: dict[str, object], reason: str) -> None:
    with pytest.raises(eip712.TypedDataError, match=reason):
        eip712.signing_preimage(typed)


def test_eip712_atomic_encodings() -> None:
    types: eip712.Types = {
        "T": [("a", "int8"), ("b", "bytes4"), ("c", "bool"), ("d", "uint8[2]"), ("e", "bytes")],
    }
    value = {"a": "-1", "b": "0x01020304", "c": True, "d": [1, 2], "e": "0xff"}
    encoded = eip712.hash_struct("T", value, types)
    assert len(encoded) == 32
    for field, bad, reason in (
        ("a", 128, "out of range"),
        ("b", "0x0102", "expected 4 bytes"),
        ("c", 1, "true or false"),
        ("d", [1], "exactly 2"),
    ):
        with pytest.raises(eip712.TypedDataError, match=reason):
            eip712.hash_struct("T", {**value, field: bad}, types)
