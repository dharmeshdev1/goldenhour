"use client";

import { useEffect, useId, useMemo, useState, useSyncExternalStore } from "react";
import { ChainChip } from "@/components/ui/chain-chip";
import { CodeTabs } from "@/components/ui/code-block";
import { ExternalIcon } from "@/components/ui/external";
import { SectionHeader } from "@/components/ui/section";
import { API_BASE, loadShard } from "@/lib/api";
import { addressUrl, CHAIN_INFO, CHAINS, chainName } from "@/lib/chains";
import { addressLabel, formatCount, formatUsd, formatUtc } from "@/lib/format";
import { canonicalAddress, candidatesFor, keyCandidates, screenCandidates, SHARD_COUNT, type KeyedCandidate, type ScreenResult } from "@/lib/screen";
import type { Chain } from "@/lib/types";
import { useGraph, useSummary } from "@/lib/use-api";

type Status = { kind: "idle" } | { kind: "busy" } | { kind: "done"; result: ScreenResult } | { kind: "error"; message: string };

function usePreview(input: string, chain: Chain | "auto") {
  const [keyed, setKeyed] = useState<KeyedCandidate[]>([]);
  useEffect(() => {
    let cancelled = false;
    const candidates = candidatesFor(input, chain);
    const t = setTimeout(async () => {
      try {
        const k = candidates.length ? await keyCandidates(candidates) : [];
        if (!cancelled) setKeyed(k);
      } catch {
        if (!cancelled) setKeyed([]);
      }
    }, 120);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [input, chain]);
  return keyed;
}

function Examples({ onPick }: { onPick: (address: string, chain: Chain) => void }) {
  const graph = useGraph();
  const picks = useMemo(() => {
    const nodes = graph.data?.nodes ?? [];
    const top = (role: string, by: "held_usd" | "received_usd") => [...nodes].filter((n) => n.role === role).sort((a, b) => b[by] - a[by])[0];
    return [
      { label: "An address on the trail", node: top("trail", "held_usd") },
      { label: "A named seed", node: top("seed", "held_usd") },
      { label: "A counterparty", node: top("counterparty", "received_usd") },
    ].filter((p) => p.node);
  }, [graph.data]);
  if (!graph.data) return <span className="text-[12.5px] text-ink-3">{graph.error ? "Examples are unavailable." : "Loading examples"}</span>;
  return (
    <>
      {picks.map((p) => (
        <button key={p.label} type="button" className="chip h-7 cursor-pointer hover:text-ink" onClick={() => onPick(p.node.address, p.node.chain)}>
          {p.label}
        </button>
      ))}
    </>
  );
}

function ResultCard({ status, dataThrough }: { status: Status; dataThrough: string | null }) {
  if (status.kind === "idle") return null;
  if (status.kind === "busy")
    return (
      <div className="card p-6" role="status">
        <p className="text-ink-2">Checking</p>
      </div>
    );
  if (status.kind === "error")
    return (
      <div className="card border-[rgb(240_84_79/0.35)] p-6" role="alert">
        <p className="text-[16px] text-ink">The check could not be completed</p>
        <p className="mt-2 text-[13.5px] text-ink-3">{status.message} No result is shown, because a failed lookup says nothing about the address.</p>
      </div>
    );
  const { result } = status;
  if (result.verdict === "not_found") {
    return (
      <div className="card p-6" role="status" aria-live="polite">
        <p className="eyebrow">Result</p>
        <p className="display mt-2 text-[32px] text-ink">Not found on the trail</p>
        <p className="mt-3 max-w-2xl text-[14px] leading-relaxed text-ink-2">
          Checked on {result.checked.map((c) => chainName(c.chain)).join(", ")}. None of these addresses is on the trail or recorded as
          receiving from it{dataThrough ? `, as of ${formatUtc(dataThrough)}` : ""}.
        </p>
        <p className="mt-4 rounded-xl border border-[rgb(242_179_61/0.3)] bg-[rgb(242_179_61/0.06)] p-4 text-[13.5px] leading-relaxed text-ink-2">
          <strong className="font-semibold text-gold-2">Not found is not the same as clean.</strong> The trail covers the value we can follow on
          the chains we index, and it stops at services. An address can still be exposed through an exchange, a mixer, a chain we do not
          follow, or activity after the last update.
        </p>
      </div>
    );
  }
  const tone =
    result.verdict === "trail"
      ? { title: "On the trail", border: "rgb(240 84 79 / 0.45)", bg: "rgb(240 84 79 / 0.07)", color: "#f0544f" }
      : { title: "Received from the trail", border: "rgb(255 133 80 / 0.45)", bg: "rgb(255 133 80 / 0.07)", color: "#ff8550" };
  return (
    <div className="card overflow-hidden" role="status" aria-live="polite" style={{ borderColor: tone.border }}>
      <div className="p-6" style={{ background: tone.bg }}>
        <p className="eyebrow" style={{ color: tone.color }}>
          Result
        </p>
        <p className="display mt-2 text-[34px] text-ink">{tone.title}</p>
        <p className="mt-2 text-[14px] text-ink-2">
          {result.verdict === "trail"
            ? "This address is part of the trail: most of the value it ever received came from the theft."
            : result.verdict === "direct"
              ? "This address received value directly from a trail address. It is not itself on the trail."
              : "This address received value from an address that was paid by the trail. It is not itself on the trail."}
        </p>
      </div>
      <div className="divide-y divide-line">
        {result.hits.map(({ entry, candidate }) => {
          const url = addressUrl(entry.chain, entry.address);
          return (
            <div key={candidate.node} className="grid gap-5 p-6 md:grid-cols-[1fr_auto]">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <ChainChip chain={entry.chain} />
                  <span className="chip">{entry.role}</span>
                  {entry.category ? <span className="chip">{entry.category}</span> : null}
                  <span className="chip num">hop {entry.hop}</span>
                </div>
                <p className="mt-3 text-[15px] text-ink">{addressLabel(entry.label, entry.address)}</p>
                <p className="mono mt-1 text-[12px] break-all text-ink-3">{entry.address}</p>
                <p className="mt-4 text-[14px] leading-relaxed text-ink-2">{entry.reason}</p>
                {url ? (
                  <a href={url} target="_blank" rel="noopener noreferrer" className="mt-3 inline-flex items-center gap-1.5 text-[13px] text-gold-2 no-underline hover:text-gold">
                    Open on {CHAIN_INFO[entry.chain].name} explorer <ExternalIcon />
                  </a>
                ) : null}
              </div>
              <dl className="grid min-w-[200px] grid-cols-2 gap-3 md:grid-cols-1">
                <div className="card-quiet p-3">
                  <dt className="eyebrow text-[10px]">Received from the trail</dt>
                  <dd className="num mt-1 text-[20px] text-ink">{formatUsd(entry.received_usd)}</dd>
                </div>
                <div className="card-quiet p-3">
                  <dt className="eyebrow text-[10px]">Held now</dt>
                  <dd className="num mt-1 text-[20px] text-ink">{formatUsd(entry.held_usd)}</dd>
                </div>
                <div className="card-quiet col-span-2 p-3 md:col-span-1">
                  <dt className="eyebrow text-[10px]">First seen</dt>
                  <dd className="num mt-1 text-[13px] text-ink-2">{entry.first_ts ? formatUtc(entry.first_ts * 1000) : "n/a"}</dd>
                </div>
              </dl>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function ScreenTool() {
  const [input, setInput] = useState("");
  const [chain, setChain] = useState<Chain | "auto">("auto");
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [showExamples, setShowExamples] = useState(false);
  const summary = useSummary(false);
  const inputId = useId();
  const chainId = useId();
  const preview = usePreview(input, chain);
  const trimmed = input.trim();
  const invalid = trimmed.length > 0 && candidatesFor(trimmed, chain).length === 0;

  const check = async (value = input, onChain = chain) => {
    const candidates = candidatesFor(value, onChain);
    if (!candidates.length) return;
    setStatus({ kind: "busy" });
    try {
      const keyed = await keyCandidates(candidates);
      const result = await screenCandidates(keyed, loadShard);
      setStatus({ kind: "done", result });
    } catch (err) {
      setStatus({ kind: "error", message: err instanceof Error ? err.message : "Network error." });
    }
  };

  const origin = useSyncExternalStore(
    () => () => {},
    () => window.location.origin,
    () => "",
  );
  const apiAbsolute = /^https?:/i.test(API_BASE) ? API_BASE : `${origin}${API_BASE}`;
  const sample = preview[0];

  return (
    <>
      <div className="shell">
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
          <div className="card p-5 sm:p-7">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void check();
              }}
            >
              <label htmlFor={inputId} className="text-[15px] font-medium text-ink">
                Address to check
              </label>
              <div className="mt-3 flex flex-col gap-3 sm:flex-row">
                <input
                  id={inputId}
                  className="input flex-1"
                  value={input}
                  onChange={(e) => {
                    setInput(e.target.value);
                    setStatus({ kind: "idle" });
                  }}
                  placeholder="0x…, bc1…, T…, r… or a Solana address"
                  spellCheck={false}
                  autoComplete="off"
                  autoCapitalize="off"
                  aria-invalid={invalid}
                  aria-describedby={`${inputId}-hint`}
                />
                <label htmlFor={chainId} className="sr-only">
                  Chain
                </label>
                <select id={chainId} className="input sm:w-44" value={chain} onChange={(e) => setChain(e.target.value as Chain | "auto")}>
                  <option value="auto">Detect chain</option>
                  {CHAINS.map((c) => (
                    <option key={c} value={c}>
                      {chainName(c)}
                    </option>
                  ))}
                </select>
                <button type="submit" className="btn btn-gold h-12" disabled={!trimmed || invalid || status.kind === "busy"}>
                  Check
                </button>
              </div>
              <p id={`${inputId}-hint`} className={`mt-2 text-[12.5px] ${invalid ? "text-ember" : "text-ink-3"}`}>
                {invalid
                  ? chain === "auto"
                    ? "This does not look like an address on any chain we follow."
                    : `This is not a valid ${chainName(chain)} address.`
                  : preview.length > 1
                    ? `Fits ${preview.length} chains: ${preview.map((p) => chainName(p.chain)).join(", ")}. All are checked with one request.`
                    : preview.length === 1
                      ? `Reads as a ${chainName(preview[0].chain)} address.`
                      : "Paste an address. Nothing is sent until you press Check."}
              </p>
            </form>
            <div className="mt-4 flex flex-wrap items-center gap-2">
              <span className="text-[12.5px] text-ink-3">Try:</span>
              {showExamples ? (
                <Examples
                  onPick={(a, c) => {
                    setInput(a);
                    setChain("auto");
                    void check(a, c);
                  }}
                />
              ) : (
                <button type="button" className="chip h-7 cursor-pointer hover:text-ink" onClick={() => setShowExamples(true)}>
                  Load example addresses from the trail
                </button>
              )}
            </div>
            <dl className="mt-6 grid gap-3 border-t border-line pt-5 text-[13px] sm:grid-cols-3">
              {[
                ["#f0544f", "On the trail", "The address is part of the trail itself."],
                ["#ff8550", "Received from the trail", "Paid directly by a trail address, without joining the trail."],
                ["#9b9082", "Not found", "Not on the trail. Not the same as clean."],
              ].map(([color, title, text]) => (
                <div key={title}>
                  <dt className="flex items-center gap-2 text-ink">
                    <span aria-hidden="true" className="inline-block size-2 rounded-full" style={{ background: color }} />
                    {title}
                  </dt>
                  <dd className="mt-1 leading-relaxed text-ink-3">{text}</dd>
                </div>
              ))}
            </dl>
          </div>

          <aside className="card p-5 sm:p-7" aria-labelledby="sees-title">
            <h2 id="sees-title" className="text-[15px] font-semibold text-ink">
              What the server sees
            </h2>
            <p className="mt-1 text-[13px] text-ink-3">Computed in your browser as you type.</p>
            <ol className="mt-5 space-y-4 text-[13px]">
              <li>
                <p className="text-ink-3">1. The address is spelled one way, with no chain attached</p>
                <p className="mono mt-1 truncate text-ink-2">{sample ? canonicalAddress(sample.address) : "address"}</p>
              </li>
              <li>
                <p className="text-ink-3">2. Your browser hashes it with SHA-256</p>
                <p className="mono mt-1 truncate text-ink-2">{sample ? sample.key : "64 hex characters"}</p>
              </li>
              <li>
                <p className="text-ink-3">3. Only the first three characters are requested</p>
                <p className="mono mt-1 text-ink">
                  {preview.length ? (
                    Array.from(new Set(preview.map((p) => p.prefix))).map((prefix) => (
                      <span key={prefix} className="mr-2 inline-block rounded-md bg-[rgb(242_179_61/0.12)] px-2 py-0.5 text-gold-2">
                        screen/{prefix}.json
                      </span>
                    ))
                  ) : (
                    <span className="text-ink-3">screen/???.json</span>
                  )}
                </p>
              </li>
            </ol>
            <p className="mt-5 border-t border-line pt-4 text-[12.5px] leading-relaxed text-ink-3">
              One request names one of {formatCount(SHARD_COUNT)} shards, whatever chain the address is on. The match against the full hash
              happens here, in the browser.
            </p>
          </aside>
        </div>

        <div className="mt-6">
          <ResultCard status={status} dataThrough={summary.data?.data_through ?? null} />
        </div>
      </div>

      <section className="shell mt-24" aria-labelledby="privacy-title">
        <SectionHeader
          id="privacy-title"
          eyebrow="Private by design"
          title="We never learn which address you checked"
          lead={
            <p>
              Screening usually means sending a customer&apos;s address to a vendor. Here the server only ever learns a three character prefix
              shared by roughly 1 in {formatCount(SHARD_COUNT)} of all addresses, the same way the Have I Been Pwned range API checks passwords
              without receiving them.
            </p>
          }
        />
        <div className="grid gap-4 md:grid-cols-3">
          {[
            ["Hash locally", "The address alone is normalized (EVM and bech32 lowercased, everything else as written) and hashed with SHA-256 in your browser. No chain is attached."],
            ["Ask for a bucket", `The browser downloads one shard, named by the first three hex characters of the hash: one of ${formatCount(SHARD_COUNT)}. Every address in that bucket looks the same to the server.`],
            ["Match privately", "The shard lists full hashes of trail addresses that share the prefix, with one entry per chain. The comparison runs here, so the answer never leaves your device."],
          ].map(([title, text], i) => (
            <div key={title} className="card p-6">
              <p className="num text-[12px] text-gold-3">0{i + 1}</p>
              <h3 className="mt-3 text-[16px] font-semibold text-ink">{title}</h3>
              <p className="mt-2 text-[13.5px] leading-relaxed text-ink-3">{text}</p>
            </div>
          ))}
        </div>
        <p className="mt-5 max-w-3xl text-[13px] leading-relaxed text-ink-3">
          Why no chain is hashed: an EVM address is the same on six chains, and some strings are valid on two. Hashing the address alone
          puts every reading in the same shard, so a lookup is one request and the server learns one prefix. The shard lists each chain the
          address appears on, and the browser keeps the chains you asked about.
        </p>
      </section>

      <section className="shell mt-24" aria-labelledby="integrate-title">
        <SectionHeader
          id="integrate-title"
          eyebrow="For integrators"
          title="Screen deposits from your own systems"
          lead={
            <p>
              Use the static shards for the same k-anonymous lookup from a backend, or run the Goldenhour API yourself and query it directly. A
              self-hosted server sees the full address, but it is your server.
            </p>
          }
        />
        <CodeTabs
          tabs={[
            {
              label: "TypeScript",
              code: `// k-anonymous lookup: only a 3 character prefix leaves your system.
const API = "${apiAbsolute}";
const EVM = ["ethereum", "optimism", "arbitrum", "base", "polygon", "avalanche"];

// The address alone is hashed: EVM and bech32 lowercased, base58 and other formats as written.
function canonical(address: string): string {
  const a = address.trim();
  return /^0x[0-9a-fA-F]{40}$/.test(a) || /^bc1[02-9ac-hj-np-z]{11,71}$/i.test(a) ? a.toLowerCase() : a;
}

export async function screen(address: string, chains: string[] = EVM) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonical(address)));
  const key = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
  const res = await fetch(\`\${API}/screen/\${key.slice(0, 3)}.json\`);
  if (res.status === 404) return []; // empty shard: not on the trail
  if (!res.ok) throw new Error(\`screening failed: HTTP \${res.status}\`);
  const shard = await res.json();
  // One entry per chain the address appears on: { chain, exposure, role, hop, received_usd, reason, ... }
  return (shard.entries[key] ?? []).filter((e: { chain: string }) => chains.includes(e.chain));
}`,
            },
            {
              label: "Python",
              code: `import hashlib
import re

import httpx

API = "${apiAbsolute}"
EVM = {"ethereum", "optimism", "arbitrum", "base", "polygon", "avalanche"}

def canonical(address: str) -> str:
    # The address alone is hashed: EVM and bech32 lowercased, base58 as written.
    a = address.strip()
    if re.fullmatch(r"0x[0-9a-fA-F]{40}", a) or re.fullmatch(r"(?i)bc1[02-9ac-hj-np-z]{11,71}", a):
        return a.lower()
    return a

def screen(address: str, chains: set[str] = EVM) -> list[dict]:
    key = hashlib.sha256(canonical(address).encode()).hexdigest()
    r = httpx.get(f"{API}/screen/{key[:3]}.json", timeout=10)
    if r.status_code == 404:
        return []  # empty shard: not on the trail
    r.raise_for_status()
    return [e for e in r.json()["entries"].get(key, []) if e["chain"] in chains]`,
            },
            {
              label: "Self-hosted REST",
              code: `# Your own Goldenhour server (see the API page). It sees the full address.
curl -s https://goldenhour.internal.example/v1/screen/ethereum/0x8ba1f109551bd432803012645ac136ddd64dba72

# 200 for any valid address; "found" says whether it is on or paid by the trail:
# {"checked": ["ethereum:0x8ba1..."], "found": true,
#  "matches": [{"exposure": "trail", "role": "seed", "hop": 1, "received_usd": 182000000, "reason": "..."}],
#  "note": "A miss means the address is not on this trail. It does not mean the address is clean."}`,
            },
          ]}
        />
        <p className="mt-4 text-[12.5px] text-ink-3">
          A result is evidence for a review, not a verdict. Exposure levels: <span className="text-ink-2">trail</span> (the address is on the
          trail) and <span className="text-ink-2">direct</span> (received money from a trail address but did not join it).
        </p>
      </section>
    </>
  );
}
