"use client";

import Graph from "graphology";
import Sigma from "sigma";
import type { NodeDisplayData } from "sigma/types";
import { useEffect, useImperativeHandle, useRef, useState, type Ref } from "react";
import type { Simulation } from "d3-force";
import GraphCanvas2D from "@/components/trail/graph-canvas-2d";
import { initialPositions, makeSimulation, ringRadii, type SimNode } from "@/components/trail/graph-layout";
import type { TrailEdge, TrailNode } from "@/lib/types";

/**
 * The trail as a WebGL graph: sigma renders, d3-force places (see graph-layout.ts). Everything
 * visual (colors, sizes, what is hidden) arrives as functions so filters never rebuild the graph.
 * Where hardware WebGL is not available, the same layout is drawn on a 2D canvas instead.
 */

export interface GraphStyle {
  color: (n: TrailNode) => string;
  size: (n: TrailNode) => number;
  visible: (n: TrailNode) => boolean;
  edgeColor: (e: TrailEdge) => string;
  edgeSize: (e: TrailEdge) => number;
}

export interface GraphHandle {
  focus: (id: string) => void;
  zoom: (dir: 1 | -1 | 0) => void;
}

export interface GraphProps {
  nodes: TrailNode[];
  edges: TrailEdge[];
  style: GraphStyle;
  layout: "rings" | "force";
  selected: string | null;
  hovered: string | null;
  onHover: (id: string | null, at: { x: number; y: number } | null) => void;
  onSelect: (id: string | null) => void;
  reduceMotion: boolean;
  handle: Ref<GraphHandle>;
  /** Changes whenever `style` changes, so the canvas refreshes. */
  styleVersion: string;
}

function drawHover(context: CanvasRenderingContext2D, data: { x: number; y: number; size: number }) {
  context.beginPath();
  context.arc(data.x, data.y, data.size + 4, 0, Math.PI * 2);
  context.strokeStyle = "#f2b33d";
  context.lineWidth = 2;
  context.stroke();
}

/**
 * WebGL that is worth using: present, and not a software rasterizer. Software WebGL (SwiftShader,
 * llvmpipe) is slower than a plain 2D canvas for this graph, so those fall back as well.
 */
function hardwareWebgl(): boolean {
  try {
    const c = document.createElement("canvas");
    const gl = (c.getContext("webgl2") ?? c.getContext("webgl")) as WebGLRenderingContext | null;
    if (!gl) return false;
    const info = gl.getExtension("WEBGL_debug_renderer_info");
    const renderer = String(gl.getParameter(info ? info.UNMASKED_RENDERER_WEBGL : gl.RENDERER) ?? "");
    gl.getExtension("WEBGL_lose_context")?.loseContext();
    return !/swiftshader|llvmpipe|softpipe|software|basic render/i.test(renderer);
  } catch {
    return false;
  }
}

export default function GraphCanvas(props: GraphProps) {
  const [mode, setMode] = useState<"webgl" | "canvas">(() => (typeof document !== "undefined" && !hardwareWebgl() ? "canvas" : "webgl"));
  if (mode === "canvas") return <GraphCanvas2D {...props} />;
  return <WebGLGraph {...props} onFail={() => setMode("canvas")} />;
}

