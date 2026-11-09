import type { Metadata } from "next";
import { MethodView } from "@/components/method/method-view";
import { PageHero } from "@/components/ui/section";
import { METHOD_INTRO } from "@/content/method";

export const metadata: Metadata = {
  title: "Method",
  description: METHOD_INTRO,
};

export default function MethodPage() {
  return (
    <>
      <PageHero eyebrow="Methodology and limitations" title="How the numbers are made" lead={<p>{METHOD_INTRO}</p>} />
      <MethodView />
    </>
  );
}
