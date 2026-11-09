import type { Metadata } from "next";
import { GuardView } from "@/components/guard/guard-view";
import { PageHero } from "@/components/ui/section";

export const metadata: Metadata = {
  title: "Guard",
  description: "A policy co-signer in front of the signing service: no delegations, no transfer without a withdrawal intent, no breach of velocity limits.",
};

export default function GuardPage() {
  return (
    <>
      <PageHero
        eyebrow="Layer 1 · Guard"
        title={
          <>
            The signer should not sign what <em className="text-gold">nobody asked for</em>.
          </>
        }
        lead={
          <p>
            Guard is a policy co-signer in front of the signing service. It refuses EIP-7702 delegations, transactions with no matching
            withdrawal intent, and velocity breaches, before anything is signed.
          </p>
        }
      />
      <GuardView />
    </>
  );
}