function WebGLGraph({
  nodes,
  edges,
  style,
  layout,
  selected,
  hovered,
  onHover,
  onSelect,
  reduceMotion,
  handle,
  styleVersion,
  onFail,
}: GraphProps & { onFail: () => void }) {
  const container = useRef<HTMLDivElement>(null);
  const rings = useRef<HTMLCanvasElement>(null);
  const sigmaRef = useRef<Sigma | null>(null);
  const graphRef = useRef<Graph | null>(null);
  const simRef = useRef<Simulation<SimNode, undefined> | null>(null);
  const styleRef = useRef(style);
  const focusRef = useRef<{ selected: string | null; hovered: string | null }>({ selected, hovered });
  const layoutRef = useRef(layout);
  const callbacks = useRef({ onHover, onSelect, onFail });
  const radiiRef = useRef<number[]>([0]);

  useEffect(() => {
    callbacks.current = { onHover, onSelect, onFail };
  }, [onHover, onSelect, onFail]);

  // Build the graph and the renderer once per data set.
  useEffect(() => {
    const el = container.current;
    if (!el) return;
    const graph = new Graph({ type: "directed", multi: false, allowSelfLoops: false });
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const radii = ringRadii(nodes, styleRef.current.size);
    radiiRef.current = radii;
    const start = initialPositions(nodes, radii);
    for (const n of nodes) {
      const p = start.get(n.id) as { x: number; y: number };
      graph.addNode(n.id, { x: p.x, y: p.y, size: styleRef.current.size(n), color: styleRef.current.color(n), label: n.label || null });
    }
    for (const e of edges) {
      if (!byId.has(e.source) || !byId.has(e.target) || graph.hasEdge(e.source, e.target)) continue;
      graph.addEdge(e.source, e.target, { size: styleRef.current.edgeSize(e), color: styleRef.current.edgeColor(e) });
    }
    graphRef.current = graph;

    let renderer: Sigma;
    try {
      const font = getComputedStyle(document.body).fontFamily;
      renderer = new Sigma(graph, el, {
        renderLabels: true,
        labelFont: font,
        labelSize: 11.5,
        labelWeight: "500",
        labelColor: { color: "#c9beaf" },
        labelDensity: 0.45,
        labelGridCellSize: 110,
        labelRenderedSizeThreshold: 8,
        defaultEdgeType: "line",
        defaultDrawNodeHover: drawHover,
        zIndex: true,
        minCameraRatio: 0.06,
        maxCameraRatio: 4,
        stagePadding: 24,
        nodeReducer: (id, data) => {
          const n = byId.get(id);
          if (!n || !styleRef.current.visible(n)) return { ...data, hidden: true };
          const { selected: sel, hovered: hov } = focusRef.current;
          const focus = hov ?? sel;
          if (!focus) return data;
          if (id === focus || id === sel) return { ...data, zIndex: 2, forceLabel: true, size: (data.size as number) + 1.5 };
          if (graph.areNeighbors(id, focus)) return { ...data, zIndex: 1, forceLabel: Boolean(data.label) };
          return { ...data, color: "#2b241e", label: null, zIndex: 0 };
        },
        edgeReducer: (id, data) => {
          const [s, t] = graph.extremities(id);
          const sn = byId.get(s);
          const tn = byId.get(t);
          if (!sn || !tn || !styleRef.current.visible(sn) || !styleRef.current.visible(tn)) return { ...data, hidden: true };
          const { selected: sel, hovered: hov } = focusRef.current;
          const focus = hov ?? sel;
          if (!focus) return data;
          if (s === focus || t === focus) return { ...data, color: "rgba(242,179,61,0.85)", size: Math.max(1.2, data.size as number), zIndex: 1 };
          return { ...data, hidden: true };
        },
      });
    } catch {
      // No usable WebGL context: draw the same layout on a 2D canvas instead.
      requestAnimationFrame(() => callbacks.current.onFail());
      return;
    }
    sigmaRef.current = renderer;
    const R = radii[radii.length - 1] + 30;
    renderer.setCustomBBox({ x: [-R, R], y: [-R, R] });

    renderer.on("enterNode", ({ node }) => {
      focusRef.current.hovered = node;
      const d = renderer.getNodeDisplayData(node);
      const at = d ? renderer.framedGraphToViewport(d) : null;
      callbacks.current.onHover(node, at);
      renderer.refresh({ skipIndexation: true });
    });
    renderer.on("leaveNode", () => {
      focusRef.current.hovered = null;
      callbacks.current.onHover(null, null);
      renderer.refresh({ skipIndexation: true });
    });
    renderer.on("clickNode", ({ node }) => callbacks.current.onSelect(node));
    renderer.on("clickStage", () => callbacks.current.onSelect(null));

    // Hop rings behind the graph, kept in step with the camera.
    const drawRings = () => {
      const c = rings.current;
      if (!c) return;
      const w = el.clientWidth;
      const h = el.clientHeight;
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      if (c.width !== w * dpr || c.height !== h * dpr) {
        c.width = w * dpr;
        c.height = h * dpr;
      }
      const ctx = c.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      if (layoutRef.current !== "rings") return;
      const center = renderer.graphToViewport({ x: 0, y: 0 });
      ctx.font = `500 10px ${getComputedStyle(document.body).fontFamily}`;
      const r0 = radiiRef.current;
      for (let hop = 1; hop < r0.length; hop++) {
        const edge = renderer.graphToViewport({ x: r0[hop], y: 0 });
        const r = Math.hypot(edge.x - center.x, edge.y - center.y);
        ctx.beginPath();
        ctx.arc(center.x, center.y, r, 0, Math.PI * 2);
        ctx.strokeStyle = hop % 5 === 0 ? "rgba(242,179,61,0.16)" : "rgba(255,232,204,0.06)";
        ctx.lineWidth = 1;
        ctx.stroke();
        if (r > 40 && (hop <= 3 || hop % 2 === 1)) {
          ctx.fillStyle = "rgba(201,190,175,0.55)";
          ctx.fillText(`hop ${hop}`, center.x + 4, center.y - r - 4);
        }
      }
    };
    renderer.on("afterRender", drawRings);

    return () => {
      simRef.current?.stop();
      renderer.kill();
      sigmaRef.current = null;
      graphRef.current = null;
    };
  }, [nodes, edges]);

  // Physics: rings or free force layout.
  useEffect(() => {
    layoutRef.current = layout;
    const graph = graphRef.current;
    if (!graph || !sigmaRef.current) return;
    const sim: SimNode[] = nodes
      .filter((n) => graph.hasNode(n.id))
      .map((n) => {
        const a = graph.getNodeAttributes(n.id);
        return { id: n.id, hop: n.hop, r: (a.size as number) + 1.2, service: n.role === "service", x: a.x as number, y: a.y as number };
      });
    const simulation = makeSimulation(sim, nodes, edges, layout, radiiRef.current);
    const apply = () => {
      const pos = new Map(sim.map((s) => [s.id, s]));
      graph.updateEachNodeAttributes(
        (id, attr) => {
          const p = pos.get(id);
          if (p) {
            attr.x = p.x;
            attr.y = p.y;
          }
          return attr;
        },
        { attributes: ["x", "y"] },
      );
    };
    if (reduceMotion) {
      simulation.stop();
      for (let i = 0; i < 260; i++) simulation.tick();
      apply();
    } else {
      simulation.on("tick", apply);
    }
    simRef.current = simulation;
    return () => {
      simulation.stop();
    };
  }, [layout, nodes, edges, reduceMotion]);

  // Restyle without rebuilding.
  useEffect(() => {
    styleRef.current = style;
    const graph = graphRef.current;
    if (!graph) return;
    const byId = new Map(nodes.map((n) => [n.id, n]));
    graph.updateEachNodeAttributes(
      (id, attr) => {
        const n = byId.get(id);
        if (n) {
          attr.color = style.color(n);
          attr.size = style.size(n);
        }
        return attr;
      },
      { attributes: ["color", "size"] },
    );
    const edgeBy = new Map(edges.map((e) => [`${e.source}>${e.target}`, e]));
    graph.updateEachEdgeAttributes(
      (_id, attr, s, t) => {
        const e = edgeBy.get(`${s}>${t}`);
        if (e) {
          attr.color = style.edgeColor(e);
          attr.size = style.edgeSize(e);
        }
        return attr;
      },
      { attributes: ["color", "size"] },
    );
    sigmaRef.current?.refresh();
    // styleVersion stands for the style functions' inputs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [styleVersion]);

  useEffect(() => {
    focusRef.current = { selected, hovered };
    sigmaRef.current?.refresh({ skipIndexation: true });
  }, [selected, hovered]);

  useImperativeHandle(
    handle,
    () => ({
      focus: (id: string) => {
        const r = sigmaRef.current;
        const d = r?.getNodeDisplayData(id) as NodeDisplayData | undefined;
        if (!r || !d) return;
        r.getCamera().animate({ x: d.x, y: d.y, ratio: 0.35 }, { duration: reduceMotion ? 0 : 700 });
      },
      zoom: (dir: 1 | -1 | 0) => {
        const camera = sigmaRef.current?.getCamera();
        if (!camera) return;
        const duration = reduceMotion ? 0 : 300;
        if (dir === 1) camera.animatedZoom({ duration });
        else if (dir === -1) camera.animatedUnzoom({ duration });
        else camera.animatedReset({ duration });
      },
    }),
    [reduceMotion],
  );

  return (
    <div className="absolute inset-0">
      <canvas ref={rings} aria-hidden="true" className="pointer-events-none absolute inset-0 h-full w-full" />
      <div ref={container} className="absolute inset-0" />
    </div>
  );
}
