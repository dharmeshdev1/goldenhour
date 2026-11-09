import { createHash } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  addressKey,
  canonicalAddress,
  candidatesFor,
  detectChains,
  entriesAt,
  keyCandidates,
  normalizeAddress,
  screenAddress,
  screenCandidates,
  sha256Hex,
  shardPrefix,
  SHARD_COUNT,
} from "@/lib/screen";
import type { ScreenEntry, ScreenShard } from "@/lib/types";

const EVM_MIXED = "0x8532FA14ACAf483729C449b8458aedDb900543D4";
const EVM_LOWER = "0x8532fa14acaf483729c449b8458aeddb900543d4";
const nodeSha = (s: string) => createHash("sha256").update(s, "utf8").digest("hex");

describe("normalizeAddress (must match goldenhour.models.normalize_address)", () => {
  it("lowercases EVM addresses and rejects malformed ones", () => {
    expect(normalizeAddress("ethereum", EVM_MIXED)).toBe(EVM_LOWER);
    expect(normalizeAddress("avalanche", `  ${EVM_MIXED}\n`)).toBe(EVM_LOWER);
    expect(normalizeAddress("ethereum", "0x8532")).toBeNull();
    expect(normalizeAddress("ethereum", `${EVM_LOWER}00`)).toBeNull();
    expect(normalizeAddress("ethereum", "0xZZ32fa14acaf483729c449b8458aeddb900543d4")).toBeNull();
  });
  it("lowercases bech32 bitcoin and keeps base58 bitcoin as is", () => {
    expect(normalizeAddress("bitcoin", "BC1QAR0SRRR7XFKVY5L643LYDNW9RE59GTZZWF5MDQ")).toBe("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq");
    expect(normalizeAddress("bitcoin", "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")).toBe("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa");
    expect(normalizeAddress("bitcoin", "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy")).toBe("3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy");
    expect(normalizeAddress("bitcoin", "bc1qb")).toBeNull();
    // 0, O, I and l are not base58
    expect(normalizeAddress("bitcoin", "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfN0")).toBeNull();
  });
  it("keeps Tron, XRPL and Solana spellings unchanged", () => {
    expect(normalizeAddress("tron", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t")).toBe("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t");
    expect(normalizeAddress("tron", "tr7nhqjekqxgtci8q8zy4pl8otszgjlj6t")).toBeNull();
    expect(normalizeAddress("xrpl", "rEb8TK3gBgk5auZkwc6sHnwrGVJH8DuaLh")).toBe("rEb8TK3gBgk5auZkwc6sHnwrGVJH8DuaLh");
    expect(normalizeAddress("solana", "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM")).toBe("9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM");
    expect(normalizeAddress("solana", "short")).toBeNull();
  });
  it("accepts NEAR named and implicit accounts", () => {
    expect(normalizeAddress("near", "relay.tg")).toBe("relay.tg");
    expect(normalizeAddress("near", "a".repeat(64))).toBe("a".repeat(64));
    expect(normalizeAddress("near", "Alice.near")).toBeNull();
  });
});

describe("detectChains and candidatesFor", () => {
  it("checks an EVM address on all six EVM chains", () => {
    const c = candidatesFor(EVM_MIXED);
    expect(c.map((x) => x.chain)).toEqual(["ethereum", "optimism", "arbitrum", "base", "polygon", "avalanche"]);
    expect(c.map((x) => x.node)).toContain(`polygon:${EVM_LOWER}`);
    expect(new Set(c.map((x) => x.address))).toEqual(new Set([EVM_LOWER]));
  });
  it("checks every reading of an ambiguous address", () => {
    // A Tron address is also valid base58 of Solana length.
    expect(detectChains("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t")).toEqual(["tron", "solana"]);
    expect(detectChains("9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM")).toEqual(["solana"]);
    expect(detectChains("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq")).toEqual(["bitcoin"]);
    expect(detectChains("relay.tg")).toEqual(["near"]);
    expect(detectChains("")).toEqual([]);
    expect(detectChains("hello world")).toEqual([]);
    expect(detectChains("aurora")).toEqual([]);
    expect(candidatesFor("aurora", "near")).toEqual([{ chain: "near", address: "aurora", node: "near:aurora" }]);
  });
  it("honors an explicit chain and refuses an address that does not fit it", () => {
    expect(candidatesFor(EVM_MIXED, "base")).toEqual([{ chain: "base", address: EVM_LOWER, node: `base:${EVM_LOWER}` }]);
    expect(candidatesFor(EVM_MIXED, "bitcoin")).toEqual([]);
  });
});

describe("hashing", () => {
  it("hashes the canonical address alone, as lowercase hex sha256", async () => {
    const key = await addressKey(EVM_MIXED);
    expect(key).toBe(nodeSha(EVM_LOWER));
    expect(key).toMatch(/^[0-9a-f]{64}$/);
    expect(await addressKey("BC1QAR0SRRR7XFKVY5L643LYDNW9RE59GTZZWF5MDQ")).toBe(nodeSha("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"));
    expect(await addressKey(" TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t ")).toBe(nodeSha("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"));
    expect(await sha256Hex("abc")).toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  });
  it("agrees with every chain's normalization on addresses valid there", () => {
    for (const input of [EVM_MIXED, "BC1QAR0SRRR7XFKVY5L643LYDNW9RE59GTZZWF5MDQ", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", "relay.tg"]) {
      for (const c of candidatesFor(input)) expect(canonicalAddress(input)).toBe(c.address);
    }
  });
  it("gives every reading of an input one key and one shard", async () => {
    for (const input of [EVM_MIXED, "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"]) {
      const keyed = await keyCandidates(candidatesFor(input));
      expect(keyed.length).toBeGreaterThan(1);
      expect(new Set(keyed.map((k) => k.key)).size).toBe(1);
    }
  });
  it("names the shard by the first three hex characters", () => {
    expect(shardPrefix("ba7816bf")).toBe("ba7");
    expect(shardPrefix("BA7816")).toBe("ba7");
    expect(() => shardPrefix("zz")).toThrow();
    expect(SHARD_COUNT).toBe(4096);
  });
});

const entry = (chain: ScreenEntry["chain"], exposure: ScreenEntry["exposure"], received_usd = 10): ScreenEntry => ({
  node: `${chain}:${EVM_LOWER}`,
  chain,
  address: EVM_LOWER,
  role: exposure === "trail" ? "seed" : "counterparty",
  category: "attacker",
  label: "FBI list #1",
  hop: 1,
  received_usd,
  held_usd: 5,
  first_ts: 1,
  reason: "named",
  exposure,
});

describe("screenCandidates", () => {
  it("checks all six EVM chains with one shard request and reports only that prefix", async () => {
    const key = nodeSha(EVM_LOWER);
    const requested: string[] = [];
    const shard: ScreenShard = { v: 2, entries: { [key]: [entry("ethereum", "trail")] } };
    const result = await screenAddress(EVM_MIXED, "auto", async (p) => {
      requested.push(p);
      return p === key.slice(0, 3) ? shard : null;
    });
    expect(requested).toEqual([key.slice(0, 3)]);
    expect(result?.prefixes).toEqual([key.slice(0, 3)]);
    expect(result?.checked).toHaveLength(6);
    expect(result?.verdict).toBe("trail");
    expect(result?.hits).toHaveLength(1);
    expect(result?.hits[0].candidate.chain).toBe("ethereum");
  });
  it("returns only the chains asked about, worst exposure first", async () => {
    const key = nodeSha(EVM_LOWER);
    const shard: ScreenShard = { v: 2, entries: { [key]: [entry("base", "direct", 99), entry("ethereum", "trail")] } };
    const load = async () => shard;
    const all = await screenAddress(EVM_MIXED, "auto", load);
    expect(all?.hits.map((h) => h.entry.chain)).toEqual(["ethereum", "base"]);
    expect(all?.verdict).toBe("trail");
    const onBase = await screenAddress(EVM_MIXED, "base", load);
    expect(onBase?.hits.map((h) => h.entry.chain)).toEqual(["base"]);
    expect(onBase?.verdict).toBe("direct");
    expect((await screenAddress(EVM_MIXED, "polygon", load))?.verdict).toBe("not_found");
  });
  it("ignores an entry whose node does not match the candidate", async () => {
    const checked = await keyCandidates(candidatesFor(EVM_MIXED, "ethereum"));
    const wrong = { v: 2, entries: { [checked[0].key]: [{ node: "ethereum:0xdead", exposure: "trail" }] } } as unknown as ScreenShard;
    const r = await screenCandidates(checked, async () => wrong);
    expect(r.verdict).toBe("not_found");
  });
  it("still reads a version 1 shard and drops malformed entries", () => {
    const key = nodeSha(EVM_LOWER);
    const v1 = { v: 1, entries: { [key]: entry("ethereum", "trail") } } as unknown as ScreenShard;
    expect(entriesAt(v1, key).map((e) => e.node)).toEqual([`ethereum:${EVM_LOWER}`]);
    const junk = { v: 2, entries: { [key]: [null, 7, { node: "x", exposure: "indirect" }, entry("base", "direct")] } } as unknown as ScreenShard;
    expect(entriesAt(junk, key).map((e) => e.chain)).toEqual(["base"]);
    expect(entriesAt({ v: 2, entries: {} }, "toString")).toEqual([]);
    expect(entriesAt(null, key)).toEqual([]);
  });
  it("returns not_found when the shard does not exist", async () => {
    const r = await screenAddress(EVM_MIXED, "auto", async () => null);
    expect(r?.verdict).toBe("not_found");
    expect(r?.checked).toHaveLength(6);
  });
  it("returns null for input that is not an address", async () => {
    expect(await screenAddress("nope", "auto", async () => null)).toBeNull();
  });
});

describe("development shards", () => {
  const dir = new URL("../../fixtures/api/v1/screen/", import.meta.url);
  const files = readdirSync(dir).filter((f) => f.endsWith(".json"));

  it("exist", () => {
    expect(files.length).toBeGreaterThan(3);
  });

  it("are keyed by sha256(address), list one entry per chain and are filed under the key's prefix", () => {
    for (const f of files) {
      const shard = JSON.parse(readFileSync(new URL(f, dir), "utf8")) as ScreenShard;
      expect(shard.v).toBe(2);
      for (const [key, list] of Object.entries(shard.entries)) {
        expect(Array.isArray(list) && list.length > 0).toBe(true);
        expect(new Set(list.map((e) => e.chain)).size).toBe(list.length);
        for (const e of list) {
          expect(key).toBe(nodeSha(canonicalAddress(e.address)));
          expect(`${key.slice(0, 3)}.json`).toBe(f);
          expect(e.node).toBe(`${e.chain}:${e.address}`);
        }
      }
    }
  });

  it("resolve through the browser lookup for every entry", async () => {
    const shards = new Map(files.map((f) => [f.slice(0, 3), JSON.parse(readFileSync(new URL(f, dir), "utf8")) as ScreenShard]));
    for (const shard of shards.values()) {
      for (const e of Object.values(shard.entries).flat()) {
        const r = await screenAddress(e.address, e.chain, async (p) => shards.get(p) ?? null);
        expect(r?.verdict).toBe(e.exposure);
        expect(r?.hits[0].entry.node).toBe(e.node);
      }
    }
  });
});
