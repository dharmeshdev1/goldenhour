"use client";

import { useReducedMotion } from "motion/react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { ChartLegend, EVENT_COLOR, GoldenChart } from "@/components/charts/golden-chart";
import { Sky } from "@/components/golden-hour/sky";
import { SourceRef } from "@/components/ui/source-ref";
import { ErrorState, Skeleton } from "@/components/ui/states";
import { formatClock, formatUsd } from "@/lib/format";
import { cleanScenarios, cleanSeries, interpolateScenario, seriesAt } from "@/lib/scenarios";
import { useCase, useGoldenHour } from "@/lib/use-api";

export function GoldenHourView() {
  const gh = useGoldenHour();
  const kase = useCase();
  const reduce = useReducedMotion();
  const sliderId = useId();
  const data = gh.data;
  const series = useMemo(() => (data ? cleanSeries(data) : null), [data]);
  const scenarios = useMemo(() => cleanScenarios(data?.scenarios), [data]);
  const maxMinute = series?.minutes.length ? series.minutes[series.minutes.length - 1] : 240;
  const sliderMax = scenarios.length ? Math.min(maxMinute, scenarios[scenarios.length - 1].response_minutes) : maxMinute;
  const [n, setN] = useState(10);
  const [playing, setPlaying] = useState(false);
  const raf = useRef(0);

  useEffect(() => {
    if (!playing) return;
    const start = performance.now();
    const from = n >= sliderMax ? 0 : n;
    const duration = ((sliderMax - from) / sliderMax) * 9000;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / Math.max(1, duration));
      setN(Math.round(from + (sliderMax - from) * t));
      if (t < 1) raf.current = requestAnimationFrame(tick);
      else setPlaying(false);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
    // Only (re)start when play is pressed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing]);

  if (gh.error && !data) {
    return (
      <div className="shell py-16">
        <ErrorState error={gh.error} onRetry={gh.reload} what="The golden hour data" />
      </div>
    );
  }

  const scenario = interpolateScenario(scenarios, n);
  const t0 = data ? Date.parse(data.t0) : NaN;
  const dusk = sliderMax > 0 ? n / sliderMax : 0;
  const escapedTotal = series?.escaped.length ? series.escaped[series.escaped.length - 1] : 0;
  const drainedTotal = series?.drained.length ? series.drained[series.drained.length - 1] : 0;
  const freezableNow = series ? seriesAt(series.minutes, series.freezable, n) : 0;
  const revoke = data?.events.find((e) => e.kind === "revoke");
  const chartLabel = series
    ? `Chart of the theft by minute after ${formatClock(data?.t0)}. ${formatUsd(drainedTotal)} drained by minute ${maxMinute}; ${formatUsd(
        escapedTotal,
      )} of stablecoins swapped before any freeze. A response line marks minute ${n}.`
    : "Loading chart";

  return (
    <Sky dusk={dusk}>
      <div className="shell pt-14 pb-20 sm:pt-20">
        <p className="eyebrow mb-4 text-gold-2">The freeze window</p>
        <h1 className="display max-w-4xl text-[46px] text-ink sm:text-[72px]">
          Every minute, more of it moves <em className="text-gold-2">out of reach</em>.
        </h1>
        <p className="lead mt-6 max-w-2xl text-[18px]">
          Stablecoins can be frozen by their issuers, but only while they sit at an address on the trail. Drag the response clock to see
          what was still within reach at each minute of the Binance theft.
        </p>

        <div className="mt-12 rounded-[20px] border border-line-2 bg-[rgb(10_8_7/0.55)] p-4 shadow-2xl backdrop-blur-md sm:p-6">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <ChartLegend />
            {data ? <p className="num text-[12px] text-ink-3">Minutes after {formatClock(data.t0)}, {new Date(t0).toUTCString().slice(5, 16)}</p> : null}
          </div>
          {series && data ? (
            <GoldenChart series={series} events={data.events} t0={data.t0} response={n} label={chartLabel} height={400} />
          ) : (
            <Skeleton className="h-[400px] w-full" label="Loading chart" />
          )}

          <div className="mt-6 grid gap-6 border-t border-line pt-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
            <div>
              <div className="flex items-center justify-between gap-3">
                <label htmlFor={sliderId} className="text-[15px] text-ink">
                  If the response had started at <span className="num text-gold-2">minute {n}</span>
                </label>
                <button
                  type="button"
                  className="btn btn-ghost btn-small"
                  onClick={() => (playing ? setPlaying(false) : setPlaying(true))}
                  disabled={!data || Boolean(reduce)}
                  aria-pressed={playing}
                >
                  {playing ? "Pause" : "Play the clock"}
                </button>
              </div>
              <input
                id={sliderId}
                type="range"
                className="range mt-4"
                min={0}
                max={sliderMax}
                step={1}
                value={n}
                onChange={(e) => {
                  setPlaying(false);
                  setN(Number(e.target.value));
                }}
                aria-valuetext={`Minute ${n}, ${Number.isFinite(t0) ? formatClock(t0 + n * 60_000) : ""}`}
                disabled={!data}
              />
              <div className="num mt-1 flex justify-between text-[11px] text-ink-3">
                <span>T0 {Number.isFinite(t0) ? formatClock(t0) : ""}</span>
                <span>
                  +{sliderMax} min {Number.isFinite(t0) ? formatClock(t0 + sliderMax * 60_000) : ""}
                </span>
              </div>
              <p className="mt-4 text-[13px] leading-relaxed text-ink-3">
                At {Number.isFinite(t0) ? formatClock(t0 + n * 60_000) : "this minute"}, {formatUsd(freezableNow)} of stablecoins sat at
                trail addresses, still freezable.
              </p>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="rounded-2xl border border-[rgb(143_211_255/0.25)] bg-[rgb(143_211_255/0.06)] p-5">
                <p className="eyebrow text-ice">Could still have been frozen</p>
                <p className="num mt-3 text-[34px] leading-none text-ink">{scenario ? formatUsd(scenario.freezable_saved_usd) : "n/a"}</p>
                <p className="mt-3 text-[12.5px] leading-relaxed text-ink-3">
                  Stablecoins that were swapped later than this response would have reached their issuers.
                </p>
              </div>
              <div className="rounded-2xl border border-[rgb(242_179_61/0.3)] bg-[rgb(242_179_61/0.07)] p-5">
                <p className="eyebrow text-gold-2">Drain prevented</p>
                <p className="num mt-3 text-[34px] leading-none text-ink">{scenario ? formatUsd(scenario.drain_prevented_usd) : "n/a"}</p>
                <p className="mt-3 text-[12.5px] leading-relaxed text-ink-3">Value that had not yet left the victim&apos;s wallets when signing stopped.</p>
              </div>
            </div>
          </div>
          {revoke && series ? (
            <p className="mt-5 rounded-xl border border-line bg-bg-1/70 px-4 py-3 text-[13px] leading-relaxed text-ink-2">
              What happened: the delegations were revoked at minute {Math.round(revoke.minute)} ({formatClock(revoke.at)}), after{" "}
              {formatUsd(drainedTotal)} had left and {formatUsd(escapedTotal)} of stablecoins had been swapped out of reach.{" "}
              <SourceRef id={revoke.source} />
            </p>
          ) : null}
        </div>

        <div className="mt-10 grid gap-6 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
          <section aria-labelledby="events-title" className="card p-6">
            <h2 id="events-title" className="text-[15px] font-semibold text-ink">
              What happened, minute by minute
            </h2>
            {data ? (
              <ol className="mt-4 space-y-3">
                {data.events.map((e, i) => (
                  <li key={i} className="grid grid-cols-[64px_1fr] gap-3 text-[13.5px]">
                    <span className="num text-ink-3">
                      T+{Math.round(e.minute)}
                      <span className="block text-[11px]">{formatClock(e.at).replace(" UTC", "")}</span>
                    </span>
                    <span className="flex items-start gap-2.5 text-ink-2">
                      <span aria-hidden="true" className="mt-1.5 inline-block size-2 shrink-0 rounded-full" style={{ background: EVENT_COLOR[e.kind] }} />
                      <span>
                        {e.label} {e.source && kase.data?.sources.some((s) => s.id === e.source) ? <SourceRef id={e.source} /> : null}
                      </span>
                    </span>
                  </li>
                ))}
              </ol>
            ) : (
              <Skeleton className="mt-4 h-48 w-full" />
            )}
          </section>
          <section aria-labelledby="notes-title" className="card p-6">
            <h2 id="notes-title" className="text-[15px] font-semibold text-ink">
              Assumptions behind the scenario
            </h2>
            <p className="mt-1 text-[13px] text-ink-3">Published with the model. Between its points the slider interpolates linearly.</p>
            {data ? (
              <ul className="mt-4 space-y-3">
                {data.notes.map((note, i) => (
                  <li key={i} className="flex gap-3 text-[13.5px] leading-relaxed text-ink-2">
                    <span aria-hidden="true" className="num mt-0.5 text-[11px] text-gold-3">
                      {String(i + 1).padStart(2, "0")}
                    </span>
                    {note}
                  </li>
                ))}
              </ul>
            ) : (
              <Skeleton className="mt-4 h-48 w-full" />
            )}
          </section>
        </div>
        {series ? (
          <details className="mt-6 card-quiet p-4">
            <summary className="text-[13px] text-ink-3 hover:text-ink">Show the minute series as a table (every 15 minutes)</summary>
            <div className="scroll-x mt-3">
              <table className="table">
                <caption className="sr-only">Drained, freezable and escaped value by minute</caption>
                <thead>
                  <tr>
                    <th scope="col">Minute</th>
                    <th scope="col">Time</th>
                    <th scope="col" className="r">
                      Drained
                    </th>
                    <th scope="col" className="r">
                      Freezable
                    </th>
                    <th scope="col" className="r">
                      Escaped
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {series.minutes
                    .map((m, i) => ({ m, i }))
                    .filter(({ m }) => m % 15 === 0)
                    .map(({ m, i }) => (
                      <tr key={m}>
                        <td className="num">+{m}</td>
                        <td className="num">{formatClock(t0 + m * 60_000)}</td>
                        <td className="r num">{formatUsd(series.drained[i])}</td>
                        <td className="r num">{formatUsd(series.freezable[i])}</td>
                        <td className="r num">{formatUsd(series.escaped[i])}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          </details>
        ) : null}
      </div>
    </Sky>
  );
}
