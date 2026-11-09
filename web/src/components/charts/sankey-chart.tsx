"use client";

import { sankey, sankeyJustify, sankeyLinkHorizontal, type SankeyLink, type SankeyNode } from "d3-sankey";
import { useReducedMotion } from "motion/react";
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { formatUsd } from "@/lib/format";
import type { ShapedFlow, ShapedNode } from "@/lib/sankey";
import { COLOR, toneColor } from "@/lib/tones";
import { useOnScreen, useWidth } from "@/lib/use-width";

/**
 * "Where the money is now": the victim, the chains value was taken on, and where that value sits
 * today. Particles move along each band in proportion to its value. Loaded only where it is used.
 */

type N = SankeyNode<ShapedNode, { usd: number }>;
type L = SankeyLink<ShapedNode, { usd: number }>;

const SHORT: Record<string, string> = {
  "state:held": "Held",
  "state:frozen": "Frozen",
};

function shortLabel(n: ShapedNode): string {
  if (SHORT[n.id]) return SHORT[n.id];
  if (n.kind === "category") return n.label.split(" ")[0];
  return n.label;
}

/** Frozen first, then held, then the services; chains by value with the grouped band last. */
function group(n: ShapedNode): number {
  if (n.tone === "frozen") return 0;
  if (n.tone === "held") return 1;
  if (n.id === "chain:__other") return 3;
  return 2;
}

/**
 * Right-hand labels, one line each, pushed apart where small bands sit close together. Returns
 * the label y for every node id; a label far from its band gets a leader line.
 */
function spreadLabels(nodes: N[], gap: number, top: number, bottom: number): Map<string, number> {
  const items = nodes
    .map((n) => ({ id: n.id, want: ((n.y0 ?? 0) + (n.y1 ?? 0)) / 2 }))
    .sort((a, b) => a.want - b.want);
  const ys = items.map((i) => i.want);
  for (let i = 0; i < ys.length; i++) ys[i] = Math.max(ys[i], i ? ys[i - 1] + gap : top);
  for (let i = ys.length - 1; i >= 0; i--) ys[i] = Math.min(ys[i], i < ys.length - 1 ? ys[i + 1] - gap : bottom);
  return new Map(items.map((it, i) => [it.id, ys[i]]));
}

interface Layout {
  nodes: N[];
  links: L[];
  width: number;
  height: number;
  labelY: Map<string, number>;
  midY: Map<string, number>;
}

function computeLayout(flow: ShapedFlow, width: number, compact: boolean): Layout | null {
  if (width <= 0 || !flow.links.length) return null;
  const height = compact ? 460 : 600;
  const left = compact ? 0 : 92;
  const right = compact ? 120 : 210;
  const gen = sankey<ShapedNode, { usd: number }>()
    .nodeId((d) => d.id)
    .nodeAlign(sankeyJustify)
    .nodeWidth(compact ? 8 : 10)
    .nodePadding(compact ? 9 : 12)
    .nodeSort((a, b) => group(a) - group(b) || b.value - a.value)
    .extent([
      [left, 8],
      [Math.max(left + 120, width - right), height - 8],
    ]);
  const graph = gen({
    nodes: flow.nodes.map((n) => ({ ...n })),
    links: flow.links.map((l) => ({ ...l, value: l.usd })),
  });
  const sinks = graph.nodes.filter((n) => !n.sourceLinks?.length);
  const labelY = spreadLabels(sinks, compact ? 15 : 18, 10, height - 10);
  const middle = graph.nodes.filter((n) => (n.depth ?? 0) > 0 && n.sourceLinks?.length);
  const midY = spreadLabels(middle, compact ? 14 : 16, 10, height - 10);
  return { nodes: graph.nodes, links: graph.links, width, height, labelY, midY };
}

const linkPath = sankeyLinkHorizontal<ShapedNode, { usd: number }>();

interface Particle {
  link: number;
  t: number;
  speed: number;
  offset: number;
  size: number;
}

function bezier(link: L, t: number, offset: number): [number, number] {
  const s = link.source as N;
  const tg = link.target as N;
  const x0 = s.x1 ?? 0;
  const x1 = tg.x0 ?? 0;
  const y0 = (link.y0 ?? 0) + offset;
  const y1 = (link.y1 ?? 0) + offset;
  const xm = (x0 + x1) / 2;
  const u = 1 - t;
  const x = u * u * u * x0 + 3 * u * u * t * xm + 3 * u * t * t * xm + t * t * t * x1;
  const y = u * u * u * y0 + 3 * u * u * t * y0 + 3 * u * t * t * y1 + t * t * t * y1;
  return [x, y];
}

