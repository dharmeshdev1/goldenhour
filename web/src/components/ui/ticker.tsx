"use client";

import { useReducedMotion } from "motion/react";
import { useEffect, useRef, useState } from "react";

const easeOut = (t: number) => 1 - Math.pow(1 - t, 4);

/**
 * A number that counts to its value: from zero on first view, then from the previous value when
 * a poll brings a new one. Reduced motion shows the value at once.
 */
export function Ticker({
  value,
  format,
  duration = 1400,
  className,
}: {
  value: number | null | undefined;
  format: (v: number) => string;
  duration?: number;
  className?: string;
}) {
  const reduce = useReducedMotion();
  const [shown, setShown] = useState<number | null>(null);
  const from = useRef(0);
  const frame = useRef(0);

  useEffect(() => {
    if (typeof value !== "number" || !Number.isFinite(value)) return;
    cancelAnimationFrame(frame.current);
    if (reduce) {
      from.current = value;
      frame.current = requestAnimationFrame(() => setShown(value));
      return () => cancelAnimationFrame(frame.current);
    }
    const start = performance.now();
    const a = from.current;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const v = a + (value - a) * easeOut(t);
      setShown(v);
      if (t < 1) frame.current = requestAnimationFrame(tick);
      else from.current = value;
    };
    frame.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame.current);
  }, [value, reduce, duration]);

  const final = typeof value === "number" && Number.isFinite(value) ? format(value) : "";
  return (
    <span className={className}>
      {/* Screen readers get the final figure, not the animation. */}
      <span aria-hidden="true">{shown === null ? (final ? format(0) : " ") : format(shown)}</span>
      <span className="sr-only">{final}</span>
    </span>
  );
}
