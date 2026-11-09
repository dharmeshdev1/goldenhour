import { chainColor } from "@/lib/chains";
import type { Tone } from "@/lib/sankey";

/** Semantic colors, matching the CSS tokens. Canvas and SVG code needs the literal values. */
export const COLOR = {
  bg: "#0a0807",
  ink: "#f4ede3",
  ink2: "#c9beaf",
  ink3: "#9b9082",
  ink4: "#6f665b",
  gold: "#f2b33d",
  gold2: "#ffd27a",
  red: "#f0544f",
  ember: "#ff8550",
  ice: "#8fd3ff",
  violet: "#ac8fff",
  slate: "#929aa8",
  green: "#6bd6a0",
  line: "rgba(255,232,204,0.08)",
} as const;

export function toneColor(tone: Tone, id = ""): string {
  switch (tone) {
    case "victim":
      return COLOR.gold;
    case "chain":
      return id.startsWith("chain:") && id !== "chain:__other" ? chainColor(id.slice(6)) : COLOR.ink3;
    case "held":
      return COLOR.ember;
    case "frozen":
      return COLOR.ice;
    case "mixer":
      return COLOR.violet;
    case "service":
      return COLOR.slate;
    default:
      return COLOR.ink3;
  }
}

export const EXIT_LABEL: Record<string, string> = {
  exchange: "Exchanges",
  mixer: "Mixers",
  bridge: "Bridges",
  lending: "Lending",
  dex: "DEX pools",
  staking: "Staking",
  other: "Other",
};

export const EXIT_COLOR: Record<string, string> = {
  exchange: "#a7b0bd",
  mixer: COLOR.violet,
  bridge: "#c2a98a",
  lending: "#8e9aa9",
  dex: "#b7a7c9",
  staking: "#9fb5a8",
  other: "#857d72",
};
