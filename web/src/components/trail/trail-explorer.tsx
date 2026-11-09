"use client";

import { useReducedMotion } from "motion/react";
import dynamic from "next/dynamic";
import { useCallback, useMemo, useRef, useState } from "react";
import type { GraphHandle, GraphStyle } from "@/components/trail/graph-canvas";
import { NodePanel } from "@/components/trail/node-panel";
import {
  categoryColor,
  colorFor,
  edgeColorFor,
  edgeSizeFor,
  MIN_STEPS,
  ROLE_INFO,
  ROLES,
  sizeFor,
  type ColorBy,
  type SizeBy,
} from "@/components/trail/trail-style";
import { ChainDot } from "@/components/ui/chain-chip";
import { SectionHeader } from "@/components/ui/section";
import { ErrorState, Skeleton } from "@/components/ui/states";
import { chainColor, chainName, isTxNode } from "@/lib/chains";
import { addressLabel, formatCount, formatDate, formatUsd, shortAddress } from "@/lib/format";
import type { Chain, NodeRole, TrailNode } from "@/lib/types";
import { useGraph, useSummary } from "@/lib/use-api";
import { useWidth } from "@/lib/use-width";

const GraphCanvas = dynamic(() => import("@/components/trail/graph-canvas"), {
  ssr: false,
  loading: () => <div className="skeleton absolute inset-0" role="status" aria-label="Loading the graph" />,
});

const SqlConsole = dynamic(() => import("@/components/trail/sql-console"), {
  ssr: false,
  loading: () => <Skeleton className="h-72 w-full" label="Loading the SQL console" />,
});

