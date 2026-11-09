import pytest

from goldenhour.models import Chain, InvalidAddress, is_virtual, node_id, normalize_address, split_node


def test_evm_addresses_are_lowercased_and_validated() -> None:
    assert normalize_address(Chain.ETHEREUM, "0x8532FA14ACAf483729C449b8458aedDb900543D4") == (
        "0x8532fa14acaf483729c449b8458aeddb900543d4"
    )
    with pytest.raises(InvalidAddress):
        normalize_address(Chain.ETHEREUM, "0x8532FA14ACAf483729C449b8458aedDb900543D")  # 39 hex digits
    with pytest.raises(InvalidAddress):
        normalize_address(Chain.BASE, "../../etc/passwd")


def test_bitcoin_bech32_is_lowercased_base58_kept() -> None:
    assert normalize_address(Chain.BITCOIN, "BC1Q0TS8GAF9HT0RR4U02HR56F8THNDQSC4KMKMFV3").startswith("bc1q0ts8")
    legacy = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
    assert normalize_address(Chain.BITCOIN, legacy) == legacy


@pytest.mark.parametrize(
    ("chain", "address"),
    [
        (Chain.TRON, "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"),
        (Chain.XRPL, "rEb8TK3gBgk5auZkwc6sHnwrGVJH8DuaLh"),
        (Chain.SOLANA, "AffzFDKZbyMiU11zmVBoAqD25ugpr7xoWWyRyGigBEaU"),
    ],
)
def test_case_sensitive_encodings_are_kept(chain: Chain, address: str) -> None:
    assert normalize_address(chain, address) == address
    with pytest.raises(InvalidAddress):
        normalize_address(chain, address + "/x")


def test_node_ids_round_trip_and_virtual_nodes() -> None:
    node = node_id("ethereum", "0xAbCdEf0000000000000000000000000000000001")
    assert node == "ethereum:0xabcdef0000000000000000000000000000000001"
    assert split_node(node) == (Chain.ETHEREUM, "0xabcdef0000000000000000000000000000000001")
    assert is_virtual("bitcoin:@tx:abcd")
    assert is_virtual("ethereum:@fees")
    assert not is_virtual(node)
