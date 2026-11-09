"""Who is behind an address: a person's wallet, or a service the trail must not walk into.

A tracer that follows money into an exchange's hot wallet starts following every customer of that
exchange. So before an address can join the trail, it is identified: is it a contract, does a public
explorer tag it, and what do its tags say it is. Each chain answers that differently, and some
barely answer at all, which is why the trail also judges addresses by behavior (see `trail.py`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from goldenhour.http import PoliteClient
from goldenhour.models import Chain, split_node

LABELS_DIR = Path(__file__).resolve().parents[2] / "labels"
if not LABELS_DIR.exists():  # installed wheel
    LABELS_DIR = Path(__file__).resolve().parent / "_labels"

BLOCKSCOUT_V2 = {
    Chain.ETHEREUM: "https://eth.blockscout.com/api/v2",
    Chain.OPTIMISM: "https://optimism.blockscout.com/api/v2",
}
PUBLIC_RPC = {
    Chain.ETHEREUM: "https://ethereum-rpc.publicnode.com",
    Chain.OPTIMISM: "https://optimism-rpc.publicnode.com",
    Chain.ARBITRUM: "https://arbitrum-one-rpc.publicnode.com",
    Chain.BASE: "https://base-rpc.publicnode.com",
    Chain.POLYGON: "https://polygon-bor-rpc.publicnode.com",
    Chain.AVALANCHE: "https://avalanche-c-chain-rpc.publicnode.com",
}
SOLANA_SYSTEM_PROGRAM = "11111111111111111111111111111111"

# Words in a public tag that say what kind of service an address is. Order matters: the first
# category whose words match wins, so "Binance: Bridge" is a bridge and "THORChain Router" a bridge.
CATEGORY_WORDS: list[tuple[str, tuple[str, ...]]] = [
    (
        "mixer",
        (
            "tornado",
            "privacy cash",
            "railgun",
            "wasabi",
            "whirlpool",
            "samourai",
            "coinjoin",
            "mixer",
            "sinbad",
            "blender",
            "privacy pool",
        ),
    ),
    (
        "bridge",
        (
            "thorchain",
            "relay",
            "across",
            "stargate",
            "wormhole",
            "debridge",
            "symbiosis",
            "chainflip",
            "mayan",
            "layerzero",
            "hop protocol",
            "synapse",
            "celer",
            "multichain",
            "orbiter",
            "rhino",
            "allbridge",
            "bridge",
            "portal",
            "squid",
            "li.fi",
            "lifi",
            "socket",
            "rango",
            "butter",
            "meson",
        ),
    ),
    (
        "lending",
        (
            "aave",
            "compound",
            "spark",
            "morpho",
            "justlend",
            "maker",
            "euler",
            "fluid",
            "kamino",
            "marginfi",
            "solend",
            "venus",
            "radiant",
            "lending",
        ),
    ),
    ("staking", ("lido", "rocket pool", "eigenlayer", "jito", "marinade", "staked", "staking")),
    (
        "dex",
        (
            "uniswap",
            "sushiswap",
            "curve",
            "balancer",
            "1inch",
            "0x:",
            "0x exchange",
            "cow protocol",
            "cowswap",
            "paraswap",
            "kyberswap",
            "sunswap",
            "jupiter",
            "raydium",
            "orca",
            "pancakeswap",
            "aggregator",
            "router",
            "odos",
            "openocean",
            "dodo",
            "maverick",
            "wrapped ether",
            "weth",
            "permit2",
            "pool",
            "swap",
        ),
    ),
    ("issuer", ("tether", "circle", "paxos", "first digital", "centre")),
    (
        "exchange",
        (
            "binance",
            "coinbase",
            "kraken",
            "okx",
            "okex",
            "bybit",
            "kucoin",
            "huobi",
            "htx",
            "gate.io",
            "mexc",
            "bitget",
            "crypto.com",
            "bitfinex",
            "bitstamp",
            "gemini",
            "upbit",
            "bithumb",
            "poloniex",
            "exch",
            "fixedfloat",
            "changenow",
            "sideshift",
            "simpleswap",
            "stealthex",
            "whitebit",
            "bitmart",
            "lbank",
            "coinex",
            "bingx",
            "deribit",
            "exchange",
            "hot wallet",
            "cold wallet",
        ),
    ),
]
SERVICE_CATEGORIES = {
    "mixer",
    "bridge",
    "lending",
    "staking",
    "dex",
    "issuer",
    "exchange",
    "exchange-deposit",
    "contract",
    "program",
    "hub",
    "burn",
}


def categorize(*names: str) -> str:
    text = " ".join(n.lower() for n in names if n)
    if not text.strip():
        return ""
    for category, words in CATEGORY_WORDS:
        for w in words:
            if re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", text):
                return category
    return ""


@dataclass(slots=True)
class Identity:
    node: str
    is_contract: bool | None = None
    delegated: bool = False  # an EOA running EIP-7702 code is still a person's wallet
    name: str = ""
    tags: list[str] = field(default_factory=list)
    category: str = ""
    entity: str = ""
    source: str = ""

    @property
    def available(self) -> bool:
        """False when no source answered: no verdict should rest on this identity."""
        return not self.source.startswith("unavailable")

    @property
    def is_service(self) -> bool:
        if self.category in SERVICE_CATEGORIES:
            return True
        return bool(self.is_contract and not self.delegated)

    def as_dict(self) -> dict[str, object]:
        return {
            "is_contract": self.is_contract,
            "delegated": self.delegated,
            "name": self.name,
            "tags": self.tags,
            "category": self.category,
            "entity": self.entity,
            "source": self.source,
        }


BURN_ADDRESSES = frozenset(
    {
        "0x0000000000000000000000000000000000000000",
        "0x000000000000000000000000000000000000dead",
        "0xdead000000000000000042069420694206942069",
    }
)


class Labels:
    """Curated labels, one YAML file per chain: `address: {label, category, entity}`."""

    def __init__(self, directory: Path = LABELS_DIR) -> None:
        self.by_node: dict[str, dict[str, str]] = {}
        for f in sorted(directory.glob("*.yaml")):
            chain = Chain(f.stem)
            for address, entry in (yaml.safe_load(f.read_text()) or {}).items():
                key = f"{chain}:{str(address).lower() if chain.is_evm else str(address)}"
                self.by_node[key] = {k: str(v) for k, v in (entry or {}).items()}

    def get(self, node: str) -> dict[str, str] | None:
        return self.by_node.get(node)


class Identifier:
    def __init__(self, client: PoliteClient, labels: Labels | None = None) -> None:
        self.client = client
        self.labels = labels or Labels()

    async def identify(self, node: str) -> Identity:
        chain, address = split_node(node)
        ident = Identity(node)
        if chain.is_evm and address in BURN_ADDRESSES:
            # Tokens sent here are destroyed. Nobody holds the key, and its history is every burn
            # on the chain, so it is never read: it is where the walk stops.
            ident.name, ident.category, ident.entity, ident.source = "Burn address", "burn", "Burn", "curated"
            return ident
        curated = self.labels.get(node)
        try:
            if chain in BLOCKSCOUT_V2:
                try:
                    await self._blockscout(ident, chain, address)
                except Exception:
                    # Blockscout throttles in bursts; a node can still say whether this is a contract.
                    await self._evm_code(ident, chain, address)
            elif chain.is_evm:
                await self._evm_code(ident, chain, address)
            elif chain is Chain.SOLANA:
                await self._solana(ident, address)
            elif chain is Chain.TRON:
                await self._tron(ident, address)
            elif chain is Chain.XRPL:
                await self._xrpl(ident, address)
        except Exception as exc:
            ident.source = f"unavailable: {type(exc).__name__}"
        if curated:
            ident.name = curated.get("label", ident.name)
            ident.category = curated.get("category", ident.category)
            ident.entity = curated.get("entity", ident.entity)
            ident.source = "curated"
        if not ident.category:
            ident.category = categorize(ident.name, *ident.tags)
        if not ident.entity and ident.category in ("exchange", "bridge", "mixer", "dex", "lending", "issuer"):
            ident.entity = (ident.tags[0] if ident.tags else ident.name).split(":")[0].strip()[:60]
        return ident

    async def scale(self, node: str) -> int | None:
        """How much history an address has, cheaply: its nonce on EVM chains (transactions it sent),
        its transaction count on Bitcoin. None when unknown (the XRP Ledger, Tron, Solana).

        Exchange hot wallets have millions; a fresh laundering address has a handful. It is the
        signal that recognizes an unlabeled exchange wallet without reading its whole history.
        """
        chain, address = split_node(node)
        try:
            if chain.is_evm and chain in PUBLIC_RPC:
                data = await self.client.post_json(
                    PUBLIC_RPC[chain],
                    {"jsonrpc": "2.0", "id": 1, "method": "eth_getTransactionCount", "params": [address, "latest"]},
                )
                result = (data or {}).get("result") if isinstance(data, dict) else None
                return int(result, 16) if isinstance(result, str) else None
            if chain is Chain.BITCOIN:
                data = await self.client.get_json(f"https://mempool.space/api/address/{address}")
                return (
                    int(((data or {}).get("chain_stats") or {}).get("tx_count", 0)) if isinstance(data, dict) else None
                )
            # The XRP Ledger has no cheap count: an account's Sequence starts at the ledger number it
            # was created in (over 100 million today), so it says nothing about activity.
        except Exception:
            return None
        return None

    async def _blockscout(self, ident: Identity, chain: Chain, address: str) -> None:
        data = await self.client.get_json(f"{BLOCKSCOUT_V2[chain]}/addresses/{address}")
        if not isinstance(data, dict):
            return
        ident.source = "blockscout"
        ident.is_contract = bool(data.get("is_contract"))
        proxy = data.get("proxy_type") or ""
        ident.delegated = proxy == "eip7702" or bool(data.get("eip7702_delegate") or data.get("is_eip7702"))
        if ident.delegated:
            ident.is_contract = False
        ident.name = str(data.get("name") or "")[:120]
        meta = data.get("metadata") or {}
        ident.tags = [str(t.get("name", ""))[:80] for t in meta.get("tags") or [] if t.get("name")][:8]
        token = data.get("token")
        if isinstance(token, dict) and token.get("symbol"):
            ident.tags.append(f"token {str(token.get('symbol'))[:20]}")

    async def _evm_code(self, ident: Identity, chain: Chain, address: str) -> None:
        data = await self.client.post_json(
            PUBLIC_RPC[chain], {"jsonrpc": "2.0", "id": 1, "method": "eth_getCode", "params": [address, "latest"]}
        )
        code = (data or {}).get("result", "0x") if isinstance(data, dict) else "0x"
        ident.source = "rpc"
        ident.delegated = isinstance(code, str) and code.lower().startswith("0xef0100")
        ident.is_contract = code not in ("0x", "0x0", "") and not ident.delegated

    async def _solana(self, ident: Identity, address: str) -> None:
        data = await self.client.post_json(
            "https://api.mainnet-beta.solana.com",
            {"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo", "params": [address, {"encoding": "base64"}]},
        )
        value = ((data or {}).get("result") or {}).get("value") if isinstance(data, dict) else None
        ident.source = "rpc"
        if not value:
            ident.is_contract = False
            return
        owner = value.get("owner", "")
        # Wallets are owned by the system program. Program accounts, pool vaults and PDAs are not.
        ident.is_contract = bool(value.get("executable")) or owner != SOLANA_SYSTEM_PROGRAM
        if ident.is_contract:
            ident.category = "program"

    async def _tron(self, ident: Identity, address: str) -> None:
        resp = await self.client.request(
            "POST", "https://api.trongrid.io/wallet/getcontract", json={"value": address, "visible": True}
        )
        data = resp.json() if resp.status_code == 200 else {}
        ident.source = "trongrid"
        ident.is_contract = bool(data and data.get("bytecode"))
        if ident.is_contract:
            ident.name = str(data.get("name") or "")[:120]

    async def _xrpl(self, ident: Identity, address: str) -> None:
        resp = await self.client.request("GET", f"https://api.xrpscan.com/api/v1/account/{address}")
        ident.source = "xrpscan"
        ident.is_contract = False
        if resp.status_code != 200:
            return
        data = resp.json()
        name = (data or {}).get("accountName") or {}
        if isinstance(name, dict) and name.get("name"):
            ident.name = str(name.get("name"))[:120]
            ident.tags = [str(name.get("desc") or "")[:80]] if name.get("desc") else []
