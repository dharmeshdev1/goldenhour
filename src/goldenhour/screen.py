"""Screening: is this address on the trail, or did it receive money from it?

The public edition answers from static shards. The address itself, spelled canonically but with no
chain attached, is hashed with SHA-256, and the client fetches only the shard named by the first
three hex characters: one shard per lookup, whatever chain the address might belong to. An EVM
address is the same string on six chains and a base58 string can read as Tron or Solana; all of
those readings hash to the same key, so the server learns one prefix shared with about one address
in 4,096, and nothing else. Each shard entry lists the matches per chain.
"""

from __future__ import annotations

import hashlib
import re

from goldenhour.models import EVM_CHAINS, Chain, InvalidAddress, normalize_address

PREFIX_CHARS = 3


_HEX40 = re.compile(r"^0x[0-9a-fA-F]{40}$")
_BECH32 = re.compile(r"^bc1[02-9ac-hj-np-z]{11,71}$", re.IGNORECASE)


def canonical(address: str) -> str:
    """The chain-free spelling hashed for screening: EVM and bech32 lowercased, base58 as is.
    Every chain's own normalization agrees with this on any address valid for that chain."""
    a = address.strip()
    if _HEX40.match(a) or _BECH32.match(a):
        return a.lower()
    return a


def address_key(address: str) -> str:
    return hashlib.sha256(canonical(address).encode("utf-8")).hexdigest()


def node_key(node: str) -> str:
    """The screening key of a node id `chain:address`: the key of its address alone."""
    return address_key(node.partition(":")[2])


def shard_of(key: str) -> str:
    return key[:PREFIX_CHARS]


def candidates(chain: str, address: str) -> list[str]:
    """Node ids to check for an address. `evm` (or any EVM chain name) checks every EVM chain,
    because one key controls the same address on all of them."""
    chain = chain.strip().lower()
    chains: list[Chain]
    if chain in ("evm", *{str(c) for c in EVM_CHAINS}):
        chains = sorted(EVM_CHAINS, key=str) if chain == "evm" else [Chain(chain)]
    else:
        chains = [Chain(chain)]
    out = []
    for c in chains:
        try:
            out.append(f"{c}:{normalize_address(c, address)}")
        except InvalidAddress:
            continue
    if not out:
        raise InvalidAddress(f"not a valid {chain} address: {address!r}")
    return out


def lookup(shard: dict, address: str, nodes: list[str]) -> list[dict]:
    """The entries in a shard for an address, limited to the chains being checked."""
    found = (shard.get("entries") or {}).get(address_key(address)) or []
    if isinstance(found, dict):  # a version 1 shard held one entry per key
        found = [found]
    wanted = set(nodes)
    return [e for e in found if isinstance(e, dict) and e.get("node") in wanted]
