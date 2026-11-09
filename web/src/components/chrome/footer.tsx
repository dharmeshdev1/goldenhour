"use client";

import Link from "next/link";
import { useNow } from "@/components/chrome/freshness";
import { Wordmark } from "@/components/chrome/logo";
import { NAV } from "@/components/chrome/nav";
import { REPO_URL } from "@/lib/api";
import { formatCount, formatRelative, formatUtc } from "@/lib/format";
import { useMeta, useSummary } from "@/lib/use-api";

function Freshness() {
  const meta = useMeta();
  const summary = useSummary();
  const now = useNow();
  const m = meta.data;
  const sources = m ? Object.entries(m.sources_ok) : [];
  const down = sources.filter(([, ok]) => !ok).map(([name]) => name);
  if (meta.error && !m) {
    return <p className="text-ink-3">The data pipeline has not published meta.json yet.</p>;
  }
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-[13px]">
      <dt className="text-ink-3">Published</dt>
      <dd className="num text-ink-2">{m ? `${formatRelative(m.generated_at, now)}, ${formatUtc(m.generated_at)}` : "loading"}</dd>
      <dt className="text-ink-3">Data through</dt>
      <dd className="num text-ink-2">{summary.data ? formatUtc(summary.data.data_through) : "loading"}</dd>
      <dt className="text-ink-3">Last run</dt>
      <dd className="num text-ink-2">
        {m ? `${m.tick.duration_s.toFixed(1)} s, ${formatCount(m.tick.fetched)} fetched, ${formatCount(m.tick.joined)} joined, ${formatCount(m.tick.errors)} errors` : "loading"}
      </dd>
      <dt className="text-ink-3">Sources</dt>
      <dd className="text-ink-2">
        {m ? (
          down.length ? (
            <span>
              {sources.length - down.length} of {sources.length} responding.{" "}
              <span className="text-ember">Not responding: {down.join(", ")}.</span>
            </span>
          ) : (
            `All ${sources.length} responding`
          )
        ) : (
          "loading"
        )}
      </dd>
      {m?.version ? (
        <>
          <dt className="text-ink-3">Pipeline</dt>
          <dd className="num text-ink-2">v{m.version}</dd>
        </>
      ) : null}
    </dl>
  );
}

export function Footer() {
  return (
    <footer className="mt-32 border-t border-line">
      <div className="shell grid gap-12 py-16 md:grid-cols-[1.2fr_1fr_1.4fr]">
        <div className="max-w-sm">
          <Wordmark />
          <p className="mt-4 text-[14px] leading-relaxed text-ink-3">
            Open-source crypto theft response. Guard the signer, catch the drain, file the freezes, follow the money.
          </p>
          <p className="mt-6 flex flex-wrap gap-x-4 gap-y-2 text-[13px]">
            <a href={REPO_URL} className="text-ink-2 hover:text-ink">
              github.com/dharmeshdev1/goldenhour
            </a>
            <span className="text-ink-3">Apache-2.0</span>
          </p>
        </div>
        <nav aria-label="Footer">
          <p className="eyebrow mb-4">Pages</p>
          <ul className="grid grid-cols-2 gap-x-6 gap-y-2 text-[14px]">
            {NAV.map((item) => (
              <li key={item.href}>
                <Link href={item.href} className="text-ink-2 no-underline hover:text-ink">
                  {item.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
        <div>
          <p className="eyebrow mb-4">Data freshness</p>
          <Freshness />
        </div>
      </div>
      <div className="border-t border-line">
        <div className="shell flex flex-col gap-2 py-6 text-[12.5px] text-ink-3 md:flex-row md:items-center md:justify-between">
          <p>Figures are estimates from public blockchain data and published sources. See the method for what the trail can and cannot see.</p>
          <p>Not affiliated with Binance or any service named on this site.</p>
        </div>
      </div>
    </footer>
  );
}
