import { EVM_CHAINS } from "@/lib/chains";
import type { Chain, Exposure, ScreenEntry, ScreenShard } from "@/lib/types";

/**
 * Private address screening, the same way the Have I Been Pwned range API checks passwords.
 *
 * The browser hashes the address alone, spelled canonically and with no chain attached, and asks
 * the server only for the shard named after the first three hex characters of the hash. An EVM
 * address is the same string on six chains and a base58 string can read as Tron or Solana; every
 * reading hashes to the same key, so a lookup is one request and the server learns one prefix,
 * shared with about 1 in 4,096 of all addresses. The shard lists the matches per chain, and the
 * chains are matched here, in the browser.
 *
 * The normalization below must match goldenhour.models.normalize_address and the canonical
 * spelling must match goldenhour.screen.canonical, or a lookup would hash a different string than
 * the pipeline did and miss.
 */

const EVM = /^0x[0-9a-fA-F]{40}$/;
const BTC_BECH32 = /^(bc1)[02-9ac-hj-np-z]{11,71}$/i;
const BTC_BASE58 = /^[13][1-9A-HJ-NP-Za-km-z]{25,34}$/;
const TRON = /^T[1-9A-HJ-NP-Za-km-z]{33}$/;
const XRPL = /^r[1-9A-HJ-NP-Za-km-z]{24,34}$/;
const SOLANA = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;
const NEAR = /^(([a-z\d]+[-_])*[a-z\d]+\.)*([a-z\d]+[-_])*[a-z\d]+$|^[0-9a-f]{64}$/;

export const SHARD_PREFIX_LENGTH = 3;
/** How many distinct shards exist: 16^3. */
export const SHARD_COUNT = 16 ** SHARD_PREFIX_LENGTH;

/** The canonical spelling of an address on a chain, or null if it is not a valid address there. */
export function normalizeAddress(chain: Chain, input: string): string | null {
  if (typeof input !== "string") return null;
  const a = input.trim();
  if ((EVM_CHAINS as readonly string[]).includes(chain)) return EVM.test(a) ? a.toLowerCase() : null;
  switch (chain) {
    case "bitcoin":
      if (BTC_BECH32.test(a)) return a.toLowerCase();
      return BTC_BASE58.test(a) ? a : null;
    case "tron":
      return TRON.test(a) ? a : null;
    case "xrpl":
      return XRPL.test(a) ? a : null;
    case "solana":
      return SOLANA.test(a) ? a : null;
    case "near":
      return NEAR.test(a) ? a : null;
    default:
      return null;
  }
}

export function nodeId(chain: Chain, normalizedAddress: string): string {
  return `${chain}:${normalizedAddress}`;
}

export interface Candidate {
  chain: Chain;
  address: string;
  node: string;
}

/**
 * Every chain an address could belong to. Formats overlap (a Tron address is also valid base58
 * for Solana, and an EVM address is the same on six chains), so the lookup checks each reading
 * rather than guessing one. NEAR names are so permissive that auto-detection only considers
 * dotted names (alice.near) and 64 character implicit accounts; a top-level NEAR name such as
 * "aurora" is checked when NEAR is chosen explicitly.
 */
export function detectChains(input: string): Chain[] {
  const a = typeof input === "string" ? input.trim() : "";
  if (!a) return [];
  const out: Chain[] = [];
  if (EVM.test(a)) out.push(...EVM_CHAINS);
  if (BTC_BECH32.test(a) || BTC_BASE58.test(a)) out.push("bitcoin");
  if (TRON.test(a)) out.push("tron");
  if (XRPL.test(a)) out.push("xrpl");
  if (SOLANA.test(a)) out.push("solana");
  const nearShaped = a.includes(".") || /^[0-9a-f]{64}$/.test(a);
  if (nearShaped && NEAR.test(a)) out.push("near");
  return out;
}

/** The node ids to check for an input, on one chain or on every chain it could belong to. */
export function candidatesFor(input: string, chain: Chain | "auto" = "auto"): Candidate[] {
  const chains = chain === "auto" ? detectChains(input) : [chain];
  const seen = new Set<string>();
  const out: Candidate[] = [];
  for (const c of chains) {
    const address = normalizeAddress(c, input);
    if (address === null) continue;
    const node = nodeId(c, address);
    if (seen.has(node)) continue;
    seen.add(node);
    out.push({ chain: c, address, node });
  }
  return out;
}