function Particles({ layout, total }: { layout: Layout; total: number }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const onScreen = useOnScreen(canvas);
  const reduce = useReducedMotion();

  useEffect(() => {
    const el = canvas.current;
    if (!el || reduce || !onScreen) return;
    const ctx = el.getContext("2d");
    if (!ctx) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    el.width = layout.width * dpr;
    el.height = layout.height * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const budget = layout.width < 640 ? 90 : 190;
    const particles: Particle[] = [];
    layout.links.forEach((link, i) => {
      const n = Math.max(1, Math.round((budget * link.value) / Math.max(1, total)));
      const w = Math.max(1, (link.width ?? 1) - 2);
      for (let k = 0; k < n; k++) {
        particles.push({
          link: i,
          t: Math.random(),
          speed: 0.07 + Math.random() * 0.06,
          offset: (Math.random() - 0.5) * w,
          size: 0.8 + Math.random() * 1.1,
        });
      }
    });
    const colors = layout.links.map((l) => toneColor((l.target as N).tone, (l.target as N).id));
    let last = performance.now();
    let raf = 0;
    const draw = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      ctx.clearRect(0, 0, layout.width, layout.height);
      ctx.globalCompositeOperation = "lighter";
      for (const p of particles) {
        p.t += p.speed * dt;
        if (p.t > 1) p.t -= 1;
        const link = layout.links[p.link];
        const [x, y] = bezier(link, p.t, p.offset);
        const fade = Math.min(1, p.t * 6, (1 - p.t) * 6);
        ctx.globalAlpha = 0.75 * fade;
        ctx.fillStyle = colors[p.link];
        ctx.beginPath();
        ctx.arc(x, y, p.size, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = "source-over";
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    const onVis = () => {
      if (document.visibilityState === "hidden") cancelAnimationFrame(raf);
      else {
        last = performance.now();
        raf = requestAnimationFrame(draw);
      }
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      cancelAnimationFrame(raf);
      document.removeEventListener("visibilitychange", onVis);
      ctx.clearRect(0, 0, layout.width, layout.height);
    };
  }, [layout, total, reduce, onScreen]);

  return (
    <canvas
      ref={canvas}
      aria-hidden="true"
      className="pointer-events-none absolute inset-0"
      style={{ width: layout.width, height: layout.height }}
    />
  );
}

export default function SankeyChart({ flow, summary }: { flow: ShapedFlow; summary: string }) {
  const wrap = useRef<HTMLDivElement>(null);
  const width = useWidth(wrap);
  const compact = width > 0 && width < 640;
  const layout = useMemo(() => computeLayout(flow, width, compact), [flow, width, compact]);
  const [hover, setHover] = useState<{ kind: "node" | "link"; index: number } | null>(null);
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null);
  const uid = useId().replace(/:/g, "");

  const active = (link: L, i: number): boolean => {
    if (!hover) return true;
    if (hover.kind === "link") return hover.index === i;
    const n = layout?.nodes[hover.index];
    return link.source === n || link.target === n;
  };

  return (
    <div ref={wrap} className="relative w-full" style={{ minHeight: layout?.height ?? 460 }}>
      {layout ? (
        <>
          <svg
            width={layout.width}
            height={layout.height}
            role="img"
            aria-label={summary}
            className="block overflow-visible"
            onMouseLeave={() => {
              setHover(null);
              setTip(null);
            }}
          >
            <defs>
              {layout.links.map((l, i) => {
                const s = l.source as N;
                const t = l.target as N;
                return (
                  <linearGradient key={i} id={`${uid}-g${i}`} gradientUnits="userSpaceOnUse" x1={s.x1} x2={t.x0}>
                    <stop offset="0" stopColor={toneColor(s.tone, s.id)} />
                    <stop offset="1" stopColor={toneColor(t.tone, t.id)} />
                  </linearGradient>
                );
              })}
            </defs>
            <g fill="none">
              {layout.links.map((l, i) => {
                const s = l.source as N;
                const t = l.target as N;
                const on = active(l, i);
                return (
                  <path
                    key={i}
                    d={linkPath(l) ?? undefined}
                    stroke={`url(#${uid}-g${i})`}
                    strokeWidth={Math.max(1, l.width ?? 1)}
                    strokeOpacity={hover ? (on ? 0.62 : 0.08) : 0.3}
                    style={{ transition: "stroke-opacity 200ms ease" }}
                    onMouseEnter={() => setHover({ kind: "link", index: i })}
                    onMouseMove={(e) => {
                      const box = wrap.current?.getBoundingClientRect();
                      if (!box) return;
                      setTip({ x: e.clientX - box.left, y: e.clientY - box.top, text: `${s.label} to ${t.label}: ${formatUsd(l.value)}` });
                    }}
                  />
                );
              })}
            </g>
            <g>
              {layout.nodes.map((n, i) => {
                const h = Math.max(1, (n.y1 ?? 0) - (n.y0 ?? 0));
                const color = toneColor(n.tone, n.id);
                const label = compact ? shortLabel(n) : n.label;
                const isLeft = (n.depth ?? 0) === 0;
                const isRight = !n.sourceLinks?.length;
                // On narrow screens only bands tall enough to hold a name are labeled; the table lists the rest.
                const showMiddle = !isLeft && !isRight && (!compact || h >= 14);
                const value = formatUsd(n.value);
                let text: ReactNode = null;
                if (isLeft) {
                  text = compact ? (
                    <text x={(n.x1 ?? 0) + 6} y={(n.y0 ?? 0) + 14} className="chart-label" fill={COLOR.ink} fontSize={12}>
                      {label}
                    </text>
                  ) : (
                    <text x={(n.x0 ?? 0) - 12} y={((n.y0 ?? 0) + (n.y1 ?? 0)) / 2} textAnchor="end" dominantBaseline="middle" fill={COLOR.ink}>
                      <tspan x={(n.x0 ?? 0) - 12} dy="-0.5em" fontSize={14}>
                        {label}
                      </tspan>
                      <tspan x={(n.x0 ?? 0) - 12} dy="1.35em" fontSize={12} fill={COLOR.ink3} className="num">
                        {value}
                      </tspan>
                    </text>
                  );
                } else if (isRight) {
                  const want = ((n.y0 ?? 0) + (n.y1 ?? 0)) / 2;
                  const y = layout.labelY.get(n.id) ?? want;
                  const x = (n.x1 ?? 0) + (Math.abs(y - want) > 3 ? 18 : 9);
                  text = (
                    <>
                      {Math.abs(y - want) > 3 ? (
                        <path d={`M${(n.x1 ?? 0) + 2},${want} C${(n.x1 ?? 0) + 9},${want} ${(n.x1 ?? 0) + 9},${y} ${x - 3},${y}`} stroke={COLOR.ink4} fill="none" />
                      ) : null}
                      <text x={x} y={y} dominantBaseline="middle" fill={COLOR.ink} fontSize={compact ? 11 : 13}>
                        {label}
                        <tspan dx={5} fontSize={compact ? 10 : 11.5} fill={COLOR.ink3} className="num">
                          {value}
                        </tspan>
                      </text>
                    </>
                  );
                } else if (showMiddle) {
                  const want = ((n.y0 ?? 0) + (n.y1 ?? 0)) / 2;
                  const y = compact ? want : layout.midY.get(n.id) ?? want;
                  const moved = Math.abs(y - want) > 3;
                  const x = (n.x1 ?? 0) + (moved ? 16 : 7);
                  text = (
                    <>
                      {moved ? (
                        <path d={`M${(n.x1 ?? 0) + 1},${want} C${(n.x1 ?? 0) + 8},${want} ${(n.x1 ?? 0) + 8},${y} ${x - 3},${y}`} stroke={COLOR.ink4} fill="none" />
                      ) : null}
                      <text x={x} y={y} dominantBaseline="middle" className="chart-label" fontSize={compact ? 10.5 : 12} fill={COLOR.ink}>
                        {label}
                        {compact ? null : (
                          <tspan className="num" fill={COLOR.ink3}>
                            {" "}
                            {value}
                          </tspan>
                        )}
                      </text>
                    </>
                  );
                }
                return (
                  <g key={n.id} onMouseEnter={() => setHover({ kind: "node", index: i })} className="cursor-default">
                    <rect x={n.x0} y={n.y0} width={(n.x1 ?? 0) - (n.x0 ?? 0)} height={h} rx={2} fill={color} />
                    {text}
                  </g>
                );
              })}
            </g>
          </svg>
          <Particles layout={layout} total={flow.total} />
          {tip ? (
            <div
              className="pointer-events-none absolute z-10 rounded-lg border border-line-2 bg-bg-2/95 px-3 py-2 text-[12.5px] text-ink shadow-xl backdrop-blur"
              style={{ left: Math.min(tip.x + 14, layout.width - 220), top: tip.y + 14, maxWidth: 240 }}
            >
              {tip.text}
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
