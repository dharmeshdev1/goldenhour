"""The normalized vocabulary every chain adapter translates into.

Eleven blockchains disagree about almost everything. Bitcoin moves coins between transaction
outputs. Ethereum moves balances between accounts and lets contracts move value invisibly inside a
call. Tron copies Ethereum with a different address encoding, the XRP Ledger has destination tags,
and Solana moves tokens between accounts that belong to owners. Everything past the adapters speaks
only the types in this module: a node is an address on a chain, and a transfer moves an amount of
one asset from one node to another at one moment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum


class Chain(StrEnum):
    ETHEREUM = "ethereum"
    OPTIMISM = "optimism"
    ARBITRUM = "arbitrum"
    BASE = "base"
    POLYGON = "polygon"
    AVALANCHE = "avalanche"
    BITCOIN = "bitcoin"
    TRON = "tron"
    XRPL = "xrpl"
    SOLANA = "solana"
    NEAR = "near"

    @property
    def is_evm(self) -> bool:
        return self in EVM_CHAINS


EVM_CHAINS = frozenset({Chain.ETHEREUM, Chain.OPTIMISM, Chain.ARBITRUM, Chain.BASE, Chain.POLYGON, Chain.AVALANCHE})

# Native asset decimals. Tokens carry their own.
NATIVE_DECIMALS: dict[Chain, int] = {
    Chain.ETHEREUM: 18,
    Chain.OPTIMISM: 18,
    Chain.ARBITRUM: 18,
    Chain.BASE: 18,
    Chain.POLYGON: 18,
    Chain.AVALANCHE: 18,
    Chain.BITCOIN: 8,
    Chain.TRON: 6,
    Chain.XRPL: 6,
    Chain.SOLANA: 9,
    Chain.NEAR: 24,
}

NATIVE_SYMBOL: dict[Chain, str] = {
    Chain.ETHEREUM: "ETH",
    Chain.OPTIMISM: "ETH",
    Chain.ARBITRUM: "ETH",
    Chain.BASE: "ETH",
    Chain.POLYGON: "POL",
    Chain.AVALANCHE: "AVAX",
    Chain.BITCOIN: "BTC",
    Chain.TRON: "TRX",
    Chain.XRPL: "XRP",
    Chain.SOLANA: "SOL",
    Chain.NEAR: "NEAR",
}

_EVM = re.compile(r"^0x[0-9a-fA-F]{40}$")
_BTC_BECH32 = re.compile(r"^(bc1)[02-9ac-hj-np-z]{11,71}$", re.IGNORECASE)
_BTC_BASE58 = re.compile(r"^[13][1-9A-HJ-NP-Za-km-z]{25,34}$")
_TRON = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")
_XRPL = re.compile(r"^r[1-9A-HJ-NP-Za-km-z]{24,34}$")
_SOLANA = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_NEAR = re.compile(r"^(([a-z\d]+[-_])*[a-z\d]+\.)*([a-z\d]+[-_])*[a-z\d]+$|^[0-9a-f]{64}$")


class InvalidAddress(ValueError):
    pass


def normalize_address(chain: Chain | str, address: str) -> str:
    """The canonical spelling of an address, or InvalidAddress.

    EVM addresses are case-insensitive (the mixed case is only a checksum), so they are lowercased.
    Bech32 bitcoin addresses are lowercased too. Base58 encodings are case-sensitive and kept as is.
    Every address that reaches storage, a file name or a URL has passed through here.
    """
    chain = Chain(chain)
    a = address.strip()
    if chain.is_evm:
        if not _EVM.match(a):
            raise InvalidAddress(f"not an EVM address: {address!r}")
        return a.lower()
    if chain is Chain.BITCOIN:
        if _BTC_BECH32.match(a):
            return a.lower()
        if _BTC_BASE58.match(a):
            return a
        raise InvalidAddress(f"not a bitcoin address: {address!r}")
    pattern = {Chain.TRON: _TRON, Chain.XRPL: _XRPL, Chain.SOLANA: _SOLANA, Chain.NEAR: _NEAR}[chain]
    if not pattern.match(a):
        raise InvalidAddress(f"not a {chain} address: {address!r}")
    return a


def node_id(chain: Chain | str, address: str) -> str:
    """The id of an address on a chain: `chain:address`. The same 0x address on two EVM chains is
    two nodes, because the same key can be used by different people on different chains."""
    return f"{Chain(chain)}:{normalize_address(chain, address)}"


def split_node(node: str) -> tuple[Chain, str]:
    chain, _, address = node.partition(":")
    return Chain(chain), address


def native_asset(chain: Chain | str) -> str:
    return f"{Chain(chain)}:native"


def token_asset(chain: Chain | str, contract: str) -> str:
    chain = Chain(chain)
    return f"{chain}:{contract.lower() if chain.is_evm else contract}"


def asset_chain(asset: str) -> Chain:
    return Chain(asset.partition(":")[0])


class Kind(StrEnum):
    NATIVE = "native"  # a top-level transfer of the chain's own coin
    INTERNAL = "internal"  # the chain's own coin moved by contract code inside a call
    TOKEN = "token"  # a fungible token: ERC-20, TRC-20, SPL, an XRPL issued currency
    UTXO = "utxo"  # a bitcoin input or output, joined through a transaction node
    FEE = "fee"  # the network fee the sender paid
    BRIDGE = "bridge"  # value that left one chain and arrived on another


# Sinks that are not addresses. Fees leave the economy; transaction nodes join bitcoin inputs to
# outputs so the same proportional rule that works for accounts also works for coins.
def fee_node(chain: Chain | str) -> str:
    return f"{Chain(chain)}:@fees"


def btc_tx_node(txid: str) -> str:
    return f"bitcoin:@tx:{txid}"


def is_virtual(node: str) -> bool:
    return ":@" in node


@dataclass(frozen=True, slots=True)
class Transfer:
    """An amount of one asset moving from one node to another.

    `uid` is stable across re-fetches from any source, so the same transfer seen twice is stored
    once. `amount` is in the asset's smallest unit (wei, satoshi, sun, drops, lamports).
    """

    uid: str
    chain: Chain
    tx: str
    block: int
    ts: int
    position: int
    sender: str
    receiver: str
    asset: str
    amount: int
    kind: Kind
    meta: dict[str, str] = field(default_factory=dict, compare=False, hash=False)


@dataclass(frozen=True, slots=True)
class Token:
    """What a source said about a token. The symbol is display only: anyone can deploy a token
    called "USDT", so identity is always the contract, never the name."""

    asset: str
    symbol: str
    decimals: int
    name: str = ""


@dataclass(slots=True)
class Page:
    """One fetch of an address's history: the transfers, tokens seen, and where to resume."""

    transfers: list[Transfer] = field(default_factory=list)
    tokens: dict[str, Token] = field(default_factory=dict)
    cursor: dict[str, object] = field(default_factory=dict)
    complete: bool = True
