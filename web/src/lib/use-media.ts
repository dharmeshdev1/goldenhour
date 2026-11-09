"use client";

import { useSyncExternalStore } from "react";

/** A media query as React state. On the server, and before hydration, it reads as false. */
export function useMedia(query: string): boolean {
  return useSyncExternalStore(
    (cb) => {
      const mq = window.matchMedia(query);
      mq.addEventListener("change", cb);
      return () => mq.removeEventListener("change", cb);
    },
    () => window.matchMedia(query).matches,
    () => false,
  );
}
