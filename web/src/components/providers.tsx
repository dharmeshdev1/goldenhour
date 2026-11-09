"use client";

import { MotionConfig } from "motion/react";
import type { ReactNode } from "react";

/** Motion respects the reader's reduced-motion setting everywhere. */
export function Providers({ children }: { children: ReactNode }) {
  return <MotionConfig reducedMotion="user">{children}</MotionConfig>;
}
