import type { Metadata } from "next";
import { ScreenTool } from "@/components/screen/screen-tool";
import { PageHero } from "@/components/ui/section";

export const metadata: Metadata = {
  title: "Screen an address",
  description: "Check whether an address is on the trail of the Binance theft, without revealing which address you checked.",
};

export default function ScreenPage() {
  return (
    <>
      <PageHero
        eyebrow="Layer 5 · Screen"
        title={
          <>
            Check an address. <em className="text-gold">Keep it to yourself.</em>
          </>
        }
        lead={
          <p>
            See whether an address is on the trail of the Binance theft, or received value from it. The address never leaves your browser; the
            server only learns a short prefix shared by thousands of others.
          </p>
        }
      />
      <ScreenTool />
    </>
  );
}
