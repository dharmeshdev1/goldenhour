import type { Chain } from "@/lib/types";

/** Every chain the pipeline follows, in the order the site lists them. */
export const CHAINS: readonly Chain[] = [
  "ethereum",
  "bitcoin",
  "xrpl",
  "tron",
  "solana",
  "base",
  "arbitrum",
  "optimism",
  "polygon",
  "avalanche",
  "near",
];

export const EVM_CHAINS: readonly Chain[] = ["ethereum", "optimism", "arbitrum", "base", "polygon", "avalanche"];

export function isChain(value: unknown): value is Chain {
  return typeof value === "string" && (CHAINS as readonly string[]).includes(value);
}

export function isEvm(chain: Chain): boolean {
  return EVM_CHAINS.includes(chain);
}

interface ChainInfo {
  name: string;
  /** A muted brand hue, used only for small chain markers and the "color by chain" graph mode. */
  color: string;
  native: string;
  explorer: { origin: string; tx: (hash: string) => string; address: (address: string) => string };
}

function std(origin: string): ChainInfo["explorer"] {
  return { origin, tx: (h) => `${origin}/tx/${h}`, address: (a) => `${origin}/address/${a}` };
}

export const CHAIN_INFO: Record<Chain, ChainInfo> = {
  ethereum: { name: "Ethereum", color: "#8f97ea", native: "ETH", explorer: std("https://etherscan.io") },
  optimism: { name: "Optimism", color: "#ff7b72", native: "ETH", explorer: std("https://optimistic.etherscan.io") },
  arbitrum: { name: "Arbitrum", color: "#5fa8e8", native: "ETH", explorer: std("https://arbiscan.io") },
  base: { name: "Base", color: "#5b84f5", native: "ETH", explorer: std("https://basescan.org") },
  polygon: { name: "Polygon", color: "#a07cf0", native: "POL", explorer: std("https://polygonscan.com") },
  avalanche: { name: "Avalanche", color: "#ec6a6c", native: "AVAX", explorer: std("https://snowtrace.io") },
  bitcoin: { name: "Bitcoin", color: "#f5a04a", native: "BTC", explorer: std("https://mempool.space") },
  tron: {
    name: "Tron",
    color: "#e86a6a",
    native: "TRX",
    explorer: {
      origin: "https://tronscan.org",
      tx: (h) => `https://tronscan.org/#/transaction/${h}`,
      address: (a) => `https://tronscan.org/#/address/${a}`,
    },
  },
  xrpl: {
    name: "XRP Ledger",
    color: "#b4bcc6",
    native: "XRP",
    explorer: {
      origin: "https://xrpscan.com",
      tx: (h) => `https://xrpscan.com/tx/${h}`,
      address: (a) => `https://xrpscan.com/account/${a}`,
    },
  },
  solana: {
    name: "Solana",
    color: "#4fd6a5",
    native: "SOL",
    explorer: {
      origin: "https://solscan.io",
      tx: (h) => `https://solscan.io/tx/${h}`,
      address: (a) => `https://solscan.io/account/${a}`,
    },
  },
  near: {
    name: "NEAR",
    color: "#d8d2c4",
    native: "NEAR",
    explorer: {
      origin: "https://nearblocks.io",
      tx: (h) => `https://nearblocks.io/txns/${h}`,
      address: (a) => `https://nearblocks.io/address/${a}`,
    },
  },
};

export function chainName(chain: string): string {
  return isChain(chain) ? CHAIN_INFO[chain].name : chain;
}

export function chainColor(chain: string): string {
  return isChain(chain) ? CHAIN_INFO[chain].color : "#8d8478";
}

// Hashes and addresses only ever contain these characters on the chains we follow. Anything else
// never reaches a URL.
const SAFE_ID = /^[A-Za-z0-9._-]{1,128}$/;

/** A link to a transaction on the chain's public explorer, or null if the hash looks wrong. */
export function txUrl(chain: string, hash: string): string | null {
  if (!isChain(chain) || !SAFE_ID.test(hash)) return null;
  return CHAIN_INFO[chain].explorer.tx(encodeURIComponent(hash));
}

/** A link to an address on the chain's public explorer, or null if the address looks wrong. */
export function addressUrl(chain: string, address: string): string | null {
  if (!isChain(chain) || !SAFE_ID.test(address)) return null;
  return CHAIN_INFO[chain].explorer.address(encodeURIComponent(address));
}

const EXPLORER_HOSTS = new Set(
  Object.values(CHAIN_INFO).map((c) => new URL(c.explorer.origin).host),
);

/**
 * An explorer URL that arrived in the data, accepted only when it is https and points at one of
 * the explorers above. Anything else is dropped (the caller falls back to building its own).
 */
export function trustedExplorerUrl(raw: unknown): string | null {
  if (typeof raw !== "string" || raw.length > 512) return null;
  try {
    const url = new URL(raw);
    if (url.protocol !== "https:" || url.username || url.password) return null;
    return EXPLORER_HOSTS.has(url.host) ? url.toString() : null;
  } catch {
    return null;
  }
}

/** Bitcoin transaction nodes have ids like `bitcoin:@tx:<txid>`; their "address" is the txid. */
export function isTxNode(id: string): boolean {
  return id.includes(":@tx:");
}

/** The best explorer link for a trail node: the transaction page for transaction nodes. */
export function nodeUrl(node: { id: string; chain: string; address: string }): string | null {
  if (isTxNode(node.id)) return txUrl(node.chain, node.address);
  if (node.id.includes(":@")) return null;
  return addressUrl(node.chain, node.address);
}
