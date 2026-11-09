"use client";

import { useEffect, useState, type RefObject } from "react";

/** The content width of an element, kept current with a ResizeObserver. 0 until measured. */
export function useWidth(ref: RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      const w = Math.round(entry.contentRect.width);
      setWidth((prev) => (prev === w ? prev : w));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return width;
}

/** True while an element is on screen (used to pause animation loops off screen). */
export function useOnScreen(ref: RefObject<HTMLElement | null>, margin = "120px"): boolean {
  const [on, setOn] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(([entry]) => setOn(entry.isIntersecting), { rootMargin: margin });
    io.observe(el);
    return () => io.disconnect();
  }, [ref, margin]);
  return on;
}
