"use client";

import { useEffect, useImperativeHandle, useRef } from "react";
import type { GraphProps } from "@/components/trail/graph-canvas";
import { initialPositions, makeSimulation, ringRadii, type SimNode } from "@/components/trail/graph-layout";
import type { TrailNode } from "@/lib/types";

/**
 * The fallback renderer for browsers without WebGL: the same layout and interactions (hover,
 * click, drag to pan, wheel to zoom) drawn on a 2D canvas. Fine for a few thousand nodes.
 */

interface View {
  k: number;
  x: number;
  y: number;
}

export default function GraphCanvas2D({ nodes, edges, style, layout, selected, hovered, onHover, onSelect, reduceMotion, handle, styleVersion }: GraphProps) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const view = useRef<View>({ k: 1, x: 0, y: 0 });
  const fit = useRef(1);
  const sim = useRef<SimNode[]>([]);
  const radii = useRef<number[]>([0]);
  const state = useRef({ style, selected, hovered, layout, onHover, onSelect });
  const frame = useRef(0);
  const draw = useRef<() => void>(() => {});

  useEffect(() => {
    state.current = { style, selected, hovered, layout, onHover, onSelect };
    draw.current();
  }, [style, selected, hovered, layout, onHover, onSelect, styleVersion]);

  // Data: positions start on the hop rings.
  useEffect(() => {
    const r = ringRadii(nodes, state.current.style.size);
    radii.current = r;
    const start = initialPositions(nodes, r);
    sim.current = nodes.map((n) => {
      const p = start.get(n.id) as { x: number; y: number };
      return { id: n.id, hop: n.hop, r: state.current.style.size(n) + 1.2, service: n.role === "service", x: p.x, y: p.y };
    });
  }, [nodes]);

  // Drawing and interaction.
  useEffect(() => {
    const el = canvas.current;
    if (!el) return;
    const ctx = el.getContext("2d");
    if (!ctx) return;
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const neighbors = new Map<string, Set<string>>();
    for (const e of edges) {
      if (!neighbors.has(e.source)) neighbors.set(e.source, new Set());
      if (!neighbors.has(e.target)) neighbors.set(e.target, new Set());
      (neighbors.get(e.source) as Set<string>).add(e.target);
      (neighbors.get(e.target) as Set<string>).add(e.source);
    }
    const font = getComputedStyle(document.body).fontFamily;
    const size = () => ({ w: el.clientWidth, h: el.clientHeight });

    const toScreen = (x: number, y: number) => {
      const { w, h } = size();
      const k = view.current.k * fit.current;
      return { x: w / 2 + view.current.x + x * k, y: h / 2 + view.current.y + y * k };
    };

    draw.current = () => {
      cancelAnimationFrame(frame.current);
      frame.current = requestAnimationFrame(() => {
        const { w, h } = size();
        const dpr = Math.min(2, window.devicePixelRatio || 1);
        if (el.width !== Math.round(w * dpr) || el.height !== Math.round(h * dpr)) {
          el.width = Math.round(w * dpr);
          el.height = Math.round(h * dpr);
        }
        const R = radii.current[radii.current.length - 1] + 30;
        fit.current = Math.min(w, h) / (2 * R);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, w, h);
        const { style: st, selected: sel, hovered: hov, layout: lay } = state.current;
        const focus = hov ?? sel;
        const near = focus ? neighbors.get(focus) ?? new Set<string>() : null;
        const k = view.current.k * fit.current;
        const pos = new Map(sim.current.map((s) => [s.id, s]));
        if (lay === "rings") {
          const c = toScreen(0, 0);
          ctx.lineWidth = 1;
          for (let hop = 1; hop < radii.current.length; hop++) {
            ctx.beginPath();
            ctx.arc(c.x, c.y, radii.current[hop] * k, 0, Math.PI * 2);
            ctx.strokeStyle = hop % 5 === 0 ? "rgba(242,179,61,0.16)" : "rgba(255,232,204,0.06)";
            ctx.stroke();
          }
        }
        for (const e of edges) {
          const s = byId.get(e.source);
          const t = byId.get(e.target);
          const a = pos.get(e.source);
          const b = pos.get(e.target);
          if (!s || !t || !a || !b || !st.visible(s) || !st.visible(t)) continue;
          const on = focus ? e.source === focus || e.target === focus : true;
          if (focus && !on) continue;
          const p = toScreen(a.x ?? 0, a.y ?? 0);
          const q = toScreen(b.x ?? 0, b.y ?? 0);
          ctx.beginPath();
          ctx.moveTo(p.x, p.y);
          ctx.lineTo(q.x, q.y);
          ctx.strokeStyle = focus ? "rgba(242,179,61,0.85)" : st.edgeColor(e);
          ctx.lineWidth = Math.max(0.5, st.edgeSize(e) * 0.6);
          ctx.stroke();
        }
        const drawNode = (n: TrailNode) => {
          const p = pos.get(n.id);
          if (!p || !st.visible(n)) return;
          const s = toScreen(p.x ?? 0, p.y ?? 0);
          const r = Math.max(1.2, st.size(n) * Math.sqrt(view.current.k));
          const dim = focus && n.id !== focus && n.id !== sel && !near?.has(n.id);
          ctx.beginPath();
          ctx.arc(s.x, s.y, r, 0, Math.PI * 2);
          ctx.fillStyle = dim ? "#2b241e" : st.color(n);
          ctx.fill();
          if (n.id === focus || n.id === sel) {
            ctx.beginPath();
            ctx.arc(s.x, s.y, r + 4, 0, Math.PI * 2);
            ctx.strokeStyle = "#f2b33d";
            ctx.lineWidth = 2;
            ctx.stroke();
          }
          if (!dim && n.label && (r >= 8 || n.id === focus || n.id === sel)) {
            ctx.font = `500 11.5px ${font}`;
            ctx.fillStyle = "#c9beaf";
            ctx.fillText(n.label, s.x + r + 4, s.y + 4);
          }
        };
        for (const n of nodes) if (!focus || n.id !== focus) drawNode(n);
        if (focus && byId.has(focus)) drawNode(byId.get(focus) as TrailNode);
      });
    };

    const hit = (mx: number, my: number): TrailNode | null => {
      let best: TrailNode | null = null;
      let bestD = Infinity;
      const pos = new Map(sim.current.map((s) => [s.id, s]));
      for (const n of nodes) {
        if (!state.current.style.visible(n)) continue;
        const p = pos.get(n.id);
        if (!p) continue;
        const s = toScreen(p.x ?? 0, p.y ?? 0);
        const r = Math.max(4, state.current.style.size(n) * Math.sqrt(view.current.k) + 3);
        const d = Math.hypot(s.x - mx, s.y - my);
        if (d <= r && d < bestD) {
          best = n;
          bestD = d;
        }
      }
      return best;
    };

    let drag: { x: number; y: number; vx: number; vy: number; moved: boolean } | null = null;
    const local = (e: PointerEvent | WheelEvent) => {
      const b = el.getBoundingClientRect();
      return { x: e.clientX - b.left, y: e.clientY - b.top };
    };
    const onDown = (e: PointerEvent) => {
      const p = local(e);
      drag = { x: p.x, y: p.y, vx: view.current.x, vy: view.current.y, moved: false };
      el.setPointerCapture(e.pointerId);
    };
    const onMove = (e: PointerEvent) => {
      const p = local(e);
      if (drag) {
        const dx = p.x - drag.x;
        const dy = p.y - drag.y;
        if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
        view.current = { ...view.current, x: drag.vx + dx, y: drag.vy + dy };
        draw.current();
        return;
      }
      const n = hit(p.x, p.y);
      const current = state.current.hovered;
      if ((n?.id ?? null) !== current) {
        if (n) {
          const s = sim.current.find((x) => x.id === n.id);
          state.current.onHover(n.id, s ? toScreen(s.x ?? 0, s.y ?? 0) : p);
        } else state.current.onHover(null, null);
      }
      el.style.cursor = n ? "pointer" : "grab";
    };
    const onUp = (e: PointerEvent) => {
      const p = local(e);
      if (drag && !drag.moved) {
        const n = hit(p.x, p.y);
        state.current.onSelect(n ? n.id : null);
      }
      drag = null;
    };
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const p = local(e);
      const { w, h } = size();
      const factor = Math.exp(-e.deltaY * 0.0015);
      const k = Math.min(12, Math.max(0.4, view.current.k * factor));
      const ratio = k / view.current.k;
      // Zoom around the cursor.
      const cx = p.x - w / 2;
      const cy = p.y - h / 2;
      view.current = { k, x: cx - (cx - view.current.x) * ratio, y: cy - (cy - view.current.y) * ratio };
      draw.current();
    };
    const onLeave = () => state.current.onHover(null, null);
    el.addEventListener("pointerdown", onDown);
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerup", onUp);
    el.addEventListener("pointerleave", onLeave);
    el.addEventListener("wheel", onWheel, { passive: false });
    const ro = new ResizeObserver(() => draw.current());
    ro.observe(el);
    draw.current();
    return () => {
      cancelAnimationFrame(frame.current);
      el.removeEventListener("pointerdown", onDown);
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerup", onUp);
      el.removeEventListener("pointerleave", onLeave);
      el.removeEventListener("wheel", onWheel);
      ro.disconnect();
    };
  }, [nodes, edges]);

  // Physics.
  useEffect(() => {
    if (!sim.current.length) return;
    const simulation = makeSimulation(sim.current, nodes, edges, layout, radii.current);
    if (reduceMotion) {
      simulation.stop();
      for (let i = 0; i < 260; i++) simulation.tick();
      draw.current();
    } else {
      simulation.on("tick", () => draw.current());
    }
    return () => {
      simulation.stop();
    };
  }, [layout, nodes, edges, reduceMotion]);

  useImperativeHandle(
    handle,
    () => ({
      focus: (id: string) => {
        const s = sim.current.find((x) => x.id === id);
        if (!s) return;
        const k = 2.6;
        view.current = { k, x: -(s.x ?? 0) * k * fit.current, y: -(s.y ?? 0) * k * fit.current };
        draw.current();
      },
      zoom: (dir: 1 | -1 | 0) => {
        if (dir === 0) view.current = { k: 1, x: 0, y: 0 };
        else {
          const k = Math.min(12, Math.max(0.4, view.current.k * (dir === 1 ? 1.5 : 1 / 1.5)));
          const ratio = k / view.current.k;
          view.current = { k, x: view.current.x * ratio, y: view.current.y * ratio };
        }
        draw.current();
      },
    }),
    [],
  );

  return <canvas ref={canvas} className="absolute inset-0 h-full w-full touch-none" style={{ cursor: "grab" }} aria-hidden="true" />;
}
