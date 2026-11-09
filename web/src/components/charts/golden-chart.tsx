"use client";

import { scaleLinear } from "d3-scale";
import { area, curveMonotoneX, line } from "d3-shape";
import { useId, useMemo, useRef, useState } from "react";
import { formatClock, formatUsd } from "@/lib/format";
import type { GoldenHour } from "@/lib/types";
import { COLOR } from "@/lib/tones";
import { useWidth } from "@/lib/use-width";

/**
 * The minute axis of the theft: drained value rising, freezable stablecoins rising and draining
 * away, value that escaped by swap, and the events that mattered. Optional response line and an
 * optional reveal point for the replay on /heist.
 */

export interface Series {
  minutes: number[];
  drained: number[];
  freezable: number[];
  escaped: number[];
}

export const EVENT_COLOR: Record<GoldenHour["events"][number]["kind"], string> = {
  theft: COLOR.red,
  freeze: COLOR.ice,
  swap: COLOR.violet,
  revoke: COLOR.gold,
  detect: COLOR.green,
};

export function GoldenChart({
  series,
  events,
  t0,
  response,
  reveal,
  height = 380,
  label,
}: {
  series: Series;
  events: GoldenHour["events"];
  t0: string;
  response?: number;
  reveal?: number;
  height?: number;
  label: string;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const width = useWidth(wrap);
  const uid = useId().replace(/:/g, "");
  const [hover, setHover] = useState<number | null>(null);
  const compact = width > 0 && width < 560;
  const m = { top: 30, right: compact ? 12 : 22, bottom: 38, left: compact ? 44 : 56 };
  const t0ms = Date.parse(t0);

  const geo = useMemo(() => {
    if (!width || !series.minutes.length) return null;
    const maxMinute = series.minutes[series.minutes.length - 1];
    const maxY = Math.max(...series.drained, ...series.freezable, 1);
    const x = scaleLinear().domain([0, maxMinute]).range([m.left, width - m.right]);
    const y = scaleLinear().domain([0, maxY]).nice(4).range([height - m.bottom, m.top]);
    const idx = series.minutes.map((_, i) => i);
    const upto = reveal === undefined ? idx : idx.filter((i) => series.minutes[i] <= reveal);
    const areaGen = (vals: number[]) =>
      area<number>()
        .x((i) => x(series.minutes[i]))
        .y0(y(0))
        .y1((i) => y(vals[i]))
        .curve(curveMonotoneX)(upto) ?? "";
    const lineGen = (vals: number[]) =>
      line<number>()
        .x((i) => x(series.minutes[i]))
        .y((i) => y(vals[i]))
        .curve(curveMonotoneX)(upto) ?? "";
    const step = maxMinute > 180 ? 30 : maxMinute > 90 ? 15 : 10;
    const ticks = Array.from({ length: Math.floor(maxMinute / step) + 1 }, (_, i) => i * step);
    return {
      x,
      y,
      maxMinute,
      drainedArea: areaGen(series.drained),
      drainedLine: lineGen(series.drained),
      freezableArea: areaGen(series.freezable),
      freezableLine: lineGen(series.freezable),
      escapedLine: lineGen(series.escaped),
      xTicks: compact ? ticks.filter((_, i) => i % 2 === 0) : ticks,
      yTicks: y.ticks(4),
    };
  }, [width, series, height, reveal, compact, m.left, m.right, m.top, m.bottom]);

  const shownEvents = events.filter((e) => reveal === undefined || e.minute <= reveal);

  return (
    <div ref={wrap} className="relative w-full" style={{ height }}>
      {geo ? (
        <svg width={width} height={height} role="img" aria-label={label} className="block">
          <defs>
            <linearGradient id={`${uid}-red`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor={COLOR.red} stopOpacity="0.42" />
              <stop offset="1" stopColor={COLOR.red} stopOpacity="0.02" />
            </linearGradient>
            <linearGradient id={`${uid}-ice`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor={COLOR.ice} stopOpacity="0.5" />
              <stop offset="1" stopColor={COLOR.ice} stopOpacity="0.04" />
            </linearGradient>
          </defs>
          {geo.yTicks.map((t) => (
            <g key={t}>
              <line x1={m.left} x2={width - m.right} y1={geo.y(t)} y2={geo.y(t)} stroke="rgba(255,232,204,0.07)" />
              <text x={m.left - 10} y={geo.y(t)} textAnchor="end" dominantBaseline="middle" fontSize={11} fill={COLOR.ink3} className="num">
                {formatUsd(t)}
              </text>
            </g>
          ))}
          {geo.xTicks.map((t) => (
            <g key={t} transform={`translate(${geo.x(t)},${height - m.bottom})`}>
              <line y1={0} y2={5} stroke="rgba(255,232,204,0.25)" />
              <text y={18} textAnchor="middle" fontSize={11} fill={COLOR.ink2} className="num">
                {t === 0 ? "T0" : `+${t}`}
              </text>
              {!compact ? (
                <text y={31} textAnchor="middle" fontSize={10} fill={COLOR.ink3} className="num">
                  {formatClock(t0ms + t * 60_000).replace(" UTC", "")}
                </text>
              ) : null}
            </g>
          ))}
          {response !== undefined ? (
            <rect
              x={geo.x(Math.min(response, geo.maxMinute))}
              y={m.top}
              width={Math.max(0, width - m.right - geo.x(Math.min(response, geo.maxMinute)))}
              height={height - m.top - m.bottom}
              fill="rgba(5,8,20,0.28)"
            />
          ) : null}
          <path d={geo.drainedArea} fill={`url(#${uid}-red)`} />
          <path d={geo.drainedLine} fill="none" stroke={COLOR.red} strokeWidth={2} />
          <path d={geo.freezableArea} fill={`url(#${uid}-ice)`} />
          <path d={geo.freezableLine} fill="none" stroke={COLOR.ice} strokeWidth={1.75} />
          <path d={geo.escapedLine} fill="none" stroke={COLOR.violet} strokeWidth={1.75} strokeDasharray="5 4" />
          {shownEvents.map((e, i) => {
            const ex = geo.x(Math.min(e.minute, geo.maxMinute));
            const on = hover === i;
            return (
              <g
                key={`${e.minute}-${i}`}
                onMouseEnter={() => setHover(i)}
                onMouseLeave={() => setHover(null)}
                className="cursor-default"
              >
                <line x1={ex} x2={ex} y1={m.top - 10} y2={height - m.bottom} stroke={EVENT_COLOR[e.kind]} strokeOpacity={on ? 0.8 : 0.28} strokeDasharray="2 3" />
                <circle cx={ex} cy={m.top - 12} r={on ? 6 : 4.5} fill={COLOR.bg} stroke={EVENT_COLOR[e.kind]} strokeWidth={2} />
                <rect x={ex - 8} y={m.top - 22} width={16} height={height - m.top - m.bottom + 22} fill="transparent" />
              </g>
            );
          })}
          {response !== undefined ? (
            <g transform={`translate(${geo.x(Math.min(response, geo.maxMinute))},0)`}>
              <line y1={m.top - 4} y2={height - m.bottom} stroke={COLOR.gold} strokeWidth={2} />
              <circle cy={height - m.bottom} r={4} fill={COLOR.gold} />
            </g>
          ) : null}
          {reveal !== undefined && reveal < geo.maxMinute ? (
            <line x1={geo.x(reveal)} x2={geo.x(reveal)} y1={m.top} y2={height - m.bottom} stroke={COLOR.ink} strokeOpacity={0.5} />
          ) : null}
        </svg>
      ) : null}
      {geo && hover !== null && shownEvents[hover] ? (
        <div
          className="pointer-events-none absolute z-10 w-56 rounded-lg border border-line-2 bg-bg-2/95 px-3 py-2 text-[12.5px] shadow-xl backdrop-blur"
          style={{
            left: Math.max(0, Math.min(geo.x(Math.min(shownEvents[hover].minute, geo.maxMinute)) - 112, width - 224)),
            top: m.top + 4,
          }}
        >
          <p className="num text-[11px] text-ink-3">
            T+{Math.round(shownEvents[hover].minute)} min, {formatClock(shownEvents[hover].at)}
          </p>
          <p className="mt-0.5 text-ink">{shownEvents[hover].label}</p>
        </div>
      ) : null}
    </div>
  );
}

export function ChartLegend({ className = "" }: { className?: string }) {
  const item = (color: string, text: string, dashed = false) => (
    <span className="inline-flex items-center gap-2">
      <svg width="18" height="8" aria-hidden="true">
        <line x1="0" x2="18" y1="4" y2="4" stroke={color} strokeWidth="2" strokeDasharray={dashed ? "4 3" : undefined} />
      </svg>
      {text}
    </span>
  );
  return (
    <div className={`flex flex-wrap gap-x-5 gap-y-2 text-[12.5px] text-ink-2 ${className}`}>
      {item(COLOR.red, "Drained from the victim")}
      {item(COLOR.ice, "Stablecoins still freezable")}
      {item(COLOR.violet, "Swapped before a freeze (escaped)", true)}
    </div>
  );
}
