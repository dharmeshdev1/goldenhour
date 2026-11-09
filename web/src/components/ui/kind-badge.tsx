import type { FeedKind } from "@/lib/types";

const KIND: Record<FeedKind, { label: string; color: string }> = {
  move: { label: "Move", color: "#b9ae9f" },
  swap: { label: "Swap", color: "#ffd27a" },
  bridge: { label: "Bridge", color: "#d8bf9c" },
  exit: { label: "Exit", color: "#a7b0bd" },
  mix: { label: "Mix", color: "#ac8fff" },
  freeze: { label: "Freeze", color: "#8fd3ff" },
  unfreeze: { label: "Unfreeze", color: "#f0a07a" },
};

export function kindLabel(kind: FeedKind): string {
  return KIND[kind]?.label ?? kind;
}

export function KindBadge({ kind }: { kind: FeedKind }) {
  const k = KIND[kind] ?? KIND.move;
  return (
    <span
      className="inline-flex h-[22px] items-center rounded-md px-2 font-mono text-[10.5px] font-medium uppercase tracking-[0.1em]"
      style={{ color: k.color, background: `color-mix(in oklab, ${k.color} 12%, transparent)`, boxShadow: `inset 0 0 0 1px color-mix(in oklab, ${k.color} 28%, transparent)` }}
    >
      {k.label}
    </span>
  );
}
