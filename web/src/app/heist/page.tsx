import type { Metadata } from "next";
import { HeistStory } from "@/components/heist/heist-story";

export const metadata: Metadata = {
  title: "The heist",
  description: "How the attackers reached the signing service, step by step, and which Goldenhour layer answers each step.",
};

export default function HeistPage() {
  return <HeistStory />;
}
