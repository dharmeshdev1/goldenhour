/**
 * The five layers of Goldenhour, each mapped to the link in the Binance chain it addresses.
 * Narrative copy only: every figure on the site comes from the API at runtime, and the few facts
 * quoted here carry the id of the source they come from.
 */

export type LayerId = "guard" | "watch" | "respond" | "trace" | "screen";

export interface Layer {
  id: LayerId;
  index: number;
  name: string;
  /** One line a non-engineer can read in two seconds. */
  promise: string;
  what: string;
  /** Where the Binance chain broke, quoted from a source. */
  binance: { text: string; source: string };
  href: string;
  cta: string;
}

export const LAYERS: Layer[] = [
  {
    id: "guard",
    index: 1,
    name: "Guard",
    promise: "Stops the signer from signing what no one asked for.",
    what: "A policy co-signer in front of the signing service. It refuses EIP-7702 delegations, transactions with no matching withdrawal intent, and velocity breaches.",
    binance: {
      text: "A container deployed with stolen credentials reached the signing service, which signed 50 EIP-7702 delegations.",
      source: "tangem",
    },
    href: "/guard/",
    cta: "See the policy and cases",
  },
  {
    id: "watch",
    index: 2,
    name: "Watch",
    promise: "Notices a drain in its first minute, not its fourth hour.",
    what: "Real-time drain detection on custody wallets: delegation changes, outflow velocity, fan-out and transfers after a halt.",
    binance: {
      text: "The delegations kept working after withdrawals were suspended, until they were revoked at 00:10 UTC.",
      source: "thoropass",
    },
    href: "/golden-hour/#watch",
    cta: "Replay detection on the theft",
  },
  {
    id: "respond",
    index: 3,
    name: "Respond",
    promise: "Gets freeze requests to issuers while the money can still be frozen.",
    what: "The freeze window. Prepares freeze packets for issuers, bridges and exchanges in minutes, with the addresses, amounts and transactions each one needs.",
    binance: {
      text: "Tether froze the stolen USDT 37 minutes after it arrived, but 434 million USDT had already been swapped for ETH.",
      source: "elliptic",
    },
    href: "/golden-hour/#respond",
    cta: "Move the response clock",
  },
  {
    id: "trace",
    index: 4,
    name: "Trace",
    promise: "Shows where the money is now, on every chain at once.",
    what: "Live cross-chain tracing: from the victim's wallets through swaps, bridges, peel chains and fan-outs, recomputed every few minutes.",
    binance: {
      text: "TRX fanned out to about 7,700 addresses, and THORChain was used to convert stolen value into bitcoin.",
      source: "elliptic",
    },
    href: "/trail/",
    cta: "Explore the trail",
  },
  {
    id: "screen",
    index: 5,
    name: "Screen",
    promise: "Lets any exchange check a deposit without revealing who it checked.",
    what: "Exposure checks exchanges and fintechs run on deposits, with k-anonymous lookups so the address being checked never leaves the browser.",
    binance: {
      text: "The FBI published 628 addresses two days later. The trail keeps growing after any list is published.",
      source: "fbi",
    },
    href: "/screen/",
    cta: "Screen an address",
  },
];

export const LAYER_BY_ID: Record<LayerId, Layer> = Object.fromEntries(LAYERS.map((l) => [l.id, l])) as Record<LayerId, Layer>;

export interface StageLayer {
  layer: LayerId;
  why: string;
  /** True for the link where Goldenhour would most directly have broken the chain. */
  breakpoint?: boolean;
}

/**
 * Which layer answers each stage of the attack. Matched on what the stage says rather than its
 * position, so the timeline can be edited or reordered in case.json without breaking the story.
 * Goldenhour does not secure laptops; for the entry stages the honest answer is that Guard keeps
 * a compromised laptop from being enough.
 */
const STAGE_RULES: { test: RegExp; result: StageLayer }[] = [
  {
    test: /revok/i,
    result: {
      layer: "guard",
      why: "With delegations refused at the signer, there would have been nothing left to revoke.",
    },
  },
  {
    test: /last transfer|kept working|suspend/i,
    result: {
      layer: "watch",
      why: "Watch treats any outflow after withdrawals are halted as critical, which would have exposed the live delegations at once.",
    },
  },
  {
    test: /signing service|delegation/i,
    result: {
      layer: "guard",
      breakpoint: true,
      why: "Guard refuses every EIP-7702 delegation from a custody wallet and holds any request that matches no withdrawal intent. This is the link it is built to break.",
    },
  },
  {
    test: /homebrew|package|pyenv|recruiter|interview|backdoor/i,
    result: {
      layer: "guard",
      why: "Goldenhour does not police laptops. Guard assumes some will be compromised and keeps signing authority out of their reach.",
    },
  },
  {
    test: /token|deploy|took over|take over|takes over/i,
    result: {
      layer: "guard",
      why: "Deployment rights are not signing rights. Guard judges what is being signed, not which service asked.",
    },
  },
  {
    test: /freez/i,
    result: {
      layer: "respond",
      why: "Respond prepares the issuer request in minutes, so a freeze can land before the swap instead of after it.",
    },
  },
  {
    test: /drain begins|leave|hot wallets/i,
    result: {
      layer: "watch",
      why: "Emptying several hot wallets within minutes is the outflow pattern Watch alerts on, wallet by wallet.",
    },
  },
  {
    test: /split|follow|fan|thorchain|bridge/i,
    result: {
      layer: "trace",
      why: "Trace follows every split as it settles, so freeze requests name where the money is now rather than where it was.",
    },
  },
  {
    test: /fbi|attribut|publishes/i,
    result: {
      layer: "screen",
      why: "Screen lets exchanges check deposits against the whole trail, privately, from the moment it is published.",
    },
  },
];

export function layerForStage(what: string, phase: "before" | "during" | "after"): StageLayer {
  for (const rule of STAGE_RULES) if (rule.test.test(what)) return rule.result;
  if (phase === "before") return { layer: "guard", why: "Guard keeps preparation from turning into signatures." };
  if (phase === "during") return { layer: "watch", why: "Watch alerts on the outflow as it happens." };
  return { layer: "trace", why: "Trace keeps following the money after the theft ends." };
}
