import type { Metadata } from "next";
import { GoldenHourView } from "@/components/golden-hour/golden-hour-view";
import { RespondSection, WatchSection } from "@/components/golden-hour/watch-respond";

export const metadata: Metadata = {
  title: "The golden hour",
  description: "Minute by minute: what was drained, what could still be frozen, and what a faster response would have saved.",
};

export default function GoldenHourPage() {
  return (
    <>
      <GoldenHourView />
      <WatchSection />
      <RespondSection />
    </>
  );
}
