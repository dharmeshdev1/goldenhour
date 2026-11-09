/**
 * Formatting for every number, amount, address and time the site shows. All dates are UTC: a
 * theft that crosses midnight is easier to follow on one clock.
 */

const MISSING = "n/a";

const int = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

function finite(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

/** Three significant digits, without trailing zeros: 6.41, 786, 12.3, 1.2. */
function sig3(v: number): string {
  const abs = Math.abs(v);
  const digits = abs >= 100 ? 0 : abs >= 10 ? 1 : 2;
  return v.toFixed(digits).replace(/\.0+$|(\.\d*?)0+$/, "$1");
}

/** Compact dollars: $6.41B, $786M, $48.2K, $412. */
export function formatUsd(value: unknown): string {
  if (!finite(value)) return MISSING;
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  if (abs >= 1e12) return `${sign}$${sig3(abs / 1e12)}T`;
  // Round first so that 999.95M reads as $1B instead of $1000M.
  if (abs >= 999.5e6) return `${sign}$${sig3(abs / 1e9)}B`;
  if (abs >= 999.5e3) return `${sign}$${sig3(abs / 1e6)}M`;
  if (abs >= 1e3) return `${sign}$${sig3(abs / 1e3)}K`;
  if (abs >= 1) return `${sign}$${int.format(abs)}`;
  if (abs === 0) return "$0";
  return `${sign}<$1`;
}

/** Whole dollars with separators: $6,412,059,000. */
export function formatUsdFull(value: unknown): string {
  if (!finite(value)) return MISSING;
  return `${value < 0 ? "-" : ""}$${int.format(Math.abs(Math.round(value)))}`;
}

/** Dollars in words for narrative copy: "$6.41 billion", "$786 million". */
export function formatUsdWords(value: unknown): string {
  if (!finite(value)) return MISSING;
  const abs = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (abs >= 999.5e6) return `${sign}$${sig3(abs / 1e9)} billion`;
  if (abs >= 999.5e3) return `${sign}$${sig3(abs / 1e6)} million`;
  return formatUsdFull(value);
}

export function formatCount(value: unknown): string {
  if (!finite(value)) return MISSING;
  return int.format(Math.round(value));
}

/** Token amounts: 233.46M XRP, 18,270 BTC, 2,961.9 ETH, 0.04213 BTC. */
export function formatAmount(amount: unknown, symbol?: string): string {
  if (!finite(amount)) return MISSING;
  const abs = Math.abs(amount);
  let text: string;
  if (abs >= 1e9) text = `${sig3(amount / 1e9)}B`;
  else if (abs >= 1e6) text = `${(amount / 1e6).toFixed(2).replace(/\.?0+$/, "")}M`;
  else if (abs >= 1000) text = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 }).format(amount);
  else if (abs >= 1) text = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(amount);
  else if (abs === 0) text = "0";
  else text = new Intl.NumberFormat("en-US", { maximumSignificantDigits: 4 }).format(amount);
  return symbol ? `${text} ${symbol}` : text;
}

export function formatPercent(share: unknown, digits = 0): string {
  if (!finite(share)) return MISSING;
  return `${(share * 100).toFixed(digits)}%`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTHS_LONG = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

const pad = (n: number) => String(n).padStart(2, "0");

export function parseTime(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string" || !value) return null;
  const t = Date.parse(value);
  return Number.isFinite(t) ? t : null;
}

/** "29 Oct 2026" */
export function formatDate(value: unknown): string {
  const t = parseTime(value);
  if (t === null) return MISSING;
  const d = new Date(t);
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

/** "20:05 UTC", or with seconds "20:05:04 UTC" */
export function formatClock(value: unknown, seconds = false): string {
  const t = parseTime(value);
  if (t === null) return MISSING;
  const d = new Date(t);
  const hm = `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
  return `${seconds ? `${hm}:${pad(d.getUTCSeconds())}` : hm} UTC`;
}

/** "29 Oct 2026, 20:05 UTC" */
export function formatUtc(value: unknown, seconds = false): string {
  const t = parseTime(value);
  if (t === null) return MISSING;
  return `${formatDate(t)}, ${formatClock(t, seconds)}`;
}

/**
 * Timeline dates arrive at three precisions: "2026-06" (a month), "2026-10-29" (a day) or a full
 * ISO timestamp. Each is shown at the precision it was reported with, never more.
 */
export function formatTimelineAt(at: string): string {
  const month = /^(\d{4})-(\d{2})$/.exec(at);
  if (month) {
    const m = Number(month[2]);
    return m >= 1 && m <= 12 ? `${MONTHS_LONG[m - 1]} ${month[1]}` : at;
  }
  const day = /^(\d{4})-(\d{2})-(\d{2})$/.exec(at);
  if (day) {
    const m = Number(day[2]);
    return m >= 1 && m <= 12 ? `${Number(day[3])} ${MONTHS[m - 1]} ${day[1]}` : at;
  }
  const t = parseTime(at);
  return t === null ? at : formatUtc(t);
}

/** True when a timeline date carries a time of day. */
export function hasTimeOfDay(at: string): boolean {
  return /T\d{2}:\d{2}/.test(at);
}

/** Elapsed time for the T+ clock: "8d 13:45:12", or "13:45:12" under a day. */
export function formatElapsed(ms: number): string {
  if (!Number.isFinite(ms)) return MISSING;
  const neg = ms < 0;
  let s = Math.floor(Math.abs(ms) / 1000);
  const days = Math.floor(s / 86400);
  s -= days * 86400;
  const h = Math.floor(s / 3600);
  s -= h * 3600;
  const m = Math.floor(s / 60);
  s -= m * 60;
  const clock = `${pad(h)}:${pad(m)}:${pad(s)}`;
  return `${neg ? "-" : ""}${days > 0 ? `${days}d ` : ""}${clock}`;
}

/** Minutes as a readable span: "45 min", "1 h 05 min", "4 h". */
export function formatMinutes(minutes: unknown): string {
  if (!finite(minutes)) return MISSING;
  const neg = minutes < 0;
  const total = Math.round(Math.abs(minutes));
  const h = Math.floor(total / 60);
  const m = total % 60;
  let text: string;
  if (h === 0) text = `${m} min`;
  else if (m === 0) text = `${h} h`;
  else text = `${h} h ${pad(m)} min`;
  return neg ? `-${text}` : text;
}

/** "just now", "4 min ago", "3 h ago", "2 days ago". */
export function formatRelative(value: unknown, now: number = Date.now()): string {
  const t = parseTime(value);
  if (t === null) return MISSING;
  const diff = now - t;
  const future = diff < 0;
  const s = Math.abs(diff) / 1000;
  let text: string;
  if (s < 45) return "just now";
  if (s < 3600) text = `${Math.max(1, Math.round(s / 60))} min`;
  else if (s < 86400) text = `${Math.round(s / 3600)} h`;
  else {
    const d = Math.round(s / 86400);
    text = `${d} ${d === 1 ? "day" : "days"}`;
  }
  return future ? `in ${text}` : `${text} ago`;
}

/** 0x8532fa…0543d4: enough of each end to compare by eye. */
export function shortAddress(address: string, head = 6, tail = 4): string {
  if (typeof address !== "string") return "";
  if (address.length <= head + tail + 1) return address;
  return `${address.slice(0, head)}…${address.slice(-tail)}`;
}

/** A label for an address: its own label when it has one, the short address otherwise. */
export function addressLabel(label: string | null | undefined, address: string): string {
  const l = typeof label === "string" ? label.trim() : "";
  return l || shortAddress(address);
}