function Segmented<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: { value: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-[12px] text-ink-3">{label}</span>
      <div className="inline-flex rounded-full border border-line-2 bg-bg-2 p-0.5" role="group" aria-label={label}>
        {options.map((o) => (
          <button
            key={o.value}
            type="button"
            aria-pressed={value === o.value}
            onClick={() => onChange(o.value)}
            className={`h-7 rounded-full px-3 text-[12.5px] transition-colors ${value === o.value ? "bg-[rgb(242_179_61/0.14)] text-gold-2" : "text-ink-3 hover:text-ink"}`}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}

type SortKey = "held_usd" | "received_usd" | "hop" | "first_ts";

function HoldersTable({ nodes, onShow }: { nodes: TrailNode[]; onShow: (id: string) => void }) {
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "held_usd", dir: -1 });
  const [limit, setLimit] = useState(15);
  const rows = useMemo(
    () =>
      nodes
        .filter((n) => n.role === "seed" || n.role === "trail")
        .sort((a, b) => sort.dir * ((a[sort.key] as number) - (b[sort.key] as number)) || b.held_usd - a.held_usd),
    [nodes, sort],
  );
  const header = (key: SortKey, label: string, right = true) => {
    const on = sort.key === key;
    return (
      <th scope="col" className={right ? "r" : undefined} aria-sort={on ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
        <button
          type="button"
          onClick={() => setSort((s) => ({ key, dir: s.key === key ? ((-s.dir) as 1 | -1) : -1 }))}
          className={`inline-flex items-center gap-1 uppercase ${on ? "text-gold-2" : "hover:text-ink"}`}
        >
          {label}
          <span aria-hidden="true">{on ? (sort.dir === 1 ? "↑" : "↓") : ""}</span>
        </button>
      </th>
    );
  };
  return (
    <div className="card overflow-hidden">
      <div className="scroll-x">
        <table className="table min-w-[760px]">
          <caption className="sr-only">Largest holders on the trail, sortable</caption>
          <thead>
            <tr>
              <th scope="col">Address</th>
              <th scope="col">Role</th>
              {header("hop", "Hop")}
              {header("received_usd", "Received")}
              {header("held_usd", "Held now")}
              {header("first_ts", "First seen")}
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, limit).map((n) => (
              <tr key={n.id} className="hover:bg-[rgb(255_236_210/0.025)]">
                <td>
                  <button type="button" onClick={() => onShow(n.id)} className="flex items-center gap-2 text-left text-ink hover:text-gold-2" title={`${n.address}: show on the graph`}>
                    <ChainDot chain={n.chain} size={7} />
                    <span className={n.label ? "" : "mono text-[12.5px]"}>{addressLabel(n.label, n.address)}</span>
                  </button>
                  <span className="mono mt-0.5 block pl-[15px] text-[11px] text-ink-3">
                    {chainName(n.chain)} · {shortAddress(n.address, 8, 6)}
                  </span>
                </td>
                <td className="text-[12.5px] whitespace-nowrap" style={{ color: ROLE_INFO[n.role].color }}>
                  {ROLE_INFO[n.role].label}
                </td>
                <td className="r num">{n.hop}</td>
                <td className="r num">{formatUsd(n.received_usd)}</td>
                <td className="r num text-ember">{formatUsd(n.held_usd)}</td>
                <td className="r num whitespace-nowrap text-ink-3">{n.first_ts ? formatDate(n.first_ts * 1000) : "n/a"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > limit ? (
        <div className="border-t border-line p-3 text-center">
          <button type="button" className="btn btn-ghost btn-small" onClick={() => setLimit((l) => l + 25)}>
            Show {Math.min(25, rows.length - limit)} more of {formatCount(rows.length)}
          </button>
        </div>
      ) : null}
    </div>
  );
}

export function TrailExplorer() {
  const graph = useGraph();
  const summary = useSummary(false);
  const reduce = useReducedMotion();
  const data = graph.data;
  const [colorBy, setColorBy] = useState<ColorBy>("role");
  const [sizeBy, setSizeBy] = useState<SizeBy>("received");
  const [layout, setLayout] = useState<"rings" | "force">("rings");
  const [chains, setChains] = useState<Set<Chain>>(new Set());
  const [roles, setRoles] = useState<Set<NodeRole>>(new Set(ROLES));
  const [minIdx, setMinIdx] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [hover, setHover] = useState<{ id: string; at: { x: number; y: number } | null } | null>(null);
  const [query, setQuery] = useState("");
  const [sqlOpen, setSqlOpen] = useState(false);
  const handle = useRef<GraphHandle>(null);
  const graphBox = useRef<HTMLDivElement>(null);
  const boxWidth = useWidth(graphBox);

  const nodes = useMemo(() => data?.nodes ?? [], [data]);
  const edges = useMemo(() => data?.edges ?? [], [data]);
  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);
  const chainCounts = useMemo(() => {
    const m = new Map<Chain, number>();
    for (const n of nodes) m.set(n.chain, (m.get(n.chain) ?? 0) + 1);
    return Array.from(m.entries()).sort((a, b) => b[1] - a[1]);
  }, [nodes]);
  const maxValue = useMemo(() => Math.max(1, ...nodes.map((n) => (sizeBy === "held" ? n.held_usd : n.received_usd))), [nodes, sizeBy]);
  const maxEdge = useMemo(() => Math.max(1, ...edges.map((e) => e.usd)), [edges]);
  const min = MIN_STEPS[minIdx];

  const style: GraphStyle = useMemo(
    () => ({
      color: (n) => colorFor(n, colorBy),
      size: (n) => sizeFor(n, sizeBy, maxValue),
      visible: (n) => (chains.size === 0 || chains.has(n.chain)) && roles.has(n.role) && (n.role === "victim" || n.received_usd >= min),
      edgeColor: (e) => edgeColorFor(e, maxEdge),
      edgeSize: (e) => edgeSizeFor(e, maxEdge),
    }),
    [colorBy, sizeBy, maxValue, chains, roles, min, maxEdge],
  );
  const styleVersion = `${colorBy}|${sizeBy}|${[...chains].sort().join(",")}|${[...roles].sort().join(",")}|${min}|${maxValue}`;
  const visibleCount = useMemo(() => nodes.filter(style.visible).length, [nodes, style]);

  const onHover = useCallback((id: string | null, at: { x: number; y: number } | null) => setHover(id ? { id, at } : null), []);
  const select = useCallback((id: string | null) => {
    setSelected(id);
    if (id) handle.current?.focus(id);
  }, []);
  const show = (id: string) => {
    graphBox.current?.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "center" });
    select(id);
  };
  const search = () => {
    const q = query.trim().toLowerCase();
    if (!q) return;
    const hit = nodes.find((n) => n.address.toLowerCase() === q) ?? nodes.find((n) => n.address.toLowerCase().includes(q) || n.label.toLowerCase().includes(q));
    if (hit) select(hit.id);
  };

  const toggle = <T,>(set: Set<T>, v: T): Set<T> => {
    const next = new Set(set);
    if (next.has(v)) next.delete(v);
    else next.add(v);
    return next;
  };

  const hovered = hover ? byId.get(hover.id) : null;
  const selectedNode = selected ? byId.get(selected) ?? null : null;

  const legend =
    colorBy === "chain"
      ? chainCounts.map(([c]) => ({ key: c, label: chainName(c), color: chainColor(c) }))
      : colorBy === "category"
        ? Array.from(new Set(nodes.map((n) => n.category))).map((c) => ({ key: c, label: c || "none", color: categoryColor(c) }))
        : ROLES.map((r) => ({ key: r, label: ROLE_INFO[r].plural, color: ROLE_INFO[r].color }));

  return (
    <>
      <div className="shell">
        {summary.data ? (
          <dl className="mb-8 grid grid-cols-2 gap-px overflow-hidden rounded-2xl border border-line bg-line sm:grid-cols-5">
            {[
              ["Addresses on the trail", formatCount(summary.data.trail.addresses)],
              ["Chains", formatCount(summary.data.trail.chains.length)],
              ["Deepest hop", formatCount(summary.data.trail.max_hop)],
              ["Transfers followed", formatCount(summary.data.trail.transfers)],
              ["Services reached", formatCount(summary.data.trail.services_touched)],
            ].map(([k, v]) => (
              <div key={k} className="bg-bg-1 px-5 py-4">
                <dt className="eyebrow text-[10px]">{k}</dt>
                <dd className="num mt-1.5 text-[24px] text-ink">{v}</dd>
              </div>
            ))}
          </dl>
        ) : null}

        <div className="card p-4 sm:p-5">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
            <Segmented label="Color" value={colorBy} onChange={setColorBy} options={[{ value: "role", label: "Role" }, { value: "category", label: "Category" }, { value: "chain", label: "Chain" }]} />
            <Segmented label="Size" value={sizeBy} onChange={setSizeBy} options={[{ value: "received", label: "Received" }, { value: "held", label: "Held now" }]} />
            <Segmented label="Layout" value={layout} onChange={setLayout} options={[{ value: "rings", label: "Hop rings" }, { value: "force", label: "Force" }]} />
            <form
              className="ml-auto flex w-full items-center gap-2 sm:w-auto"
              role="search"
              onSubmit={(e) => {
                e.preventDefault();
                search();
              }}
            >
              <label htmlFor="trail-search" className="sr-only">
                Find an address or label
              </label>
              <input id="trail-search" className="input h-9 min-h-9 text-[13px] sm:w-64" placeholder="Find address or label" value={query} onChange={(e) => setQuery(e.target.value)} />
              <button type="submit" className="btn btn-ghost btn-small">
                Find
              </button>
            </form>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-1.5 border-t border-line pt-4" role="group" aria-label="Filter by chain">
            <span className="mr-1 text-[12px] text-ink-3">Chains</span>
            {chainCounts.map(([c, count]) => {
              const on = chains.size === 0 || chains.has(c);
              return (
                <button
                  key={c}
                  type="button"
                  aria-pressed={chains.has(c)}
                  onClick={() => setChains((s) => toggle(s, c))}
                  className={`chip h-7 cursor-pointer transition-opacity ${on ? "" : "opacity-45"} ${chains.has(c) ? "border-gold-3 text-ink" : ""}`}
                >
                  <ChainDot chain={c} size={6} />
                  {chainName(c)}
                  <span className="num text-[11px] text-ink-3">{count}</span>
                </button>
              );
            })}
            {chains.size ? (
              <button type="button" className="text-[12px] text-ink-3 underline hover:text-ink" onClick={() => setChains(new Set())}>
                All chains
              </button>
            ) : null}
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-3">
            <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Filter by role">
              <span className="mr-1 text-[12px] text-ink-3">Roles</span>
              {ROLES.map((r) => (
                <button
                  key={r}
                  type="button"
                  aria-pressed={roles.has(r)}
                  onClick={() => setRoles((s) => toggle(s, r))}
                  className={`chip h-7 cursor-pointer ${roles.has(r) ? "" : "opacity-45"}`}
                >
                  <span aria-hidden="true" className="inline-block size-2 rounded-full" style={{ background: ROLE_INFO[r].color }} />
                  {ROLE_INFO[r].plural}
                </button>
              ))}
            </div>
            <label className="flex items-center gap-3 text-[12px] text-ink-3">
              Min. received
              <input type="range" className="range w-32" min={0} max={MIN_STEPS.length - 1} step={1} value={minIdx} onChange={(e) => setMinIdx(Number(e.target.value))} aria-valuetext={formatUsd(min)} />
              <span className="num w-12 text-ink">{min ? formatUsd(min) : "Any"}</span>
            </label>
          </div>
        </div>

        <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
          <div
            ref={graphBox}
            className="card relative h-[520px] overflow-hidden sm:h-[640px]"
            style={{ background: "radial-gradient(60% 60% at 50% 50%, rgba(242,179,61,0.06), transparent 70%), var(--color-bg-1)" }}
            role="img"
            aria-label={`Graph of ${formatCount(visibleCount)} addresses on the trail, arranged by hop from the victim. Use the table below for a keyboard accessible list.`}
          >
            {graph.error && !data ? (
              <div className="p-6">
                <ErrorState error={graph.error} onRetry={graph.reload} what="The trail graph" />
              </div>
            ) : data ? (
              <GraphCanvas
                nodes={nodes}
                edges={edges}
                style={style}
                styleVersion={styleVersion}
                layout={layout}
                selected={selected}
                hovered={hover?.id ?? null}
                onHover={onHover}
                onSelect={select}
                reduceMotion={Boolean(reduce)}
                handle={handle}
              />
            ) : (
              <div className="skeleton absolute inset-0" role="status" aria-label="Loading the graph" />
            )}
            {hovered && hover?.at ? (
              <div
                className="pointer-events-none absolute z-20 w-60 rounded-xl border border-line-2 bg-bg-2/95 p-3 text-[12.5px] shadow-2xl backdrop-blur"
                style={{ left: Math.max(8, Math.min(hover.at.x + 16, (boxWidth || 600) - 252)), top: Math.max(8, hover.at.y - 20) }}
              >
                <p className="eyebrow text-[10px]" style={{ color: ROLE_INFO[hovered.role].color }}>
                  {ROLE_INFO[hovered.role].label} · {chainName(hovered.chain)}
                </p>
                <p className="mt-1 truncate text-[13.5px] text-ink">{isTxNode(hovered.id) ? `Transaction ${shortAddress(hovered.address, 8, 6)}` : addressLabel(hovered.label, hovered.address)}</p>
                <dl className="num mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-ink-3">
                  <dt>{hovered.role === "victim" ? "Sent" : "Received"}</dt>
                  <dd className="text-right text-ink">{formatUsd(hovered.received_usd)}</dd>
                  <dt>Held now</dt>
                  <dd className="text-right text-ember">{formatUsd(hovered.held_usd)}</dd>
                  <dt>Hop</dt>
                  <dd className="text-right text-ink">{hovered.hop}</dd>
                </dl>
                <p className="mt-2 text-[11px] text-ink-3">Click to pin details</p>
              </div>
            ) : null}
            <div className="absolute top-3 left-3 z-10 flex max-w-[70%] flex-wrap gap-x-3 gap-y-1 rounded-lg bg-bg/70 px-3 py-2 text-[11.5px] text-ink-2 backdrop-blur">
              {legend.slice(0, 11).map((l) => (
                <span key={l.key} className="inline-flex items-center gap-1.5">
                  <span aria-hidden="true" className="inline-block size-2 rounded-full" style={{ background: l.color }} />
                  {l.label}
                </span>
              ))}
            </div>
            <div className="absolute right-3 bottom-3 z-10 flex flex-col gap-1">
              {[
                { d: 1 as const, label: "Zoom in", icon: "M8 3v10M3 8h10" },
                { d: -1 as const, label: "Zoom out", icon: "M3 8h10" },
                { d: 0 as const, label: "Reset view", icon: "M3 3h4M3 3v4M13 13H9M13 13V9M13 3H9M13 3v4M3 13h4M3 13V9" },
              ].map((b) => (
                <button key={b.label} type="button" onClick={() => handle.current?.zoom(b.d)} aria-label={b.label} className="inline-flex size-9 items-center justify-center rounded-lg border border-line-2 bg-bg-2/90 text-ink-2 hover:text-ink">
                  <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
                    <path d={b.icon} stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" fill="none" />
                  </svg>
                </button>
              ))}
            </div>
            <p className="absolute bottom-3 left-3 z-10 rounded-lg bg-bg/70 px-3 py-1.5 text-[11.5px] text-ink-3 backdrop-blur">
              {formatCount(visibleCount)} of {formatCount(nodes.length)} shown
              {data?.truncated ? ` · top ${formatCount(nodes.length)} of ${formatCount(data.total_nodes)} by value` : ""}
            </p>
          </div>
          <div className="min-h-[300px] xl:h-[640px]">
            {selectedNode ? (
              <NodePanel node={selectedNode} nodes={byId} edges={edges} onSelect={select} onClose={() => setSelected(null)} />
            ) : (
              <div className="card-quiet flex h-full flex-col justify-center p-6 text-[13.5px] leading-relaxed text-ink-3">
                <p className="text-[15px] text-ink">Select an address</p>
                <p className="mt-2">
                  Click any node, or a row in the table below, to pin its details here: explorer links, where its value came from and went, and
                  why it is on the trail.
                </p>
                <p className="mt-3">Rings are hops from the victim. Bitcoin transactions sit between their inputs and outputs. Gold edges are cross-chain swaps.</p>
              </div>
            )}
          </div>
        </div>
      </div>

      <section className="shell mt-20" aria-labelledby="holders-title">
        <SectionHeader
          id="holders-title"
          as="h2"
          eyebrow="Largest holders"
          title="Where the stolen value sits"
          lead={<p>Addresses on the trail, sorted by what they hold now. Choose a column to sort; choose an address to find it on the graph.</p>}
        />
        {data ? <HoldersTable nodes={nodes} onShow={show} /> : <Skeleton className="h-96 w-full" />}
      </section>

      <section className="shell mt-20" aria-labelledby="sql-title">
        <SectionHeader
          id="sql-title"
          as="h2"
          eyebrow="For analysts"
          title="Query the full trail with SQL"
          lead={
            <p>
              The graph shows the top addresses by value. Every transfer and address is in two Parquet files you can query here, in your browser,
              with DuckDB. Nothing you type leaves this page.
            </p>
          }
        />
        <div className="card p-5 sm:p-6">
          {sqlOpen ? (
            <SqlConsole />
          ) : (
            <div className="flex flex-col items-start gap-4 sm:flex-row sm:items-center sm:justify-between">
              <p className="max-w-xl text-[14px] leading-relaxed text-ink-2">
                The console downloads the query engine (about 7 MB compressed) and the data files on first use.
              </p>
              <button type="button" className="btn btn-gold" onClick={() => setSqlOpen(true)}>
                Open the SQL console
              </button>
            </div>
          )}
        </div>
      </section>
    </>
  );
}
