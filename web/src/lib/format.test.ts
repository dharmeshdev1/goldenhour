import { describe, expect, it } from "vitest";
import {
  addressLabel,
  formatAmount,
  formatClock,
  formatCount,
  formatDate,
  formatElapsed,
  formatMinutes,
  formatPercent,
  formatRelative,
  formatTimelineAt,
  formatUsd,
  formatUsdFull,
  formatUsdWords,
  formatUtc,
  hasTimeOfDay,
  shortAddress,
} from "@/lib/format";

describe("formatUsd", () => {
  it("compacts to three significant digits", () => {
    expect(formatUsd(6_412_059_000)).toBe("$6.41B");
    expect(formatUsd(786_000_000)).toBe("$786M");
    expect(formatUsd(12_340_000)).toBe("$12.3M");
    expect(formatUsd(48_210)).toBe("$48.2K");
    expect(formatUsd(412)).toBe("$412");
  });
  it("drops trailing zeros", () => {
    expect(formatUsd(2_000_000_000)).toBe("$2B");
    expect(formatUsd(2_500_000_000)).toBe("$2.5B");
    expect(formatUsd(10_000_000)).toBe("$10M");
  });
  it("rounds across unit boundaries instead of printing 1000M", () => {
    expect(formatUsd(999_960_000)).toBe("$1B");
    expect(formatUsd(999_600)).toBe("$1M");
  });
  it("handles zero, tiny, negative and missing values", () => {
    expect(formatUsd(0)).toBe("$0");
    expect(formatUsd(0.4)).toBe("<$1");
    expect(formatUsd(-3_200_000)).toBe("-$3.2M");
    expect(formatUsd(Number.NaN)).toBe("n/a");
    expect(formatUsd(undefined)).toBe("n/a");
  });
  it("spells out full and worded amounts", () => {
    expect(formatUsdFull(6_412_059_000.4)).toBe("$6,412,059,000");
    expect(formatUsdWords(6_430_000_000)).toBe("$6.43 billion");
    expect(formatUsdWords(786_000_000)).toBe("$786 million");
    expect(formatUsdWords(52_000)).toBe("$52,000");
  });
});

describe("formatAmount", () => {
  it("formats token amounts by magnitude", () => {
    expect(formatAmount(233_460_000, "XRP")).toBe("233.46M XRP");
    expect(formatAmount(18_270, "BTC")).toBe("18,270 BTC");
    expect(formatAmount(2_961.9, "ETH")).toBe("2,961.9 ETH");
    expect(formatAmount(1.25)).toBe("1.25");
    expect(formatAmount(0.042137, "BTC")).toBe("0.04214 BTC");
    expect(formatAmount(1_180_000_000, "USDT")).toBe("1.18B USDT");
    expect(formatAmount(120_000_000, "USDP")).toBe("120M USDP");
  });
  it("counts and percents", () => {
    expect(formatCount(7_742)).toBe("7,742");
    expect(formatCount(Infinity)).toBe("n/a");
    expect(formatPercent(0.4371)).toBe("44%");
    expect(formatPercent(0.4371, 1)).toBe("43.7%");
  });
});

describe("dates", () => {
  it("formats UTC dates and clocks", () => {
    expect(formatDate("2026-10-29T20:05:04Z")).toBe("29 Oct 2026");
    expect(formatClock("2026-10-29T20:05:04Z")).toBe("20:05 UTC");
    expect(formatClock("2026-10-29T20:05:04Z", true)).toBe("20:05:04 UTC");
    expect(formatUtc("2026-10-30T00:10:00Z")).toBe("30 Oct 2026, 00:10 UTC");
    expect(formatUtc("not a date")).toBe("n/a");
  });
  it("keeps timeline dates at the precision they were reported with", () => {
    expect(formatTimelineAt("2026-06")).toBe("June 2026");
    expect(formatTimelineAt("2026-10-20")).toBe("20 Oct 2026");
    expect(formatTimelineAt("2026-10-29T20:05:00Z")).toBe("29 Oct 2026, 20:05 UTC");
    expect(formatTimelineAt("sometime")).toBe("sometime");
    expect(hasTimeOfDay("2026-10-29T20:05:00Z")).toBe(true);
    expect(hasTimeOfDay("2026-10-29")).toBe(false);
  });
  it("formats the T+ clock", () => {
    expect(formatElapsed(0)).toBe("00:00:00");
    expect(formatElapsed(((3 * 60 + 7) * 60 + 9) * 1000)).toBe("03:07:09");
    expect(formatElapsed((8 * 86400 + 13 * 3600 + 45 * 60 + 12) * 1000)).toBe("8d 13:45:12");
    expect(formatElapsed(-61_000)).toBe("-00:01:01");
  });
  it("formats minute spans", () => {
    expect(formatMinutes(37)).toBe("37 min");
    expect(formatMinutes(65)).toBe("1 h 05 min");
    expect(formatMinutes(240)).toBe("4 h");
    expect(formatMinutes(-71.5)).toBe("-1 h 12 min");
  });
  it("formats relative times", () => {
    const now = Date.parse("2026-11-07T09:00:00Z");
    expect(formatRelative("2026-11-07T08:59:40Z", now)).toBe("just now");
    expect(formatRelative("2026-11-07T08:56:00Z", now)).toBe("4 min ago");
    expect(formatRelative("2026-11-07T06:00:00Z", now)).toBe("3 h ago");
    expect(formatRelative("2026-11-06T09:00:00Z", now)).toBe("1 day ago");
    expect(formatRelative("2026-11-05T09:00:00Z", now)).toBe("2 days ago");
    expect(formatRelative("2026-11-07T09:05:00Z", now)).toBe("in 5 min");
  });
});

describe("addresses", () => {
  it("shortens long addresses and keeps short ones", () => {
    expect(shortAddress("0x8532fa14acaf483729c449b8458aeddb900543d4")).toBe("0x8532…43d4");
    expect(shortAddress("alice.near")).toBe("alice.near");
    expect(addressLabel("  ", "0x8532fa14acaf483729c449b8458aeddb900543d4")).toBe("0x8532…43d4");
    expect(addressLabel("FBI list #12", "0xabc")).toBe("FBI list #12");
  });
});
