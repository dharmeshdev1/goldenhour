import type { Metadata } from "next";
import { PageHero } from "@/components/ui/section";
import { TrailExplorer } from "@/components/trail/trail-explorer";

export const metadata: Metadata = {
  title: "The trail",
  description: "Every address the stolen value reached, on every chain, as a graph, a table and SQL.",
};

export default function TrailPage() {
  return (
    <>
      <PageHero
        eyebrow="Layer 4 · Trace"
        title={
          <>
            Follow the money, <em className="text-gold">hop by hop</em>.
          </>
        }
        lead={
          <p>
            An address joins the trail when most of the value it ever received came from the trail. The walk starts at the victim&apos;s
            wallets and the addresses named by investigators, and stops at services.
          </p>
        }
      />
      <TrailExplorer />
    </>
  );
}
