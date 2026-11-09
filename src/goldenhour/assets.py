"""Which assets have a dollar value in Goldenhour's accounting.

Attackers' addresses are showered with airdropped tokens: worthless lookalikes named "ETH" or
"USDT", and tokens with a thin pool that a price feed will happily quote. Counting those would
inflate what the attacker "holds" by any amount a spammer likes. So a token is valued only if it is
a well-known asset listed here by contract address, or if the victim's own wallets sent it during
the theft (Binance held it, so it is real, and it was stolen). Every chain's native coin counts.
"""

from __future__ import annotations

from goldenhour.models import Chain, token_asset

MAJORS: dict[Chain, dict[str, str]] = {
    Chain.ETHEREUM: {
        "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT",
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC",
        "0x6b175474e89094c44da98b954eedeac495271d0f": "DAI",
        "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": "WETH",
        "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": "WBTC",
        "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf": "cbBTC",
        "0xae7ab96520de3a18e5e111b5eaab095312d7fe84": "stETH",
        "0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0": "wstETH",
        "0xc5f0f7b66764f6ec8c8dff7ba683102295e16409": "FDUSD",
        "0x6c3ea9036406852006290770bedfcaba0e23a0e8": "PYUSD",
        "0x4c9edd5852cd905f086c759e8383e09bff1e68b3": "USDe",
        "0x514910771af9ca656af840dff83e8264ecf986ca": "LINK",
        "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984": "UNI",
        "0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9": "AAVE",
        "0x95ad61b0a150d79219dcf64e1e6cc01f0b64c4ce": "SHIB",
        "0x6982508145454ce325ddbe47a25d4ec3d2311933": "PEPE",
        "0xb8c77482e45f1f44de1745f52c74426c631bdd52": "BNB",
        # Aave v3 receipts: a deposit of stolen USDT or WETH becomes these.
        "0x23878914efe38d27c4d67ab83ed1b93a74d4086a": "aEthUSDT",
        "0x4d5f47fa6a74757f35c14fd3a6ef8e3c9bc514e8": "aEthWETH",
        "0x98c23e9d8f34fefb1b7bd6a91b7ff122f4e16f5c": "aEthUSDC",
    },
    Chain.ARBITRUM: {
        "0xaf88d065e77c8cc2239327c5edb3a432268e5831": "USDC",
        "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9": "USDT",
        "0x82af49447d8a07e3bd95bd0d56f35241523fbab1": "WETH",
        "0x912ce59144191c1204e64559fe8253a0e49e6548": "ARB",
    },
    Chain.BASE: {
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": "USDC",
        "0x4200000000000000000000000000000000000006": "WETH",
    },
    Chain.OPTIMISM: {
        "0x0b2c639c533813f4aa9d7837caf62653d097ff85": "USDC",
        "0x94b008aa00579c1307b0ef2c499ad98a8ce58e58": "USDT",
        "0x4200000000000000000000000000000000000006": "WETH",
        "0x4200000000000000000000000000000000000042": "OP",
    },
    Chain.POLYGON: {
        "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359": "USDC",
        "0xc2132d05d31c914a87c6611c10748aeb04b58e8f": "USDT",
        "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619": "WETH",
    },
    Chain.AVALANCHE: {
        "0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e": "USDC",
        "0x9702230a8ea53601f5cd2dc00fdbc13d4df4a8c7": "USDT",
        "0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7": "WAVAX",
    },
    Chain.TRON: {
        "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t": "USDT",
        "TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8": "USDC",
        "TPYmHEhy5n8TCEfYGqW2rPxsghSfzghPDn": "USDD",
        "TNUC9Qb1rRpS5CbWLmNMxXBjyFoydXjWFR": "WTRX",
    },
    Chain.SOLANA: {
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT",
        "So11111111111111111111111111111111111111112": "WSOL",
        "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn": "JitoSOL",
        "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": "mSOL",
    },
}

MAJOR_ASSETS: frozenset[str] = frozenset(
    token_asset(chain, contract) for chain, tokens in MAJORS.items() for contract in tokens
)


def is_native(asset: str) -> bool:
    return asset.endswith(":native")


def valued(asset: str, stolen: set[str] | frozenset[str] = frozenset()) -> bool:
    """Whether an asset counts in dollar terms: native, a listed major, or stolen from the victim."""
    return is_native(asset) or asset in MAJOR_ASSETS or asset in stolen
