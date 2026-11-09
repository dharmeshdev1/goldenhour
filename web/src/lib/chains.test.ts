import { describe, expect, it } from "vitest";
import { addressUrl, nodeUrl, trustedExplorerUrl, txUrl } from "@/lib/chains";

describe("explorer links", () => {
  it("builds links for every chain's explorer", () => {
    expect(txUrl("ethereum", "0xabc")).toBe("https://etherscan.io/tx/0xabc");
    expect(txUrl("optimism", "0xabc")).toBe("https://optimistic.etherscan.io/tx/0xabc");
    expect(txUrl("bitcoin", "ff00")).toBe("https://mempool.space/tx/ff00");
    expect(txUrl("tron", "ab12")).toBe("https://tronscan.org/#/transaction/ab12");
    expect(addressUrl("tron", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t")).toBe("https://tronscan.org/#/address/TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t");
    expect(addressUrl("xrpl", "rEb8TK3gBgk5auZkwc6sHnwrGVJH8DuaLh")).toBe("https://xrpscan.com/account/rEb8TK3gBgk5auZkwc6sHnwrGVJH8DuaLh");
    expect(addressUrl("near", "relay.tg")).toBe("https://nearblocks.io/address/relay.tg");
    expect(txUrl("solana", "5x")).toBe("https://solscan.io/tx/5x");
  });
  it("refuses unknown chains and unsafe identifiers", () => {
    expect(txUrl("dogecoin", "ab")).toBeNull();
    expect(txUrl("ethereum", "0xabc/../../evil")).toBeNull();
    expect(addressUrl("ethereum", "javascript:alert(1)")).toBeNull();
    expect(addressUrl("ethereum", "")).toBeNull();
  });
  it("links bitcoin transaction nodes to the transaction page", () => {
    expect(nodeUrl({ id: "bitcoin:@tx:ff00", chain: "bitcoin", address: "ff00" })).toBe("https://mempool.space/tx/ff00");
    expect(nodeUrl({ id: "ethereum:@fees", chain: "ethereum", address: "@fees" })).toBeNull();
  });
  it("only trusts https explorer URLs from the data", () => {
    expect(trustedExplorerUrl("https://etherscan.io/tx/0xabc")).toBe("https://etherscan.io/tx/0xabc");
    expect(trustedExplorerUrl("http://etherscan.io/tx/0xabc")).toBeNull();
    expect(trustedExplorerUrl("https://etherscan.io.evil.example/tx")).toBeNull();
    expect(trustedExplorerUrl("javascript:alert(1)")).toBeNull();
    expect(trustedExplorerUrl("https://user:pw@etherscan.io/")).toBeNull();
    expect(trustedExplorerUrl(42)).toBeNull();
  });
});
