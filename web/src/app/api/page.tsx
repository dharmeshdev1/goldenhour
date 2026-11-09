import type { Metadata } from "next";
import { ApiDocs } from "@/components/api-docs/api-docs";
import { PageHero } from "@/components/ui/section";

export const metadata: Metadata = {
  title: "API",
  description: "The static JSON and Parquet files behind every figure, and the self-hosted REST API for screening, policy checks and live streams.",
};

export default function ApiPage() {
  return (
    <>
      <PageHero
        eyebrow="API"
        title={
          <>
            The same data, <em className="text-gold">for your systems</em>.
          </>
        }
        lead={
          <p>
            Every figure on this site comes from files you can fetch yourself. Integrate them as they are, or run the server and keep screening
            and policy checks inside your own network.
          </p>
        }
      />
      <ApiDocs />
    </>
  );
}