/**
 * The chain-free spelling that is hashed: EVM and bech32 lowercased, everything else as written.
 * On any address valid for a chain it agrees with that chain's own normalization.
 */
export function canonicalAddress(input: string): string {
  const a = input.trim();
  return EVM.test(a) || BTC_BECH32.test(a) ? a.toLowerCase() : a;
}

/** Lowercase hex SHA-256 of the UTF-8 bytes of a string, computed locally with WebCrypto. */
export async function sha256Hex(text: string): Promise<string> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) throw new Error("This browser cannot compute SHA-256 (WebCrypto needs a secure context).");
  const digest = await subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

/** The shard a key lives in: its first three hex characters. */
export function shardPrefix(key: string): string {
  const prefix = key.slice(0, SHARD_PREFIX_LENGTH).toLowerCase();
  if (!/^[0-9a-f]{3}$/.test(prefix)) throw new Error(`not a sha256 hex key: ${key}`);
  return prefix;
}

export interface KeyedCandidate extends Candidate {
  key: string;
  prefix: string;
}

/** The screening key of an address: SHA-256 of its canonical spelling, with no chain. */
export async function addressKey(address: string): Promise<string> {
  return sha256Hex(canonicalAddress(address));
}

/** Key each candidate. Every reading of one input has the same address, so it hashes once. */
export async function keyCandidates(candidates: Candidate[]): Promise<KeyedCandidate[]> {
  const keys = new Map<string, string>();
  for (const c of candidates) {
    const spelled = canonicalAddress(c.address);
    if (!keys.has(spelled)) keys.set(spelled, await sha256Hex(spelled));
  }
  return candidates.map((c) => {
    const key = keys.get(canonicalAddress(c.address)) as string;
    return { ...c, key, prefix: shardPrefix(key) };
  });
}

export type Verdict = Exposure | "not_found";

export interface ScreenHit {
  candidate: KeyedCandidate;
  entry: ScreenEntry;
}

export interface ScreenResult {
  verdict: Verdict;
  hits: ScreenHit[];
  checked: KeyedCandidate[];
  /** The shard names the server saw, which is everything it learns about the lookup. */
  prefixes: string[];
}

const RANK: Record<Exposure, number> = { trail: 0, direct: 1 };

function isEntry(v: unknown): v is ScreenEntry {
  if (!v || typeof v !== "object") return false;
  const e = v as Partial<ScreenEntry>;
  return typeof e.node === "string" && typeof e.exposure === "string" && Object.hasOwn(RANK, e.exposure);
}

/**
 * The entries a shard holds under a key: a list with one entry per chain the address appears on.
 * A version 1 shard held a single entry per key and is still read. Anything malformed is dropped.
 */
export function entriesAt(shard: ScreenShard | null | undefined, key: string): ScreenEntry[] {
  const entries = shard?.entries;
  if (!entries || typeof entries !== "object" || !Object.hasOwn(entries, key)) return [];
  const found: unknown = (entries as Record<string, unknown>)[key];
  return (Array.isArray(found) ? found : [found]).filter(isEntry);
}

/** Look a set of keyed candidates up in their shards. `loadShard` returns null for a missing shard. */
export async function screenCandidates(
  checked: KeyedCandidate[],
  loadShard: (prefix: string) => Promise<ScreenShard | null>,
): Promise<ScreenResult> {
  const prefixes = Array.from(new Set(checked.map((c) => c.prefix)));
  const shards = new Map<string, ScreenShard | null>();
  await Promise.all(
    prefixes.map(async (p) => {
      shards.set(p, await loadShard(p));
    }),
  );
  const hits: ScreenHit[] = [];
  for (const c of checked) {
    // The key covers every chain; only the entry for this candidate's node is a match.
    const entry = entriesAt(shards.get(c.prefix), c.key).find((e) => e.node === c.node);
    if (entry) hits.push({ candidate: c, entry });
  }
  hits.sort((a, b) => RANK[a.entry.exposure] - RANK[b.entry.exposure] || b.entry.received_usd - a.entry.received_usd);
  const verdict: Verdict = hits.length ? hits[0].entry.exposure : "not_found";
  return { verdict, hits, checked, prefixes };
}

/** Normalize, hash and look up one input. */
export async function screenAddress(
  input: string,
  chain: Chain | "auto",
  loadShard: (prefix: string) => Promise<ScreenShard | null>,
): Promise<ScreenResult | null> {
  const candidates = candidatesFor(input, chain);
  if (!candidates.length) return null;
  return screenCandidates(await keyCandidates(candidates), loadShard);
}
